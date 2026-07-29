# Parcel Report Design Spec
**Date:** 2026-07-29  
**User:** defiantvatic@gmail.com  
**Purpose:** Generate detailed, printable multi-page reports for evaluating parcels for septic, home sites, and hobby farming.

---

## Overview

Add a "Generate Report" feature to the TN Land Tool web UI that produces a detailed, printable HTML report focused on three use cases: septic system feasibility, home site potential, and hobby farming suitability. Users click a button on the parcel panel, and a formatted report opens in a new browser tab, optimized for printing to PDF.

---

## Core Use Cases

1. **Septic Suitability:** Evaluate soil drainage, hydric soils, slope steepness, and proximity to water to determine if a parcel can support a septic system.
2. **Home Site Potential:** Assess buildable area, slope, road access, utilities, and flood/wetland risk to evaluate home site feasibility.
3. **Hobby Farming:** Review soil quality (prime farmland status, soil types), land-use classification, acreage, and environmental constraints for small-scale agriculture.

---

## Report Structure

### 1. Header & Executive Summary
- **Parcel identification:** Address, county, parcel ID, owner, acreage
- **Coordinates:** Latitude/longitude for reference
- **Quick assessment:** One-sentence summary per use case (e.g., "Suitable for septic—excellent drainage" or "Marginal for farming—slopes exceed 15%")
- **Disclaimer:** Standard liability notice about data sources and limitations

### 2. Septic Suitability Section
**Why it matters:** Septic systems require adequate soil drainage, stable slopes, and distance from water sources.

**Data included:**
- Soil drainage ratings (map units, prime farmland status, hydric soils)
- Slope steepness (terrain slope %)
- Proximity indicators (wetlands, floodplain, creeks)
- Soil composition breakdown (clay, silt, sand percentages where available)
- Key constraints: slopes >15%, hydric soils, poor drainage = risk factors

### 3. Home Site Potential Section
**Why it matters:** Building sites need stable slopes, flood safety, access, and utilities.

**Data included:**
- Slope analysis (% and degree breakdown)
- Flood zone status (FEMA floodplain, base flood elevation if available)
- Wetlands presence (NWI data)
- Road access (frontage distance, surface type)
- Utilities (water, sewer availability if in county records)
- Constraints: slopes >20%, floodplain areas, wetlands = development risk

### 4. Hobby Farming Section
**Why it matters:** Agricultural viability depends on soil quality, land-use zoning, and environmental factors.

**Data included:**
- Soil types (all SSURGO map units with descriptions)
- Prime farmland status
- Land-use classification (county assessor records)
- Parcel acreage
- Constraints: prime farmland loss, poor soil, hydric/wetlands = farming challenges

### 5. Environmental & Water Features Section
**Why it matters:** Water, wetlands, and hazards are critical for all uses.

**Data included:**
- Water features: creeks, ponds, springs (from OSM Overpass, if available)
- Floodplain coverage (FEMA 100-year flood zone)
- Wetlands (USFWS NWI inventory)
- Elevation and prominence
- Distance to protected lands (if available)

### 6. Visual Elements
- **Parcel map:** Leaflet map showing parcel boundary, surrounding context
- **Soil breakdown:** Stacked bar chart or table of SSURGO map units and drainage ratings
- **Slope heatmap:** Color-coded breakdown of slope percentages (e.g., 0-5%, 5-10%, 10-15%, 15%+)
- **Water features overlay:** Mark on map where visible

### 7. Data Sources & Disclaimers
- List all data sources (Comptroller, county services, FEMA, USFWS, USGS, NRCS, OSM)
- Standard disclaimer: tool for reference only, not legal/professional advice
- Known limitations (e.g., soil data is inventory, not professional engineering survey)

---

## Technical Implementation

### Backend (Python/FastAPI)
1. **New endpoint:** `GET /api/report/<lon>/<lat>` or `POST /api/report` with coordinates
   - Reuses existing `analysis.parcel_report()` to fetch all data
   - Returns JSON with all parcel, soil, slope, flood, wetland, road, elevation data
   - Optionally enriches with water feature proximity (if Overpass layer already queried)

2. **No new data sources:** Use existing aggregation; water features pulled from roads/Overpass if already available, otherwise note "not available"

### Frontend (HTML/CSS/JavaScript)
1. **Report button:** Add "Generate Report" button to parcel panel (visible only after a parcel is loaded)
2. **Report template:** New HTML template (`report.html`) with CSS optimized for print
   - Responsive layout that works on screen and PDF printout
   - Sections collapsible on screen, all visible on print
   - Page breaks between major sections
   - Print-friendly colors (dark theme preserved for readability)

3. **Report generation flow:**
   - User clicks "Generate Report" on a loaded parcel
   - Frontend sends request to `/api/report?lon=X&lat=Y`
   - Backend returns JSON; frontend renders into report template
   - Opens in new tab/window with print stylesheet active
   - User prints to PDF via browser print dialog

### Data Visualization
- **Slope chart:** Use existing terrain data to create a simple stacked bar showing % area in each slope bucket
- **Soil table:** Clean, readable table of SSURGO map units with drainage ratings
- **Map:** Embed a static or simple Leaflet map showing parcel boundary and nearby water/roads

---

## Success Criteria

1. ✅ Report opens in browser, displays all parcel data relevant to septic/home/farming
2. ✅ Sections are clearly labeled and organized
3. ✅ Print-to-PDF produces readable, professional-looking document
4. ✅ No new external dependencies (use existing data sources)
5. ✅ Report loads within 2-3 seconds of clicking button
6. ✅ Soil and slope information is **readable** (addresses original pain point)
7. ✅ User can print and file the report for reference

---

## Scope Exclusions

- **PDF generation on server:** Using browser print is simpler, sufficient for user needs
- **Email/share feature:** Out of scope; print to PDF handles sharing
- **Historical reports/storage:** Out of scope; user responsibility to save
- **Professional engineer sign-off:** Out of scope; this is informational only
- **Water feature detection API:** Use existing data; if springs/ponds aren't in OSM, note as unavailable

---

## Unknowns & Future Enhancements

- **Water feature detection:** Currently limited to what's in OSM Overpass. Could later add USGS stream data or manual pond detection from aerial imagery.
- **Soil engineering data:** Report uses NRCS SSURGO (soil inventory); could enhance with bearing capacity or percolation rates if a new data source is added.
- **Customizable sections:** Users might want to hide/show sections; out of scope for v1.

---

## Files to Create/Modify

| File | Change | Purpose |
|------|--------|---------|
| `tnland/server.py` | Add `/api/report` endpoint | Serve report data |
| `tnland/static/report.html` | Create | Report template |
| `tnland/static/report.css` | Create | Print-optimized styles |
| `tnland/static/report.js` | Create | Report rendering logic |
| `tnland/static/index.html` | Modify | Add "Generate Report" button |
| `tnland/sources/water.py` | **Optional, future** | Water feature aggregation (not v1) |

---

## Notes

- The report reuses all existing data aggregation; no new network calls or data sources added
- Print stylesheet ensures the dark theme is readable in print
- Report is self-contained (no external CDNs) for offline printing
- User testing should focus on the soil/slope readability improvement

