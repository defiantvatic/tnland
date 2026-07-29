"""Geometry helpers: projection, area, buffering, Esri <-> GeoJSON."""

from __future__ import annotations

import math
from typing import Any, Iterable

from pyproj import CRS, Transformer
from shapely.geometry import (
    LineString,
    MultiPolygon,
    Polygon,
    mapping,
    shape,
)
from shapely.ops import transform as shp_transform

WGS84 = "EPSG:4326"

_transformer_cache: dict[tuple[str, str], Transformer] = {}


def utm_epsg_for(lon: float, lat: float) -> str:
    """Pick the right UTM zone so distances and areas come out in real metres.

    Tennessee straddles zones 15N, 16N and 17N. Using Web Mercator instead
    would inflate every distance by ~1/cos(latitude) -- about 24% at TN's
    latitude -- which would quietly corrupt acreage, slope and frontage.
    """
    zone = int(math.floor((lon + 180) / 6) + 1)
    north = lat >= 0
    return f"EPSG:{32600 + zone if north else 32700 + zone}"


def _transformer(src: str, dst: str) -> Transformer:
    key = (src, dst)
    if key not in _transformer_cache:
        _transformer_cache[key] = Transformer.from_crs(
            CRS.from_user_input(src), CRS.from_user_input(dst), always_xy=True
        )
    return _transformer_cache[key]


def reproject(geom, src: str, dst: str):
    if src == dst:
        return geom
    return shp_transform(_transformer(src, dst).transform, geom)


def to_utm(geom_wgs84):
    """Return (projected_geometry, utm_epsg) for a WGS84 shapely geometry."""
    c = geom_wgs84.centroid
    epsg = utm_epsg_for(c.x, c.y)
    return reproject(geom_wgs84, WGS84, epsg), epsg


SQM_PER_ACRE = 4046.8564224


def acres(geom_wgs84) -> float:
    """True ground acreage, computed in UTM rather than degrees."""
    projected, _ = to_utm(geom_wgs84)
    return projected.area / SQM_PER_ACRE


def esri_to_shapely(esri: dict[str, Any]):
    """Convert an Esri JSON geometry to shapely. Handles rings and paths."""
    if not esri:
        return None
    if "rings" in esri:
        rings = esri["rings"]
        if not rings:
            return None
        # Esri packs outer rings and holes into one flat list and distinguishes
        # them by winding: OUTER RINGS ARE CLOCKWISE, holes counter-clockwise.
        # The shoelace sum is negative for clockwise, so negative == outer.
        # Getting this backwards silently returns the hole as the parcel, or
        # an empty geometry that blows up downstream, so it is worth being
        # explicit about.
        polys: list[Polygon] = []
        holes: list[list] = []
        for ring in rings:
            if len(ring) < 4:
                continue
            if _signed_area(ring) > 0:
                holes.append(ring)
            else:
                polys.append(Polygon(ring))
        if not polys:
            # Some services emit every ring counter-clockwise regardless of
            # role. Fall back to containment: a ring inside a larger ring is a
            # hole, a ring standing on its own is another part of a multipart
            # parcel. Assuming everything after the largest ring is a hole
            # would silently delete the detached parts of a split tract.
            candidates = [Polygon(r) for r in rings if len(r) >= 4]
            candidates = [p for p in candidates if not p.is_empty]
            if not candidates:
                return None
            candidates.sort(key=lambda p: p.area, reverse=True)
            polys, holes = [], []
            for cand in candidates:
                point = cand.representative_point()
                if any(point.within(p) for p in polys):
                    holes.append(list(cand.exterior.coords))
                else:
                    polys.append(cand)
        if holes and polys:
            # Attach each hole to whichever outer ring actually contains it.
            for hole in holes:
                hole_poly = Polygon(hole)
                if hole_poly.is_empty:
                    continue
                point = hole_poly.representative_point()
                for i, outer in enumerate(polys):
                    if point.within(outer):
                        polys[i] = Polygon(
                            outer.exterior.coords,
                            list(outer.interiors) + [hole_poly.exterior.coords],
                        )
                        break
        polys = [p for p in polys if not p.is_empty]
        if not polys:
            return None
        geom = polys[0] if len(polys) == 1 else MultiPolygon(polys)
        if not geom.is_valid:
            geom = geom.buffer(0)
        return None if geom.is_empty else geom
    if "paths" in esri:
        paths = [p for p in esri["paths"] if len(p) >= 2]
        if not paths:
            return None
        if len(paths) == 1:
            return LineString(paths[0])
        from shapely.geometry import MultiLineString

        return MultiLineString(paths)
    if "x" in esri and "y" in esri:
        from shapely.geometry import Point

        return Point(esri["x"], esri["y"])
    return None


