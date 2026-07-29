"""FEMA flood zones and USFWS wetlands for a parcel."""

from __future__ import annotations

from typing import Any

from .. import config, geo
from ..http import SourceError, arcgis_query_all, pick_working


def flood(parcel_geom) -> dict[str, Any]:
    """Intersect the parcel with the National Flood Hazard Layer.

    Returns per-zone acreage rather than a single yes/no, because "5% of a
    40-acre tract clips zone AE along a creek" and "the whole parcel is in
    AE" are completely different purchases.
    """
    out: dict[str, Any] = {
        "available": False,
        "zones": [],
        "sfha_acres": 0.0,
        "sfha_pct": 0.0,
        "source": None,
    }
    try:
        url = pick_working(config.FEMA_NFHL)
    except SourceError as exc:
        out["error"] = str(exc)
        return out
    out["source"] = url

    try:
        feats = arcgis_query_all(
            url,
            geometry=geo.shapely_to_esri(geo.simplify_for_query(parcel_geom)),
            out_fields=config.FEMA_FIELDS,
            max_records=500,
        )
    except SourceError as exc:
        out["error"] = str(exc)
        return out

    out["available"] = True
    by_zone: dict[str, list] = {}
    meta: dict[str, dict[str, Any]] = {}
    for f in feats:
        attrs = f.get("attributes", {}) or {}
        zone = (attrs.get("FLD_ZONE") or "UNKNOWN").strip()
        shp = geo.esri_to_shapely(f.get("geometry") or {})
        if shp is None:
            continue
        by_zone.setdefault(zone, []).append(shp)
        bfe = attrs.get("STATIC_BFE")
        meta.setdefault(zone, {
            "subtype": attrs.get("ZONE_SUBTY"),
            "sfha": (attrs.get("SFHA_TF") or "").upper() == "T",
            # FEMA uses -9999 as the no-data sentinel for base flood elevation
            "base_flood_elevation": None if bfe in (None, -9999) else bfe,
        })

    sfha_shapes = []
    for zone, shapes in by_zone.items():
        acres_in, pct = geo.pct_covered(parcel_geom, shapes)
        info = meta.get(zone, {})
        is_sfha = info.get("sfha") or zone.upper() in config.SFHA_ZONES
        out["zones"].append({
            "zone": zone,
            "subtype": info.get("subtype"),
            "acres": round(acres_in, 3),
            "pct": round(pct, 1),
            "sfha": bool(is_sfha),
            "base_flood_elevation": info.get("base_flood_elevation"),
        })
        if is_sfha:
            sfha_shapes.extend(shapes)

    out["zones"].sort(key=lambda z: -z["acres"])
    sfha_acres, sfha_pct = geo.pct_covered(parcel_geom, sfha_shapes)
    out["sfha_acres"] = round(sfha_acres, 3)
    out["sfha_pct"] = round(sfha_pct, 1)
    if not out["zones"]:
        out["note"] = (
            "No mapped flood hazard polygons intersect this parcel. Note that "
            "some rural Tennessee counties are unmapped rather than flood-free."
        )
    return out


def wetlands(parcel_geom) -> dict[str, Any]:
    """Intersect the parcel with the National Wetlands Inventory."""
    out: dict[str, Any] = {
        "available": False,
        "types": [],
        "acres": 0.0,
        "pct": 0.0,
    }
    try:
        url = pick_working(config.NWI)
    except SourceError as exc:
        out["error"] = str(exc)
        return out
    out["source"] = url

    try:
        # NWI qualifies its field names with the table prefix. Asking for
        # plain "ATTRIBUTE" returns an error, so we use Wetlands.* here.
        feats = arcgis_query_all(
            url,
            geometry=geo.shapely_to_esri(geo.simplify_for_query(parcel_geom)),
            out_fields=config.NWI_FIELDS,
            max_records=500,
        )
    except SourceError as exc:
        # Fall back to all fields if the qualified names are ever rejected.
        try:
            feats = arcgis_query_all(
                url,
                geometry=geo.shapely_to_esri(geo.simplify_for_query(parcel_geom)),
                out_fields="*",
                max_records=500,
            )
        except SourceError:
            out["error"] = str(exc)
            return out

    out["available"] = True
    grouped: dict[str, list] = {}
    codes: dict[str, str] = {}
    for f in feats:
        attrs = f.get("attributes", {}) or {}
        wtype = (
            attrs.get("Wetlands.WETLAND_TYPE")
            or attrs.get("WETLAND_TYPE")
            or "Wetland"
        )
        code = attrs.get("Wetlands.ATTRIBUTE") or attrs.get("ATTRIBUTE")
        shp = geo.esri_to_shapely(f.get("geometry") or {})
        if shp is None:
            continue
        grouped.setdefault(wtype, []).append(shp)
        if code and wtype not in codes:
            codes[wtype] = code

    all_shapes = []
    for wtype, shapes in grouped.items():
        acres_in, pct = geo.pct_covered(parcel_geom, shapes)
        out["types"].append({
            "type": wtype,
            "code": codes.get(wtype),
            "acres": round(acres_in, 3),
            "pct": round(pct, 1),
        })
        all_shapes.extend(shapes)

    out["types"].sort(key=lambda t: -t["acres"])
    total_acres, total_pct = geo.pct_covered(parcel_geom, all_shapes)
    out["acres"] = round(total_acres, 3)
    out["pct"] = round(total_pct, 1)
    out["note"] = (
        "NWI is a remote-sensing inventory, not a jurisdictional determination. "
        "Only the Army Corps can decide whether a wetland is regulated."
    )
    return out
