# CLAUDE.md

Guidance for Claude Code and other AI assistants working in this repository.

## Project overview

**TN Land Tool** is a local land research application for Tennessee, built
entirely on free public data. It provides parcel information, environmental
analysis, and property screening through a web-based map interface. It runs
locally with no external uploads, API keys, or subscriptions.

Sources: TN Comptroller (parcels, land use), five county GIS services, FEMA,
USFWS, USGS 3DEP, USDA NRCS, OpenStreetMap, Census TIGER, and FOSSGIS
Valhalla (drive-time routing).

## Quick start

```bash
# Setup
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m tnland doctor            # verify every live data source
python -m tnland                   # run the app, opens 127.0.0.1:8823
python selftest.py                 # 289 offline checks

# CLI
python -m tnland address "2926 Bryant Ridge Rd, Baxter, TN 38544"
python -m tnland address "0 McBroom Branch Rd, Baxter, TN"  # every parcel on the road
python -m tnland parcel -86.586 35.845          # report in terminal
python -m tnland parcel -86.586 35.845 --json
python -m tnland doctor --verbose               # every candidate URL tried
python -m tnland doctor --lon -86.58 --lat 35.84  # test a specific location
python -m tnland cache stats
python -m tnland cache clear
```

## The one rule

**Run `python selftest.py` after every change. It must stay at 0 failures.**

Every check in that file exists because something was actually broken. When
you fix a bug, add a regression test in the same edit. When a test fails, the
test is probably right — this codebase was built without network access to
the live services, so the suite is the only thing standing between a
plausible-looking change and silently wrong acreage figures.

For anything touching the network, also run `python -m tnland doctor`.

## Invariants you must not break

These are encoded as tests. Violating one usually produces *wrong data*
rather than an error, which is why they are listed explicitly.

**Esri polygon winding is the opposite of GeoJSON.** In Esri JSON, outer
rings are CLOCKWISE and holes are counter-clockwise. `geo._signed_area` is
negative for clockwise. Get this backwards and a parcel with a carve-out
returns the *hole* as the parcel, or an empty geometry that crashes on
`.centroid`. Outbound query geometry must also be clockwise — Esri reads a
lone counter-clockwise ring as a zero-area hole and returns nothing. See
`geo.esri_to_shapely` and `geo.shapely_to_esri`.

**Never GET an ArcGIS `/query`.** Always POST. A real parcel outline is
several KB of JSON and a 100-item `IN()` clause is not much smaller. ArcGIS
behind IIS caps query strings at 2048 bytes and answers HTTP 404.15; behind
nginx it answers 414. Both look like a broken layer. `http.arcgis_query`
already POSTs — keep it that way.

**Never page manually.** Use `http.arcgis_query_all`. It handles four real
server behaviours: a `maxRecordCount` below your page size (short page plus
`exceededTransferLimit` — must keep going), a server that ignores
`resultOffset` (must dedupe and stop, not loop), a server that rejects
`resultOffset` outright with "Pagination is not supported" (must retry
unpaged — Hamilton County), and a server that 5xxes ONLY when
`resultOffset` is present (the offset forces an ordered scan; under load
the Esri-hosted statewide layer answers the identical query unpaged —
must fall back to one unpaged page without marking the layer
pagination-hostile, because it is transient).

**Never cache an error.** ArcGIS returns errors with HTTP 200 and an
`{"error": {...}}` body. `http._cacheable` rejects those. A cached error
makes one transient hiccup look like a permanently broken parcel for 14 days
— or a year, for the long-TTL sources.

**Never hardcode a layer index.** Use `http.find_layer_by_name`. The
Comptroller land-use service reports its layer id inconsistently. Guessing
`/0` yields missing attributes on every parcel in the state, cached for a
month, looking like bad data rather than a bad lookup.

**All distance and area maths happens in UTM,** via `geo.to_utm`. Tennessee
spans three zones (15N, 16N, 17N) and the tool picks per location. Never Web
Mercator — it inflates distances by ~1/cos(latitude), about 24% at
Tennessee's latitude, silently corrupting acreage, slope and road frontage.
A test computes the same shape at two longitudes and requires the same answer.

**Field-name gotchas that will not error, just return nothing:**

