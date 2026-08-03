"""Local web server. Nothing here is exposed beyond your machine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, cache, comps as comps_mod, config, geo, lists
from . import build_info
from . import progress as progress_mod
from .sources import parcels

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="TN Land Tool", docs_url="/api/docs")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return {
        "version": build_info(),
        "basemaps": config.BASEMAPS,
        "land_use_codes": config.LAND_USE_CODES,
        "vacant_codes": sorted(config.VACANT_LU_CODES),
        "raw_land_codes": sorted(config.RAW_LAND_LU_CODES),
        "comps_counties": comps_mod.available_counties(),
        "counties_without_service": config.COUNTIES_NO_SERVICE,
        "county_services": {
            k: {"label": v["label"], "status": v.get("status"),
                "note": v.get("note")}
            for k, v in config.COUNTY_SERVICES.items()
        },
    }


@app.get("/api/progress")
def get_progress(job: str) -> dict[str, Any]:
    """Live status of an in-flight report, for the polling frontend."""
    return progress_mod.snapshot(job)


@app.get("/api/parcel")
def parcel(lon: float, lat: float,
           layers: str = "flood,wetlands,slope,roads,soils",
           job: str | None = None):
    include = {x.strip() for x in layers.split(",") if x.strip()}
    return analysis.parcel_report(lon, lat, include=include, job=job)


@app.get("/api/report")
def report(lon: float, lat: float,
           layers: str = "flood,wetlands,slope,roads,soils,drivetimes,soilanalysis",
           job: str | None = None):
    """Generate data for a detailed printable parcel report."""
    include = {x.strip() for x in layers.split(",") if x.strip()}
    return analysis.parcel_report(lon, lat, include=include, job=job)


@app.get("/report")
def report_page() -> FileResponse:
    """Serve the report template (populated by frontend JavaScript)."""
    return FileResponse(STATIC / "report.html")


@app.get("/api/search/address")
def search_address(q: str,
                   layers: str = "flood,wetlands,slope,roads,soils",
                   job: str | None = None,
                   min_acres: float | None = None):
    if len(q.strip()) < 5:
        raise HTTPException(400, "Address search needs at least 5 characters")
    include = {x.strip() for x in layers.split(",") if x.strip()}
    return analysis.address_report(q, include=include, job=job,
                                   min_acres=min_acres)


@app.get("/api/search/owner")
def search_owner(q: str, county: str | None = None, limit: int = 200):
    if len(q.strip()) < 3:
        raise HTTPException(400, "Owner search needs at least 3 characters")
    found = parcels.search_owner(q, county=county, limit=limit)
    parcels.apply_bulk_landuse(found)
    return {"count": len(found), "results": [lists._row(r) for r in found]}


@app.get("/api/search/parcel")
def search_parcel(q: str, limit: int = 50):
    found = parcels.search_parcel_id(q, limit=limit)
    return {"count": len(found), "results": [lists._row(r) for r in found]}


@app.get("/api/counties")
def get_counties():
    return {"counties": parcels.counties()}


class ScreenRequest(BaseModel):
    geometry: dict[str, Any]
    min_acres: float | None = None
    max_acres: float | None = None
    vacant_only: bool = False
    raw_land_only: bool = False
    exclude_structures: bool = False
    owner_out_of_county: bool = False
    min_appraisal: float | None = None
    max_appraisal: float | None = None
    land_use_codes: list[str] | None = None
    max_records: int = 5000


@app.post("/api/screen")
def screen(req: ScreenRequest):
    try:
        geom = geo.geojson_to_shapely(req.geometry)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Bad geometry: {exc}") from exc
    payload = req.model_dump()
    payload.pop("geometry")
    return lists.screen(geom, **payload)


class ExportRequest(BaseModel):
    rows: list[dict[str, Any]]
    dedupe: bool = True
    require_mailing: bool = False


@app.post("/api/export.csv")
def export_csv(req: ExportRequest) -> PlainTextResponse:
    body = lists.to_csv(req.rows, dedupe=req.dedupe,
                        require_mailing=req.require_mailing)
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="tn-parcels.csv"'},
    )


class CompsRequest(BaseModel):
    geometry: dict[str, Any]
    county: str
    radius_miles: float = 5.0
    acre_tolerance: float = 0.6
    years_back: int = 5
    subject_acres: float | None = None


@app.post("/api/comps")
def get_comps(req: CompsRequest):
    geom = geo.geojson_to_shapely(req.geometry)
    return comps_mod.comps(
        geom,
        req.county,
        radius_miles=req.radius_miles,
        acre_tolerance=req.acre_tolerance,
        years_back=req.years_back,
        subject_acres=req.subject_acres,
    )


@app.get("/api/cache")
def cache_stats():
    return cache.stats()


@app.delete("/api/cache")
def cache_clear():
    return {"cleared": cache.clear()}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
