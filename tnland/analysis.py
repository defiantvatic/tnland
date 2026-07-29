"""Assemble a full due-diligence report for one parcel."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import config, geo
from .sources import geocode, hazards, parcels, roads, soils, terrain


def parcel_report(lon: float, lat: float,
                  include: set[str] | None = None) -> dict[str, Any]:
    include = include or {"flood", "wetlands", "slope", "roads", "soils"}
    record = parcels.at_point(lon, lat)
    if record is None:
        return {
            "found": False,
            "message": (
                "No parcel found at that point. Tennessee's statewide layer "
                "covers 86 counties; Knox, Williamson, Chester and Hickman "
                "have no public parcel service, so clicks there return nothing."
            ),
        }

    geom = record.get("geometry")
    report: dict[str, Any] = {"found": True, "parcel": _serialise(record)}
    if geom is None:
        report["message"] = "Parcel found but returned no geometry."
        return report

    # Each source is an independent network call; run them together so a
    # report takes as long as the slowest one, not the sum of all of them.
    jobs = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        if "flood" in include:
            jobs["flood"] = pool.submit(hazards.flood, geom)
        if "wetlands" in include:
            jobs["wetlands"] = pool.submit(hazards.wetlands, geom)
        if "slope" in include:
            jobs["terrain"] = pool.submit(terrain.slope, geom)
        if "roads" in include:
            jobs["access"] = pool.submit(roads.access, geom)
        if "soils" in include:
            jobs["soils"] = pool.submit(soils.soils, geom)
        jobs["elevation"] = pool.submit(
            terrain.point_elevation, geom.centroid.x, geom.centroid.y
        )

        for name, future in jobs.items():
            try:
                report[name] = future.result()
            except Exception as exc:  # noqa: BLE001
                report[name] = {
                    "available": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }

    report["geometry"] = geo.shapely_to_geojson(geom)
    report["centroid"] = [geom.centroid.x, geom.centroid.y]
    report["flags"] = _flags(report)
    report["disclaimer"] = DISCLAIMER
    return report


def address_report(address: str,
                   include: set[str] | None = None) -> dict[str, Any]:
    """Resolve a street address to a parcel and run the full report.

    Returns the report directly when there is one unambiguous match, or a
    list of candidates when the geocoder is unsure. Falls back to matching
    the assessor's own ADDRESS field when no geocoder recognises the address.
    """
    # "0 McBroom Branch Rd" and friends are not addresses -- the county
    # never assigned a number. Geocoding one either fails outright or, worse,
    # snaps to a neighbour. Answer the real question instead: which parcels
    # front this road?
    if geocode.is_unnumbered(address):
        from .roadsearch import parcels_on_road

        street, locality = geocode.split_address(address)
        if street and locality:
            result = parcels_on_road(street, locality)
            result["kind"] = "road"
            result["query"] = address
            if result.get("found"):
                return result
            fallback = geocode.search_address_field(address)
            if fallback:
                result["candidates"] = [
                    {"address": h.get("situs_address"), "county": h.get("county"),
                     "owner": h.get("owner"), "parcel_id": h.get("parcel_id"),
                     "lon": h["geometry"].centroid.x if h.get("geometry") is not None else None,
                     "lat": h["geometry"].centroid.y if h.get("geometry") is not None else None}
                    for h in fallback[:20]
                ]
                result["message"] += (
                    " Falling back to parcels whose assessor address mentions "
                    "that road.")
            return result

    matches = geocode.geocode(address)

    for m in matches:
        report = parcel_report(m["lon"], m["lat"], include=include)
        if report.get("found"):
            report["matched_address"] = m["address"]
            report["geocoder"] = m["source"]
            if len(matches) > 1:
                report["other_matches"] = matches[1:]
            return report

    # The geocoder either found nothing, or every point it found landed
    # outside parcel coverage. Try the assessor's address field.
    hits = geocode.search_address_field(address)
    if len(hits) == 1 and hits[0].get("geometry") is not None:
        c = hits[0]["geometry"].centroid
        report = parcel_report(c.x, c.y, include=include)
        if report.get("found"):
            report["matched_address"] = hits[0].get("situs_address")
            report["geocoder"] = "Assessor address field"
            return report
    if hits:
        return {
            "found": False,
            "candidates": [
                {"address": h.get("situs_address"), "county": h.get("county"),
                 "owner": h.get("owner"), "parcel_id": h.get("parcel_id"),
                 "lon": h["geometry"].centroid.x if h.get("geometry") is not None else None,
                 "lat": h["geometry"].centroid.y if h.get("geometry") is not None else None}
                for h in hits[:20]
            ],
            "message": f"{len(hits)} parcels have a similar address on file. "
                       "Pick one.",
        }

    if matches:
        return {
            "found": False,
            "candidates": matches,
            "message": (
                "That address geocoded, but no parcel covers the resulting "
                "point. It may fall in Knox, Williamson, Chester or Hickman "
                "county, which publish no parcel data."
            ),
        }
    return {
        "found": False,
        "message": (
            "Could not find that address. Try the parcel ID instead, or "
            "click the location on the map."
        ),
    }


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k != "geometry"}
    code = record.get("land_use_code")
    out["is_raw_land"] = bool(code and code in config.RAW_LAND_LU_CODES)
    out["is_vacant_code"] = bool(code and code in config.VACANT_LU_CODES)
    imp = record.get("improvement_value")
    buildings = record.get("buildings")
    if buildings is not None:
        out["structures_present"] = buildings > 0
    elif imp is not None:
        out["structures_present"] = imp > 0
    else:
        out["structures_present"] = None
    acres = record.get("deeded_acres") or record.get("gis_acres")
    appraisal = record.get("appraisal")
    if acres and appraisal and acres > 0:
        out["appraised_per_acre"] = round(appraisal / acres, 0)
    return out


def _flags(report: dict[str, Any]) -> list[dict[str, str]]:
    """Short, plain-language warnings and green lights, worst first."""
    flags: list[dict[str, str]] = []
    parcel = report.get("parcel", {})

    flood = report.get("flood", {})
    if flood.get("available"):
        pct = flood.get("sfha_pct", 0)
        zones = flood.get("zones", []) or []
        # Non-SFHA zones still matter. A tract sitting entirely in the 0.2%
        # annual chance shaded X zone carries no insurance mandate but is a
        # real conversation with a lender, so it must not be reported as a
        # green "minor clip" just because the SFHA percentage is zero.
        # Sum, not max: FEMA zone polygons are disjoint, and a parcel split
        # across X, D and OPEN WATER at 30% each is 90% mapped flood zone.
        # Taking the largest single zone would call that a minor clip.
        other = [z for z in zones if not z.get("sfha")]
        other_pct = min(sum(z.get("pct", 0) or 0 for z in other), 100.0)
        if pct >= 50:
            flags.append({"level": "bad", "text":
                          f"{pct:.0f}% of the parcel is in a FEMA special "
                          "flood hazard area."})
        elif pct > 5:
            flags.append({"level": "warn", "text":
                          f"{pct:.0f}% of the parcel is in a FEMA special "
                          "flood hazard area."})
        elif other_pct >= 40:
            names = ", ".join(
                (z.get("subtype") or f"zone {z.get('zone')}") for z in other[:2]
            )
            flags.append({"level": "warn", "text":
                          f"{other_pct:.0f}% sits in a mapped non-SFHA flood "
                          f"zone ({names}). No insurance mandate, but lenders "
                          "will ask."})
        elif zones:
            flags.append({"level": "ok", "text":
                          "Only a minor clip of mapped flood zone."})
        else:
            flags.append({"level": "ok", "text":
                          "No mapped FEMA flood hazard on this parcel."})

    wet = report.get("wetlands", {})
    if wet.get("available") and wet.get("pct", 0) > 5:
        flags.append({"level": "warn", "text":
                      f"{wet['pct']:.0f}% overlaps National Wetlands Inventory "
                      "polygons."})

    ter = report.get("terrain", {})
    if ter.get("available"):
        if ter.get("buildable_pct", 0) < 20:
            flags.append({"level": "bad", "text":
                          f"Only {ter['buildable_pct']:.0f}% of the parcel is "
                          "under 15% slope."})
        elif ter.get("mean_slope_pct", 0) > 20:
            flags.append({"level": "warn", "text":
                          f"Steep ground: mean slope {ter['mean_slope_pct']:.0f}%."})
        else:
            flags.append({"level": "ok", "text":
                          f"{ter.get('terrain')}, "
                          f"{ter.get('buildable_pct', 0):.0f}% under 15% slope."})

    acc = report.get("access", {})
    if acc.get("available"):
        if acc.get("landlocked_signal"):
            flags.append({"level": "bad", "text":
                          "No mapped road touches this parcel -- possible "
                          "landlocked tract."})
        elif not acc.get("has_road_contact"):
            flags.append({"level": "warn", "text":
                          "No road frontage; access may depend on an easement."})
        elif acc.get("public_frontage_ft", 0) <= 0:
            flags.append({"level": "warn", "text":
                          "Frontage is on a track or service road only."})
        else:
            flags.append({"level": "ok", "text": acc.get("summary", "")})

    if parcel.get("structures_present") is False:
        flags.append({"level": "ok", "text":
                      "Assessment records show no structures on this parcel."})
    elif parcel.get("structures_present") is True:
        n = parcel.get("buildings")
        flags.append({"level": "info", "text":
                      f"Assessment records show {int(n)} structure(s)."
                      if n else "Assessment records show improvements."})

    soil = report.get("soils", {})
    if soil.get("available") and soil.get("summary", {}).get("has_hydric_soil"):
        flags.append({"level": "warn", "text":
                      "Hydric soils present -- a wetland indicator."})

    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    flags.sort(key=lambda f: order.get(f["level"], 4))
    return flags


DISCLAIMER = (
    "All data comes from free public sources: the Tennessee Comptroller, "
    "county GIS services, FEMA, USFWS, USGS and USDA NRCS. It is screening "
    "information, not a survey, title report, appraisal, or jurisdictional "
    "wetland determination. Verify anything you would act on."
)
