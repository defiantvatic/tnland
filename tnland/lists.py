"""Screening: pull every parcel in a drawn area, filter it, export CSV.

This is the list-building half of the tool. It deliberately does NOT include
skip tracing -- there is no free, lawful bulk source for phone numbers, and
pretending otherwise would be the one dishonest feature in the app. What it
does give you is owner name and, where the county publishes it, owner mailing
address, which is enough to send letters.
"""

from __future__ import annotations

import csv
import io
from typing import Any

from . import config, geo
from .sources import parcels


def screen(
    geom_wgs84,
    *,
    min_acres: float | None = None,
    max_acres: float | None = None,
    vacant_only: bool = False,
    raw_land_only: bool = False,
    exclude_structures: bool = False,
    owner_out_of_county: bool = False,
    max_appraisal: float | None = None,
    min_appraisal: float | None = None,
    land_use_codes: list[str] | None = None,
    max_records: int = 5000,
) -> dict[str, Any]:
    records = parcels.in_area(geom_wgs84, max_records=max_records)
    total_found = len(records)
    parcels.apply_bulk_landuse(records)

    kept: list[dict[str, Any]] = []
    for rec in records:
        acres = rec.get("deeded_acres") or rec.get("gis_acres")
        if min_acres is not None and (acres is None or acres < min_acres):
            continue
        if max_acres is not None and (acres is None or acres > max_acres):
            continue

        code = rec.get("land_use_code")
        if vacant_only and (code not in config.VACANT_LU_CODES):
            continue
        if raw_land_only and (code not in config.RAW_LAND_LU_CODES):
            continue
        if land_use_codes and code not in set(land_use_codes):
            continue

        if exclude_structures:
            buildings = rec.get("buildings")
            imp = rec.get("improvement_value")
            has_structure = None
            if buildings is not None:
                has_structure = buildings > 0
            elif imp is not None:
                has_structure = imp > 0
            if has_structure is not False:
                continue

        appraisal = rec.get("appraisal")
        if max_appraisal is not None and (appraisal is None or appraisal > max_appraisal):
            continue
        if min_appraisal is not None and (appraisal is None or appraisal < min_appraisal):
            continue

        if owner_out_of_county:
            mailing = (rec.get("owner_mailing") or "").upper()
            county = (rec.get("county") or "").upper()
            # Only decidable where the county publishes a mailing address.
            if not mailing:
                continue
            if county and county.split()[0] in mailing:
                continue

        kept.append(rec)

    kept.sort(key=lambda r: -(r.get("deeded_acres") or r.get("gis_acres") or 0))

    return {
        "searched": total_found,
        "matched": len(kept),
        "truncated": total_found >= max_records,
        "area_acres": round(geo.acres(geom_wgs84), 1),
        "results": [_row(r) for r in kept],
        "coverage_note": _coverage_note(records),
    }


def _row(rec: dict[str, Any]) -> dict[str, Any]:
    acres = rec.get("deeded_acres") or rec.get("gis_acres")
    appraisal = rec.get("appraisal")
    geom = rec.get("geometry")
    return {
        "county": rec.get("county"),
        "parcel_id": rec.get("parcel_id"),
        "gislink": rec.get("gislink"),
        "owner": rec.get("owner"),
        "owner_mailing": rec.get("owner_mailing"),
        "situs_address": rec.get("situs_address"),
        "acres": round(acres, 3) if acres else None,
        "land_use_code": rec.get("land_use_code"),
        "land_use": rec.get("land_use"),
        "appraisal": appraisal,
        "land_value": rec.get("land_value"),
        "improvement_value": rec.get("improvement_value"),
        "appraised_per_acre": round(appraisal / acres) if (appraisal and acres) else None,
        "buildings": rec.get("buildings"),
        "year_built": rec.get("year_built"),
        "water_sewer": (rec.get("utilities") or {}).get("water_sewer"),
        "electric": (rec.get("utilities") or {}).get("electric"),
        "gas": (rec.get("utilities") or {}).get("gas"),
        "state_floodplain": (rec.get("state_floodplain") or {}).get("in_floodplain"),
        "pct_in_floodplain": (rec.get("state_floodplain") or {}).get("pct_of_parcel"),
        "sale_price": rec.get("sale_price"),
        "sale_date": rec.get("sale_date"),
        "tpad_url": rec.get("tpad_url"),
        "source": rec.get("source"),
        "lon": round(geom.centroid.x, 6) if geom is not None else None,
        "lat": round(geom.centroid.y, 6) if geom is not None else None,
    }


def _coverage_note(records: list[dict[str, Any]]) -> str | None:
    missing = [c for c in config.COUNTIES_NO_SERVICE]
    if not records:
        return (
            "No parcels returned. If your area falls in Knox, Williamson, "
            "Chester or Hickman county, that is expected -- those four have no "
            "public parcel service. "
            + " ".join(f"{c.title()}: {config.COUNTIES_NO_SERVICE[c]}"
                       for c in missing)
        )
    with_landuse = sum(1 for r in records if r.get("land_use_code"))
    if with_landuse < len(records) * 0.5:
        return (
            f"Only {with_landuse} of {len(records)} parcels matched a "
            "Comptroller land-use record, so value and vacancy filters will "
            "under-report in this area."
        )
    return None


CSV_COLUMNS = [
    "county", "parcel_id", "owner", "owner_mailing", "situs_address",
    "acres", "land_use_code", "land_use", "appraisal", "land_value",
    "improvement_value", "appraised_per_acre", "buildings", "year_built",
    "water_sewer", "electric", "gas", "state_floodplain",
    "pct_in_floodplain", "sale_price", "sale_date", "lat", "lon",
    "tpad_url", "source",
]


def to_csv(rows: list[dict[str, Any]], *, dedupe: bool = True,
           require_mailing: bool = False) -> str:
    seen: set[str] = set()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        if require_mailing and not row.get("owner_mailing"):
            continue
        if dedupe:
            key = "|".join(str(row.get(k) or "") for k in
                           ("county", "parcel_id", "owner"))
            if key in seen:
                continue
            seen.add(key)
        writer.writerow(row)
    return buf.getvalue()
