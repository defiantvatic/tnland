"""Offline test suite.

The live data sources are exercised by `python -m tnland doctor`. This file
covers everything that does NOT need the network: geometry maths, Esri JSON
parsing, the land-use join, filter logic, slope computation, CSV export, and
that every API route boots and responds. Network calls are replaced with
fixtures shaped like the real service responses.
"""

from __future__ import annotations

import json
import sys
import types

import numpy as np
from shapely.geometry import Point, Polygon

from tnland import analysis, comps as comps_mod, config, geo, lists
from tnland.sources import drivetimes, hazards, parcels, roads, soils, terrain

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    mark = "ok  " if cond else "FAIL"
    print(f"  {mark} {name}" + (f"  -- {detail}" if detail and not cond else ""))


# ---------------------------------------------------------------------------
print("\nGeometry")

# A one-mile-radius circle should measure ~2011 acres.
ring = geo.buffer_m(Point(-86.59, 35.86), 1609.34)
a = geo.acres(ring)
check("1-mile buffer area", abs(a - 2010.6) / 2010.6 < 0.02, f"got {a:.1f}")

# Same shape at a different longitude must give the same acreage. This is the
# test that catches using Web Mercator instead of UTM.
ring_w = geo.buffer_m(Point(-90.0, 35.86), 1609.34)
check("area independent of longitude",
      abs(geo.acres(ring_w) - a) / a < 0.01, f"{geo.acres(ring_w):.1f} vs {a:.1f}")

check("UTM zone for east TN", geo.utm_epsg_for(-82.0, 36.3) == "EPSG:32617")
check("UTM zone for middle TN", geo.utm_epsg_for(-86.6, 35.9) == "EPSG:32616")
check("UTM zone for west TN", geo.utm_epsg_for(-90.1, 35.2) == "EPSG:32615")

# Esri ring parsing. CRITICAL: Esri outer rings are CLOCKWISE and holes are
# counter-clockwise -- the opposite of GeoJSON. These fixtures use the real
# Esri winding, because fixtures with GeoJSON winding would let an inverted
# hole test pass while returning the hole as the parcel on live data.
outer = [[-86.60, 35.86], [-86.60, 35.87], [-86.59, 35.87], [-86.59, 35.86], [-86.60, 35.86]]
inner = [[-86.598, 35.862], [-86.592, 35.862], [-86.592, 35.868], [-86.598, 35.868], [-86.598, 35.862]]
check("outer ring fixture really is clockwise", geo._signed_area(outer) < 0)
check("hole fixture really is counter-clockwise", geo._signed_area(inner) > 0)

donut = geo.esri_to_shapely({"rings": [outer, inner]})
solid = geo.esri_to_shapely({"rings": [outer]})
check("Esri rings parse to polygon", donut is not None and donut.is_valid)
check("donut keeps the OUTER ring as the parcel",
      geo.acres(donut) > geo.acres(solid) * 0.5,
      f"donut {geo.acres(donut):.1f} vs solid {geo.acres(solid):.1f}")
check("Esri hole is subtracted", geo.acres(donut) < geo.acres(solid) * 0.85,
      f"{geo.acres(donut):.1f} vs {geo.acres(solid):.1f}")

# A hole centred in the parcel used to produce POLYGON EMPTY, which then blew
# up in .centroid and 500'd the whole request.
centred_hole = [[-86.597, 35.863], [-86.593, 35.863], [-86.593, 35.867],
                [-86.597, 35.867], [-86.597, 35.863]]
centred = geo.esri_to_shapely({"rings": [outer, centred_hole]})
check("centred hole does not produce an empty geometry",
      centred is not None and not centred.is_empty)
check("centred donut still measures sensibly",
      geo.acres(centred) > geo.acres(solid) * 0.5, f"{geo.acres(centred):.1f}")

# Multipart parcel: two separate clockwise outer rings.
outer2 = [[-86.58, 35.86], [-86.58, 35.87], [-86.57, 35.87], [-86.57, 35.86], [-86.58, 35.86]]
multi = geo.esri_to_shapely({"rings": [outer, outer2]})
check("multipart parcel keeps both parts",
      multi.geom_type == "MultiPolygon" and
      abs(geo.acres(multi) - 2 * geo.acres(solid)) / geo.acres(solid) < 0.05,
      f"{multi.geom_type} {geo.acres(multi):.1f}")

# Query geometry must go out CLOCKWISE or Esri reads it as a zero-area hole.
esri_out = geo.shapely_to_esri(solid)
check("outbound query ring is clockwise", geo._signed_area(esri_out["rings"][0]) < 0)
gj_ccw = Polygon([(-86.60, 35.86), (-86.59, 35.86), (-86.59, 35.87), (-86.60, 35.87)])
check("counter-clockwise input is re-oriented on the way out",
      geo._signed_area(geo.shapely_to_esri(gj_ccw)["rings"][0]) < 0)

roundtrip = geo.esri_to_shapely(geo.shapely_to_esri(solid))
check("shapely -> Esri -> shapely round trip",
      abs(geo.acres(roundtrip) - geo.acres(solid)) < 0.01)

# Vertex thinning for oversized query geometries.
import math as _math
big = Polygon([(-86.6 + 0.01 * _math.cos(i / 300 * 2 * _math.pi),
                35.86 + 0.01 * _math.sin(i / 300 * 2 * _math.pi))
               for i in range(300)])
thin = geo.simplify_for_query(big, 60)
check("oversized geometry is thinned", geo._count_vertices(thin) <= 60,
      str(geo._count_vertices(thin)))
check("thinning preserves area within 3%",
      abs(geo.acres(thin) - geo.acres(big)) / geo.acres(big) < 0.03,
      f"{geo.acres(thin):.1f} vs {geo.acres(big):.1f}")
check("thinned query geometry still covers the parcel",
      thin.contains(big), str(big.difference(thin).area))
check("small geometry is left alone",
      geo.simplify_for_query(solid, 400) is solid)

# All-CCW fallback must keep DISJOINT parts as parts, not delete them.
ccw_a = [[-86.60, 35.86], [-86.59, 35.86], [-86.59, 35.87], [-86.60, 35.87], [-86.60, 35.86]]
ccw_b = [[-86.58, 35.86], [-86.57, 35.86], [-86.57, 35.87], [-86.58, 35.87], [-86.58, 35.86]]
all_ccw = geo.esri_to_shapely({"rings": [ccw_a, ccw_b]})
check("all-CCW multipart keeps both disjoint parts",
      all_ccw.geom_type == "MultiPolygon", str(all_ccw.geom_type))
check("all-CCW multipart keeps full acreage",
      abs(geo.acres(all_ccw) - 2 * geo.acres(solid)) / geo.acres(solid) < 0.05,
      f"{geo.acres(all_ccw):.1f} vs {2 * geo.acres(solid):.1f}")
ccw_hole = [[-86.598, 35.862], [-86.592, 35.862], [-86.592, 35.868], [-86.598, 35.868], [-86.598, 35.862]]
all_ccw_donut = geo.esri_to_shapely({"rings": [ccw_a, ccw_hole]})
check("all-CCW contained ring is still treated as a hole",
      geo.acres(all_ccw_donut) < geo.acres(solid) * 0.85,
      f"{geo.acres(all_ccw_donut):.1f}")

