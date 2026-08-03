"""Assemble a full due-diligence report for one parcel."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import config, geo, progress
from .sources import (drivetimes, geocode, hazards, parcels, roads,
                      soilanalysis, soils, terrain)


def parcel_report(lon: float, lat: float,
                  include: set[str] | None = None,
                  job: str | None = None) -> dict[str, Any]:
    include = include or {"flood", "wetlands", "slope", "roads", "soils",
                          "drivetimes"}
    progress.update(job, "parcel", "running")
    record = parcels.at_point(lon, lat)
    if record is None:
        progress.update(job, "parcel", "failed", "no parcel at that point")
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
    progress.update(job, "parcel", "done",
                    f"{record.get('county', '')} {record.get('parcel_id', '')}".strip())

    # Each source is an independent network call; run them together so a
    # report takes as long as the slowest one, not the sum of all of them.
    # as_completed (not dict order) so progress reflects reality: fast
    # sources report done while slow ones still show running.
    futures: dict[Any, str] = {}
    started: dict[str, float] = {}
    with ThreadPoolExecutor(max_workers=7) as pool:
        def submit(name: str, fn, *args) -> None:
            progress.update(job, name, "running")
            started[name] = time.time()
            futures[pool.submit(fn, *args)] = name

        if "flood" in include:
            submit("flood", hazards.flood, geom)
        if "wetlands" in include:
            submit("wetlands", hazards.wetlands, geom)
        if "slope" in include:
            submit("terrain", terrain.slope, geom)
        if "roads" in include:
            submit("access", roads.access, geom)
        if "soils" in include:
            submit("soils", soils.soils, geom)
        if "drivetimes" in include:
            submit("drivetimes", drivetimes.drive_times,
                   geom.centroid.x, geom.centroid.y,
                   lambda msg: progress.update(job, "drivetimes", "running", msg))
        if "soilanalysis" in include:
            submit("soilanalysis", soilanalysis.septic, geom)
        submit("elevation", terrain.point_elevation,
               geom.centroid.x, geom.centroid.y)

        for future in as_completed(futures):
            name = futures[future]
            secs = time.time() - started[name]
            try:
                report[name] = future.result()
                ok = report[name].get("available", True)
                progress.update(job, name, "done" if ok else "failed",
                                f"{secs:.1f}s")
            except Exception as exc:  # noqa: BLE001
                report[name] = {
                    "available": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                progress.update(job, name, "failed", type(exc).__name__)

    report["geometry"] = geo.shapely_to_geojson(geom)
    report["centroid"] = [geom.centroid.x, geom.centroid.y]
    report["flags"] = _flags(report)
    report["disclaimer"] = DISCLAIMER
    return report


def address_report(address: str,
                   include: set[str] | None = None,
                   job: str | None = None,
                   min_acres: float | None = None) -> dict[str, Any]:
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
            result = parcels_on_road(street, locality, min_acres=min_acres)
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
        report = parcel_report(m["lon"], m["lat"], include=include, job=job)
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
        report = parcel_report(c.x, c.y, include=include, job=job)
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
        # A numbered address that geocodes but hits no parcel usually landed
        # in the road right-of-way (Census interpolates along the
        # centreline). Offer the bordering parcels rather than guessing --
        # the listing's acreage makes the right one obvious.
        m = matches[0]
        try:
            nearby = parcels.near_point(m["lon"], m["lat"])
        except Exception:  # noqa: BLE001 -- rescue is best-effort
            nearby = []
        if nearby:
            return {
                "found": False,
                "kind": "right_of_way",
                "candidates": [
                    {"address": " -- ".join(filter(None, [
                        h.get("situs_address") or "(no address on file)",
                        f"{h['deeded_acres']:g} ac" if h.get("deeded_acres")
                        else None,
                        f"{h.get('distance_ft', 0):,} ft away"])),
                     "county": h.get("county"), "owner": h.get("owner"),
                     "parcel_id": h.get("parcel_id"),
                     "acres": h.get("deeded_acres"),
                     "lon": h["geometry"].centroid.x,
                     "lat": h["geometry"].centroid.y}
                    for h in nearby
                ],
                "message": (
                    f"{m['address']} geocodes onto the road itself -- a "
                    "right-of-way strip no parcel covers. These are the "
                    "parcels bordering that spot, nearest first. The "
                    "listing's acreage is usually the giveaway; click the "
                    "one that matches."
                ),
            }
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

    sa = report.get("soilanalysis", {})
    if sa.get("available"):
        s = sa.get("summary", {})
        ok_ac = s.get("workable_acres", 0)
        best = s.get("best")
        if ok_ac >= 2 and best:
            flags.append({"level": "ok", "text":
                          f"Septic: ~{ok_ac:.0f} ac rate no worse than "
                          f"'somewhat limited' -- best area is the "
                          f"{best['name'].split(',')[0]} ({best['position']})."})
        elif ok_ac > 0:
            flags.append({"level": "warn", "text":
                          f"Septic siting is tight: only ~{ok_ac:.1f} ac rate "
                          "better than 'very limited'. Perc test early."})
        else:
            flags.append({"level": "warn", "text":
                          "Every mapped soil rates 'very limited' for septic "
                          "-- budget for an engineered system and perc test "
                          "before anything else."})

    dt = report.get("drivetimes", {})
    if dt.get("available"):
        # Only thresholded categories can pass or fail; informational ones
        # (threshold_min None) appear in the panel but never flag.
        required = [r for r in dt.get("results", [])
                    if r.get("threshold_min")]
        over = [r for r in required if r.get("found") and r.get("over")]
        # A required category with no match and no service error means the
        # search genuinely came up empty -- worth a warning. A service error
        # already shows in the panel and should not masquerade as a finding.
        empty = [r for r in required
                 if not r.get("found") and not r.get("error")]
        for r in over:
            level = "bad" if r["minutes"] > 2 * r["threshold_min"] else "warn"
            flags.append({"level": level, "text":
                          f"Nearest {r['label'].lower()} is "
                          f"{r['minutes']:.0f} min away ({r['name']}) -- "
                          f"target is {r['threshold_min']} min."})
        for r in empty:
            flags.append({"level": "warn", "text":
                          f"No {r['label'].lower()} found within its "
                          "search radius."})
        met = [r for r in required if r.get("found") and r.get("over") is False]
        # "All targets met" must mean ALL: a category that errored out is
        # unknown, not met, and must suppress the green summary.
        if required and len(met) == len(required):
            sample = ", ".join(f"{r['label'].lower()} {r['minutes']:.0f} min"
                               for r in met[:3])
            flags.append({"level": "ok", "text":
                          f"All drive-time targets met ({sample})."})

    order = {"bad": 0, "warn": 1, "info": 2, "ok": 3}
    flags.sort(key=lambda f: order.get(f["level"], 4))
    return flags


DISCLAIMER = (
    "All data comes from free public sources: the Tennessee Comptroller, "
    "county GIS services, FEMA, USFWS, USGS and USDA NRCS. It is screening "
    "information, not a survey, title report, appraisal, or jurisdictional "
    "wetland determination. Verify anything you would act on."
)
