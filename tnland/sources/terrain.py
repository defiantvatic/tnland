"""Elevation and slope from USGS 3DEP.

Slope is computed locally from a downloaded DEM rather than asking the server
for a slope image, because a server-side slope render has edge artifacts at
the request boundary and gives you a picture instead of numbers. We pull a
bare-earth DEM in a metric projection, buffer it by a pixel so the gradient
at the parcel edge is real, and reduce it to a distribution.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np

from .. import cache, config, geo
from ..http import SourceError, client, get_json, pick_working


def point_elevation(lon: float, lat: float) -> dict[str, Any]:
    try:
        data = get_json(
            config.EPQS,
            {"x": lon, "y": lat, "units": "Feet", "wkid": 4326},
            ttl_days=365,
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc)}
    try:
        # EPQS has been observed returning `value` as both a number and a
        # quoted string, so never trust the JSON type here.
        value = float(data.get("value"))
    except (TypeError, ValueError):
        return {"available": False, "error": f"Unexpected EPQS response: {data}"}
    if value < -1000:
        return {"available": False, "error": "Point outside 3DEP coverage"}
    return {
        "available": True,
        "elevation_ft": round(value, 1),
        "source_resolution_m": data.get("resolution"),
    }


def _fetch_dem(parcel_geom) -> tuple[np.ndarray, float] | None:
    """Download a bare-earth DEM covering the parcel. Returns (array, pixel_m)."""
    projected, epsg = geo.to_utm(parcel_geom)
    pad = config.DEM_TARGET_GSD_M * 2
    minx, miny, maxx, maxy = projected.bounds
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad

    width_m = max(maxx - minx, 1.0)
    height_m = max(maxy - miny, 1.0)
    gsd = config.DEM_TARGET_GSD_M
    cols = int(math.ceil(width_m / gsd))
    rows = int(math.ceil(height_m / gsd))

    # Very large tracts would otherwise request an enormous raster.
    scale = max(cols / config.DEM_MAX_PIXELS, rows / config.DEM_MAX_PIXELS, 1.0)
    if scale > 1.0:
        cols = int(cols / scale)
        rows = int(rows / scale)
        gsd = gsd * scale
    cols = max(cols, 4)
    rows = max(rows, 4)

    epsg_code = int(epsg.split(":")[1])
    service = pick_working(config.DEM_IMAGESERVER)
    params = {
        "bbox": f"{minx},{miny},{maxx},{maxy}",
        "bboxSR": epsg_code,
        "imageSR": epsg_code,
        "size": f"{cols},{rows}",
        "format": "tiff",
        "pixelType": "F32",
        "noData": -9999,
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    key = cache.make_key("dem", params, service)
    cached_path = cache.get(key, ttl_days=365)
    raw: bytes | None = None
    if cached_path:
        try:
            from pathlib import Path

            raw = Path(cached_path).read_bytes()
        except OSError:
            raw = None
    if raw is None:
        resp = client().get(service.rstrip("/") + "/exportImage", params=params)
        resp.raise_for_status()
        raw = resp.content
        if raw[:4] not in (b"II*\x00", b"MM\x00*"):
            raise SourceError(
                "3DEP did not return a TIFF. First bytes: " + repr(raw[:40])
            )
        from pathlib import Path

        cdir = Path.home() / ".tnland" / "dem"
        cdir.mkdir(parents=True, exist_ok=True)
        path = cdir / f"{key}.tif"
        path.write_bytes(raw)
        cache.put(key, str(path))

    array = _read_tiff(raw)
    if array is None:
        return None
    return array.astype("float32"), gsd


def _read_tiff(raw: bytes) -> np.ndarray | None:
    try:
        import tifffile

        return tifffile.imread(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        pass
    try:
        import rasterio

        with rasterio.io.MemoryFile(raw) as mem, mem.open() as src:
            return src.read(1)
    except Exception:  # noqa: BLE001
        return None


def slope(parcel_geom) -> dict[str, Any]:
    """Slope statistics for the parcel, in percent and degrees."""
    out: dict[str, Any] = {"available": False}
    try:
        fetched = _fetch_dem(parcel_geom)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if fetched is None:
        out["error"] = (
            "Could not decode the 3DEP DEM. Install 'tifffile' (or 'rasterio') "
            "to enable slope analysis."
        )
        return out

    dem, pixel_m = fetched
    if dem.ndim == 3:
        dem = dem[..., 0] if dem.shape[-1] <= 4 else dem[0]
    dem = np.where(dem <= -9000, np.nan, dem)
    if dem.shape[0] < 3 or dem.shape[1] < 3:
        out["error"] = "Parcel too small for a meaningful DEM sample"
        return out

    # np.gradient returns (d/drow, d/dcol); rows run north->south in the
    # exported image, which does not matter because we only use magnitude.
    dzdy, dzdx = np.gradient(dem, pixel_m, pixel_m)
    grade = np.sqrt(dzdx ** 2 + dzdy ** 2)
    valid = grade[np.isfinite(grade)]
    if valid.size == 0:
        out["error"] = "DEM returned no valid elevation values here"
        return out

    pct = valid * 100.0
    deg = np.degrees(np.arctan(valid))
    elev = dem[np.isfinite(dem)]

    out.update({
        "available": True,
        "pixel_size_m": round(pixel_m, 2),
        "mean_slope_pct": round(float(np.mean(pct)), 1),
        "median_slope_pct": round(float(np.median(pct)), 1),
        "max_slope_pct": round(float(np.percentile(pct, 99)), 1),
        "mean_slope_deg": round(float(np.mean(deg)), 1),
        "elevation_min_ft": round(float(np.min(elev)) * 3.28084, 1),
        "elevation_max_ft": round(float(np.max(elev)) * 3.28084, 1),
        "relief_ft": round(float(np.max(elev) - np.min(elev)) * 3.28084, 1),
        "buildable_pct": round(float(np.mean(pct <= 15) * 100), 1),
        "steep_pct": round(float(np.mean(pct > 30) * 100), 1),
        "distribution": {
            "0-5%": round(float(np.mean(pct <= 5) * 100), 1),
            "5-10%": round(float(np.mean((pct > 5) & (pct <= 10)) * 100), 1),
            "10-15%": round(float(np.mean((pct > 10) & (pct <= 15)) * 100), 1),
            "15-25%": round(float(np.mean((pct > 15) & (pct <= 25)) * 100), 1),
            "25%+": round(float(np.mean(pct > 25) * 100), 1),
        },
    })
    out["terrain"] = _describe(out["mean_slope_pct"], out["buildable_pct"])
    out["note"] = (
        f"Computed from a {out['pixel_size_m']} m bare-earth DEM. Percentages "
        "are of the parcel's bounding box, which slightly overstates area for "
        "irregular parcels."
    )
    return out


def _describe(mean_pct: float, buildable_pct: float) -> str:
    if mean_pct < 4:
        return "Essentially flat"
    if mean_pct < 10:
        return "Gently rolling"
    if mean_pct < 18:
        return "Rolling, mostly workable"
    if buildable_pct > 35:
        return "Hilly with usable benches"
    if mean_pct < 30:
        return "Hilly, limited flat area"
    return "Steep, expect significant site work"