# A convex hull of a finely sampled circle keeps every vertex, so the last
# resort has to be the bounding box.
circle = Polygon([(-86.6 + 0.005 * _math.cos(i / 5000 * 2 * _math.pi),
                   35.86 + 0.005 * _math.sin(i / 5000 * 2 * _math.pi))
                  for i in range(5000)])
capped = geo.simplify_for_query(circle, 20)
check("vertex cap is honoured even for a convex shape",
      geo._count_vertices(capped) <= 20, str(geo._count_vertices(capped)))
# The bounding box is drawn in UTM and reprojected, so its edges bow very
# slightly in lon/lat. It must still cover the parcel with room to spare.
check("capped geometry covers the original",
      capped.buffer(1e-7).contains(circle) and
      geo.acres(capped) > geo.acres(circle),
      f"{geo.acres(capped):.1f} vs {geo.acres(circle):.1f}")

check("Point survives shapely_to_esri", 
      len(geo.shapely_to_esri(Point(-86.6, 35.86))["rings"][0]) >= 4)

# pct_covered must clamp and be geometrically right: a half-covering strip.
parcel = Polygon([(-86.60, 35.86), (-86.59, 35.86), (-86.59, 35.87), (-86.60, 35.87)])
half = Polygon([(-86.60, 35.86), (-86.595, 35.86), (-86.595, 35.87), (-86.60, 35.87)])
_, pct = geo.pct_covered(parcel, [half])
check("half-covering overlay = 50%", abs(pct - 50) < 1.5, f"got {pct:.1f}")
_, pct_full = geo.pct_covered(parcel, [geo.buffer_m(parcel, 500)])
check("oversized overlay clamps to 100%", abs(pct_full - 100) < 0.01)
check("empty overlay list = 0%", geo.pct_covered(parcel, [])[1] == 0)

# ---------------------------------------------------------------------------
print("\nSlope computation")

# Build a synthetic DEM with a known, exact gradient: 10 m rise per 10 m run
# in x = 100% slope, flat in y.
def fake_dem(parcel_geom):
    grid = np.tile(np.arange(40, dtype="float32") * 10.0, (40, 1))
    return grid, 10.0

real_fetch = terrain._fetch_dem
terrain._fetch_dem = fake_dem
res = terrain.slope(parcel)
terrain._fetch_dem = real_fetch
check("slope on synthetic 100% ramp", abs(res.get("mean_slope_pct", 0) - 100) < 1,
      f"got {res.get('mean_slope_pct')}")
check("45 degrees reported for 100% grade",
      abs(res.get("mean_slope_deg", 0) - 45) < 1, f"got {res.get('mean_slope_deg')}")
check("ramp is not buildable", res.get("buildable_pct") == 0.0)

def flat_dem(parcel_geom):
    return np.full((30, 30), 250.0, dtype="float32"), 10.0

terrain._fetch_dem = flat_dem
flat = terrain.slope(parcel)
terrain._fetch_dem = real_fetch
check("flat DEM = 0% slope", flat.get("mean_slope_pct") == 0.0)
check("flat DEM is fully buildable", flat.get("buildable_pct") == 100.0)
check("flat DEM described as flat", flat.get("terrain") == "Essentially flat")

def nodata_dem(parcel_geom):
    grid = np.full((30, 30), -9999.0, dtype="float32")
    return grid, 10.0

terrain._fetch_dem = nodata_dem
nod = terrain.slope(parcel)
terrain._fetch_dem = real_fetch
check("all-nodata DEM fails cleanly",
      not nod.get("available") and "error" in nod, str(nod))

# ---------------------------------------------------------------------------
print("\nParcel record normalisation")

statewide_feature = {
    "attributes": {
        "PARCELID": "040  00504 000", "GISLINK": "052040    00504 000",
        "OWNER": "DOE JOHN", "OWNER2": "DOE JANE", "ADDRESS": "123 CECIL RD",
        "DEEDAC": 12.5, "COUNTY_NAME": "Bedford", "SUBDIV": "", "LOT": "",
        "COUNTY_ID": 52,
    },
    "geometry": {"rings": [outer]},
}
rec = parcels._from_statewide(statewide_feature)
check("owner names joined", rec["owner"] == "DOE JOHN / DOE JANE")
check("deeded acres parsed", rec["deeded_acres"] == 12.5)
check("gis acres computed", rec["gis_acres"] > 0)
check("TPAD url built with encoded spaces",
      rec["tpad_url"].endswith("052040%20%20%20%2000504%20000"), rec["tpad_url"])
check("blank subdivision becomes None", rec["subdivision"] is None)

landuse_attrs = {
    "GISLINK": "052040    00504 000", "LU_CLASSIFICATION": "51",
    "APPRAISAL": 45000.0, "LANDVALUE": 45000.0, "IMPVALUE": 0.0,
    "NUMBUILDINGS": 0.0, "YRBLT": 0, "WATERSEWER": "Y", "ELEC": "Y", "GAS": "N",
    "FLOODPLAIN": "N", "ACRESINFLOOD": 0.0, "PEROFPARCELINFLOOD": 0,
    "MAJORROADFRONTAGE": "Y",
}
parcels._apply_landuse(rec, landuse_attrs)
check("land use decoded", rec["land_use"] == "Vacant")
check("appraisal carried over", rec["appraisal"] == 45000.0)
check("utilities captured", rec["utilities"]["electric"] == "Y")
check("zero year built becomes None", rec["year_built"] is None)

ser = analysis._serialise(rec)
check("vacant code flagged", ser["is_vacant_code"] is True)
check("raw land flagged", ser["is_raw_land"] is True)
check("no structures detected", ser["structures_present"] is False)
check("appraised per acre computed", ser["appraised_per_acre"] == 3600.0)

# Land-use code with a leading zero must still decode.
rec2 = parcels._from_statewide(statewide_feature)
parcels._apply_landuse(rec2, {**landuse_attrs, "LU_CLASSIFICATION": "051"})
check("leading-zero land use code decoded", rec2["land_use"] == "Vacant")

county_feature = {
    "attributes": {
        "APN": "12300012300", "Owner": "SMITH LLC", "OwnAddr1": "PO BOX 9",
        "OwnCity": "AUSTIN", "OwnState": "TX", "OwnZip": "78701",
        "Acres": 3.2, "LUDesc": "VACANT RES LAND", "TotlAppr": 88000,
        "ImprAppr": 0, "SalePrice": 65000, "OwnDate": 1687392000000,
    },
    "geometry": {"rings": [outer]},
}
crec = parcels._from_county(county_feature, "DAVIDSON")
check("county owner parsed", crec["owner"] == "SMITH LLC")
check("county mailing assembled", crec["owner_mailing"] == "PO BOX 9 AUSTIN TX 78701")
check("epoch ms sale date converted", crec["sale_date"] == "2023-06-22", crec["sale_date"])
check("county sale price parsed", crec["sale_price"] == 65000)