- USFWS NWI qualifies field names with the table: `Wetlands.ATTRIBUTE`, not
  `ATTRIBUTE`.
- FEMA uses `-9999` as the no-data sentinel for `STATIC_BFE`.
- FEMA `SFHA_TF` is the string `"T"`/`"F"`, not a boolean.
- SSURGO `farmlndcl` is a sentence — "All areas are prime farmland", not
  "Prime". Matching a `"prime"` prefix reports No on every parcel in the state.
- ArcGIS date fields are epoch **milliseconds**, and go negative for pre-1970
  sales. `parcels._epoch_to_date` handles four encodings; the 1e8–1e11 band is
  genuinely ambiguous and deliberately returns `None` rather than fabricating
  a date that would then be used as a comp.

## Architecture

**FastAPI server** (`server.py`) — routes `/api/parcel`, `/api/search/*`,
`/api/screen`, `/api/comps`, `/api/export.csv`, `/api/config`. Serves the
frontend from `tnland/static/`. Binds `127.0.0.1:8823`.

**Aggregation** (`analysis.py`) — `parcel_report()` is the central
aggregator. It runs the five layer queries concurrently in a
ThreadPoolExecutor, so a report takes as long as the slowest source rather
than the sum. `httpx.Client` is thread-safe and the sqlite cache is
lock-guarded; keep it that way if you add a source. `_flags()` turns the raw
results into the plain-language "at a glance" list, worst first.

**Parcels** (`sources/parcels.py`) — two tiers. The statewide Comptroller
service covers 86 counties with owner, acreage and parcel ID. The Comptroller
OLG land-use service joins to it on `GISLINK` and adds appraised value, land
vs improvement value, building count, utilities and precomputed floodplain
coverage — this is what makes vacancy filtering possible without paid AI
imagery classification. Five of the nine excluded counties have their own
adapters in `config.COUNTY_SERVICES`; four have no public service at all.

**Other sources** — `hazards.py` (FEMA flood, USFWS wetlands), `terrain.py`
(3DEP elevation, slope computed locally from a downloaded DEM), `roads.py`
(OpenStreetMap via Overpass, falling back to Census TIGER/Line), `soils.py`
(NRCS SSURGO), `geocode.py` (Census geocoder, falling back to Nominatim and
then to the assessor's own ADDRESS field), `drivetimes.py` (nearest
hospital / grocery / pharmacy / big-box / airport by real road-network
drive time -- ONE combined Overpass query finds candidates for every
tag category at once, results are classified locally from their tags,
and per-category Valhalla time matrices run concurrently to pick the
truly nearest by road; thresholds live in `config.DRIVETIME_CATEGORIES`,
informational categories use `threshold_min: None` and never flag).

`soilanalysis.py` (the soil-surveyor layer: real SSURGO polygon
intersection gives acres of each soil ON the parcel with a compass
position, plus NRCS's own "Septic Tank Absorption Fields" rating per
soil with the named limiting features, capability class and prime
farmland; three Soil Data Access queries, cached; a complex map unit
speaks with its dominant RATED component so rock outcrop cannot mute
the rating).

Drive times and soil analysis are the slow layers, so they are ON DEMAND: map clicks,
address search and the CLI default to the fast layers, with a
"Check drive times" / "Analyze soils" buttons on the panel
(`--drivetimes` / `--septic` on the CLI)
to run them for a parcel worth the wait. `/api/report` -- the printable
report -- always includes both: generating a report is the "I'm serious
about this parcel" signal. Tests pin these defaults.

Address lookup geocodes to a point and asks which parcel contains it, rather
than matching the parcel layer's ADDRESS field directly. That field is often
blank on rural vacant land and inconsistently formatted ("RD" vs "ROAD"), so
resolving to a coordinate is far more reliable. The field match remains as a
last resort for addresses TIGER does not know.

**Unnumbered addresses** (`roadsearch.py`) — listing sites write vacant land
as "0 McBroom Branch Rd" because the county never assigned a house number.
Verified live: the Census geocoder returns zero matches for these. Do not
"get close" by geocoding the road and returning a point — that snaps to
whichever neighbour happens to sit under it, which is worse than failing.
`geocode.is_unnumbered` detects the pattern (0, 00, TBD, Lot N, or no leading
digit) and `roadsearch.parcels_on_road` answers the real question instead:
find the road in TIGER, buffer it into a corridor, and return every parcel
fronting it with the raw land sorted first.

