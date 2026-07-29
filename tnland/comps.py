"""Land comparables.

Honest scope: Tennessee is a full-disclosure state -- sale consideration is
sworn on the face of the deed under Tenn. Code Ann. 67-4-409 -- but the state
does not publish transaction-level sales in any machine-readable form. TPAD
has them per parcel behind an HTML page that blocks automated clients, and
the Comptroller only releases aggregate ratio studies.

So comps work in exactly four counties, which happen to be the ones excluded
from the statewide parcel layer because they run their own GIS: Davidson,
Hamilton, Montgomery and Rutherford. Everywhere else this module tells you
so plainly rather than inventing a number.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

from . import config, geo
from .http import arcgis_query_all
from .sources.parcels import _from_county


def available_counties() -> list[dict[str, str]]:
    return [
        {"county": c, "label": config.COUNTY_SERVICES[c]["label"]}
        for c in config.COMPS_COUNTIES
    ]


def comps(
    parcel_geom,
    county: str,
    *,
    radius_miles: float = 5.0,
    acre_tolerance: float = 0.6,
    years_back: int = 5,
    subject_acres: float | None = None,
    max_results: int = 300,
) -> dict[str, Any]:
    county = (county or "").upper()
    if county not in config.COMPS_COUNTIES:
        return {
            "available": False,
            "reason": (
                f"{county.title() or 'This county'} does not publish sale "
                "prices in a queryable service. Tennessee records actual sale "
                "consideration on the deed, but only Davidson, Hamilton, "
                "Montgomery and Rutherford expose it through a public API. "
                "For other counties, open the parcel in TPAD to read its sale "
                "history one at a time."
            ),
            "counties_with_comps": available_counties(),
        }

    cfg = config.COUNTY_SERVICES[county]
    fields = cfg["fields"]
    price_field = fields.get("sale_price")
    date_field = fields.get("sale_date")
    acre_field = fields.get("acres")
    valid_field = fields.get("valid_sale")
    if not price_field:
        return {"available": False,
                "reason": f"{cfg['label']} exposes no sale price field."}

    subject_acres = subject_acres or geo.acres(parcel_geom)
    search_area = geo.buffer_m(parcel_geom.centroid, radius_miles * 1609.34)

    lo = subject_acres * (1 - acre_tolerance)
    hi = subject_acres * (1 + acre_tolerance)
    where = f"{price_field} > 1000"
    if acre_field:
        where += f" AND {acre_field} >= {lo:.4f} AND {acre_field} <= {hi:.4f}"

    try:
        feats = arcgis_query_all(
            cfg["url"],
            geometry=geo.shapely_to_esri(geo.simplify_for_query(search_area, 200)),
            where=where,
            max_records=max_results * 4,
            return_geometry=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{cfg['label']} query failed: {exc}"}

    cutoff = dt.date.today().year - years_back
    rows: list[dict[str, Any]] = []
    for f in feats:
        rec = _from_county(f, county)
        price = rec.get("sale_price")
        acres_v = rec.get("deeded_acres") or rec.get("gis_acres")
        if not price or not acres_v or acres_v <= 0:
            continue
        sale_date = rec.get("sale_date")
        year = None
        if sale_date and len(str(sale_date)) >= 4:
            try:
                year = int(str(sale_date)[:4])
            except ValueError:
                year = None
        if year is not None and year < cutoff:
            continue
        ppa = price / acres_v
        # Filter obvious non-arm's-length noise: $1 family transfers at the
        # bottom, and improved-property sales dragged in by a bad acreage
        # figure at the top.
        if ppa < 100 or ppa > 500000:
            continue
        if date_field and rec.get("sale_date") is None:
            continue
        distance_mi = _distance_miles(parcel_geom, rec.get("geometry"))
        rows.append({
            "parcel_id": rec.get("parcel_id"),
            "owner": rec.get("owner"),
            "acres": round(acres_v, 2),
            "sale_price": round(price),
            "sale_date": rec.get("sale_date"),
            "price_per_acre": round(ppa),
            "land_use": rec.get("land_use") or rec.get("land_use_code"),
            "improvement_value": rec.get("improvement_value"),
            "valid_sale": (f.get("attributes", {}) or {}).get(valid_field)
            if valid_field else None,
            "distance_miles": distance_mi,
            "centroid": [rec["geometry"].centroid.x, rec["geometry"].centroid.y]
            if rec.get("geometry") is not None else None,
        })

    # Where the county publishes a validity flag (Davidson's ValidSale), drop
    # the transfers it marks as not usable for valuation -- quitclaims,
    # foreclosures, family transfers. Fall back to everything if that leaves
    # too little to be meaningful, since the flag's coding varies.
    qualified = [r for r in rows if r.get("valid_sale") not in _INVALID_SALE]
    if len(qualified) >= 5:
        rows, validity = qualified, "county-flagged valid sales"
    else:
        validity = "no validity filter applied"

    # Prefer sales with no improvement value -- those are land trades, which
    # is what you want when pricing a lot. Fall back to everything if that
    # leaves too few.
    land_only = [r for r in rows if not r.get("improvement_value")]
    used, basis = (land_only, "vacant-land sales only") if len(land_only) >= 5 \
        else (rows, "all qualifying sales, including improved property")
    basis = f"{basis}, {validity}"

    # Sort nearest first. `or 999` would be wrong here: an adjacent lot
    # rounds to 0.0 miles, which is falsy, and would be pushed to the very
    # bottom -- exactly the comps you most want are the first ones cut.
    used.sort(key=lambda r: 999.0 if r["distance_miles"] is None
              else r["distance_miles"])
    used = used[:max_results]

    if not used:
        return {
            "available": True,
            "count": 0,
            "reason": (
                "No qualifying sales found. Try widening the radius, the "
                "acreage tolerance, or the year range."
            ),
            "county": cfg["label"],
        }

    ppa_values = sorted(r["price_per_acre"] for r in used)
    result = {
        "available": True,
        "county": cfg["label"],
        "basis": basis,
        "count": len(used),
        "subject_acres": round(subject_acres, 2),
        "radius_miles": radius_miles,
        "years_back": years_back,
        "price_per_acre": {
            "min": ppa_values[0],
            "p25": _pct(ppa_values, 25),
            "median": int(statistics.median(ppa_values)),
            "p75": _pct(ppa_values, 75),
            "max": ppa_values[-1],
        },
        "implied_value": {
            "low": int(_pct(ppa_values, 25) * subject_acres),
            "mid": int(statistics.median(ppa_values) * subject_acres),
            "high": int(_pct(ppa_values, 75) * subject_acres),
        },
        "sales": used[:60],
        "note": (
            "Recorded consideration is the greater of price paid or value, and "
            "includes non-arm's-length transfers. Treat the median as a "
            "starting point, not an appraisal."
        ),
    }
    return result


# Values a county uses to mark a transfer as unusable for valuation. Kept
# permissive because the coding is not documented consistently across
# counties -- anything unrecognised is treated as usable.
_INVALID_SALE = {"N", "n", "No", "NO", "no", "F", "f", "0", 0, False}


def _pct(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if lo == hi:
        return float(sorted_values[lo])
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo))


def _distance_miles(a, b) -> float | None:
    if b is None:
        return None
    try:
        pa, epsg = geo.to_utm(a.centroid)
        pb = geo.reproject(b.centroid, geo.WGS84, epsg)
        return round(pa.distance(pb) / 1609.34, 2)
    except Exception:  # noqa: BLE001
        return None
