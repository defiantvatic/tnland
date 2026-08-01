"""Drive times to daily-needs destinations.

Answers "how far is the nearest hospital / grocery / big-box store" by real
road-network drive time, not straight-line distance. Around the Highland Rim
that distinction is the whole point: a river gorge can turn five air-miles
into a twenty-five minute drive.

Two free, keyless services:

  1. Overpass finds candidate places by OSM tag (or brand regex, for
     categories like big-box that have no tag). Results are cached against
     a grid-snapped centre, so every parcel in the same ~2 km cell shares
     one query -- the hospitals near a parcel do not change between clicks.
  2. Valhalla (FOSSGIS) answers one time matrix per category: the nearest
     candidates by air go in, the truly nearest by road comes out. Matrixing
     several candidates matters; taking the nearest-by-air alone silently
     picks the wrong destination whenever terrain intervenes.

Per-category failures name the failing service and never sink the whole
panel. Times are free-flow -- no traffic model -- which around rural
Tennessee is close to reality.
"""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt
from typing import Any

import httpx

from .. import cache, config
from ..http import SourceError, client


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2)
    return 6371 * 2 * asin(sqrt(a))


def _snap(x: float) -> float:
    return round(round(x / config.DRIVETIME_GRID_DEG) * config.DRIVETIME_GRID_DEG, 6)


# ---------------------------------------------------------------------------
# Candidate places
# ---------------------------------------------------------------------------

def _poi_query(cat: dict[str, Any], lat: float, lon: float) -> str:
    # Snap the centre for cache sharing; widen the radius so the snap can
    # never exclude a place that a parcel-exact query would have found.
    slat, slon = _snap(lat), _snap(lon)
    radius_m = int(cat["search_mi"] * 1609.34) + 3000
    if "brands" in cat:
        rx = cat["brands"]
        body = (f'nwr["shop"]["brand"~"{rx}",i](around:{radius_m},{slat},{slon});'
                f'nwr["shop"]["name"~"{rx}",i](around:{radius_m},{slat},{slon});')
    else:
        body = f'{cat["overpass"]}(around:{radius_m},{slat},{slon});'
    return f"[out:json][timeout:25];({body});out center tags;"


# Winner-first mirror order, learned per process. Seven categories crawling
# the same dead mirror at a 25 s timeout each turns one Overpass outage into
# minutes of dead waiting; after the first success the working mirror leads.
_mirror_order: list[str] | None = None


def _mirrors() -> list[str]:
    return _mirror_order if _mirror_order else list(config.OVERPASS)


def _remember_winner(endpoint: str) -> None:
    global _mirror_order
    _mirror_order = [endpoint] + [m for m in config.OVERPASS if m != endpoint]


def _overpass_pois(cat: dict[str, Any], lat: float, lon: float) -> list[dict[str, Any]]:
    query = _poi_query(cat, lat, lon)
    key = cache.make_key("overpass-poi", query)
    hit = cache.get(key, ttl_days=config.DRIVETIME_POI_TTL_DAYS)
    if hit is not None:
        return _extract_pois(hit)

    errors = []
    for endpoint in _mirrors():
        try:
            resp = client().post(
                endpoint, data={"data": query},
                timeout=config.OVERPASS_TIMEOUT,
            )
            if resp.status_code == 429:
                errors.append(f"{endpoint}: rate limited")
                continue
            resp.raise_for_status()
            data = resp.json()
            cache.put(key, data)
            _remember_winner(endpoint)
            return _extract_pois(data)
        except httpx.HTTPStatusError as exc:
            errors.append(f"{endpoint}: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{endpoint}: {type(exc).__name__}")
    raise SourceError("; ".join(errors))


def _extract_pois(data: dict[str, Any]) -> list[dict[str, Any]]:
    pois, seen = [], set()
    for el in data.get("elements", []):
        ident = (el.get("type"), el.get("id"))
        if ident in seen:
            continue
        seen.add(ident)
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        name = (el.get("tags") or {}).get("name")
        # Unnamed matches are dropped: a result the user cannot verify by
        # name is worth less than an honest "none found".
        if lat is None or lon is None or not name:
            continue
        pois.append({"name": name, "lat": lat, "lon": lon})
    return pois