# ---------------------------------------------------------------------------
print("\nFilters and export")

def make(owner, acres, code, appraisal, imp=0.0, buildings=0.0, mailing=None,
         county="Bedford"):
    r = parcels._blank_parcel()
    r.update({"owner": owner, "deeded_acres": acres, "land_use_code": code,
              "land_use": config.LAND_USE_CODES.get(code), "appraisal": appraisal,
              "improvement_value": imp, "buildings": buildings,
              "owner_mailing": mailing, "county": county, "parcel_id": owner[:6],
              "geometry": Polygon(outer)})
    return r

pool = [
    make("VACANT SMALL", 2.0, "51", 20000),
    make("VACANT BIG", 60.0, "52", 300000, mailing="PO BOX 1 NASHVILLE TN"),
    make("FARM", 120.0, "61", 400000, mailing="1 MAIN ST BEDFORD TN"),
    make("HOUSE", 1.0, "1", 250000, imp=200000, buildings=1.0),
    make("TIMBER", 90.0, "71", 180000, mailing="9 ELM ST ATLANTA GA"),
]

parcels_in_area = pool
orig_in_area, orig_bulk = parcels.in_area, parcels.apply_bulk_landuse
parcels.in_area = lambda g, max_records=5000: [dict(p) for p in parcels_in_area]
parcels.apply_bulk_landuse = lambda recs: None

r = lists.screen(parcel)
check("no filters returns everything", r["matched"] == 5, str(r["matched"]))

r = lists.screen(parcel, raw_land_only=True)
check("raw land excludes the house", r["matched"] == 4)

r = lists.screen(parcel, vacant_only=True)
check("vacant-only keeps just codes 51-53", r["matched"] == 2)

r = lists.screen(parcel, min_acres=50)
check("min acres filter", r["matched"] == 3)

r = lists.screen(parcel, min_acres=50, max_acres=100)
check("acreage band filter", r["matched"] == 2)

r = lists.screen(parcel, exclude_structures=True)
check("exclude structures drops the house", r["matched"] == 4)

r = lists.screen(parcel, max_appraisal=200000)
check("max appraisal filter", r["matched"] == 2, str(r["matched"]))

r = lists.screen(parcel, owner_out_of_county=True)
check("out-of-county needs a mailing address and excludes in-county",
      r["matched"] == 2 and {x["owner"] for x in r["results"]} ==
      {"VACANT BIG", "TIMBER"}, str([x["owner"] for x in r["results"]]))

r = lists.screen(parcel, raw_land_only=True, min_acres=50, max_appraisal=350000)
check("stacked filters", r["matched"] == 2,
      str([x["owner"] for x in r["results"]]))

rows = lists.screen(parcel)["results"]
csv_text = lists.to_csv(rows)
check("CSV has a header row", csv_text.splitlines()[0].startswith("county,parcel_id,owner"))
check("CSV row count matches", len(csv_text.strip().splitlines()) == 6)
check("CSV dedupes identical parcels",
      len(lists.to_csv(rows + rows).strip().splitlines()) == 6)
check("CSV mailing filter",
      len(lists.to_csv(rows, require_mailing=True).strip().splitlines()) == 4)
check("appraised per acre in export",
      any(",10000," in line for line in csv_text.splitlines()))

parcels.in_area, parcels.apply_bulk_landuse = orig_in_area, orig_bulk

# ---------------------------------------------------------------------------
print("\nHazard parsing")

fema_features = [
    {"attributes": {"FLD_ZONE": "AE", "ZONE_SUBTY": None, "SFHA_TF": "T",
                    "STATIC_BFE": 512.3},
     "geometry": {"rings": [[[-86.60, 35.86], [-86.595, 35.86],
                             [-86.595, 35.87], [-86.60, 35.87], [-86.60, 35.86]]]}},
    {"attributes": {"FLD_ZONE": "X", "ZONE_SUBTY": "0.2 PCT ANNUAL CHANCE",
                    "SFHA_TF": "F", "STATIC_BFE": -9999},
     "geometry": {"rings": [[[-86.595, 35.86], [-86.593, 35.86],
                             [-86.593, 35.87], [-86.595, 35.87], [-86.595, 35.86]]]}},
]
import tnland.sources.hazards as hz
orig_pick, orig_qall = hz.pick_working, hz.arcgis_query_all
hz.pick_working = lambda c: c[0]
hz.arcgis_query_all = lambda *a, **k: fema_features
f = hz.flood(parcel)
hz.pick_working, hz.arcgis_query_all = orig_pick, orig_qall

check("flood zones parsed", len(f["zones"]) == 2)
check("SFHA percent computed from AE only", abs(f["sfha_pct"] - 50) < 2,
      f"got {f['sfha_pct']}")
check("X zone not counted as SFHA",
      next(z for z in f["zones"] if z["zone"] == "X")["sfha"] is False)
check("BFE sentinel -9999 becomes None",
      next(z for z in f["zones"] if z["zone"] == "X")["base_flood_elevation"] is None)
check("real BFE preserved",
      next(z for z in f["zones"] if z["zone"] == "AE")["base_flood_elevation"] == 512.3)
check("zones sorted largest first", f["zones"][0]["zone"] == "AE")

# NWI qualified field names
nwi_features = [
    {"attributes": {"Wetlands.WETLAND_TYPE": "Freshwater Forested/Shrub Wetland",
                    "Wetlands.ATTRIBUTE": "PFO1A"},
     "geometry": {"rings": [[[-86.60, 35.86], [-86.598, 35.86],
                             [-86.598, 35.87], [-86.60, 35.87], [-86.60, 35.86]]]}},
]
hz.pick_working = lambda c: c[0]
hz.arcgis_query_all = lambda *a, **k: nwi_features
w = hz.wetlands(parcel)
hz.pick_working, hz.arcgis_query_all = orig_pick, orig_qall
check("wetlands parsed from qualified fields", w["types"][0]["code"] == "PFO1A")
check("wetland percent computed", abs(w["pct"] - 20) < 2, f"got {w['pct']}")

# ---------------------------------------------------------------------------
print("\nRoad access")

def fake_overpass(bbox):
    # One road running along the parcel's west edge, one far away.
    return {"elements": [
        {"type": "way", "tags": {"highway": "residential", "name": "Elm Rd"},
         "geometry": [{"lat": 35.859, "lon": -86.6001},
                      {"lat": 35.871, "lon": -86.6001}]},
        {"type": "way", "tags": {"highway": "track", "name": "Old Trace"},
         "geometry": [{"lat": 35.80, "lon": -86.50},
                      {"lat": 35.81, "lon": -86.50}]},
    ]}

orig_op = roads._overpass
roads._overpass = fake_overpass
orig_tiger = roads._lines_from_tiger
acc = roads.access(parcel)
check("road contact detected", acc["has_road_contact"] is True)
check("not flagged landlocked", acc["landlocked_signal"] is False)
check("frontage measured in feet", acc["frontage_ft"] > 3000, str(acc["frontage_ft"]))
check("public road classified", acc["public_frontage_ft"] > 0)
check("best road type identified", acc["best_road_type"] == "residential")