Numbered addresses have the opposite failure: the Census geocoder
interpolates them onto the road centreline, and the right-of-way strip
belongs to no parcel (seen live: '9515 Highway 147, Stewart' landed 2 m
from the listing-side boundary). `parcels.near_point` rescues these by
offering the bordering parcels as candidates -- acreage and distance in
the label -- rather than silently snapping to a possibly-wrong neighbour.

Slope is computed locally rather than via the server's `Slope Degrees` raster
function, because a server-side render has edge artifacts at the request
boundary and returns a picture instead of numbers.

Roads has two independent sources because the public Overpass mirrors
rate-limit, time out and go down regularly. TIGER is second because it is
coarser — no surface tags, less precise rural geometry — but it is
government-hosted and reliable. Both normalise to the same output; the panel
names which one answered.

**Geometry** (`geo.py`) — projection, acreage, buffering, Esri ↔ shapely
conversion, and `simplify_for_query` which thins oversized outlines before
they go into a request body (buffering outward so the query polygon always
covers the real parcel).

**HTTP and cache** (`http.py`, `cache.py`) — shared `httpx.Client`, ArcGIS
query and paging helpers, endpoint discovery. SQLite cache at
`~/.tnland/cache.sqlite`, 14-day default TTL; elevation rasters in
`~/.tnland/dem/`. There is deliberately **no** automatic retry layer — each
source decides its own fallback behaviour, because "try again" is rarely the
right answer when a government service is down.

**Progress** (`progress.py`) — in-memory job registry behind
`/api/progress`. The frontend sends a client-generated `job` id with report
requests and polls for per-source running/done/failed snapshots; the same
events are logged so the serve terminal narrates activity. In-memory and
single-process on purpose (local tool); jobs expire after five minutes.

**Screening and comps** (`lists.py`, `comps.py`) — area filters and CSV
export; comparable sales with median and quartile price per acre, available
in four counties only.

## Adding or changing a data source

Every endpoint lives in `config.py` as a **list of candidate URLs**, never a
bare string inline. `http.pick_working` probes them at runtime, keeps the
first that answers, and caches the choice; `doctor` reports which won. Mark
each as CONFIRMED (metadata was actually read) or REPORTED (documented but
unverified).

`pick_working` tries metadata first, then falls back to an actual `/query` —
because `hazards.fema.gov` serves queries fine but refuses metadata reads to
non-browser clients.

To add a whole new layer: add the candidates to `config.py`, write a module
under `sources/` returning `{"available": bool, ...}` with an `error` key on
failure, call it from `analysis.parcel_report()`, add a check to `doctor.py`,
and add fixtures plus assertions to `selftest.py`.

### Recipe: adding a county

The most likely edit. Add an entry to `config.COUNTY_SERVICES`:

```python
"COUNTYNAME": {
    "label": "Countyname (Seat)",
    "url": "https://.../MapServer/0",
    "status": "confirmed",
    "fields": {
        "parcel_id": "...", "owner": "...",
        "owner_mail": ["ADDR1", "CITY", "STATE", "ZIP"],  # list, joined
        "acres": "...", "land_use": "...", "appraisal": "...",
        "improvement_value": "...",
        "sale_price": "...", "sale_date": "...",   # optional
    },
},
```

If it exposes both `sale_price` and `sale_date`, also add it to
`config.COMPS_COUNTIES` — that is what turns comps on. If the county is one
of the nine outside the statewide layer, remove it from
`config.COUNTIES_NO_SERVICE`. A test asserts those two sets stay disjoint and
cover exactly nine counties.

Then run `python -m tnland doctor`. It pulls one real feature per county and
fails if any configured field name is not on the layer — the only way to
catch a mis-transcribed field name, since a wrong name returns `None` forever
rather than erroring.

## Things this project deliberately does not do

Do not add these, even if asked to "make it more like Land Portal":

- **Skip tracing.** No free, lawful bulk source for phone numbers exists. The
  tool gives owner names and mailing addresses and stops.
