"""Tennessee parcel geometry, ownership, and assessment attributes.

Two tiers:

  * The statewide Comptroller service covers 86 counties and carries owner
    name, deeded acreage and parcel ID.
  * The Comptroller OLG land-use service covers the same parcels and adds
    appraised value, land vs improvement value, building count, utilities,
    land-use classification and precomputed floodplain coverage. It joins on
    GISLINK. This is what makes vacancy filtering possible without paying
    anyone for AI imagery classification.

The nine counties absent from the statewide service (Davidson, Hamilton,
Knox, Montgomery, Rutherford, Shelby, Williamson, Chester, Hickman) are
handled by per-county adapters in config.COUNTY_SERVICES where a public
service exists.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from .. import config, geo
from ..http import (
    SourceError,
    arcgis_query,
    arcgis_query_all,
    find_layer_by_name,
    pick_working,
)


# Epoch anchor for date maths. See _epoch_to_date for why we add a timedelta
# to this rather than calling datetime.fromtimestamp().
_EPOCH = _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)


def _statewide_url() -> str:
    return pick_working(config.TN_PARCELS)


def _landuse_url() -> str:
    service = pick_working(config.TN_LANDUSE_SERVICE)
    return find_layer_by_name(service, config.TN_LANDUSE_LAYER_NAME_HINT)


# ---------------------------------------------------------------------------
# Normalised parcel record
# ---------------------------------------------------------------------------

def _blank_parcel() -> dict[str, Any]:
    return {
        "source": None,
        "county": None,
        "parcel_id": None,
        "gislink": None,
        "owner": None,
        "owner_mailing": None,
        "situs_address": None,
        "subdivision": None,
        "lot": None,
        "deeded_acres": None,
        "gis_acres": None,
        "land_use_code": None,
        "land_use": None,
        "zoning": None,
        "appraisal": None,
        "land_value": None,
        "improvement_value": None,
        "buildings": None,
        "year_built": None,
        "utilities": {},
        "sale_price": None,
        "sale_date": None,
        "state_floodplain": None,
        "tpad_url": None,
        "geometry": None,
        "notes": [],
    }


def _from_statewide(feature: dict[str, Any]) -> dict[str, Any]:
    attrs = feature.get("attributes", {}) or {}
    f = config.TN_PARCEL_FIELDS
    rec = _blank_parcel()
    rec["source"] = "TN Comptroller statewide parcels"
    rec["parcel_id"] = _clean(attrs.get(f["parcel_id"]))
    rec["gislink"] = _clean(attrs.get(f["gislink"]))
    owner = _clean(attrs.get(f["owner"]))
    owner2 = _clean(attrs.get(f["owner2"]))
    rec["owner"] = " / ".join(x for x in (owner, owner2) if x) or None
    rec["situs_address"] = _clean(attrs.get(f["address"]))
    rec["subdivision"] = _clean(attrs.get(f["subdivision"]))
    rec["lot"] = _clean(attrs.get(f["lot"]))
    rec["deeded_acres"] = _num(attrs.get(f["acres"]))
    rec["county"] = _clean(attrs.get(f["county"]))
    if rec["gislink"]:
        rec["tpad_url"] = config.TPAD_URL.format(
            gislink=rec["gislink"].replace(" ", "%20")
        )
    _attach_geometry(rec, feature)
    return rec


def _apply_landuse(rec: dict[str, Any], attrs: dict[str, Any]) -> None:
    """Merge Comptroller OLG land-use attributes onto a parcel record."""
    code = _clean(attrs.get("LU_CLASSIFICATION"))
    if code is not None:
        code = code.strip().lstrip("0") or code.strip()
    rec["land_use_code"] = code
    rec["land_use"] = config.LAND_USE_CODES.get(code or "", None)
    rec["appraisal"] = _num(attrs.get("APPRAISAL"))
    rec["land_value"] = _num(attrs.get("LANDVALUE"))
    rec["improvement_value"] = _num(attrs.get("IMPVALUE"))
    rec["buildings"] = _num(attrs.get("NUMBUILDINGS"))
    rec["year_built"] = _num(attrs.get("YRBLT")) or None
    rec["utilities"] = {
        "water_sewer": _clean(attrs.get("WATERSEWER")),
        "electric": _clean(attrs.get("ELEC")),
        "gas": _clean(attrs.get("GAS")),
    }
    rec["state_floodplain"] = {
        "in_floodplain": _clean(attrs.get("FLOODPLAIN")),
        "acres_in_flood": _num(attrs.get("ACRESINFLOOD")),
        "pct_of_parcel": _num(attrs.get("PEROFPARCELINFLOOD")),
        "house_in_floodplain": _clean(attrs.get("HOUSEINFLOODPLAIN")),
    }
    rec["state_context"] = {
        "major_road_frontage": _clean(attrs.get("MAJORROADFRONTAGE")),
        "borders_railroad": _clean(attrs.get("BORDERSRAILROAD")),
        "interstate_within_10min": _clean(attrs.get("INTERSTATEACCESSTENMINUTES")),
        "retail_within_15min": _clean(attrs.get("RETAILACCESSFIFTEENMINUTES")),
        "population_growth_area": _clean(attrs.get("POPGROWTHAREA")),
        "employment_hub": _clean(attrs.get("EMPLOYMENTHUB")),
    }
    if not rec.get("situs_address"):
        rec["situs_address"] = _clean(attrs.get("ADDRESS"))


def _from_county(feature: dict[str, Any], county: str) -> dict[str, Any]:
    cfg = config.COUNTY_SERVICES[county]
    fields = cfg["fields"]
    attrs = feature.get("attributes", {}) or {}
    rec = _blank_parcel()
    rec["source"] = f"{cfg['label']} county GIS"
    rec["county"] = county.title()
    if cfg.get("note"):
        rec["notes"].append(cfg["note"])

    def pull(key):
        name = fields.get(key)
        return attrs.get(name) if name else None

    rec["parcel_id"] = _clean(pull("parcel_id"))
    rec["owner"] = _clean(pull("owner"))
    rec["situs_address"] = _clean(pull("address"))
    rec["deeded_acres"] = _num(pull("acres"))
    rec["land_use"] = _clean(pull("land_use"))
    rec["land_use_code"] = _clean(pull("land_use_code"))
    rec["zoning"] = _clean(pull("zoning"))
    rec["appraisal"] = _num(pull("appraisal"))
    rec["land_value"] = _num(pull("land_value"))
    rec["improvement_value"] = _num(pull("improvement_value"))
    rec["sale_price"] = _num(pull("sale_price"))
    rec["sale_date"] = _epoch_to_date(pull("sale_date"))

    mail_fields = fields.get("owner_mail") or []
    parts = [_clean(attrs.get(k)) for k in mail_fields]
    rec["owner_mailing"] = " ".join(p for p in parts if p) or None

    _attach_geometry(rec, feature)
    return rec


def _attach_geometry(rec: dict[str, Any], feature: dict[str, Any]) -> None:
    """Parse a feature's geometry, tolerating anything malformed.

    One bad ring in a county extract must not 500 an entire area search, so
    a parcel that fails to parse keeps its attributes and simply has no shape.
    """
    try:
        geom = geo.esri_to_shapely(feature.get("geometry") or {})
    except Exception as exc:  # noqa: BLE001
        rec["notes"].append(f"Geometry could not be parsed: {exc}")
        return
    if geom is None or geom.is_empty:
        return
    rec["geometry"] = geom
    try:
        rec["gis_acres"] = round(geo.acres(geom), 3)
    except Exception:  # noqa: BLE001
        rec["gis_acres"] = None


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def at_point(lon: float, lat: float) -> dict[str, Any] | None:
    """Find the parcel containing a clicked point, from whichever tier has it."""
    point = {"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}

    try:
        data = arcgis_query(
            _statewide_url(),
            geometry=point,
            geometry_type="esriGeometryPoint",
            out_fields=list(config.TN_PARCEL_FIELDS.values()),
        )
        feats = data.get("features", [])
        if feats:
            rec = _from_statewide(feats[0])
            _enrich_statewide(rec)
            return rec
    except SourceError:
        pass

    for county in config.COUNTY_SERVICES:
        try:
            data = arcgis_query(
                config.COUNTY_SERVICES[county]["url"],
                geometry=point,
                geometry_type="esriGeometryPoint",
            )
        except Exception:  # noqa: BLE001 - a dead county server must not break the click
            continue
        feats = data.get("features", [])
        if feats:
            return _from_county(feats[0], county)
    return None


def _enrich_statewide(rec: dict[str, Any]) -> None:
    """Join the OLG land-use attributes onto a statewide parcel via GISLINK."""
    if not rec.get("gislink"):
        return
    try:
        url = _landuse_url()
        safe = rec["gislink"].replace("'", "''")
        data = arcgis_query(
            url,
            where=f"GISLINK = '{safe}'",
            return_geometry=False,
            out_fields="*",
        )
        feats = data.get("features", [])
        if feats:
            _apply_landuse(rec, feats[0].get("attributes", {}))
        else:
            rec["notes"].append(
                "No Comptroller land-use record matched this GISLINK, so "
                "appraised value and land-use class are unavailable."
            )
    except SourceError as exc:
        rec["notes"].append(f"Land-use layer unavailable: {exc}")


def _acres_where(min_acres: float | None, field: str) -> str | None:
    """WHERE clause for a minimum-acreage filter, or None for no filter.

    float() both validates and neutralises the value before it enters the
    query string. Note the trade-off, stated to the user where this is
    used: parcels with no recorded acreage cannot pass a >= test, so the
    filter drops them.
    """
    if not min_acres or min_acres <= 0:
        return None
    return f"{field} >= {float(min_acres)}"


def near_point(lon: float, lat: float, radius_m: float = 150.0,
               limit: int = 12) -> list[dict[str, Any]]:
    """Parcels within radius_m of a point, nearest first, with distance_ft.

    Exists for numbered addresses that geocode into the road right-of-way:
    the Census geocoder interpolates along the street centreline, and the
    centreline strip belongs to no parcel -- seen live with '9515 Highway
    147, Stewart', whose point landed 2 m from the listing-side boundary.
    Never used for unnumbered '0 Road Name' listings, which must not snap.
    """
    from shapely.geometry import Point

    pt = Point(lon, lat)
    found = in_area(geo.buffer_m(pt, radius_m), max_records=50)
    apply_bulk_landuse(found)
    pt_utm, epsg = geo.to_utm(pt)
    rows = []
    for rec in found:
        g = rec.get("geometry")
        if g is None:
            continue
        g_utm = geo.reproject(g, geo.WGS84, epsg)
        rec["distance_ft"] = round(g_utm.distance(pt_utm) * 3.28084)
        rows.append(rec)
    rows.sort(key=lambda r: r["distance_ft"])
    return rows[:limit]


def in_area(geom_wgs84, max_records: int = 5000,
            min_acres: float | None = None) -> list[dict[str, Any]]:
    """All parcels intersecting a drawn polygon. Used for list building.

    min_acres filters server-side, so the max_records cap applies to
    parcels that matter rather than being eaten by small lots.
    """
    esri = geo.shapely_to_esri(geo.simplify_for_query(geom_wgs84))
    out: list[dict[str, Any]] = []

    kwargs: dict[str, Any] = {}
    where = _acres_where(min_acres, "DEEDAC")
    if where:
        kwargs["where"] = where
    try:
        feats = arcgis_query_all(
            _statewide_url(),
            geometry=esri,
            out_fields=list(config.TN_PARCEL_FIELDS.values()),
            max_records=max_records,
            **kwargs,
        )
        out.extend(_from_statewide(f) for f in feats)
    except SourceError:
        pass

    for county, cfg in config.COUNTY_SERVICES.items():
        ckwargs: dict[str, Any] = {}
        cwhere = _acres_where(min_acres, cfg["fields"].get("acres", ""))
        if cwhere and cfg["fields"].get("acres"):
            ckwargs["where"] = cwhere
        try:
            feats = arcgis_query_all(
                cfg["url"], geometry=esri, max_records=max_records, **ckwargs
            )
        except Exception:  # noqa: BLE001
            continue
        out.extend(_from_county(f, county) for f in feats)
    return out


def bulk_landuse(gislinks: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch OLG land-use attributes for many parcels in batched IN() queries.

    One request per parcel would mean thousands of round trips for a county
    search. We batch by GISLINK instead.
    """
    result: dict[str, dict[str, Any]] = {}
    links = [g for g in gislinks if g]
    if not links:
        return result
    try:
        url = _landuse_url()
    except SourceError:
        return result

    BATCH = 100
    for i in range(0, len(links), BATCH):
        chunk = links[i:i + BATCH]
        quoted = ",".join("'" + g.replace("'", "''") + "'" for g in chunk)
        try:
            feats = arcgis_query_all(
                url,
                where=f"GISLINK IN ({quoted})",
                return_geometry=False,
                out_fields="*",
                max_records=BATCH * 2,
            )
        except Exception:  # noqa: BLE001
            continue
        for f in feats:
            attrs = f.get("attributes", {}) or {}
            link = _clean(attrs.get("GISLINK"))
            if link:
                result[link] = attrs
    return result


