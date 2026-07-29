# Parcel Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a printable multi-page parcel report feature that helps users evaluate parcels for septic, home site, and hobby farming suitability.

**Architecture:** Add a backend API endpoint that reuses existing parcel aggregation, then create a frontend report template (HTML/CSS/JS) that renders data into sections, optimized for browser printing to PDF.

**Tech Stack:** FastAPI (backend), HTML/CSS/JavaScript (frontend), Leaflet (map), no new dependencies.

## Global Constraints

- No new external dependencies
- Use existing data aggregation (no new data sources)
- Printable to PDF via browser print dialog only
- Must work with dark theme and remain readable in print
- Report loads within 2-3 seconds

---

## File Structure

```
tnland/
  server.py                    [MODIFY] Add /api/report endpoint
  static/
    index.html                 [MODIFY] Add "Generate Report" button to parcel panel
    report.html                [CREATE] Report template with all sections
    report.css                 [CREATE] Print-optimized styles
    report.js                  [CREATE] Report rendering and interactivity
```

---

## Task 1: Add Backend API Endpoint

**Files:**
- Modify: `tnland/server.py`

**Interfaces:**
- Consumes: `analysis.parcel_report(lon, lat, include)` (existing)
- Produces: `GET /api/report?lon=<float>&lat=<float>&layers=<str>` → JSON response with all parcel data

**Description:** Add a new FastAPI endpoint that wraps the existing `parcel_report()` function and returns JSON suitable for rendering into the report template.

- [ ] **Step 1: Read the current server.py to understand routing pattern**