def _signed_area(ring: Iterable[Iterable[float]]) -> float:
    pts = list(ring)
    total = 0.0
    for i in range(len(pts) - 1):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[i + 1][0], pts[i + 1][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def shapely_to_esri(geom, precision: int = 7) -> dict[str, Any]:
    """Convert shapely polygon(s) to Esri JSON for use as a query geometry.

    Two things matter here and both are easy to get wrong:

    * Winding. Esri reads a lone counter-clockwise ring as a HOLE, i.e. a
      zero-area polygon, and returns no features. Shapely and GeoJSON both
      produce counter-clockwise exteriors, so every query polygon has to be
      re-oriented clockwise on the way out.
    * Precision. Full float64 coordinates triple the size of the request for
      no benefit; 7 decimal places is about 11 mm.
    """
    from shapely.geometry.polygon import orient

    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        # A Point's envelope is a Point, and an axis-aligned LineString's is a
        # zero-area line -- neither has an exterior to orient. Give anything
        # degenerate a hair of width so it becomes a real polygon.
        env = geom.envelope
        if env.geom_type != "Polygon" or env.area == 0:
            env = geom.buffer(1e-7).envelope
        polys = [env]

    rings = []
    for p in polys:
        if p.is_empty:
            continue
        # sign=-1.0 gives a clockwise exterior and counter-clockwise holes,
        # which is exactly the Esri convention.
        p = orient(p, sign=-1.0)
        rings.append(_round_ring(p.exterior.coords, precision))
        for interior in p.interiors:
            rings.append(_round_ring(interior.coords, precision))
    return {"rings": rings, "spatialReference": {"wkid": 4326}}


def _round_ring(coords, precision: int) -> list[list[float]]:
    return [[round(c[0], precision), round(c[1], precision)] for c in coords]


def simplify_for_query(geom, max_vertices: int = 400):
    """Thin a geometry down before sending it as a query parameter.

    Parcel outlines from county services routinely carry hundreds of vertices
    from digitised deed lines. That detail changes nothing about which flood
    or wetland polygons intersect, and it makes requests large enough to be
    rejected by the server.
    """
    count = _count_vertices(geom)
    if count <= max_vertices:
        return geom
    projected, epsg = to_utm(geom)
    for tolerance in (0.5, 1.0, 2.0, 5.0, 10.0, 25.0):
        thinned = projected.simplify(tolerance, preserve_topology=True)
        if thinned.is_empty or not thinned.is_valid:
            continue
        # Simplification cuts corners INWARD on a convex boundary, so the
        # thinned shape sits slightly inside the parcel. Querying with it
        # could miss a flood or wetland sliver that only touches the edge.
        # Push it back out by the tolerance so the query geometry always
        # covers the real parcel; anything extra it catches is clipped
        # against the true outline later anyway.
        grown = thinned.buffer(tolerance * 1.5, join_style=2, mitre_limit=2.0)
        if grown.is_empty or not grown.is_valid:
            grown = thinned
        if _count_vertices(grown) <= max_vertices:
            return reproject(grown, epsg, WGS84)
    # A convex hull is NOT guaranteed to be smaller -- for a finely sampled
    # circle it keeps every vertex -- so the hull only counts if it actually
    # got under the limit. Otherwise fall back to the bounding box, which is
    # always five vertices and always contains the parcel.
    hull = projected.convex_hull
    if _count_vertices(hull) <= max_vertices:
        return reproject(hull, epsg, WGS84)
    return reproject(projected.envelope, epsg, WGS84)


def _count_vertices(geom) -> int:
    if geom.is_empty:
        return 0
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords) + sum(len(i.coords) for i in geom.interiors)
    if hasattr(geom, "geoms"):
        return sum(_count_vertices(g) for g in geom.geoms)
    return len(getattr(geom, "coords", []))


def geojson_to_shapely(gj: dict[str, Any]):
    if gj.get("type") == "Feature":
        gj = gj["geometry"]
    return shape(gj)


def shapely_to_geojson(geom) -> dict[str, Any]:
    return mapping(geom)


def to_wkt_wgs84(geom_wgs84) -> str:
    """WKT for NRCS Soil Data Access, which wants lon/lat WGS84."""
    simplified = geom_wgs84.simplify(0.00002, preserve_topology=True)
    if simplified.is_empty:
        simplified = geom_wgs84
    return simplified.wkt


def bbox_of(geom) -> tuple[float, float, float, float]:
    return geom.bounds


def buffer_m(geom_wgs84, metres: float):
    """Buffer a WGS84 geometry by a true metric distance."""
    projected, epsg = to_utm(geom_wgs84)
    return reproject(projected.buffer(metres), epsg, WGS84)


def pct_covered(parcel_wgs84, pieces: list) -> tuple[float, float]:
    """Return (acres_covered, percent_of_parcel) for a list of overlay shapes."""
    if not pieces:
        return 0.0, 0.0
    projected, epsg = to_utm(parcel_wgs84)
    total = 0.0
    for piece in pieces:
        if piece is None or piece.is_empty:
            continue
        try:
            clipped = reproject(piece, WGS84, epsg).intersection(projected)
        except Exception:
            continue
        if not clipped.is_empty:
            total += clipped.area
    covered_acres = total / SQM_PER_ACRE
    parcel_acres = projected.area / SQM_PER_ACRE
    pct = (covered_acres / parcel_acres * 100.0) if parcel_acres > 0 else 0.0
    return covered_acres, min(pct, 100.0)
