"""SSURGO soils via the NRCS Soil Data Access endpoint.

SDA is POST-only and form-urlencoded. The response comes back as a bare
table: {"Table": [[column names], [types], [row], [row], ...]}. We ask for
"json+columnname+metadata" so the first two rows are the header, then turn
that into dicts.
"""

from __future__ import annotations

from typing import Any

from .. import config, geo
from ..http import SourceError, post_form

_QUERY = """
SELECT
    m.mukey,
    m.musym,
    m.muname,
    m.farmlndcl,
    (SELECT TOP 1 c2.compname FROM component c2
       WHERE c2.mukey = m.mukey ORDER BY c2.comppct_r DESC) AS dominant_component,
    (SELECT TOP 1 c3.comppct_r FROM component c3
       WHERE c3.mukey = m.mukey ORDER BY c3.comppct_r DESC) AS dominant_pct,
    (SELECT TOP 1 c4.drainagecl FROM component c4
       WHERE c4.mukey = m.mukey ORDER BY c4.comppct_r DESC) AS drainage_class,
    (SELECT TOP 1 c5.slope_r FROM component c5
       WHERE c5.mukey = m.mukey ORDER BY c5.comppct_r DESC) AS typical_slope,
    (SELECT TOP 1 c6.hydricrating FROM component c6
       WHERE c6.mukey = m.mukey ORDER BY c6.comppct_r DESC) AS hydric
FROM SDA_Get_Mukey_from_intersection_with_WktWgs84('{wkt}') AS s
JOIN mapunit m ON m.mukey = s.mukey
"""


def soils(parcel_geom) -> dict[str, Any]:
    out: dict[str, Any] = {"available": False, "units": []}
    wkt = geo.to_wkt_wgs84(parcel_geom)
    if len(wkt) > 60000:
        wkt = geo.to_wkt_wgs84(parcel_geom.convex_hull)
        out["note_geometry"] = "Parcel simplified to its convex hull for the soil query."

    query = _QUERY.format(wkt=wkt.replace("'", "''"))
    last_error = None
    data = None
    for endpoint in config.SDA_POST:
        try:
            data = post_form(
                endpoint,
                {"query": query, "format": "json+columnname+metadata"},
                ttl_days=365,
            )
            break
        except Exception as exc:  # noqa: BLE001
            last_error = f"{endpoint}: {type(exc).__name__}: {exc}"
    if data is None:
        out["error"] = f"Soil Data Access unavailable ({last_error})"
        return out

    table = data.get("Table") if isinstance(data, dict) else None
    if not table or len(table) < 3:
        out["error"] = "No SSURGO map units returned for this parcel."
        return out

    columns = table[0]
    rows = [dict(zip(columns, r)) for r in table[2:]]
    if not rows:
        out["error"] = "No SSURGO map units returned for this parcel."
        return out

    out["available"] = True
    for row in rows:
        out["units"].append({
            "symbol": row.get("musym"),
            "name": row.get("muname"),
            "dominant_component": row.get("dominant_component"),
            "component_pct": _num(row.get("dominant_pct")),
            "drainage": row.get("drainage_class"),
            "typical_slope_pct": _num(row.get("typical_slope")),
            "farmland_class": row.get("farmlndcl"),
            "hydric": row.get("hydric"),
        })

    # SSURGO writes farmlndcl as a sentence, not a code: "All areas are prime
    # farmland", "Prime farmland if drained", "Farmland of statewide
    # importance", "Not prime farmland". Matching on a "prime" prefix misses
    # the most common positive value entirely, so match on the phrase and
    # explicitly exclude the negative.
    prime = [
        u for u in out["units"]
        if "prime farmland" in (u.get("farmland_class") or "").lower()
        and not (u.get("farmland_class") or "").lower().startswith("not prime")
    ]
    hydric = [u for u in out["units"] if (u.get("hydric") or "").lower() == "yes"]
    out["summary"] = {
        "map_unit_count": len(out["units"]),
        "has_prime_farmland": bool(prime),
        "has_hydric_soil": bool(hydric),
        "drainage_classes": sorted({
            u["drainage"] for u in out["units"] if u.get("drainage")
        }),
    }
    out["note"] = (
        "SSURGO reports which soil map units touch the parcel, not how much "
        "of it each covers. Hydric soils are a wetland indicator worth "
        "checking against the NWI layer above."
    )
    return out


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
