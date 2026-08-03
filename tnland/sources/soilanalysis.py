"""Deep soil analysis: per-unit acreage and NRCS septic interpretations.

The regular soils panel lists which SSURGO map units touch the parcel. This
module answers the surveyor questions: how many acres of each soil the
parcel actually contains (real polygon intersection, measured in UTM),
where each soil body sits (compass position), and what NRCS's own
"Septic Tank Absorption Fields" interpretation says about each -- the
rating class plus the named limiting features (slow water movement, depth
to bedrock, flooding, slope).

Three Soil Data Access queries per parcel, cached like everything else.
Run on demand (a panel button, like drive times) so the free USDA service
only pays for parcels that earned the attention; the printable report
includes it automatically.

Honesty notes baked into the output: SSURGO is mapped at 1:24,000 with
2-5 acre minimum delineations, ratings describe the dominant named soil,
and only an on-site perc test (required by the county anyway) settles
septic. "Very limited" is a design warning, not a prohibition -- engineered
and alternative systems are common on the Highland Rim.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from shapely import wkt as shp_wkt
from shapely.ops import unary_union

from .. import config, geo
from ..http import SourceError, post_form

SEPTIC_RULE = "ENG - Septic Tank Absorption Fields"

_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII",
          8: "VIII"}


def _sda(sql: str) -> list[list[Any]]:
    """Run one Soil Data Access query, trying both hosts."""
    errors = []
    for url in config.SDA_POST:
        try:
            data = post_form(url, {"query": sql, "format": "JSON+COLUMNNAME"})
            return data.get("Table", [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {type(exc).__name__}")
    raise SourceError("Soil Data Access unavailable -- " + "; ".join(errors))


def _compass(pt, parcel) -> str:
    """Rough position of a soil body relative to the parcel centre."""
    c = parcel.centroid
    minx, miny, maxx, maxy = parcel.bounds
    dx = (pt.x - c.x) / max((maxx - minx) / 2, 1e-9)
    dy = (pt.y - c.y) / max((maxy - miny) / 2, 1e-9)
    if abs(dx) < 0.25 and abs(dy) < 0.25:
        return "central"
    ns = "N" if dy > 0.25 else ("S" if dy < -0.25 else "")
    ew = "E" if dx > 0.25 else ("W" if dx < -0.25 else "")
    return (ns + ew) or "central"


def _capability(cl: Any, sub: Any) -> str | None:
    try:
        return _ROMAN[int(cl)] + (sub or "")
    except (TypeError, ValueError, KeyError):
        return None


def _rows_as_dicts(table: list[list[Any]]) -> list[dict[str, Any]]:
    if not table:
        return []
    header = table[0]
    return [dict(zip(header, row)) for row in table[1:]]


def septic(parcel_geom) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False}

    # 1. Which soil polygons intersect the parcel, and by how many acres.
    wkt = geo.simplify_for_query(parcel_geom, 200).wkt.replace("'", "''")
    try:
        poly_rows = _rows_as_dicts(_sda(
            "SELECT mukey, mupolygongeo.STAsText() AS wkt FROM mupolygon "
            "WHERE mupolygongeo.STIntersects(geometry::STGeomFromText"
            f"('{wkt}', 4326)) = 1"))
    except SourceError as exc:
        out["error"] = str(exc)
        return out

    pieces: dict[str, list[Any]] = defaultdict(list)
    for row in poly_rows:
        try:
            inter = shp_wkt.loads(row["wkt"]).intersection(parcel_geom)
        except Exception:  # noqa: BLE001
            continue
        if not inter.is_empty:
            pieces[str(row["mukey"])].append(inter)
    if not pieces:
        out["error"] = "SSURGO has no soil polygons mapped at this parcel."
        return out

    bodies = {mk: unary_union(p) for mk, p in pieces.items()}
    acres = {mk: geo.acres(b) for mk, b in bodies.items()}
    total = sum(acres.values())
    keys = ",".join(f"'{mk}'" for mk in bodies)

    # 2. Map unit names and their major components.
    try:
        comp_rows = _rows_as_dicts(_sda(
            "SELECT mu.mukey, mu.musym, mu.muname, mu.farmlndcl, c.compname, "
            "c.comppct_r, c.drainagecl, c.slope_r, c.nirrcapcl, c.nirrcapscl, "
            "c.hydricrating "
            "FROM mapunit mu JOIN component c ON c.mukey = mu.mukey "
            f"WHERE mu.mukey IN ({keys}) AND c.majcompflag = 'Yes' "
            "ORDER BY mu.mukey, c.comppct_r DESC"))
        interp_rows = _rows_as_dicts(_sda(
            "SELECT c.mukey, c.compname, ci.ruledepth, ci.interphrc "
            "FROM component c JOIN cointerp ci ON ci.cokey = c.cokey "
            f"WHERE c.mukey IN ({keys}) AND c.majcompflag = 'Yes' "
            f"AND ci.mrulename = '{SEPTIC_RULE}' "
            "ORDER BY c.mukey, ci.ruledepth"))
    except SourceError as exc:
        out["error"] = str(exc)
        return out

    ratings: dict[tuple[str, str], str] = {}
    reasons: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in interp_rows:
        key = (str(r["mukey"]), r["compname"])
        if str(r["ruledepth"]) == "0":
            ratings[key] = r["interphrc"]
        elif r["interphrc"] and "not rated" not in r["interphrc"].lower():
            reasons[key].append(r["interphrc"])

    by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    for r in comp_rows:
        mk = str(r["mukey"])
        meta.setdefault(mk, {"symbol": r["musym"], "name": r["muname"],
                             "farmland": r["farmlndcl"]})
        key = (mk, r["compname"])
        by_unit[mk].append({
            "name": r["compname"],
            "pct": _num(r["comppct_r"]),
            "drainage": r["drainagecl"],
            "slope_pct": _num(r["slope_r"]),
            "capability": _capability(r["nirrcapcl"], r["nirrcapscl"]),
            "hydric": r["hydricrating"],
            "septic_rating": ratings.get(key),
            "septic_reasons": reasons.get(key, []),
        })

    units = []
    for mk, body in sorted(bodies.items(), key=lambda kv: -acres[kv[0]]):
        comps = by_unit.get(mk, [])
        # The unit speaks with its dominant *rated* component's voice; rock
        # outcrop and other unrated pieces stay visible in the component list.
        rated = [c for c in comps
                 if c["septic_rating"] and "not rated" not in
                 c["septic_rating"].lower()]
        lead = rated[0] if rated else (comps[0] if comps else None)
        m = meta.get(mk, {})
        units.append({
            "mukey": mk,
            "symbol": m.get("symbol"),
            "name": m.get("name", f"map unit {mk}"),
            "acres": round(acres[mk], 2),
            "pct": round(acres[mk] / total * 100, 1),
            "position": _compass(body.centroid, parcel_geom),
            "prime_farmland": (m.get("farmland") or "").startswith("All areas"),
            "septic_rating": lead["septic_rating"] if lead else None,
            "septic_reasons": lead["septic_reasons"] if lead else [],
            "capability": lead["capability"] if lead else None,
            "drainage": lead["drainage"] if lead else None,
            "components": comps,
        })

    workable = [u for u in units if u["septic_rating"] in
                ("Not limited", "Somewhat limited")]
    workable_acres = round(sum(u["acres"] for u in workable), 2)
    best = max(workable, key=lambda u: u["acres"], default=None)

    out.update({
        "available": True,
        "total_acres": round(total, 2),
        "units": units,
        "summary": {
            "workable_acres": workable_acres,
            "very_limited_acres": round(sum(
                u["acres"] for u in units
                if u["septic_rating"] == "Very limited"), 2),
            "best": ({"name": best["name"], "acres": best["acres"],
                      "position": best["position"],
                      "rating": best["septic_rating"]} if best else None),
        },
        "note": (
            "NRCS 'Septic Tank Absorption Fields' interpretation per soil, "
            "acreage from real polygon intersection. SSURGO is mapped at "
            "1:24,000 (2-5 acre minimum areas); ratings describe the named "
            "soil, and 'very limited' means engineered/alternative design, "
            "not necessarily unbuildable. Only the county-required perc "
            "test settles it."
        ),
    })
    return out


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