- **Scraping TPAD.** `assessment.cot.tn.gov` is HTML-only, has no API, and
  returns 403 to non-browser clients. We generate a deep link instead.
- **MLS listings.** Licensed data, cannot be redistributed.
- **Inventing a number when a source cannot supply one.** Comps in an
  unsupported county return an explanation, not an estimate. A sale that
  cannot be dated is dropped, not guessed. This is the project's main design
  commitment — every "unavailable" message names the actual reason.

## Known limitations

- **Four counties have no public parcel data**: Knox (auth required),
  Williamson (broken TLS), Chester (third-party CAMA portal), Hickman (none).
  Clicks there return nothing, with an explanation.
- **Comps work in four counties only**: Davidson, Hamilton, Montgomery,
  Rutherford. Tennessee is a full-disclosure state — sale consideration is
  sworn on the deed under Tenn. Code Ann. § 67-4-409 — but publishes no
  statewide machine-readable transaction data.
- **No skip tracing or MLS.** Stops at owner name and mailing address.
  MLS numbers cannot be resolved: boards such as UCMLS expose data only
  through RETS/IDX under a licensed vendor agreement. Use the listing's
  street address instead.
- **Vacancy detection is from county records, not satellite imagery.** Uses
  the assessor's land-use classification and building count: more reliable
  and free, but inherits the county's data quality and update lag.
- **Road access is a screening signal, not a title opinion.** Centrelines
  show physical contact, not legal access.
- **Wetlands are an inventory, not a determination.** Only the Army Corps can
  say whether a wetland is jurisdictional.
- **Slope percentages are of the parcel's bounding box**, slightly
  overstating area for irregular parcels.

## Debugging

**Broken external service** — `python -m tnland doctor --verbose` shows every
candidate URL and what each server said. If a URL is genuinely dead, remove
it from the candidate list in `config.py`; if a replacement is known, add it.
The next run probes and adopts it. Note that a cached failure is *not*
possible (see the caching invariant), so a stale error means the service is
still down.

**Geometry** — verify ring orientation with `geo._signed_area()`; confirm the
zone with `geo.utm_epsg_for(lon, lat)`; isolate with
`python selftest.py | grep -i geometry`.

**Wrong or missing attributes on every parcel** — almost always a field-name
or layer-index problem, not a data problem. Run `doctor`; its county check
compares configured field names against a live feature.

## Layout

```
tnland/
  config.py       every endpoint, field mapping and tuning constant
  http.py         HTTP client, ArcGIS query + paging, endpoint discovery
  geo.py          projection, area, Esri <-> shapely, query simplification
  cache.py        sqlite disk cache
  analysis.py     assembles a parcel report; the plain-language flags
  comps.py        comparable sales (4 counties only)
  lists.py        area screening, filters, CSV export
  server.py       FastAPI routes
  doctor.py       live health check
  progress.py     live per-source status for the polling frontend
  sources/        parcels, hazards, terrain, roads, soils, drivetimes,
                  soilanalysis
  static/index.html   the entire frontend
```

## Configuration

Set `CONTACT` in `config.py` to a real email before heavy use. Overpass and
the OSM tile servers both require automated clients to identify themselves,
and both may block clients that don't. Overpass fair use is 10,000 queries
and 1 GB per day; the cache keeps normal use far below that.

Other tunings: `HTTP_TIMEOUT`, `OVERPASS_TIMEOUT`, `DEM_TARGET_GSD_M`
(slope raster resolution), `CACHE_TTL_DAYS`, `ROAD_FRONTAGE_BUFFER_M`,
`ACCESS_SEARCH_RADIUS_M`.

## Versioning

`__version__` lives in `tnland/__init__.py`. Bump it with every change set
(minor for features, patch for fixes). `build_info()` appends the git short
hash of the code on disk plus `+edits` when the tree is dirty, and it is
displayed in the web header, the serve banner, doctor, and the printable
report footer -- that is how the user verifies which code is actually
running when the two clones drift.

## Style

Comments explain *why*, especially where the code looks wrong but isn't (the
winding convention, the `999.0 if x is None` sort key, the ambiguous date
band). Do not strip those. Do not add comments that restate the code.

User-facing strings name the real limitation in plain language. "Knox County
requires authentication" beats "no data available".
