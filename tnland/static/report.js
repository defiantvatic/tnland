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
