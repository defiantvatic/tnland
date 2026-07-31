# TN Land Tool

A local land research tool for Tennessee, built entirely on free public data.
It covers most of what Land Portal charges for, using the Tennessee
Comptroller's own parcel and assessment services plus FEMA, USFWS, USGS and
USDA NRCS.

Runs on your machine. Nothing is uploaded anywhere. No accounts, no API keys,
no subscription.

---

## Install

Requires Python 3.10 or newer.

```bash
cd tnland
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Check that every data source is reachable before you rely on it:

```bash
python -m tnland doctor
```

Then start it:

```bash
python -m tnland
```

Your browser opens at `http://127.0.0.1:8823`.

---

## What it does

**Click any parcel** and you get, in one panel:

| | Source |
|---|---|
| Owner name, parcel ID, deeded and GIS acreage | TN Comptroller, 86 counties |
| Appraised value, land vs improvement value, building count | Comptroller OLG (IMPACT CAMA) |
| Land use classification — vacant, ag, timber, residential | Comptroller OLG |
| Water/sewer, electric, gas on record | Comptroller OLG |
| FEMA flood zones, acreage and % of parcel in each | FEMA NFHL |
| Wetlands by type, acreage and % | USFWS NWI |
| Slope distribution, % buildable under 15%, relief, elevation | USGS 3DEP |
| Road frontage in feet, landlocked screening | OpenStreetMap |
| Soil map units, drainage class, prime farmland, hydric soils | USDA NRCS SSURGO |
| Drive time to the nearest hospital, grocery, pharmacy, dentist, big-box store and large airport, checked against your targets | OpenStreetMap + FOSSGIS Valhalla |

Plus a plain-language "at a glance" summary that sorts the problems to the top.

**Draw a polygon or box** and screen every parcel inside it by acreage,
appraised value, land use, structures present, and whether the owner's mailing
address is outside the county. Export the result to CSV with owner names and
mailing addresses for a letter campaign.

**Comps** — median and quartile price per acre from recorded sales, with an
implied value range for your subject parcel. Works in four counties (see
limitations).

**Search** by owner name or parcel ID statewide.

---

## Limitations, stated plainly

These are real and you should know them before you rely on this.

**Four counties have no public parcel data at all.** Knox, Williamson, Chester
and Hickman are excluded from the state layer and publish nothing queryable.
Knox requires authentication, Williamson's server has a broken TLS
certificate, Chester uses a third-party CAMA portal, Hickman has nothing.
Clicking in those counties returns nothing. The tool says so rather than
failing silently.

**Comps only work in Davidson, Hamilton, Montgomery and Rutherford.**
Tennessee *is* a full-disclosure state — sale consideration is sworn on the
deed under Tenn. Code Ann. § 67-4-409 — but the state publishes no
transaction-level sales data in machine-readable form. Only those four
counties expose sale price through a public API. For the other 91, the tool
gives you a one-click deep link to the parcel's TPAD record, where you can
read its sale history by hand.

**No skip tracing.** There is no free, lawful bulk source for phone numbers.
The tool gives you owner names and, where the county publishes them, mailing
addresses, and stops there. Anything more requires a paid data broker.

**No MLS listings.** MLS data is licensed and cannot be redistributed free.

**Vacancy detection is from county records, not AI imagery.** Land Portal runs
satellite imagery through a model. This uses the county assessor's own land-use
classification and building count, which is more reliable and free — but it
inherits the county's data quality and update lag.

**Road access is a screening signal, not a title opinion.** OpenStreetMap road
centrelines tell you whether a road physically touches the parcel. They cannot
tell you whether you have legal access. A parcel with no mapped road may have a
recorded easement; a parcel touching a road may have no right to use it.

**Wetlands are an inventory, not a determination.** Only the Army Corps can say
whether a wetland is jurisdictional.

**Slope percentages are of the parcel's bounding box**, which slightly
overstates the area for irregularly shaped parcels.

**Drive times are free-flow** -- no traffic model -- and computed from the
parcel centroid. Targets are edited in `config.DRIVETIME_CATEGORIES`.

---

## Commands

```bash
python -m tnland                    # start the map interface
python -m tnland doctor             # test every data source, verbosely
python -m tnland doctor --verbose   # show every candidate URL tried
python -m tnland parcel -86.586 35.845     # one parcel report in the terminal
python -m tnland parcel -86.586 35.845 --json
python -m tnland cache stats        # how much is cached
python -m tnland cache clear        # wipe the cache
python selftest.py                  # offline test suite, 227 checks
```

---

## How it holds up when a government server changes

Every source declares a list of candidate URLs rather than one. On first use
the tool probes them, keeps whichever answers, and remembers the choice. The
Comptroller land-use layer is found by matching its *name* rather than a
hardcoded layer index, because that index is reported inconsistently by the
service itself.

When something does break, `doctor` tells you exactly which host failed and
what it said, instead of the app quietly returning empty results.

All responses are cached in `~/.tnland/cache.sqlite`, so repeat lookups are
instant and the public servers are not hammered. Elevation rasters are cached
in `~/.tnland/dem/`.

---

## Before you use it heavily

Open `tnland/config.py` and set `CONTACT` to your email address. Both the
Overpass API and the OpenStreetMap tile servers ask that automated clients
identify themselves with a real contact, and both are within their rights to
block clients that don't. Overpass's fair-use limit is 10,000 queries and 1 GB
per day — the cache keeps you far below that in normal use.

---

## Attribution

- Parcel and assessment data: Tennessee Comptroller of the Treasury, Division
  of Property Assessments
- County GIS: Nashville/Davidson, Hamilton County, APSU GIS Center
  (Montgomery), Rutherford County, City of Memphis
- Flood: FEMA National Flood Hazard Layer
- Wetlands: U.S. Fish and Wildlife Service National Wetlands Inventory
- Elevation: USGS 3D Elevation Program
- Soils: USDA NRCS Soil Survey Geographic Database
- Roads and street basemap: © OpenStreetMap contributors, ODbL
- Imagery: USGS The National Map orthoimagery; Esri World Imagery

---

## Disclaimer

Screening information only. Not a survey, title report, appraisal, flood
determination, or jurisdictional wetland determination. Verify anything you
would act on.
