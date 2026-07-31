"""Live health check for every external data source.

Because this tool depends entirely on other people's servers, the most useful
thing it can do when something breaks is tell you precisely which server
broke. Run `python -m tnland doctor` any time results look wrong.

Design note: an earlier version probed a hardcoded lat/lon, which turned out
to land in Rutherford county -- one of the nine that run their own GIS. That
meant the check passed while never touching the statewide parcel service or
the land-use join, i.e. it gave a green light to the two code paths most
worth verifying. So this version does not guess a location. It asks the
statewide service for a real parcel by attribute, then tests everything
against whatever it gets back.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from shapely.geometry import Point

from . import config, geo
from .http import SourceError, arcgis_query, probe
from .sources import drivetimes, hazards, parcels, roads, soils, terrain

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

_state = {"fail": 0, "warn": 0}


def _line(status: str, label: str, detail: str = "") -> None:
    marks = {"pass": f"{GREEN}PASS{RESET}", "fail": f"{RED}FAIL{RESET}",
             "warn": f"{YELLOW}WARN{RESET}", "skip": f"{DIM}SKIP{RESET}"}
    if status == "fail":
        _state["fail"] += 1
    elif status == "warn":
        _state["warn"] += 1
    print(f"  {marks[status]}  {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def _timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None]:
    start = time.time()
    try:
        return fn(), time.time() - start, None
    except Exception as exc:  # noqa: BLE001
        return None, time.time() - start, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Finding a real parcel to test against
# ---------------------------------------------------------------------------

def _statewide_sample() -> dict[str, Any] | None:
    """Pull one real parcel from the statewide service.

    Asks for a mid-size rural tract with a deeded acreage on file, because
    that exercises the most: slope over varied ground, a soils query with
    several map units, and an acreage figure to cross-check the geometry
    against.
    """
    url = parcels._statewide_url()
    for where in (
        "DEEDAC > 20 AND DEEDAC < 200 AND OWNER IS NOT NULL",
        "DEEDAC > 5 AND OWNER IS NOT NULL",
        "OWNER IS NOT NULL",
    ):
        try:
            data = arcgis_query(
                url,
                where=where,
                out_fields=list(config.TN_PARCEL_FIELDS.values()),
                result_record_count=1,
                use_cache=False,
            )
        except SourceError:
            continue
        feats = data.get("features", [])
        if feats:
            return parcels._from_statewide(feats[0])
    return None


# ---------------------------------------------------------------------------

def run(verbose: bool = False, lon: float | None = None,
        lat: float | None = None) -> int:
    print(f"\n{BOLD}TN Land Tool -- source health check{RESET}\n")

    # --- endpoint reachability ------------------------------------------
    print(f"{BOLD}Endpoint discovery{RESET}")
    groups = {
        "TN statewide parcels": config.TN_PARCELS,
        "TN land use (Comptroller OLG)": config.TN_LANDUSE_SERVICE,
        "FEMA flood zones": config.FEMA_NFHL,
        "USFWS wetlands": config.NWI,
        "USGS 3DEP DEM": config.DEM_IMAGESERVER,
    }
    for label, candidates in groups.items():
        result = probe(candidates)
        ok = result["working"] is not None
        _line("pass" if ok else "fail", label)
        if verbose or not ok:
            for c in result["candidates"]:
                mark = "ok " if c.get("ok") else "err"
                print(f"        {DIM}{mark} {c['url']}{RESET}")
                if not c.get("ok"):
                    print(f"        {DIM}    {c.get('detail', c.get('http'))}{RESET}")

    # --- county services and their field mappings ------------------------
    print(f"\n{BOLD}County services (the 9 outside the statewide layer){RESET}")
    for name, cfg in config.COUNTY_SERVICES.items():
        _check_county(name, cfg, verbose)
    for name, why in config.COUNTIES_NO_SERVICE.items():
        _line("skip", f"{name.title()}", "no public service by design -- " + why[:60])

    # --- the statewide path ----------------------------------------------
    print(f"\n{BOLD}Statewide path{RESET}")
    if lon is not None and lat is not None:
        record, secs, err = _timed(lambda: parcels.at_point(lon, lat))
        origin = f"point {lat}, {lon}"
    else:
        record, secs, err = _timed(_statewide_sample)
        origin = "sampled from the statewide service"

    if err or record is None:
        _line("fail", "Parcel lookup", err or "no parcel found")
        print("\nCannot test the data layers without a parcel.\n")
        return 1

    from_statewide = "statewide" in (record.get("source") or "").lower()
    _line("pass", "Parcel lookup",
          f"{secs:.1f}s  {record.get('county')} / {record.get('parcel_id')} / "
          f"{record.get('owner')}  [{origin}]")

    if from_statewide:
        if record.get("gislink"):
            _line("pass", "GISLINK present", repr(record["gislink"]))
            lu, _, lu_err = _timed(lambda: parcels._enrich_statewide(record))
            if lu_err:
                _line("fail", "Land-use join", lu_err)
            elif record.get("land_use_code"):
                _line("pass", "Land-use join",
                      f"code {record['land_use_code']} = {record.get('land_use')}, "
                      f"appraisal {record.get('appraisal')}, "
                      f"buildings {record.get('buildings')}")
            else:
                _line("fail", "Land-use join",
                      "statewide parcel with a GISLINK matched no OLG record -- "
                      "appraised value and vacancy filters will not work")
        else:
            _line("fail", "GISLINK present",
                  "statewide parcel has no GISLINK, so no land-use join is possible")
    else:
        _line("skip", "Land-use join",
              f"parcel came from {record.get('source')}, which carries its own "
              "values and has no GISLINK -- not applicable")

    # --- data layers ------------------------------------------------------
    print(f"\n{BOLD}Data layers{RESET}")
    geom = record.get("geometry")
    if geom is None:
        _line("fail", "Parcel geometry", "no geometry returned")
        return 1

    checks: list[tuple[str, Callable[[], dict], Callable[[dict], str]]] = [
        ("FEMA flood", lambda: hazards.flood(geom),
         lambda r: f"{len(r.get('zones', []))} zone(s), "
                   f"{r.get('sfha_pct', 0)}% SFHA"),
        ("Wetlands", lambda: hazards.wetlands(geom),
         lambda r: f"{r.get('pct', 0)}% wetland"),
        ("Slope / DEM", lambda: terrain.slope(geom),
         lambda r: f"mean {r.get('mean_slope_pct')}%, "
                   f"{r.get('buildable_pct')}% under 15%, "
                   f"{r.get('pixel_size_m')} m pixels"),
        ("Road access", lambda: roads.access(geom),
         lambda r: r.get("summary", "")[:70]),
        ("SSURGO soils", lambda: soils.soils(geom),
         lambda r: f"{len(r.get('units', []))} map unit(s)"),
    ]
    for label, fn, describe in checks:
        result, secs, err = _timed(fn)
        if err:
            _line("fail", label, err)
        elif not result.get("available"):
            _line("fail", label, result.get("error", "unavailable"))
        else:
            _line("pass", label, f"{secs:.1f}s  {describe(result)}")

    c = geom.centroid
    dt, secs, err = _timed(lambda: drivetimes.drive_times(c.x, c.y))
    if err or not (dt or {}).get("available"):
        _line("fail", "Drive times", err or (dt or {}).get("error", ""))
    else:
        found = [r for r in dt["results"] if r.get("found")]
        broken = [r for r in dt["results"] if r.get("error")]
        sample = (f"{found[0]['label'].lower()} {found[0]['minutes']:.0f} min "
                  f"({found[0]['name']})" if found else "no matches")
        status = "warn" if broken else "pass"
        detail = f"{secs:.1f}s  {len(found)}/{len(dt['results'])} categories, {sample}"
        if broken:
            detail += "  [" + broken[0].get("error", "")[:60] + "]"
        _line(status, "Drive times", detail)

    elev, secs, err = _timed(lambda: terrain.point_elevation(c.x, c.y))
    if err or not (elev or {}).get("available"):
        _line("fail", "Point elevation", err or (elev or {}).get("error", ""))
    else:
        _line("pass", "Point elevation",
              f"{secs:.1f}s  {elev['elevation_ft']} ft "
              f"({elev.get('source_resolution_m')} m source)")

    # --- geometry sanity -------------------------------------------------
    print(f"\n{BOLD}Geometry sanity{RESET}")
    computed = geo.acres(geom)
    deeded = record.get("deeded_acres")
    if deeded and deeded > 0:
        delta = abs(computed - deeded) / deeded * 100
        _line("pass" if delta < 25 else "warn",
              f"GIS acres {computed:.2f} vs deeded {deeded:.2f}",
              f"{delta:.0f}% apart")
    else:
        _line("warn", "Acreage cross-check",
              "this parcel has no deeded acreage on file, so the geometry "
              "could not be validated against the record")

    ring = geo.buffer_m(Point(c.x, c.y), 1609.34)
    ring_acres = geo.acres(ring)
    expected = 3.14159265 * (1609.34 ** 2) / 4046.856
    _line("pass" if abs(ring_acres - expected) / expected < 0.02 else "fail",
          f"1-mile buffer measures {ring_acres:,.0f} acres",
          f"expected ~{expected:,.0f}")

    # --- summary ----------------------------------------------------------
    print()
    fails, warns = _state["fail"], _state["warn"]
    if fails:
        print(f"{RED}{fails} check(s) failed{RESET}"
              + (f", {warns} warning(s)" if warns else ""))
        print(f"{DIM}Re-run with --verbose to see every candidate URL tried.{RESET}")
    elif warns:
        print(f"{YELLOW}All critical checks passed, {warns} warning(s).{RESET}")
    else:
        print(f"{GREEN}All checks passed.{RESET}")
    print()
    return 1 if fails else 0


def _check_county(name: str, cfg: dict[str, Any], verbose: bool) -> None:
    """Pull one real feature and confirm the configured field names exist.

    Every county field mapping in config.py was written from service metadata
    rather than from live data, so this is where a mis-transcribed field name
    shows up -- as a mapping that silently yields None on every parcel.
    """
    fields = cfg["fields"]
    try:
        data = arcgis_query(
            cfg["url"], where="1=1", result_record_count=1,
            return_geometry=False, use_cache=False,
        )
    except Exception as exc:  # noqa: BLE001
        _line("fail", f"{cfg['label']}", f"{type(exc).__name__}: {exc}")
        return

    feats = data.get("features", [])
    if not feats:
        _line("warn", f"{cfg['label']}", "service answered but returned no features")
        return

    attrs = feats[0].get("attributes", {}) or {}
    missing, empty = [], []
    for key, field in fields.items():
        names = field if isinstance(field, list) else [field]
        for n in names:
            if n not in attrs:
                missing.append(f"{key}={n}")
            elif attrs[n] in (None, ""):
                empty.append(key)

    if missing:
        _line("fail", f"{cfg['label']}",
              "field(s) not on the layer: " + ", ".join(missing))
        return

    has_sales = bool(fields.get("sale_price"))
    detail = "all configured fields present"
    detail += ", sales available" if has_sales else ", no sale data"
    if empty and verbose:
        detail += f"  (null on this row: {', '.join(sorted(set(empty)))})"
    _line("pass", f"{cfg['label']}", detail)