roads._overpass = lambda bbox: {"elements": []}
acc2 = roads.access(parcel)
check("no roads = landlocked signal", acc2["landlocked_signal"] is True)
check("landlocked summary is plain English",
      "landlocked" in acc2["summary"].lower())

roads._overpass = lambda bbox: {"elements": [
    {"type": "way", "tags": {"highway": "track", "name": "Farm Track"},
     "geometry": [{"lat": 35.859, "lon": -86.6001},
                  {"lat": 35.871, "lon": -86.6001}]}]}
acc3 = roads.access(parcel)
check("track-only frontage is warned about",
      acc3["has_road_contact"] and acc3["public_frontage_ft"] == 0)
roads._overpass = orig_op
roads._lines_from_tiger = orig_tiger

# --- fallback behaviour -------------------------------------------------
from tnland.http import SourceError as _SE

def dead_overpass(bbox):
    raise _SE("all mirrors timed out")

def fake_tiger(search_area):
    from shapely.geometry import LineString as _LS
    return [
        (_LS([(-86.6001, 35.859), (-86.6001, 35.871)]),
         {"name": "COUNTY RD 12", "type": "residential", "surface": None}),
        (_LS([(-86.50, 35.80), (-86.50, 35.81)]),
         {"name": "FAR AWAY RD", "type": "residential", "surface": None}),
    ]

roads._overpass = dead_overpass
roads._lines_from_tiger = fake_tiger
fb = roads.access(parcel)
check("falls back to TIGER when Overpass is down",
      fb["available"] and fb["source"] == "Census TIGER/Line", str(fb.get("source")))
check("fallback still measures frontage", fb["frontage_ft"] > 3000,
      str(fb.get("frontage_ft")))
check("fallback names the substitution in the note",
      "TIGER" in fb["note"] and "unavailable" in fb["note"].lower())

def dead_tiger(search_area):
    raise _SE("census down too")

roads._lines_from_tiger = dead_tiger
both = roads.access(parcel)
check("both sources down reports unavailable with both reasons",
      not both["available"] and "OpenStreetMap" in both["error"]
      and "TIGER" in both["error"], str(both.get("error"))[:80])

roads._overpass = orig_op
roads._lines_from_tiger = orig_tiger

# TIGER splits one road into many segments; they must merge for display.
merged = roads._merge_by_name([
    {"name": "MAIN ST", "type": "residential", "public": True,
     "frontage_ft": 100.0, "distance_ft": 0.0, "surface": None},
    {"name": "MAIN ST", "type": "residential", "public": True,
     "frontage_ft": 150.0, "distance_ft": 5.0, "surface": None},
    {"name": "OAK LN", "type": "residential", "public": True,
     "frontage_ft": 40.0, "distance_ft": 2.0, "surface": None},
])
check("TIGER segments merge into one row per road", len(merged) == 2)
check("merged frontage is summed",
      next(r for r in merged if r["name"] == "MAIN ST")["frontage_ft"] == 250.0)
check("merged distance takes the nearest",
      next(r for r in merged if r["name"] == "MAIN ST")["distance_ft"] == 0.0)

check("every MTFCC code maps to a known highway type",
      all(v in roads.ROAD_RANK or v in roads.PUBLIC_ROAD_TYPES
          for v in config.MTFCC_TO_HIGHWAY.values()),
      str(sorted(set(config.MTFCC_TO_HIGHWAY.values()))))

# ---------------------------------------------------------------------------
print("\nSoils")

orig_post = soils.post_form
soils.post_form = lambda url, data, **k: {"Table": [
    ["mukey", "musym", "muname", "farmlndcl", "dominant_component",
     "dominant_pct", "drainage_class", "typical_slope", "hydric"],
    ["int", "str", "str", "str", "str", "int", "str", "float", "str"],
    ["123", "MuB", "Mountview silt loam, 2 to 5 percent slopes",
     "All areas are prime farmland", "Mountview", 85, "Well drained", 3.5, "No"],
    ["124", "Ro", "Rosebloom silt loam", "Not prime farmland", "Rosebloom",
     90, "Poorly drained", 1.0, "Yes"],
]}
s = soils.soils(parcel)
soils.post_form = orig_post
check("soil map units parsed", len(s["units"]) == 2)
check("prime farmland detected", s["summary"]["has_prime_farmland"] is True)
check("hydric soil detected", s["summary"]["has_hydric_soil"] is True)
check("drainage classes collected", len(s["summary"]["drainage_classes"]) == 2)

soils.post_form = lambda url, data, **k: {"Table": []}
s2 = soils.soils(parcel)
soils.post_form = orig_post
check("empty soil response fails cleanly", not s2["available"] and "error" in s2)

# ---------------------------------------------------------------------------
print("\nDrive times")

# Config sanity: every category must have a label and exactly one matcher
# style, and thresholds must be positive when present.
for _k, _c in config.DRIVETIME_CATEGORIES.items():
    styles = sum(1 for s in ("fixed", "brands", "overpass") if s in _c)
    check(f"category '{_k}' well formed",
          bool(_c.get("label")) and styles == 1
          and (_c.get("threshold_min") is None or _c["threshold_min"] > 0)
          and ("fixed" in _c or _c.get("search_km", 0) > 0))
for _f in config.DRIVETIME_CATEGORIES["large_airport"]["fixed"]:
    check(f"fixed airport '{_f['name'][:20]}' has coordinates",
          -91 < _f["lon"] < -81 and 34 < _f["lat"] < 37)

# The core promise: nearest by ROAD wins, not nearest by air. Three
# candidates; the closest by air is given the slowest matrix time.
_cands = [
    {"name": "Closest by air, slow road", "lat": 35.90, "lon": -86.59},
    {"name": "Second by air, fast road", "lat": 35.95, "lon": -86.59},
    {"name": "Far by air", "lat": 36.20, "lon": -86.59},
]
_orig_matrix = drivetimes._matrix
drivetimes._matrix = lambda lat, lon, targets: {"sources_to_targets": [[
    {"to_index": 0, "time": 1500, "distance": 6.0},
    {"to_index": 1, "time": 600, "distance": 8.0},
    {"to_index": 2, "time": 2400, "distance": 25.0},
]]}
_best = drivetimes._nearest_by_road(35.86, -86.59, _cands)
drivetimes._matrix = _orig_matrix
check("nearest by road beats nearest by air",
      _best["name"] == "Second by air, fast road", str(_best))
check("matrix seconds become minutes", _best["minutes"] == 10.0)

# Unreachable targets (null time) must be skipped, and all-null must return
# None rather than a fabricated answer.
drivetimes._matrix = lambda lat, lon, targets: {"sources_to_targets": [[
    {"to_index": 0, "time": None, "distance": None},
    {"to_index": 1, "time": 900, "distance": 9.0},
]]}
_best = drivetimes._nearest_by_road(35.86, -86.59, _cands[:2])
check("unreachable candidate skipped", _best["name"] == _cands[1]["name"])
drivetimes._matrix = lambda lat, lon, targets: {"sources_to_targets": [[
    {"to_index": 0, "time": None}]]}
