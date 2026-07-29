"""Road access and frontage.

Two independent sources, tried in order:

  1. OpenStreetMap via Overpass -- richer tagging (surface, name, exact
     classification) but the public mirrors rate-limit, time out and go down
     regularly. Not dependable on its own.
  2. Census TIGER/Line via the TIGERweb ArcGIS service -- government-hosted,
     reliable, nationwide, and queried through the same machinery as every
     other layer in this app. Slightly coarser: no surface tags, and rural
     geometry is less precise than OSM where a mapper has been active.

Both are normalised to the same output so the report reads identically
either way; the panel names which source answered.

Important caveat the UI repeats to the user: these are centrelines of
*physical* roads. A parcel touching one is not guaranteed legal access, and
a parcel touching none may still have a recorded easement. This is a
screening signal, not a title opinion.
"""

from __future__ import annotations

from typing import Any

import httpx
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union

from .. import cache, config, geo
from ..http import SourceError, arcgis_query_all, client, pick_working

# Ranked worst-to-best so a parcel touching several roads reports its best one.
ROAD_RANK = {
    "motorway": 9, "trunk": 8, "primary": 7, "secondary": 6,
    "tertiary": 5, "unclassified": 4, "residential": 4,
    "service": 3, "track": 2, "path": 1, "footway": 1, "cycleway": 1,
    "unknown": 0,
}
PUBLIC_ROAD_TYPES = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street", "motorway_link",
    "trunk_link", "primary_link", "secondary_link", "tertiary_link",
}


# ---------------------------------------------------------------------------
# Source 1: OpenStreetMap / Overpass
# ---------------------------------------------------------------------------

def _overpass(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    """bbox is (min_lat, min_lon, max_lat, max_lon) -- Overpass order."""
    query = (
        "[out:json][timeout:25];"
        f'way["highway"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});'
        "out geom;"
    )
    key = cache.make_key("overpass", query)
    hit = cache.get(key, ttl_days=30)
    if hit is not None:
        return hit

    errors = []
    for endpoint in config.OVERPASS:
        try:
            # A short per-mirror timeout on purpose. Three mirrors hanging for
            # a minute each means the user waits three minutes to reach a
            # fallback that would have answered in two seconds.
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
            return data
        except httpx.HTTPStatusError as exc:
            errors.append(f"{endpoint}: HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{endpoint}: {type(exc).__name__}")
    raise SourceError("; ".join(errors))


def _lines_from_overpass(search_area) -> list[tuple[LineString, dict[str, Any]]]:
    minx, miny, maxx, maxy = search_area.bounds
    data = _overpass((miny, minx, maxy, maxx))
    lines = []
    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue
        coords = [(p["lon"], p["lat"]) for p in element.get("geometry", [])]
        if len(coords) < 2:
            continue
        tags = element.get("tags", {}) or {}
        lines.append((LineString(coords), {
            "name": tags.get("name") or tags.get("ref") or "(unnamed)",
            "type": tags.get("highway", "unknown"),
            "surface": tags.get("surface"),
        }))
    return lines


# ---------------------------------------------------------------------------
# Source 2: Census TIGER/Line
# ---------------------------------------------------------------------------

def _lines_from_tiger(search_area) -> list[tuple[LineString, dict[str, Any]]]:
    service = pick_working(config.TIGER_ROADS)
    esri = geo.shapely_to_esri(geo.simplify_for_query(search_area, 100))
    lines: list[tuple[LineString, dict[str, Any]]] = []
    errors = []

    for layer_id in config.TIGER_ROAD_LAYERS:
        url = f"{service.rstrip('/')}/{layer_id}"
        try:
            feats = arcgis_query_all(
                url, geometry=esri, out_fields=["NAME", "MTFCC"],
                max_records=2000,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"layer {layer_id}: {type(exc).__name__}")
            continue
        for f in feats:
            attrs = f.get("attributes", {}) or {}
            shp = geo.esri_to_shapely(f.get("geometry") or {})
            if shp is None or shp.is_empty:
                continue
            mtfcc = (attrs.get("MTFCC") or "").strip().upper()
            info = {
                "name": (attrs.get("NAME") or "").strip() or "(unnamed)",
                "type": config.MTFCC_TO_HIGHWAY.get(mtfcc, "unclassified"),
                "surface": None,
            }
            parts = list(shp.geoms) if hasattr(shp, "geoms") else [shp]
            for part in parts:
                if isinstance(part, LineString) and len(part.coords) >= 2:
                    lines.append((part, info))

    if not lines and errors:
        raise SourceError("TIGER unavailable -- " + "; ".join(errors))
    return lines


# ---------------------------------------------------------------------------

def access(parcel_geom) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}
    search = geo.buffer_m(parcel_geom, config.ACCESS_SEARCH_RADIUS_M + 50)

    lines: list[tuple[LineString, dict[str, Any]]] = []
    source = None
    problems: list[str] = []

    for label, loader in (("OpenStreetMap", _lines_from_overpass),
                          ("Census TIGER/Line", _lines_from_tiger)):
        try:
            lines = loader(search)
            source = label
            break
        except SourceError as exc:
            problems.append(f"{label}: {exc}")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{label}: {type(exc).__name__}: {exc}")

    if source is None:
        out["error"] = "No road source answered. " + " | ".join(problems)
        return out

    out["available"] = True
    out["source"] = source
    if problems:
        out["fallback_note"] = (
            f"Used {source} because " + problems[0].split(":", 1)[0] +
            " was unavailable."
        )

    if not lines:
        out.update({
            "has_road_contact": False,
            "landlocked_signal": True,
            "nearest_road_ft": None,
            "public_frontage_ft": 0.0,
            "frontage_ft": 0.0,
            "best_road_type": None,
            "roads": [],
            "summary": (
                "No mapped road anywhere near this parcel. Treat as possibly "
                "landlocked until you confirm an easement."
            ),
        })
        out["note"] = _note(source)
        return out

    projected, epsg = geo.to_utm(parcel_geom)
    boundary = projected.boundary
    frontage_zone = boundary.buffer(config.ROAD_FRONTAGE_BUFFER_M)

    roads: list[dict[str, Any]] = []
    nearest_m = float("inf")
    frontage_pieces = []

    for line, info in lines:
        proj_line = geo.reproject(line, geo.WGS84, epsg)
        distance = projected.distance(proj_line)
        nearest_m = min(nearest_m, distance)
        touching = proj_line.intersection(frontage_zone)
        length = _length(touching)
        if length > 0 or distance <= config.ACCESS_SEARCH_RADIUS_M:
            roads.append({
                "name": info["name"],
                "type": info["type"],
                "surface": info.get("surface"),
                "public": info["type"] in PUBLIC_ROAD_TYPES,
                "frontage_ft": round(length * 3.28084, 1),
                "distance_ft": round(distance * 3.28084, 1),
            })
        if length > 0:
            frontage_pieces.append(touching)

    total_frontage_m = _length(unary_union(frontage_pieces)) if frontage_pieces else 0.0

    # TIGER splits a single road into many short segments, so merge the
    # per-segment rows back into one line per road name before display.
    roads = _merge_by_name(roads)
    roads.sort(key=lambda r: (-r["frontage_ft"], r["distance_ft"]))
    public_frontage = sum(
        r["frontage_ft"] for r in roads if r["public"] and r["frontage_ft"] > 0
    )

    has_contact = total_frontage_m > 0
    out.update({
        "has_road_contact": has_contact,
        "landlocked_signal": not has_contact
        and nearest_m > config.ACCESS_SEARCH_RADIUS_M,
        "nearest_road_ft": round(nearest_m * 3.28084, 1)
        if nearest_m < float("inf") else None,
        "frontage_ft": round(total_frontage_m * 3.28084, 1),
        "public_frontage_ft": round(public_frontage, 1),
        "best_road_type": max(
            (r["type"] for r in roads if r["frontage_ft"] > 0),
            key=lambda t: ROAD_RANK.get(t, 0),
            default=None,
        ),
        "roads": roads[:12],
    })
    out["summary"] = _summarise(out)
    out["note"] = _note(source)
    return out


