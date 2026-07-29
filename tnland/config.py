"""
Every external data source used by TN Land Tool, in one place.

Design note: this tool was written without the ability to make live calls to
these hosts, so nothing here is trusted blindly. Each source declares a list of
CANDIDATE urls. At runtime `discovery.py` probes them, keeps the first that
answers, and caches the choice. `python -m tnland doctor` prints exactly which
candidate won and which failed, so a broken government endpoint shows up as a
clear message instead of a silent empty result.

Confidence labels below reflect how each endpoint was verified during the build:
  CONFIRMED  - service metadata was successfully read
  REPORTED   - documented / corroborated by multiple sources but not read directly
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Tennessee statewide parcels  (86 of 95 counties)
# --------------------------------------------------------------------------
# CONFIRMED. Fields: OBJECTID, COUNTY_ID, PARCEL_TYPE, GISLINK, PARCELID,
# PARCEL, ADDRESS, DEEDAC, OWNER, OWNER2, SUBDIV, LOT, LINK_TPAD, LINK_TPV,
# COUNTY_NAME.  maxRecordCount 2000, pagination supported.
TN_PARCELS = [
    "https://services1.arcgis.com/YuVBSS7Y1of2Qud1/arcgis/rest/services"
    "/Tennessee_Property_Boundaries_Public_Use/FeatureServer/0",
]

TN_PARCEL_FIELDS = {
    "parcel_id": "PARCELID",
    "gislink": "GISLINK",
    "owner": "OWNER",
    "owner2": "OWNER2",
    "address": "ADDRESS",
    "acres": "DEEDAC",
    "county": "COUNTY_NAME",
    "county_id": "COUNTY_ID",
    "subdivision": "SUBDIV",
    "lot": "LOT",
}

# --------------------------------------------------------------------------
# Tennessee statewide land use / CAMA extract  (Comptroller OLG)
# --------------------------------------------------------------------------
# CONFIRMED (layer discovered by name at runtime -- the service's own /layers
# response labelled it inconsistently, so we never hardcode the index).
# This is the single most valuable free layer in Tennessee: it carries
# appraised value, land vs improvement value, building counts, utilities,
# precomputed floodplain coverage, and a land-use classification.
TN_LANDUSE_SERVICE = [
    "https://tnmap.tn.gov/arcgis/rest/services/ENVIRONMENTAL"
    "/COMPTROLLER_OLG_LANDUSE/MapServer",
]
TN_LANDUSE_LAYER_NAME_HINT = "land use"

# LU_CLASSIFICATION coded values (CONFIRMED from service domain).
LAND_USE_CODES = {
    "1": "Single family residential, < 5 acres",
    "2": "Single family residential, >= 5 acres",
    "3": "Duplex (2 units)",
    "4": "Multifamily (3+ units)",
    "5": "Mobile home (1-4)",
    "6": "Mobile home park (5+)",
    "7": "Resort residential",
    "11": "General commercial",
    "12": "Office",
    "13": "Miscellaneous commercial",
    "14": "Miscellaneous commercial",
    "15": "Miscellaneous commercial",
    "16": "Miscellaneous commercial",
    "21": "Light industrial / warehousing",
    "22": "Heavy industrial",
    "31": "Public use",
    "32": "Semi-public use",
    "41": "Utilities",
    "51": "Vacant",
    "52": "Vacant",
    "53": "Vacant",
    "61": "Agricultural tract",
    "62": "Agricultural tract",
    "63": "Agricultural tract",
    "64": "Agricultural tract",
    "65": "Agricultural tract",
    "71": "Timber tract",
    "72": "Timber tract",
    "73": "Timber tract",
    "74": "Timber tract",
    "75": "Timber tract",
    "81": "Water feature",
    "82": "Water feature",
    "91": "Road / rail right of way",
    "92": "Road / rail right of way",
    "93": "Road / rail right of way",
    "96": "Unclassified improvements < $30K",
    "97": "Unclassified improvements >= $30K",
    "98": "CAAS data unavailable",
}

VACANT_LU_CODES = {"51", "52", "53"}
AG_LU_CODES = {"61", "62", "63", "64", "65"}
TIMBER_LU_CODES = {"71", "72", "73", "74", "75"}
# Codes that mean "no house on it" for land-buying purposes.
RAW_LAND_LU_CODES = VACANT_LU_CODES | AG_LU_CODES | TIMBER_LU_CODES

# --------------------------------------------------------------------------
# FEMA National Flood Hazard Layer
# --------------------------------------------------------------------------
# The hazards.fema.gov host blocks non-browser clients from *metadata* reads
# but serves /query fine from a normal client. The ArcGIS Online mirror is
# FEMA-owned and CONFIRMED; it is listed second as a failover.
FEMA_NFHL = [
    "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28",
    "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28",
    "https://services.arcgis.com/2gdL2gxYNFY2TOUb/arcgis/rest/services"
    "/FEMA_National_Flood_Hazard_Layer/FeatureServer/0",
]
FEMA_FIELDS = ["FLD_ZONE", "ZONE_SUBTY", "SFHA_TF", "STATIC_BFE"]

# Zones that are Special Flood Hazard Areas (mandatory insurance if financed).
SFHA_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE", "A1-30", "V1-30"}

# --------------------------------------------------------------------------
# USFWS National Wetlands Inventory
# --------------------------------------------------------------------------
# CONFIRMED. Gotcha: field names are table-qualified. `outFields=ATTRIBUTE`
# fails; you must ask for `Wetlands.ATTRIBUTE`.
NWI = [
    "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services"
    "/Wetlands/MapServer/0",
]
NWI_FIELDS = ["Wetlands.ATTRIBUTE", "Wetlands.WETLAND_TYPE", "Wetlands.ACRES"]

# --------------------------------------------------------------------------
# USGS 3DEP elevation
# --------------------------------------------------------------------------
# CONFIRMED live. Elevation is in the top-level "value" key; it has been seen
# returned both as a number and as a quoted string, so always coerce to float.
EPQS = "https://epqs.nationalmap.gov/v1/json"

# CONFIRMED live: exportImage supports format=tiff, bboxSR, imageSR,
# pixelType=F32. There is NO pixelSize parameter -- resolution is controlled
# by the bbox-to-size ratio. Max 8000x8000.
DEM_IMAGESERVER = [
    "https://elevation.nationalmap.gov/arcgis/rest/services"
    "/3DEPElevation/ImageServer",
]

# --------------------------------------------------------------------------
# NRCS Soil Data Access (SSURGO)
# --------------------------------------------------------------------------
# POST-only, form-urlencoded body with `query` and `format` keys.
SDA_POST = [
    "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest",
    "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest",
]

# --------------------------------------------------------------------------
# OpenStreetMap Overpass (road access / frontage)
# --------------------------------------------------------------------------
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Overpass mirrors go down, rate-limit, and time out often enough that they
# cannot be the only road source. Census TIGER/Line is the fallback: same
# ArcGIS query machinery as every other layer here, government-hosted, and
# far more reliable. CONFIRMED: layer 2 = Primary Roads, 8 = Local Roads,
# both esriGeometryPolyline with NAME and MTFCC. maxRecordCount 100000.
TIGER_ROADS = [
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb"
    "/Transportation/MapServer",
]
TIGER_ROAD_LAYERS = [2, 6, 8]   # primary, secondary, local

# TIGER MTFCC feature codes mapped onto the OSM highway vocabulary this app
# already speaks, so both sources produce identical output.
MTFCC_TO_HIGHWAY = {
    "S1100": "primary", "S1200": "secondary", "S1400": "residential",
    "S1500": "track", "S1630": "primary_link", "S1640": "service",
    "S1710": "footway", "S1720": "footway", "S1730": "service",
    "S1740": "service", "S1750": "service", "S1780": "service",
    "S1820": "cycleway", "S1830": "path",
}

# Fail fast on Overpass rather than hanging three times over before the
# fallback gets a chance.
OVERPASS_TIMEOUT = 25.0

# Overpass and the OSM tile servers both require a real User-Agent naming the
# app and a contact. Edit CONTACT below to your own email before heavy use.
CONTACT = "tn-land-tool (local install)"
USER_AGENT = f"TNLandTool/1.0 ({CONTACT})"

# --------------------------------------------------------------------------
# County parcel services for the 9 counties missing from the state layer
# --------------------------------------------------------------------------
# Chester, Davidson, Hamilton, Hickman, Knox, Montgomery, Rutherford, Shelby,
# and Williamson maintain their own data and are NOT in the statewide service.
#
# Ironically these are also the only Tennessee counties that publish
# machine-readable SALE PRICES -- which is what makes comps possible at all.
COUNTY_SERVICES = {
    "DAVIDSON": {
        "label": "Davidson (Nashville)",
        "url": "https://maps.nashville.gov/arcgis/rest/services"
               "/Cadastral/Parcels/MapServer/0",
        "status": "confirmed",
        "fields": {
            "parcel_id": "APN",
            "owner": "Owner",
            "owner_mail": ["OwnAddr1", "OwnAddr2", "OwnCity", "OwnState", "OwnZip"],
            "acres": "Acres",
            "land_use": "LUDesc",
            "land_use_code": "LUCode",
            "zoning": "Zoning",
            "appraisal": "TotlAppr",
            "land_value": "LandAppr",
            "improvement_value": "ImprAppr",
            "sale_price": "SalePrice",
            "sale_date": "OwnDate",
            "valid_sale": "ValidSale",
        },
    },
    "HAMILTON": {
        "label": "Hamilton (Chattanooga)",
        "url": "https://mapsdev.hamiltontn.gov/hcwa03/rest/services"
               "/Live_RealProperty/MapServer/0",
        "status": "confirmed-but-dev-host",
        "note": "Served from a host named 'mapsdev' with a layer named "
                "RealPropertyParcels2015. Verify currency before relying on it.",
        "fields": {
            "parcel_id": "GISLINK",
            "owner": "OWNERNAME1",
            "owner_mail": ["MASTNUM", "MASTNAME", "MALINE2", "MACITY",
                           "MASTATE", "MAZIP"],
            "acres": "DEEDACRES",
            "land_use_code": "LUCODE",
            "zoning": "Zoning",
            "appraisal": "APPVALUE",
            "land_value": "LANDVALUE",
            "improvement_value": "BUILDVALUE",
            "sale_price": "SALE1CONSD",
            "sale_date": "SALE1DATE",
        },
    },
    "MONTGOMERY": {
        "label": "Montgomery (Clarksville)",
        "url": "https://apnsgis4.apsu.edu/arcgis/rest/services"
               "/CMCGIS/MontViewer/FeatureServer/2",
        "status": "confirmed",
        "fields": {
            "parcel_id": "parcelid",
            "owner": "owner",
            "owner_mail": ["mailingaddress", "mailcity", "mailstate", "mailzip"],
            "acres": "deedacreage",
            "land_use": "landusedesc",
            "zoning": "zoning",
            "appraisal": "appraisedvalue",
            "improvement_value": "bldgmktvalue",
            "sale_price": "salesprice",
            "sale_date": "salesdate",
        },
    },
    "RUTHERFORD": {
        "label": "Rutherford (Murfreesboro)",
        "url": "https://maps.rutherfordcountytn.gov/server/rest/services"
               "/CoreServices/Parcels/FeatureServer/0",
        "status": "confirmed",
        "fields": {
            "parcel_id": "ParcelID",
            "owner": "Owner1",
            "acres": "DEEDACRES",
            "land_use_code": "LandUseCode",
            "zoning": "ZONING",
            "appraisal": "TotalValue",
            "land_value": "TotalLandValue",
            "improvement_value": "TotalBuildingValue",
            "sale_price": "SalePrice",
            "sale_date": "SaleDate",
        },
    },
    "SHELBY": {
        "label": "Shelby (Memphis)",
        "url": "https://maps.memphistn.gov/mapping/rest/services"
               "/Basemaps/TaxParcel_Basemap/MapServer/0",
        "status": "confirmed-geometry-and-owner-only",
        "note": "No appraised value, no sale price, no sale date on this "
                "service. Shelby's own gis.shelbycountytn.gov returns 403.",
        "fields": {
            "parcel_id": "PARCELID",
            "owner": "OWNER",
            "owner_mail": ["OWN_ADDR1", "OWN_ADDR2", "OWN_CITY",
                           "OWN_STATE", "OWN_ZIPCODE"],
            "acres": "CALC_ACRE",
            "land_use": "PROPERTY_TYPE",
            "address": "PARCEL_ADDRESS",
        },
    },
}

# Counties with no machine-readable public parcel service found at build time.
COUNTIES_NO_SERVICE = {
    "KNOX": "KGIS requires authentication (HTTP 401). Search by hand at "
            "https://propertyinfo.knoxcountytn.gov/",
    "WILLIAMSON": "Server is HTTP-only with a mismatched TLS certificate. "
                  "Search by hand at https://inigo.williamson-tn.org/property_search/",
    "CHESTER": "Uses a third-party CAMA portal with no GIS service. "
               "http://chester.capturecama.com/",
    "HICKMAN": "No public county GIS service found.",
}

# Counties that publish sale price + sale date, i.e. where comps work.
COMPS_COUNTIES = ["DAVIDSON", "HAMILTON", "MONTGOMERY", "RUTHERFORD"]

# --------------------------------------------------------------------------
# Per-parcel assessment lookup for the other 86 counties
# --------------------------------------------------------------------------
# TPAD is HTML-only, has no API, and returns 403 to non-browser clients. We do
# not scrape it. We generate the deep link so you can open the parcel in one
# click, which is the honest and compliant option.
TPAD_URL = "https://assessment.cot.tn.gov/TPAD/Parcel/GIS?gislink={gislink}"

# --------------------------------------------------------------------------
# Basemaps (no API key required)
# --------------------------------------------------------------------------
BASEMAPS = {
    "usgs_imagery": {
        "url": "https://basemap.nationalmap.gov/arcgis/rest/services"
               "/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "label": "USGS Imagery",
        "attribution": "USDA, USGS The National Map: Orthoimagery",
        "maxZoom": 16,
    },
    "esri_imagery": {
        "url": "https://services.arcgisonline.com/ArcGIS/rest/services"
               "/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "label": "Esri Imagery",
        "attribution": "Source: Esri, Vantor, Earthstar Geographics, "
                       "and the GIS User Community",
        "maxZoom": 19,
    },
    "osm": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "label": "Street map",
        "attribution": '&copy; <a href="https://www.openstreetmap.org/copyright">'
                       "OpenStreetMap</a> contributors",
        "maxZoom": 19,
    },
}

# --------------------------------------------------------------------------
# Tuning
# --------------------------------------------------------------------------
HTTP_TIMEOUT = 60.0
DEM_TARGET_GSD_M = 10.0      # metres per pixel for slope analysis
DEM_MAX_PIXELS = 1200        # cap per side; keeps requests polite and fast
ROAD_FRONTAGE_BUFFER_M = 12.0  # how close an OSM centreline counts as frontage
ACCESS_SEARCH_RADIUS_M = 60.0  # how far to look before calling it landlocked
CACHE_TTL_DAYS = 14