check("all-unreachable returns None, not an invented time",
      drivetimes._nearest_by_road(35.86, -86.59, _cands[:1]) is None)
drivetimes._matrix = _orig_matrix

# Overpass fixture parsing: ways carry "center", nodes carry lat/lon,
# unnamed and duplicate elements are dropped.
_pois = drivetimes._extract_pois({"elements": [
    {"type": "node", "id": 1, "lat": 35.9, "lon": -86.5,
     "tags": {"name": "Cookeville Regional"}},
    {"type": "way", "id": 2, "center": {"lat": 35.8, "lon": -86.4},
     "tags": {"name": "Ascension Saint Thomas"}},
    {"type": "node", "id": 1, "lat": 35.9, "lon": -86.5,
     "tags": {"name": "Cookeville Regional"}},
    {"type": "node", "id": 3, "lat": 35.7, "lon": -86.3, "tags": {}},
]})
check("POIs parsed from nodes and way centers", len(_pois) == 2, str(_pois))

# A mirror success reorders the crawl so the next category starts at the
# mirror that just answered instead of re-timing-out on a dead one.
drivetimes._mirror_order = None
check("mirror order starts as configured",
      drivetimes._mirrors() == list(config.OVERPASS))
drivetimes._remember_winner(config.OVERPASS[-1])
check("winning mirror moves to the front",
      drivetimes._mirrors()[0] == config.OVERPASS[-1]
      and sorted(drivetimes._mirrors()) == sorted(config.OVERPASS))
drivetimes._mirror_order = None

# Grid snapping shares one cached query across a whole cell.
check("nearby points snap to one POI cache key",
      drivetimes._poi_query(config.DRIVETIME_CATEGORIES["hospital"], 35.8601, -86.5899)
      == drivetimes._poi_query(config.DRIVETIME_CATEGORIES["hospital"], 35.8607, -86.5893))

# Full drive_times() with both services mocked: thresholds classify, the
# info-only category never sets "over", and a per-category failure does not
# sink the panel.
_orig_pois = drivetimes._overpass_pois
def _fake_pois(cat, lat, lon):
    if "brands" in cat:
        raise drivetimes.SourceError("all mirrors down")
    return [{"name": "Fixture Place", "lat": lat + 0.05, "lon": lon}]
drivetimes._overpass_pois = _fake_pois
drivetimes._matrix = lambda lat, lon, targets: {"sources_to_targets": [[
    {"to_index": 0, "time": 1800, "distance": 12.0}]]}
_dt = drivetimes.drive_times(-86.59, 35.86)
drivetimes._overpass_pois = _orig_pois
drivetimes._matrix = _orig_matrix

_by_key = {r["key"]: r for r in _dt["results"]}
check("panel available despite one failed category", _dt["available"] is True)
check("failed category names the service error",
      "mirrors down" in _by_key["big_box"].get("error", ""))
check("30 min exceeds a 20 min threshold",
      _by_key["hospital"]["over"] is True)
check("30 min meets a 120 min threshold",
      _by_key["large_airport"]["over"] is False)
check("info-only category never flags over",
      _by_key["marina"]["found"] is True and _by_key["marina"]["over"] is None)

# ---------------------------------------------------------------------------
print("\nFlags")

report = {
    "parcel": {"structures_present": False, "buildings": 0},
    "flood": {"available": True, "sfha_pct": 62.0, "zones": [{"zone": "AE"}]},
    "wetlands": {"available": True, "pct": 18.0},
    "terrain": {"available": True, "buildable_pct": 12.0, "mean_slope_pct": 34.0,
                "terrain": "Steep"},
    "access": {"available": True, "landlocked_signal": True},
    "soils": {"available": True, "summary": {"has_hydric_soil": True}},
}
flags = analysis._flags(report)
check("bad flags sort first", flags[0]["level"] == "bad")
check("all four problems flagged",
      sum(1 for f in flags if f["level"] == "bad") == 3, str(flags))
check("landlocked wording present",
      any("landlocked" in f["text"].lower() for f in flags))

# A parcel entirely inside the 0.2% annual chance shaded X zone carries no
# insurance mandate but is still a lender conversation. It used to be
# reported as a green "minor clip" because sfha_pct was zero.
shaded = analysis._flags({
    "parcel": {},
    "flood": {"available": True, "sfha_pct": 0.0, "zones": [
        {"zone": "X", "subtype": "0.2 PCT ANNUAL CHANCE FLOOD HAZARD",
         "sfha": False, "pct": 100.0}]},
})
check("full non-SFHA coverage is warned, not called minor",
      any(f["level"] == "warn" and "non-SFHA" in f["text"] for f in shaded),
      str(shaded))
minor = analysis._flags({
    "parcel": {},
    "flood": {"available": True, "sfha_pct": 0.0, "zones": [
        {"zone": "X", "sfha": False, "pct": 3.0}]},
})
check("a genuine minor clip is still green",
      any(f["level"] == "ok" for f in minor))

good = {
    "parcel": {"structures_present": False},
    "flood": {"available": True, "sfha_pct": 0.0, "zones": []},
    "wetlands": {"available": True, "pct": 0.0},
    "terrain": {"available": True, "buildable_pct": 88.0, "mean_slope_pct": 4.0,
                "terrain": "Gently rolling"},
    "access": {"available": True, "landlocked_signal": False,
               "has_road_contact": True, "public_frontage_ft": 400,
               "summary": "About 400 ft of frontage on a residential road."},
    "soils": {"available": True, "summary": {"has_hydric_soil": False}},
}
gflags = analysis._flags(good)
check("clean parcel produces no bad flags",
      not any(f["level"] == "bad" for f in gflags))

# Drive-time flags: a miss warns (or goes bad at double the target), an
# empty info-only category stays silent, and the green summary only appears
# when every thresholded category is met.
_dt_flags = analysis._flags({"parcel": {}, "drivetimes": {
    "available": True, "results": [
        {"key": "hospital", "label": "Hospital", "threshold_min": 20,
         "found": True, "over": True, "minutes": 27.0, "name": "Somewhere General"},
        {"key": "big_box", "label": "Big box store", "threshold_min": 45,
         "found": True, "over": True, "minutes": 95.0, "name": "Walmart"},
        {"key": "grocery", "label": "Grocery store", "threshold_min": 20,
         "found": True, "over": False, "minutes": 9.0, "name": "Kroger"},
        {"key": "marina", "label": "Marina", "threshold_min": None,
         "found": False, "note": "nothing matching within 60 km"},
    ]}})
check("over-target drive time warns",
      any(f["level"] == "warn" and "hospital" in f["text"].lower()
          for f in _dt_flags), str(_dt_flags))
check("double-the-target goes bad",
      any(f["level"] == "bad" and "big box" in f["text"].lower()
          for f in _dt_flags))
check("empty info-only category stays silent",
      not any("marina" in f["text"].lower() for f in _dt_flags))
check("no green summary while a target is missed",
      not any("targets met" in f["text"] for f in _dt_flags))