def _merge_by_name(roads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for r in roads:
        key = (r["name"], r["type"])
        if key in merged:
            merged[key]["frontage_ft"] = round(
                merged[key]["frontage_ft"] + r["frontage_ft"], 1)
            merged[key]["distance_ft"] = min(
                merged[key]["distance_ft"], r["distance_ft"])
        else:
            merged[key] = dict(r)
    return list(merged.values())


def _length(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    if isinstance(geom, LineString):
        return geom.length
    if isinstance(geom, MultiLineString):
        return sum(g.length for g in geom.geoms)
    if hasattr(geom, "geoms"):
        return sum(_length(g) for g in geom.geoms)
    return 0.0


def _summarise(out: dict[str, Any]) -> str:
    if out["landlocked_signal"]:
        near = out.get("nearest_road_ft")
        return (
            "No mapped road touches this parcel"
            + (f"; nearest is about {near:,.0f} ft away." if near else ".")
            + " Treat as possibly landlocked until you confirm an easement."
        )
    if not out["has_road_contact"]:
        return (
            f"No frontage, but a road passes within "
            f"{out['nearest_road_ft']:,.0f} ft. Check for an access easement."
        )
    if out["public_frontage_ft"] <= 0:
        return (
            f"About {out['frontage_ft']:,.0f} ft of contact, but only with a "
            "service road, track or driveway rather than a public road."
        )
    return (
        f"About {out['public_frontage_ft']:,.0f} ft of frontage on a "
        f"{out['best_road_type']} road."
    )


def _note(source: str) -> str:
    base = (
        "Physical road contact is not the same as legal access; confirm "
        "easements and right-of-way width with a title search."
    )
    if source.startswith("Census"):
        return (
            "Derived from Census TIGER/Line centrelines (OpenStreetMap was "
            "unavailable). TIGER has no surface information and its rural "
            "geometry is coarser, so treat frontage figures as approximate. "
            + base
        )
    return "Derived from OpenStreetMap centrelines. " + base