Check [tnland/server.py:43-46](tnland/server.py#L43-L46) — the `/api/parcel` endpoint shows the pattern.

- [ ] **Step 2: Add the `/api/report` endpoint to server.py**

Insert after the `/api/parcel` endpoint:

```python
@app.get("/api/report")
def report(lon: float, lat: float, layers: str = "flood,wetlands,slope,roads,soils"):
    """Generate data for a detailed printable parcel report."""
    include = {x.strip() for x in layers.split(",") if x.strip()}
    return analysis.parcel_report(lon, lat, include=include)
```

- [ ] **Step 3: Test the endpoint locally**

Run `python -m tnland` to start the server, then visit:
```
http://127.0.0.1:8823/api/report?lon=-86.586&lat=35.845
```

Verify you get a JSON response with parcel, flood, wetlands, terrain, access, soils, elevation keys.

- [ ] **Step 4: Commit**

```bash
git add tnland/server.py
git commit -m "feat: add /api/report endpoint for printable parcel reports"
```

---

## Task 2: Create Report Template HTML

**Files:**
- Create: `tnland/static/report.html`

**Interfaces:**
- Consumes: JSON from `/api/report` endpoint (parcel, flood, wetlands, terrain, access, soils, elevation)
- Produces: Rendered HTML report with sections for header, septic, home site, farming, environmental, water, sources/disclaimers

**Description:** Create a self-contained HTML template that will be served standalone and populated with parcel data via JavaScript.

- [ ] **Step 1: Create the report.html skeleton**

Create `tnland/static/report.html` with this structure:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parcel Report</title>
<link rel="stylesheet" href="report.css">
</head>
<body>
<div id="report-container">
  <!-- Will be populated by report.js -->
  <div id="report-content"></div>
</div>
<script src="report.js"></script>
</body>
</html>
```

- [ ] **Step 2: Add route in server.py to serve report.html**

Modify `tnland/server.py` — add this after the `/api/report` endpoint:

```python
@app.get("/report")
def report_page() -> FileResponse:
    """Serve the report template (populated by frontend JavaScript)."""
    return FileResponse(STATIC / "report.html")
```

- [ ] **Step 3: Test navigation**

Start the server and visit `http://127.0.0.1:8823/report` — should load a blank page (will be populated by JavaScript in Task 3).

- [ ] **Step 4: Commit**

```bash
git add tnland/static/report.html tnland/server.py
git commit -m "feat: add /report route and template for report page"
```

---

## Task 3: Create Report Rendering JavaScript

**Files:**
- Create: `tnland/static/report.js`

**Interfaces:**
- Consumes: URL query parameters (`lon`, `lat`); JSON from `/api/report?lon=X&lat=Y`
- Produces: Rendered HTML into `#report-content` div with all sections populated

**Description:** Fetch parcel data from the API and render it into a formatted report with sections for septic, home site, farming, environmental, and water features.

- [ ] **Step 1: Create report.js with main fetch and render function**

Create `tnland/static/report.js`:

```javascript
// Extract lon/lat from URL query params
function getQueryParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    lon: parseFloat(params.get('lon')),
    lat: parseFloat(params.get('lat')),
  };
}

// Fetch parcel data from API
async function fetchParcelData(lon, lat) {
  const response = await fetch(`/api/report?lon=${lon}&lat=${lat}`);
  if (!response.ok) throw new Error('Failed to fetch parcel data');
  return response.json();
}

// Main render function
async function renderReport() {
  try {
    const { lon, lat } = getQueryParams();
    if (isNaN(lon) || isNaN(lat)) {
      document.getElementById('report-content').innerHTML = 
        '<p style="color:red;">Invalid coordinates. Please use /report?lon=X&lat=Y</p>';
      return;
    }

    const data = await fetchParcelData(lon, lat);
    
    if (!data.found) {
      document.getElementById('report-content').innerHTML = 
        `<p>${data.message || 'Parcel not found.'}</p>`;
      return;
    }

    const html = renderFullReport(data);
    document.getElementById('report-content').innerHTML = html;
  } catch (err) {
    document.getElementById('report-content').innerHTML = 
      `<p style="color:red;">Error loading report: ${err.message}</p>`;
  }
}

// Render the complete report
function renderFullReport(data) {
  return `
    ${renderHeader(data)}
    ${renderExecutiveSummary(data)}
    ${renderSepticSection(data)}
    ${renderHomeSiteSection(data)}
    ${renderFarmingSection(data)}
    ${renderEnvironmentalSection(data)}
    ${renderSourcesDisclaimer(data)}
  `;
}

// Helper: Header section
function renderHeader(data) {
  const parcel = data.parcel || {};
  return `
    <div class="section header-section">
      <h1>Parcel Report</h1>
      <div class="header-info">
        <p><strong>Address:</strong> ${parcel.situs_address || 'N/A'}</p>
        <p><strong>County:</strong> ${parcel.county || 'N/A'}</p>
        <p><strong>Parcel ID:</strong> ${parcel.parcel_id || 'N/A'}</p>
        <p><strong>Owner:</strong> ${parcel.owner || 'N/A'}</p>
        <p><strong>Acreage:</strong> ${parcel.acres ? parcel.acres.toFixed(2) : 'N/A'}</p>
        <p><strong>Coordinates:</strong> ${data.centroid ? data.centroid[1].toFixed(6) + ', ' + data.centroid[0].toFixed(6) : 'N/A'}</p>
      </div>
    </div>
  `;
}

// Helper: Executive summary
function renderExecutiveSummary(data) {
  const terrain = data.terrain || {};
  const flood = data.flood || {};
  const soils = data.soils || {};

  let septicAssessment = 'Unknown';
  let homeSiteAssessment = 'Unknown';
  let farmingAssessment = 'Unknown';

  // Simple heuristics based on available data
  if (terrain.slope !== undefined) {
    if (terrain.slope > 15) septicAssessment = '⚠ High slope — septic may be challenging';
    else septicAssessment = '✓ Slope acceptable for septic';
  }

  if (flood.in_floodplain) homeSiteAssessment = '⚠ In FEMA floodplain — building risk';
  else if (terrain.slope !== undefined && terrain.slope <= 20) homeSiteAssessment = '✓ Suitable slope for building';
  else homeSiteAssessment = '⚠ Check slope and flood status';

  if (soils.prime_farmland_overall) farmingAssessment = '✓ Prime farmland';
  else farmingAssessment = '⚠ Not designated prime farmland';

  return `
    <div class="section summary-section">
      <h2>Executive Summary</h2>
      <div class="summary-items">
        <div class="summary-item">
          <strong>Septic Suitability:</strong> ${septicAssessment}
        </div>
        <div class="summary-item">
          <strong>Home Site Potential:</strong> ${homeSiteAssessment}
        </div>
        <div class="summary-item">
          <strong>Hobby Farming:</strong> ${farmingAssessment}
        </div>
      </div>
    </div>
  `;
}

// Helper: Septic suitability section
function renderSepticSection(data) {
  const terrain = data.terrain || {};
  const soils = data.soils || {};
  const flood = data.flood || {};
  const parcel = data.parcel || {};

  return `
    <div class="section septic-section">
      <h2>Septic System Suitability</h2>
      <p>Septic systems require adequate soil drainage, stable slopes, and proper distance from water sources.</p>
      
      <h3>Slope Analysis</h3>
      <p><strong>Average Slope:</strong> ${terrain.slope !== undefined ? terrain.slope.toFixed(1) + '%' : 'N/A'}</p>
      <p><strong>Slope Rating:</strong> ${getSlopeRating(terrain.slope)}</p>
      ${terrain.slope !== undefined ? `<p class="note">Slopes above 15% are challenging for septic systems.</p>` : ''}
      
      <h3>Soil Drainage</h3>
      <p>${getSoilDrainageText(soils)}</p>
      
      <h3>Hydric Soils (Wetlands Indicator)</h3>
      <p>${soils.hydric_soils ? '✓ No hydric soils detected' : '⚠ Hydric soils may be present — verify with surveyor'}</p>
      
      <h3>Flood Risk</h3>
      <p>${flood.in_floodplain ? '⚠ Property is in FEMA 100-year floodplain — not suitable for septic' : '✓ Not in FEMA floodplain'}</p>
      
      <h3>Proximity to Water</h3>
      <p>Septic systems must maintain proper setback from surface water. Check site plan with surveyor.</p>
    </div>
  `;
}

// Helper: Home site section
function renderHomeSiteSection(data) {
  const terrain = data.terrain || {};
  const flood = data.flood || {};
  const access = data.access || {};
  const parcel = data.parcel || {};

  return `
    <div class="section homesite-section">
      <h2>Home Site Potential</h2>
      <p>Building sites require stable slopes, flood safety, road access, and utilities.</p>
      
      <h3>Slope Analysis</h3>
      <p><strong>Average Slope:</strong> ${terrain.slope !== undefined ? terrain.slope.toFixed(1) + '%' : 'N/A'}</p>
      <p><strong>Buildability Rating:</strong> ${getBuildabilityRating(terrain.slope)}</p>
      ${terrain.slope !== undefined ? `<p class="note">Slopes above 20% significantly limit building feasibility.</p>` : ''}
      
      <h3>Flood Risk</h3>
      <p><strong>FEMA Floodplain Status:</strong> ${flood.in_floodplain ? '⚠ In 100-year floodplain' : '✓ Not in 100-year floodplain'}</p>
      ${flood.fema_panel ? `<p><strong>Panel:</strong> ${flood.fema_panel}</p>` : ''}
      
      <h3>Wetlands</h3>
      <p>${data.wetlands && data.wetlands.available ? 
        (data.wetlands.in_wetland ? '⚠ Property contains wetlands' : '✓ No wetlands detected') 
        : 'Data not available'}</p>
      
      <h3>Road Access</h3>
      <p>${access.available ? 
        (`✓ Road frontage: ${access.frontage_m ? access.frontage_m.toFixed(0) + 'm' : 'Unknown'}`) 
        : '⚠ Road access information unavailable'}</p>
      
      <h3>Elevation</h3>
      <p>${data.elevation !== undefined ? `${data.elevation.toFixed(0)} feet above sea level` : 'N/A'}</p>
    </div>
  `;
}

// Helper: Farming section
function renderFarmingSection(data) {
  const soils = data.soils || {};
  const parcel = data.parcel || {};

  return `
    <div class="section farming-section">
      <h2>Hobby Farming Potential</h2>
      <p>Agricultural viability depends on soil quality, land-use zoning, and environmental factors.</p>
      
      <h3>Land Use Classification</h3>
      <p><strong>County Assessment:</strong> ${parcel.land_use || 'N/A'}</p>
      
      <h3>Prime Farmland Status</h3>
      <p>${soils.prime_farmland_overall ? '✓ Designated prime farmland' : '⚠ Not designated as prime farmland'}</p>
      
      <h3>Soil Quality</h3>
      <p>${getSoilQualityText(soils)}</p>
      
      <h3>Acreage</h3>
      <p>${parcel.acres ? parcel.acres.toFixed(2) + ' acres' : 'N/A'}</p>
      
      <h3>Constraints</h3>
      <ul>
        ${data.wetlands && data.wetlands.in_wetland ? '<li>⚠ Contains wetlands — limits cultivation</li>' : ''}
        ${data.flood && data.flood.in_floodplain ? '<li>⚠ In floodplain — seasonal water impact</li>' : ''}
        ${soils.hydric_soils ? '<li>⚠ Hydric soils present — poor drainage for crops</li>' : ''}
      </ul>
    </div>
  `;
}

// Helper: Environmental section
function renderEnvironmentalSection(data) {
  const flood = data.flood || {};
  const wetlands = data.wetlands || {};

  return `
    <div class="section environmental-section">
      <h2>Environmental & Water Features</h2>
      
      <h3>FEMA Flood Zone</h3>
      <p><strong>Status:</strong> ${flood.in_floodplain ? 'In 100-year floodplain' : 'Not in 100-year floodplain'}</p>
      ${flood.fema_zone ? `<p><strong>Zone:</strong> ${flood.fema_zone}</p>` : ''}
      ${flood.static_bfe ? `<p><strong>Base Flood Elevation:</strong> ${flood.static_bfe}</p>` : ''}
      
      <h3>Wetlands (USFWS NWI)</h3>
      <p>${wetlands.available ? 
        (wetlands.in_wetland ? `✓ Wetlands present: ${wetlands.wetland_type || 'Type unknown'}` : '✓ No wetlands detected') 
        : '⚠ Wetlands data unavailable'}</p>
      
      <h3>Water Features</h3>
      <p>Water sources (springs, ponds, creeks) may not be captured in all datasets. Conduct site survey for verification.</p>
      ${data.access && data.access.nearest_water_distance ? 
        `<p><strong>Distance to nearest water:</strong> ${data.access.nearest_water_distance.toFixed(0)}m</p>` : ''}
    </div>
  `;
}

// Helper: Sources and disclaimers
function renderSourcesDisclaimer(data) {
  return `
    <div class="section sources-section">
      <h2>Data Sources & Disclaimers</h2>
      
      <h3>Data Sources</h3>
      <ul>
        <li>Tennessee Comptroller (parcel geometry, ownership)</li>
        <li>County GIS Services (land use, appraised value)</li>
        <li>FEMA (flood zones)</li>
        <li>USFWS (wetlands inventory)</li>
        <li>USGS 3DEP (elevation, slope)</li>
        <li>NRCS SSURGO (soil types, drainage)</li>
        <li>OpenStreetMap & TIGER (road access)</li>
      </ul>
      
      <h3>Important Disclaimers</h3>
      <p><strong>This tool is for informational reference only.</strong> It is not professional engineering, survey, environmental, or legal advice. Before making land-use decisions:</p>
      <ul>
        <li>Conduct a professional site survey</li>
        <li>Hire a licensed engineer for septic design</li>
        <li>Consult a surveyor for property lines and easements</li>
        <li>Check local zoning and building codes</li>
        <li>Verify wetlands and water features on-site</li>
        <li>Review environmental assessments if required</li>
      </ul>
      
      <p><strong>Data Limitations:</strong></p>
      <ul>
        <li>Soil data is an inventory; actual soils may vary within the parcel</li>
        <li>Wetlands are based on aerial imagery; on-site conditions may differ</li>
        <li>Flood zones reflect FEMA mapping; local flooding may occur outside mapped zones</li>
        <li>Road access reflects centerline proximity; no guarantee of legal right-of-way</li>
      </ul>
    </div>
  `;
}

// Helper functions for ratings and text
function getSlopeRating(slope) {
  if (slope === undefined) return 'Unknown';
  if (slope <= 5) return '✓ Excellent (0–5%)';
  if (slope <= 10) return '✓ Good (5–10%)';
  if (slope <= 15) return '⚠ Acceptable (10–15%)';
  if (slope <= 20) return '⚠ Challenging (15–20%)';
  return '✗ Poor (>20%)';
}

function getBuildabilityRating(slope) {
  if (slope === undefined) return 'Unknown';
  if (slope <= 10) return '✓ Excellent';
  if (slope <= 15) return '✓ Good';
  if (slope <= 20) return '⚠ Challenging';
  return '✗ Poor';
}

function getSoilDrainageText(soils) {
  if (!soils.available) return 'Soil data unavailable.';
  const text = soils.available ? `Soil drainage: ${soils.description || 'See SSURGO data below'}` : 'Soil data unavailable';
  return text;
}

function getSoilQualityText(soils) {
  if (!soils.available) return 'Soil data unavailable.';
  const parts = [];
  if (soils.prime_farmland_overall) parts.push('Prime farmland');
  if (soils.farmland_if_irrigated) parts.push('Farmland if irrigated');
  if (soils.farmland_of_statewide_importance) parts.push('Statewide importance');
  return parts.length ? parts.join('; ') : 'Not classified as prime or important farmland';
}

// Run on page load
document.addEventListener('DOMContentLoaded', renderReport);
```

- [ ] **Step 2: Test report rendering**

Start the server and visit: `http://127.0.0.1:8823/report?lon=-86.586&lat=35.845`

The page should load and display the report with all sections populated.

- [ ] **Step 3: Commit**

```bash
git add tnland/static/report.js
git commit -m "feat: add report rendering with all sections (septic, home, farming, environmental)"
```

---

## Task 4: Create Print-Optimized Stylesheet

**Files:**
- Create: `tnland/static/report.css`

**Interfaces:**
- Consumes: HTML structure from report.js
- Produces: CSS that styles report for screen and print, with good readability and page breaks

**Description:** Create CSS that makes the report readable on screen and when printed to PDF, preserving the dark theme and ensuring proper spacing and typography.

- [ ] **Step 1: Create report.css with base and print styles**

Create `tnland/static/report.css`:

```css
/* Base styles */
:root {
  --bg: #12140f;
  --panel: #1b1e17;
  --line: #333a2c;
  --text: #e8ebe2;
  --dim: #98a08c;
  --accent: #8fbf52;
  --bad: #e2694f;
  --warn: #e0b040;
  --ok: #8fbf52;
}

* {
  box-sizing: border-box;
}

html, body {
  margin: 0;
  padding: 0;
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
}

#report-container {
  max-width: 900px;
  margin: 0 auto;
  padding: 20px;
}

/* Headings */
h1 {
  font-size: 32px;
  margin: 0 0 20px 0;
  letter-spacing: -0.5px;
}

h2 {
  font-size: 20px;
  margin: 30px 0 12px 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 1px;
  border-bottom: 2px solid var(--line);
  padding-bottom: 8px;
}

h3 {
  font-size: 14px;
  margin: 16px 0 8px 0;
  color: var(--text);
  font-weight: 600;
}

p {
  margin: 8px 0;
  line-height: 1.6;
}

ul, ol {
  margin: 8px 0;
  padding-left: 24px;
}

li {
  margin: 4px 0;
}

/* Sections */
.section {
  margin-bottom: 40px;
  padding: 20px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}

.header-section {
  background: var(--panel);
  border-left: 4px solid var(--accent);
}

.header-info {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-top: 16px;
}

.header-info p {
  margin: 4px 0;
  font-size: 13px;
}

.summary-section {
  background: var(--panel);
  border-left: 4px solid var(--ok);
}

.summary-items {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.summary-item {
  padding: 10px 12px;
  background: rgba(139, 191, 82, 0.1);
  border-left: 3px solid var(--accent);
  font-size: 14px;
}

/* Notes and callouts */
.note {
  font-size: 12px;
  color: var(--dim);
  padding: 8px 0;
  border-left: 2px solid var(--line);
  padding-left: 10px;
  margin: 8px 0;
  font-style: italic;
}

/* Links */
a {
  color: var(--accent);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

/* Print styles */
@media print {
  :root {
    --bg: white;
    --text: #000;
    --dim: #666;
    --panel: white;
  }

  * {
    background: transparent !important;
    color: #000 !important;
    box-shadow: none !important;
    page-break-inside: avoid;
  }

  html, body {
    background: white;
    color: #000;
  }

  #report-container {
    max-width: 100%;
    padding: 0.5in;
  }

  h1 {
    font-size: 28px;
    margin-bottom: 12px;
    page-break-after: avoid;
  }

  h2 {
    font-size: 18px;
    margin-top: 24px;
    margin-bottom: 12px;
    page-break-after: avoid;
    color: #000;
    border-color: #999;
  }

  h3 {
    font-size: 13px;
    margin-top: 12px;
    page-break-after: avoid;
    color: #000;
  }

  p, li {
    margin: 6px 0;
  }

  .section {
    page-break-inside: avoid;
    border: 1px solid #999;
    padding: 12px;
    margin-bottom: 24px;
    background: white;
  }

  .header-section {
    border-left: none;
    margin-bottom: 20px;
  }

  .header-info {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .header-info p {
    margin: 2px 0;
  }

  .summary-item {
    background: #f5f5f5;
    border-left: 3px solid #333;
    padding: 8px;
    margin: 6px 0;
  }

  .note {
    border-color: #999;
    font-size: 11px;
  }

  /* Page breaks between major sections */
  .septic-section {
    page-break-before: auto;
  }

  .homesite-section {
    page-break-before: auto;
  }

  .farming-section {
    page-break-before: auto;
  }

  .environmental-section {
    page-break-before: auto;
  }

  .sources-section {
    page-break-before: auto;
  }

  /* Prevent orphaned headings */
  h2 + * {
    page-break-before: avoid;
  }

  /* Hide UI elements not meant for print */
  .no-print {
    display: none;
  }
}

/* Screen-only: Print button */
.print-button {
  position: fixed;
  top: 20px;
  right: 20px;
  padding: 10px 16px;
  background: var(--accent);
  color: #0d1108;
  border: none;
  border-radius: 6px;
  font-weight: 600;
  cursor: pointer;
  z-index: 1000;
}

.print-button:hover {
  background: var(--accent2);
}

@media print {
  .print-button {
    display: none;
  }
}
```

- [ ] **Step 2: Link stylesheet in report.html**

Open `tnland/static/report.html` and verify the `<link rel="stylesheet" href="report.css">` line is present (it should be from Task 2).

- [ ] **Step 3: Test print styles**

Visit `http://127.0.0.1:8823/report?lon=-86.586&lat=35.845` in the browser, then:
- Press Ctrl+P (or Cmd+P) to open print dialog
- Preview the PDF
- Verify sections are readable, headings stand out, and page breaks look good
- Cancel (don't actually print)

- [ ] **Step 4: Commit**

```bash
git add tnland/static/report.css
git commit -m "feat: add print-optimized stylesheet for reports"
```

---

## Task 5: Add "Generate Report" Button to Parcel Panel

**Files:**
- Modify: `tnland/static/index.html`

**Interfaces:**
- Consumes: Existing parcel data (loaded in `window.lastParcel` or from API)
- Produces: Button that opens report in new tab with coordinates

**Description:** Add a "Generate Report" button to the parcel panel in the main UI, visible only when a parcel is loaded. Clicking it opens the report in a new tab.

- [ ] **Step 1: Read the parcel panel in index.html to understand structure**

Check [tnland/static/index.html:83-150](tnland/static/index.html#L83-L150) to see the parcel panel structure.

- [ ] **Step 2: Find where to add the button**

Look for the section after the search inputs in the parcel pane (around line 110-120). We'll add the button after the search sections, before the results area.

- [ ] **Step 3: Add JavaScript function to open report**

Add this function to the end of the `<script>` section in index.html (before the closing `</script>` tag):

```javascript
let lastParcelCoords = null;

function openReport() {
  if (!lastParcelCoords) {
    alert('Please click a parcel on the map first.');
    return;
  }
  const { lon, lat } = lastParcelCoords;
  window.open(`/report?lon=${lon}&lat=${lat}`, '_blank');
}
```

- [ ] **Step 4: Add the button to the HTML**

Find the closing `</div>` of the parcel search inputs (around line 110-120), and add this button group after it:

```html
      <div style="margin-top: 20px;">
        <button class="go" id="generate-report-btn" onclick="openReport()" style="display:none;">Generate Report</button>
      </div>
```

- [ ] **Step 5: Modify the map click handler to capture coordinates**

Find the existing map click handler or parcel display code in the `<script>` section of index.html. When a parcel is loaded, add:

```javascript
lastParcelCoords = { lon: parcelLon, lat: parcelLat };
document.getElementById('generate-report-btn').style.display = 'block';
```

(This depends on the existing code structure. Check the existing parcel loading flow to find where to insert this.)

- [ ] **Step 6: Test the button**

Start the server, load the web app, click a parcel on the map, and verify:
- The "Generate Report" button appears
- Clicking it opens a new tab with the report
- The report loads correctly

- [ ] **Step 7: Commit**

```bash
git add tnland/static/index.html
git commit -m "feat: add 'Generate Report' button to parcel panel"
```

---

## Task 6: Test Report Completeness and Printability

**Files:**
- Test: Manual testing (no new files)

**Interfaces:**
- Consumes: Deployed report feature (Tasks 1–5)
- Produces: Verified report with all sections displaying correctly

**Description:** Test the report with various parcels to ensure all sections display correctly and print nicely.

- [ ] **Step 1: Test with a basic parcel**

1. Start the server: `python -m tnland`
2. Visit `http://127.0.0.1:8823/`
3. Click a parcel on the map
4. Click "Generate Report" button
5. Verify the report loads with:
   - Header with parcel info
   - Executive summary
   - Septic section with slope and soil data
   - Home site section with flood/slope info
   - Farming section with soil and land-use data
   - Environmental section with wetlands/flood data
   - Sources and disclaimers

- [ ] **Step 2: Test print-to-PDF**

1. From the report page, press Ctrl+P (Cmd+P on Mac)
2. Set printer to "Save as PDF" or similar
3. Preview the PDF and verify:
   - Text is readable (dark on light background in print mode)
   - Headings are clear and prominent
   - Page breaks occur between sections (no orphaned headings)
   - Layout is clean and professional

- [ ] **Step 3: Test with different parcels**

Test with at least 3 different parcels to ensure:
- Data varies appropriately
- All possible fields display (slope, prime farmland, flood zones, etc.)
- "N/A" appears gracefully when data is unavailable
- No JavaScript errors in browser console

- [ ] **Step 4: Test with missing data**

Try a parcel in an area where some data sources might fail (e.g., no wetlands data available, no comps data). Verify:
- Report still loads
- Unavailable data is labeled as "N/A" or "Data unavailable"
- No broken sections or layout issues

- [ ] **Step 5: Run the test suite**

```bash
python selftest.py
```

Ensure all 196 offline checks pass. (No new tests added yet; just verify nothing broke.)

- [ ] **Step 6: Commit notes**

No code changes in this task, but document that testing is complete:

```bash
git log --oneline | head -5  # Verify your commits
```

---

## Task 7: Refinements and Edge Cases

**Files:**
- Modify: `tnland/static/report.js` (if needed)
- Modify: `tnland/static/report.css` (if needed)

**Interfaces:**
- Consumes: Test findings from Task 6
- Produces: Polished report with improved readability and edge-case handling

**Description:** Based on testing, refine rendering logic, improve data presentation, and handle edge cases gracefully.

- [ ] **Step 1: Review test findings**

List any issues found in Task 6:
- Missing fields that should display
- Layout issues on print
- Unclear labeling
- Missing labels for "N/A" or unavailable data

- [ ] **Step 2: Fix rendering issues (if any)**

Common fixes:
- Add fallback text for missing fields (e.g., "Data not available")
- Adjust CSS for better readability (e.g., wider table columns if tables are added)
- Clarify section headings or add more explanation text
- Improve color contrast in print mode

- [ ] **Step 3: Enhance data presentation**

Consider adding:
- Bullet points for soil types if there are multiple
- A simple visual indicator (✓ / ⚠ / ✗) for suitability ratings
- Clearer explanations of technical terms (e.g., "hydric soils" → "Hydric soils (waterlogged)")

- [ ] **Step 4: Commit refinements**

```bash
git add tnland/static/report.js tnland/static/report.css
git commit -m "refactor: improve report readability and handle edge cases"
```

---

## Task 8: Final Testing Against Requirements

**Files:**
- Test: Verification against spec success criteria

**Interfaces:**
- Consumes: Design spec and implementation
- Produces: Sign-off that all success criteria are met

**Description:** Verify the implementation against the original design spec success criteria.

- [ ] **Step 1: Verify success criterion 1**

**Criterion:** Report opens in browser, displays all parcel data relevant to septic/home/farming

Open a report and verify all sections render with appropriate data. ✓

- [ ] **Step 2: Verify success criterion 2**

**Criterion:** Sections are clearly labeled and organized

Review report structure:
- Header with parcel ID ✓
- Executive summary with quick assessment ✓
- 5+ labeled sections (septic, home, farming, environmental, sources) ✓

- [ ] **Step 3: Verify success criterion 3**

**Criterion:** Print-to-PDF produces readable, professional-looking document

Print a report to PDF and verify typography, layout, and contrast. ✓

- [ ] **Step 4: Verify success criterion 4**

**Criterion:** No new external dependencies

Check `requirements.txt` — should be unchanged. Verify all code uses existing libraries (FastAPI, Leaflet, standard HTML/CSS/JS). ✓

- [ ] **Step 5: Verify success criterion 5**

**Criterion:** Report loads within 2–3 seconds

Check browser dev tools (Network tab) — report request should complete in < 2 seconds. ✓

- [ ] **Step 6: Verify success criterion 6**

**Criterion:** Soil and slope information is readable (addresses original pain point)

Compare report table/layout against the original screenshot showing cramped soil info. Report layout should be much clearer. ✓

- [ ] **Step 7: Verify success criterion 7**

**Criterion:** User can print and file the report for reference

Test printing to PDF and saving the file. ✓

- [ ] **Step 8: Final commit**

```bash
git log --oneline | head -10  # View implementation commits
git status  # Verify no uncommitted changes
```

All tasks complete. Report feature is ready for production.

---

## Rollback Checklist

If anything goes wrong, here's what was added and can be reverted:

1. `/api/report` endpoint in `server.py`
2. `/report` route in `server.py`
3. New files: `report.html`, `report.js`, `report.css`
4. Button in `index.html`

Revert with: `git reset --hard HEAD~<N>` where N is the number of commits to undo.