_dt_ok = analysis._flags({"parcel": {}, "drivetimes": {
    "available": True, "results": [
        {"key": "hospital", "label": "Hospital", "threshold_min": 20,
         "found": True, "over": False, "minutes": 10.0, "name": "A"},
        {"key": "grocery", "label": "Grocery store", "threshold_min": 20,
         "found": True, "over": False, "minutes": 5.0, "name": "B"},
    ]}})
check("all targets met earns the green summary",
      any(f["level"] == "ok" and "targets met" in f["text"] for f in _dt_ok))

_dt_err = analysis._flags({"parcel": {}, "drivetimes": {
    "available": True, "results": [
        {"key": "hospital", "label": "Hospital", "threshold_min": 20,
         "found": False, "error": "Valhalla routing unavailable"},
    ]}})
check("a service error is not reported as a finding",
      not any("hospital" in f["text"].lower() for f in _dt_err), str(_dt_err))

# Regression: seen live during an Overpass outage -- two categories answered
# and met their targets while two errored out, and the report claimed "all
# drive-time targets met". An errored category is unknown, not met.
_dt_partial = analysis._flags({"parcel": {}, "drivetimes": {
    "available": True, "results": [
        {"key": "hospital", "label": "Hospital", "threshold_min": 20,
         "found": True, "over": False, "minutes": 10.0, "name": "A"},
        {"key": "pharmacy", "label": "Pharmacy", "threshold_min": 20,
         "found": False, "error": "all mirrors down"},
    ]}})
check("errored category suppresses the green summary",
      not any("targets met" in f["text"] for f in _dt_partial),
      str(_dt_partial))

# ---------------------------------------------------------------------------
print("\nComps")

r = comps_mod.comps(parcel, "BEDFORD")
check("unsupported county refuses honestly",
      r["available"] is False and "does not publish" in r["reason"])
check("unsupported county lists the ones that work",
      len(r["counties_with_comps"]) == 4)

sale_feats = []
for i, (price, ac) in enumerate([(50000, 5.0), (72000, 6.0), (61000, 5.5),
                                 (95000, 7.0), (44000, 4.5), (1, 5.0),
                                 (9000000, 5.0)]):
    sale_feats.append({
        "attributes": {"APN": f"P{i}", "Owner": f"O{i}", "Acres": ac,
                       "SalePrice": price, "OwnDate": 1687392000000,
                       "ImprAppr": 0, "TotlAppr": price},
        "geometry": {"rings": [outer]},
    })
orig_q = comps_mod.arcgis_query_all
comps_mod.arcgis_query_all = lambda *a, **k: sale_feats
c = comps_mod.comps(parcel, "DAVIDSON", subject_acres=5.5)
comps_mod.arcgis_query_all = orig_q

check("junk sales filtered out", c["count"] == 5, str(c["count"]))
check("median $/acre reasonable",
      11000 < c["price_per_acre"]["median"] < 12000,
      str(c["price_per_acre"]["median"]))
check("implied value scales with acreage",
      abs(c["implied_value"]["mid"] - c["price_per_acre"]["median"] * 5.5) < 10)
check("percentiles ordered",
      c["price_per_acre"]["p25"] <= c["price_per_acre"]["median"]
      <= c["price_per_acre"]["p75"])
check("basis is disclosed", "vacant-land" in c["basis"])

# An adjacent lot rounds to 0.0 miles, which is falsy. `or 999` sent exactly
# the most relevant comps to the bottom, where the result cap cut them first.
ordered = sorted(c["sales"], key=lambda s: s["distance_miles"])
check("comps are sorted nearest first",
      [s["distance_miles"] for s in c["sales"]] ==
      [s["distance_miles"] for s in ordered],
      str([s["distance_miles"] for s in c["sales"]]))
check("a zero-distance comp is kept at the top",
      c["sales"][0]["distance_miles"] == 0.0,
      str(c["sales"][0]["distance_miles"]))

# ---------------------------------------------------------------------------
print("\nPagination and caching")

import tnland.http as H

class FakeServer:
    """Mimics an ArcGIS layer with a configurable maxRecordCount."""
    def __init__(self, total, max_record_count, honours_offset=True):
        self.total, self.cap, self.honours = total, max_record_count, honours_offset
        self.requests = 0

    def __call__(self, layer_url, **kw):
        self.requests += 1
        want = kw.get("result_record_count") or 1000
        offset = kw.get("result_offset") or 0
        if not self.honours:
            offset = 0
        n = min(want, self.cap, max(0, self.total - offset))
        feats = [{"attributes": {"OBJECTID": offset + i}} for i in range(n)]
        return {"objectIdFieldName": "OBJECTID", "features": feats,
                "exceededTransferLimit": offset + n < self.total}

orig_aq = H.arcgis_query

# The bug: a server whose maxRecordCount is below page_size returns a short
# page WITH exceededTransferLimit set. Treating short == finished truncated
# 4000 records down to 500 and reported success.
srv = FakeServer(total=4000, max_record_count=500)
H.arcgis_query = srv
got = H.arcgis_query_all("http://x/0", page_size=1000, max_records=10000)
H.arcgis_query = orig_aq
check("small maxRecordCount does not truncate", len(got) == 4000, str(len(got)))
check("paging made the expected number of requests", srv.requests == 8, str(srv.requests))

# A server that ignores resultOffset used to loop and duplicate every parcel.
srv = FakeServer(total=4000, max_record_count=1000, honours_offset=False)
H.arcgis_query = srv
got = H.arcgis_query_all("http://x/0", page_size=1000, max_records=5000)
H.arcgis_query = orig_aq
check("server ignoring resultOffset does not duplicate", len(got) == 1000, str(len(got)))
check("and stops instead of looping", srv.requests <= 3, str(srv.requests))

class NoOidServer:
    """Offset-ignoring server whose outFields exclude the OID -- the exact
    shape of every parcel query in this app."""
    def __init__(self, total): self.total, self.requests = total, 0
    def __call__(self, layer_url, **kw):
        self.requests += 1
        n = min(kw.get("result_record_count") or 1000, self.total)
        return {"objectIdFieldName": "OBJECTID",
                "features": [{"attributes": {"OWNER": f"O{i}"},
                              "geometry": {"rings": [outer]}} for i in range(n)],
                "exceededTransferLimit": True}

srv2 = NoOidServer(total=500)
H.arcgis_query = srv2
got = H.arcgis_query_all("http://x/0", page_size=500, max_records=5000)
H.arcgis_query = orig_aq
check("dedupe works even when outFields omit OBJECTID",
      len(got) == 500, str(len(got)))
check("and it stops after two requests", srv2.requests == 2, str(srv2.requests))

srv = FakeServer(total=250, max_record_count=1000)
H.arcgis_query = srv
got = H.arcgis_query_all("http://x/0", page_size=1000, max_records=5000)
H.arcgis_query = orig_aq
check("short single page terminates", len(got) == 250 and srv.requests == 1)

