"""Find every parcel fronting a named road.

This exists for the most common vacant-land listing address there is:
"0 McBroom Branch Rd". The zero is a placeholder for a house number the
county never assigned, so no geocoder can place it -- the address is not a
real address. Returning an approximate point would be worse than useless,
because the user would then be looking at whichever neighbouring parcel
happened to be under it.

Instead: locate the road itself in Census TIGER, buffer it into a corridor,
and return every parcel that touches the corridor, with the raw land sorted
to the top. That is the actual question behind the listing -- "which parcel
on this road is for sale?" -- and it is answerable.
"""

from __future__ import annotations

from typing import Any

from shapely.ops import unary_union

from . import config, geo
from .http import SourceError, arcgis_query_all, pick_working
from .sources import geocode, parcels

# How far either side of the centreline counts as fronting the road.
CORRIDOR_M = 90.0
# How far from the town centre to look for the road.
DEFAULT_SEARCH_MILES = 12.0


def find_road(street: str, anchor: dict[str, Any],
              search_miles: float = DEFAULT_SEARCH_MILES) -> dict[str, Any]:
    """Locate a named road near a point. Returns geometry plus its name."""
    from shapely.geometry import Point

    service = pick_working(config.TIGER_ROADS)
    area = geo.buffer_m(Point(anchor["lon"], anchor["lat"]),
                        search_miles * 1609.34)
    esri = geo.shapely_to_esri(geo.simplify_for_query(area, 100))

    safe = street.upper().replace("'", "''")
    where = (f"UPPER(NAME) LIKE '%{safe}%' OR UPPER(BASENAME) LIKE '%{safe}%'")

    pieces, names, errors = [], set(), []
    for layer_id in config.TIGER_ROAD_LAYERS:
        url = f"{service.rstrip('/')}/{layer_id}"
        try:
            feats = arcgis_query_all(
                url, geometry=esri, where=where,
                out_fields=["NAME", "BASENAME", "MTFCC"], max_records=1000,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"layer {layer_id}: {type(exc).__name__}")
            continue
        for f in feats:
            shp = geo.esri_to_shapely(f.get("geometry") or {})
            if shp is None or shp.is_empty:
                continue
            pieces.append(shp)
            nm = (f.get("attributes", {}) or {}).get("NAME")
            if nm:
                names.add(nm.strip())

    if not pieces:
        return {"found": False, "errors": errors,
                "searched_miles": search_miles}
    merged = unary_union(pieces)
    projected, epsg = geo.to_utm(merged)
    return {
        "found": True,
        "geometry": merged,
        "names": sorted(names),
        "length_miles": round(projected.length / 1609.34, 2),
        "segments": len(pieces),
    }


def parcels_on_road(street: str, locality: str,
                    search_miles: float = DEFAULT_SEARCH_MILES,
                    max_parcels: int = 400) -> dict[str, Any]:
    anchor = geocode.locality_anchor(locality)
    if anchor is None:
        return {
            "found": False,
            "message": (
                f"Could not locate {locality!r}. Include the town and state, "
                "for example 'McBroom Branch Rd, Baxter, TN'."
            ),
        }

    road = find_road(street, anchor, search_miles)
    if not road.get("found"):
        return {
            "found": False,
            "message": (
                f"No road matching {street!r} within {search_miles:.0f} miles "
                f"of {locality}. Check the spelling, or search the owner name "
                "instead."
            ),
            "anchor": anchor,
        }

    corridor = geo.buffer_m(road["geometry"], CORRIDOR_M)
    try:
        found = parcels.in_area(corridor, max_records=max_parcels)
    except SourceError as exc:
        return {"found": False, "message": f"Parcel query failed: {exc}"}

    parcels.apply_bulk_landuse(found)

    rows = []
    for rec in found:
        geom = rec.get("geometry")
        if geom is None:
            continue
        code = rec.get("land_use_code")
        acres = rec.get("deeded_acres") or rec.get("gis_acres")
        rows.append({
            "parcel_id": rec.get("parcel_id"),
            "owner": rec.get("owner"),
            "county": rec.get("county"),
            "acres": round(acres, 2) if acres else None,
            "land_use": rec.get("land_use"),
            "land_use_code": code,
            "appraisal": rec.get("appraisal"),
            "situs_address": rec.get("situs_address"),
            "is_raw_land": bool(code and code in config.RAW_LAND_LU_CODES),
            "structures": rec.get("buildings"),
            "tpad_url": rec.get("tpad_url"),
            "lon": round(geom.centroid.x, 6),
            "lat": round(geom.centroid.y, 6),
        })

    # A "0 <road>" listing is vacant land by definition, so put the raw land
    # first and the biggest tracts above the small ones. Improved parcels
    # stay in the list rather than being filtered out, because the county's
    # land-use coding is not always current.
    rows.sort(key=lambda r: (not r["is_raw_land"], -(r["acres"] or 0)))

    raw = sum(1 for r in rows if r["is_raw_land"])
    return {
        "found": True,
        "road_names": road["names"],
        "road_length_miles": road["length_miles"],
        "road_geometry": geo.shapely_to_geojson(road["geometry"]),
        "corridor_ft": round(CORRIDOR_M * 3.28084),
        "anchor": anchor,
        "count": len(rows),
        "raw_land_count": raw,
        "results": rows,
        "message": (
            f"{len(rows)} parcels front {', '.join(road['names'][:3])} "
            f"({road['length_miles']} mi mapped); {raw} are vacant, "
            "agricultural or timber."
        ),
        "note": (
            "Listing sites use a zero house number when the county has not "
            "assigned one, so the address cannot be geocoded. These are every "
            "parcel within "
            f"{round(CORRIDOR_M * 3.28084)} ft of the road centreline -- click "
            "the one matching the listing's acreage."
        ),
    }