# ---------------------------------------------------------------------------
# Valhalla time matrix
# ---------------------------------------------------------------------------

def _matrix(lat: float, lon: float,
            targets: list[dict[str, Any]]) -> dict[str, Any]:
    body = {
        "sources": [{"lat": lat, "lon": lon}],
        "targets": [{"lat": t["lat"], "lon": t["lon"]} for t in targets],
        "costing": "auto",
        "units": "miles",
    }
    key = cache.make_key("valhalla-matrix", body)
    hit = cache.get(key, ttl_days=config.DRIVETIME_POI_TTL_DAYS)
    if hit is not None:
        return hit

    errors = []
    for base in config.VALHALLA:
        try:
            resp = client().post(base.rstrip("/") + "/sources_to_targets",
                                 json=body)
            resp.raise_for_status()
            data = resp.json()
            # Valhalla reports errors as JSON bodies; never cache one.
            if "sources_to_targets" not in data:
                errors.append(f"{base}: {str(data)[:100]}")
                continue
            cache.put(key, data)
            return data
        except httpx.HTTPStatusError as exc:
            errors.append(f"{base}: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{base}: {type(exc).__name__}")
    raise SourceError("Valhalla routing unavailable -- " + "; ".join(errors))


def _nearest_by_road(lat: float, lon: float,
                     candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = sorted(candidates,
                    key=lambda p: _haversine_km(lat, lon, p["lat"], p["lon"]))
    shortlist = ranked[:config.DRIVETIME_MAX_CANDIDATES]
    data = _matrix(lat, lon, shortlist)

    best = None
    for cell in data["sources_to_targets"][0]:
        if cell.get("time") is None:
            continue
        if best is None or cell["time"] < best["time"]:
            best = cell
    if best is None:
        return None
    poi = shortlist[best["to_index"]]
    return {
        "name": poi["name"],
        "minutes": round(best["time"] / 60.0, 1),
        "miles": round(best.get("distance") or 0.0, 1),
        "lat": poi["lat"],
        "lon": poi["lon"],
    }


# ---------------------------------------------------------------------------

def drive_times(lon: float, lat: float,
                on_progress=None) -> dict[str, Any]:
    """Drive time to the nearest destination in every configured category.

    (lon, lat) argument order matches every other source in this app.
    on_progress, when given, is called with a short message as each
    category starts -- the UI shows it while the slow parts run.
    """
    results: list[dict[str, Any]] = []
    answered = 0

    for cat_key, cat in config.DRIVETIME_CATEGORIES.items():
        if on_progress:
            on_progress(f"searching: {cat['label']}")
        entry: dict[str, Any] = {
            "key": cat_key,
            "label": cat["label"],
            "threshold_min": cat.get("threshold_min"),
        }
        try:
            if "fixed" in cat:
                candidates = list(cat["fixed"])
            else:
                candidates = _overpass_pois(cat, lat, lon)
            if not candidates:
                entry.update({
                    "found": False,
                    "note": f"nothing matching within {cat['search_mi']} mi",
                })
            else:
                nearest = _nearest_by_road(lat, lon, candidates)
                if nearest is None:
                    entry.update({"found": False,
                                  "note": "no drivable route found"})
                else:
                    entry.update({"found": True, **nearest})
                    t = cat.get("threshold_min")
                    entry["over"] = (nearest["minutes"] > t) if t else None
            answered += 1
        except SourceError as exc:
            entry.update({"found": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            entry.update({"found": False,
                          "error": f"{type(exc).__name__}: {exc}"})
        results.append(entry)

    if answered == 0:
        first_error = next((r["error"] for r in results if r.get("error")),
                           "no category answered")
        return {"available": False, "results": results, "error": first_error}

    return {
        "available": True,
        "results": results,
        "note": (
            "Free-flow drive times on the OpenStreetMap road network "
            "(Valhalla) from the parcel centroid. No traffic model. "
            "Verify the named place is what you expect -- OSM tagging "
            "is community-maintained."
        ),
    }