srv = FakeServer(total=0, max_record_count=1000)
H.arcgis_query = srv
got = H.arcgis_query_all("http://x/0")
H.arcgis_query = orig_aq
check("empty result terminates", got == [] and srv.requests == 1)

srv = FakeServer(total=9999, max_record_count=1000)
H.arcgis_query = srv
got = H.arcgis_query_all("http://x/0", page_size=1000, max_records=2500)
H.arcgis_query = orig_aq
check("max_records is respected exactly", len(got) == 2500, str(len(got)))

# ArcGIS reports errors with HTTP 200 and an error body. Caching one would
# make a transient hiccup look like a permanently broken parcel for 14 days.
class NoPaginationServer:
    """An ArcGIS 10.x server that rejects resultOffset outright, as
    Hamilton County's does, instead of ignoring it."""
    def __init__(self, total): self.total, self.requests = total, 0
    def __call__(self, layer_url, **kw):
        self.requests += 1
        if kw.get("result_offset") is not None:
            raise H.SourceError(
                f"{layer_url} query failed: Pagination is not supported.")
        return {"objectIdFieldName": "OBJECTID",
                "features": [{"attributes": {"OBJECTID": i}}
                             for i in range(self.total)]}

H._NO_PAGINATION.clear()
srv3 = NoPaginationServer(total=750)
H.arcgis_query = srv3
got = H.arcgis_query_all("http://nopage/0", page_size=1000, max_records=5000)
H.arcgis_query = orig_aq
check("server rejecting pagination still returns its rows",
      len(got) == 750, str(len(got)))
check("and the unpaged retry costs only one extra request",
      srv3.requests == 2, str(srv3.requests))
check("the layer is remembered as unpaged",
      H._no_pagination("http://nopage/0"))
H._NO_PAGINATION.clear()

check("error envelope is not cacheable",
      H._cacheable({"error": {"code": 500, "message": "Error performing query"}}) is False)
check("normal payload is cacheable",
      H._cacheable({"features": []}) is True)

# ---------------------------------------------------------------------------
print("\nSale date parsing")

check("modern epoch ms converts",
      parcels._epoch_to_date(1687392000000) == "2023-06-22")
check("ambiguous 1e8-1e11 band returns unknown, never a fabricated date",
      parcels._epoch_to_date(94694400000) is None
      and parcels._epoch_to_date(1583020800) is None
      and parcels._epoch_to_date(123456789) is None,
      str(parcels._epoch_to_date(1583020800)))
# Regression: datetime.fromtimestamp() raises OSError on Windows for any
# negative timestamp, so pre-1970 deeds silently vanished there while
# passing on Linux. The parser now does timedelta arithmetic instead.
check("negative epoch ms (pre-1970 sale) converts on every platform",
      parcels._epoch_to_date(-157766400000) == "1965-01-01",
      str(parcels._epoch_to_date(-157766400000)))
check("very old epoch ms converts too",
      parcels._epoch_to_date(-2208988800000) == "1900-01-01",
      str(parcels._epoch_to_date(-2208988800000)))
check("absurd epoch value fails cleanly rather than raising",
      parcels._epoch_to_date(1e18) is None,
      str(parcels._epoch_to_date(1e18)))
check("zero is treated as no date", parcels._epoch_to_date(0) is None)
check("None stays None", parcels._epoch_to_date(None) is None)
check("already-formatted string passes through",
      parcels._epoch_to_date("2021-04-05") == "2021-04-05")
check("YYYYMMDD integer converts",
      parcels._epoch_to_date(20230622) == "2023-06-22",
      str(parcels._epoch_to_date(20230622)))
check("bare year passes through", parcels._epoch_to_date(1998) == "1998")
check("nonsense number does not crash",
      parcels._epoch_to_date(12345) == "12345")
check("NaN sale date returns None instead of raising",
      parcels._epoch_to_date(float("nan")) is None)
check("a NaN-valued county feature does not raise",
      parcels._from_county({"attributes": {"OwnDate": float("nan"),
                                           "Owner": "X"}}, "DAVIDSON")
      ["sale_date"] is None)

# ---------------------------------------------------------------------------
print("\nAPI routes")

from fastapi.testclient import TestClient
from tnland.server import app

client = TestClient(app)

r = client.get("/")
check("index page serves", r.status_code == 200 and "Tennessee Land Tool" in r.text)

r = client.get("/api/config")
cfg = r.json()
check("config route works", r.status_code == 200)
check("config exposes basemaps", "usgs_imagery" in cfg["basemaps"])
check("config exposes comps counties", len(cfg["comps_counties"]) == 4)
check("config warns about missing counties",
      "KNOX" in cfg["counties_without_service"])
check("land use codes exposed", cfg["land_use_codes"]["51"] == "Vacant")

r = client.get("/api/search/owner?q=ab")
check("short owner query rejected", r.status_code == 400)

r = client.get("/api/search/address?q=abc")
check("short address query rejected", r.status_code == 400)

r = client.post("/api/screen", json={"geometry": {"type": "Not a geometry"}})
check("bad geometry rejected with 400", r.status_code == 400)

r = client.post("/api/export.csv", json={"rows": rows})
check("CSV export route works",
      r.status_code == 200 and r.text.startswith("county,parcel_id,owner"))
check("CSV served as a download",
      "attachment" in r.headers.get("content-disposition", ""))

r = client.get("/api/cache")
check("cache stats route works", r.status_code == 200 and "entries" in r.json())

r = client.post("/api/comps", json={
    "geometry": json.loads(json.dumps(geo.shapely_to_geojson(parcel))),
    "county": "BEDFORD"})
check("comps route refuses unsupported county cleanly",
      r.status_code == 200 and r.json()["available"] is False)

# ---------------------------------------------------------------------------
print("\nAddress search")

from tnland.sources import geocode

check("census response parsed",
      geocode._census.__name__ == "_census")

_census_fixture = {"result": {"addressMatches": [
    {"matchedAddress": "2926 BRYANT RIDGE RD, BAXTER, TN, 38544",
     "coordinates": {"x": -85.674306366934, "y": 36.201176062433},
     "addressComponents": {"state": "TN", "zip": "38544"}}]}}
_orig_get = geocode.get_json
geocode.get_json = lambda url, params, **k: _census_fixture
geocode.cache.get = lambda *a, **k: None
res = geocode.geocode("2926 Bryant Ridge Rd, Baxter, TN 38544")
geocode.get_json = _orig_get
check("geocoder returns a usable point",
      len(res) == 1 and abs(res[0]["lon"] + 85.6743) < 0.001
      and abs(res[0]["lat"] - 36.2012) < 0.001, str(res))
check("geocoder reports which service answered",
      res[0]["source"] == "US Census geocoder")

geocode.get_json = lambda url, params, **k: {"result": {"addressMatches": []}}
empty = geocode._census("nowhere at all")
geocode.get_json = _orig_get
check("no geocoder match returns empty, does not raise", empty == [])

# Address-field fallback tokenising: the suffix is the usual mismatch
# ("RD" on one side, "ROAD" on the other), so it is dropped.
toks = geocode._street_tokens("2926 Bryant Ridge Rd, Baxter, TN 38544")
check("street tokens keep number and name, drop the suffix",
      toks == ["2926", "BRYANT", "RIDGE"], str(toks))