def apply_bulk_landuse(records: list[dict[str, Any]]) -> None:
    lookup = bulk_landuse([r.get("gislink") for r in records])
    for rec in records:
        attrs = lookup.get(rec.get("gislink") or "")
        if attrs:
            _apply_landuse(rec, attrs)


def search_owner(name: str, county: str | None = None,
                 limit: int = 200) -> list[dict[str, Any]]:
    safe = name.replace("'", "''").upper()
    where = f"UPPER(OWNER) LIKE '%{safe}%'"
    if county:
        where += f" AND UPPER(COUNTY_NAME) = '{county.replace(chr(39), '').upper()}'"
    feats = arcgis_query_all(
        _statewide_url(),
        where=where,
        out_fields=list(config.TN_PARCEL_FIELDS.values()),
        max_records=limit,
    )
    return [_from_statewide(f) for f in feats]


def search_parcel_id(parcel_id: str, limit: int = 50) -> list[dict[str, Any]]:
    safe = parcel_id.replace("'", "''").upper()
    feats = arcgis_query_all(
        _statewide_url(),
        where=f"UPPER(PARCELID) LIKE '%{safe}%' OR UPPER(GISLINK) LIKE '%{safe}%'",
        out_fields=list(config.TN_PARCEL_FIELDS.values()),
        max_records=limit,
    )
    records = [_from_statewide(f) for f in feats]
    apply_bulk_landuse(records)
    return records