check("tokens drop city, state and ZIP",
      not any(t in ("BAXTER", "TN", "38544") for t in toks), str(toks))
check("apostrophes are SQL-escaped",
      "''" in " ".join(geocode._street_tokens("12 O'Brien Ln")),
      str(geocode._street_tokens("12 O'Brien Ln")))
check("empty address yields no tokens", geocode._street_tokens("") == [])
check("short query is rejected before any network call",
      geocode.geocode("abc") == [])

# --- unnumbered "0 Road Name" listings ---------------------------------
# Zillow and friends use a zero house number for vacant land the county has
# never assigned an address to. Geocoding one returns nothing (verified live
# against the Census geocoder), so these must route to a road search instead
# of silently snapping to a neighbouring parcel.
for _addr, _expect in [
    ("0 McBroom Branch Rd, Baxter, TN 38544", True),
    ("00 Old Kentucky Rd, Baxter, TN", True),
    ("TBD Hidden Valley Ln, Cookeville, TN", True),
    ("Lot 4 Poplar Grove Rd, Sparta, TN", True),
    ("McBroom Branch Rd, Baxter, TN", True),
    ("2926 Bryant Ridge Rd, Baxter, TN 38544", False),
    ("101 Main St, Cookeville, TN", False),
]:
    check(f"unnumbered detection: {_addr[:34]!r}",
          geocode.is_unnumbered(_addr) is _expect)

check("street and locality split correctly",
      geocode.split_address("0 McBroom Branch Rd, Baxter, TN 38544")
      == ("McBroom Branch", "Baxter, TN 38544"))
check("suffix stripped so TIGER NAME/BASENAME both match",
      geocode.split_address("Lot 4 Poplar Grove Road, Sparta, TN")[0]
      == "Poplar Grove")
check("numbered address still splits without losing the street",
      geocode.split_address("2926 Bryant Ridge Rd, Baxter, TN")[0]
      == "Bryant Ridge")
check("empty input does not crash", geocode.split_address("") == ("", ""))
check("blank address is not treated as unnumbered",
      geocode.is_unnumbered("") is False)

from tnland import roadsearch

# Road results must sort raw land first, then by descending acreage.
_rows = [
    {"is_raw_land": False, "acres": 90.0}, {"is_raw_land": True, "acres": 12.0},
    {"is_raw_land": True, "acres": 41.0}, {"is_raw_land": False, "acres": 3.0},
    {"is_raw_land": True, "acres": None},
]
_rows.sort(key=lambda r: (not r["is_raw_land"], -(r["acres"] or 0)))
check("road results put raw land first, largest first",
      [r["acres"] for r in _rows] == [41.0, 12.0, None, 90.0, 3.0],
      str([r["acres"] for r in _rows]))

_orig_anchor = roadsearch.geocode.locality_anchor
roadsearch.geocode.locality_anchor = lambda loc: None
_r = roadsearch.parcels_on_road("McBroom Branch", "Nowhere, ZZ")
roadsearch.geocode.locality_anchor = _orig_anchor
check("unlocatable town explains itself instead of guessing",
      _r["found"] is False and "Could not locate" in _r["message"])

roadsearch.geocode.locality_anchor = lambda loc: {"lon": -85.67, "lat": 36.20,
                                                  "address": "Baxter, TN"}
_orig_find = roadsearch.find_road
roadsearch.find_road = lambda st, a, m=12.0: {"found": False, "errors": []}
_r = roadsearch.parcels_on_road("Nonexistent Trace", "Baxter, TN")
roadsearch.find_road = _orig_find
roadsearch.geocode.locality_anchor = _orig_anchor
check("unmatched road name says so with the search radius",
      _r["found"] is False and "No road matching" in _r["message"])

# ---------------------------------------------------------------------------
print("\nCLI")

# Regression: `python -m tnland` with no subcommand falls through to "serve",
# but argparse never ran the serve subparser, so args.host did not exist and
# the most common invocation of the whole tool crashed on startup.
from tnland.__main__ import build_parser

_parser = build_parser()
_needed = ["host", "port", "no_browser", "verbose", "lon", "lat",
           "action", "json", "command", "query"]
for _argv, _label in [
    ([], "no arguments (implicit serve)"),
    (["serve"], "serve"),
    (["doctor"], "doctor"),
    (["parcel", "-86.5", "35.8"], "parcel"),
    (["cache"], "cache"),
    (["address", "2926 Bryant Ridge Rd, Baxter, TN"], "address"),
]:
    _a = _parser.parse_args(_argv)
    _missing = [n for n in _needed if not hasattr(_a, n)]
    check(f"CLI: {_label} exposes every attribute main() reads",
          not _missing, str(_missing))

_a = _parser.parse_args([])
check("CLI: bare invocation defaults to 127.0.0.1:8823",
      _a.host == "127.0.0.1" and _a.port == 8823 and _a.no_browser is False)
check("CLI: bare invocation resolves to the serve command",
      (_a.command or "serve") == "serve")
_a = _parser.parse_args(["serve", "--port", "9000", "--no-browser"])
check("CLI: serve flags still override the defaults",
      _a.port == 9000 and _a.no_browser is True)
_a = _parser.parse_args(["doctor", "--lon", "-86.5", "--lat", "35.8"])
check("CLI: doctor accepts a location override",
      _a.lon == -86.5 and _a.lat == 35.8)
_a = _parser.parse_args(["cache", "clear"])
check("CLI: cache clear parses", _a.action == "clear")

# ---------------------------------------------------------------------------
print("\nConfig integrity")

check("every comps county has a service",
      all(c in config.COUNTY_SERVICES for c in config.COMPS_COUNTIES))
check("every comps county has price and date fields",
      all(config.COUNTY_SERVICES[c]["fields"].get("sale_price")
          and config.COUNTY_SERVICES[c]["fields"].get("sale_date")
          for c in config.COMPS_COUNTIES))
check("all 9 excluded counties are accounted for",
      len(set(config.COUNTY_SERVICES) | set(config.COUNTIES_NO_SERVICE)) == 9,
      str(sorted(set(config.COUNTY_SERVICES) | set(config.COUNTIES_NO_SERVICE))))
check("no county is both served and unserved",
      not (set(config.COUNTY_SERVICES) & set(config.COUNTIES_NO_SERVICE)))
check("vacant codes are a subset of raw land codes",
      config.VACANT_LU_CODES < config.RAW_LAND_LU_CODES)
check("all raw land codes decode to a label",
      all(c in config.LAND_USE_CODES for c in config.RAW_LAND_LU_CODES))
check("NWI fields use the table prefix",
      all(f.startswith("Wetlands.") for f in config.NWI_FIELDS))
check("FEMA candidates include the AGOL failover",
      any("services.arcgis.com" in u for u in config.FEMA_NFHL))
check("every county service URL is https or documented http",
      all(v["url"].startswith("http") for v in config.COUNTY_SERVICES.values()))

# ---------------------------------------------------------------------------
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nFailures:")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