def counties() -> list[str]:
    """Distinct county names in the statewide layer, plus the nine extras."""
    from ..http import get_json

    url = _statewide_url()
    try:
        data = get_json(
            url + "/query",
            {
                "f": "json",
                "where": "1=1",
                "outFields": "COUNTY_NAME",
                "returnDistinctValues": "true",
                "returnGeometry": "false",
                "orderByFields": "COUNTY_NAME",
            },
            ttl_days=30,
        )
        names = sorted({
            f["attributes"]["COUNTY_NAME"]
            for f in data.get("features", [])
            if f.get("attributes", {}).get("COUNTY_NAME")
        })
    except Exception:  # noqa: BLE001
        names = []
    for county in config.COUNTY_SERVICES:
        label = county.title()
        if label not in names:
            names.append(label)
    return sorted(names)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if num != num else num  # drop NaN


def _epoch_to_date(value: Any) -> str | None:
    """ArcGIS date fields come back as epoch milliseconds."""
    if value in (None, "", 0):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return _clean(value)
    # NaN. json.loads accepts a bare NaN literal, so this does arrive from
    # real services, and int(nan) raises -- one bad row must not take down an
    # entire area search or comps run.
    if num != num:
        return None

    # Counties encode dates four different ways and the magnitudes barely
    # overlap, so classify by range rather than guessing:
    #
    #   YYYY            1900 - 2100          ~2.1e3
    #   YYYYMMDD        19000101 - 21001231  ~2.1e7
    #   ambiguous band                        1e8 - 1e11
    #   epoch millis    1965 -> -1.6e11, 2026 -> 1.8e12
    #
    # The band between is genuinely ambiguous: as milliseconds it can only
    # ever mean 1970-1973, as seconds it means 1973-5138, and plenty of
    # non-dates (deed book/page composites, instrument numbers) land there
    # too. Rather than fabricate a confident-looking wrong date -- which
    # would then be used as a comp -- treat it as unknown. Comps drop sales
    # they cannot date, which is the right outcome.
    if abs(num) >= 1e11:
        try:
            # Deliberately NOT datetime.fromtimestamp(). On Windows that
            # raises OSError for any negative timestamp, so every pre-1970
            # deed would silently come back as None -- and it does so only
            # on Windows, which makes it invisible in Linux testing. Adding
            # a timedelta to the epoch is platform-independent.
            return (
                _EPOCH + _dt.timedelta(milliseconds=num)
            ).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None
    if abs(num) >= 1e8:
        return None

    whole = int(num)
    if 19000101 <= whole <= 21001231:
        try:
            return _dt.date(whole // 10000, whole // 100 % 100,
                            whole % 100).strftime("%Y-%m-%d")
        except ValueError:
            return None
    if 1900 <= whole <= 2100:
        return str(whole)
    return _clean(value)
