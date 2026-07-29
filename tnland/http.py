"""Shared HTTP client, ArcGIS query helpers, and endpoint auto-discovery.

Nothing in this project hardcodes a single URL and hopes. Sources declare a
list of candidates; `pick_working` probes them and remembers the winner. When
a government server changes a path (which they do), the tool degrades to a
readable error on one panel instead of failing everywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Iterable, Sequence

import httpx

from . import cache, config

log = logging.getLogger("tnland")


class SourceError(RuntimeError):
    """A data source failed in a way worth showing the user verbatim."""


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=config.HTTP_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": config.USER_AGENT,
                "Accept": "application/json, text/plain, */*",
            },
        )
    return _client


def get_json(url: str, params: dict[str, Any] | None = None,
             use_cache: bool = True, ttl_days: float | None = None) -> Any:
    key = cache.make_key("GET", url, params)
    if use_cache:
        hit = cache.get(key, ttl_days)
        if hit is not None:
            return hit
    resp = client().get(url, params=params)
    resp.raise_for_status()
    data = _parse_json(resp, url)
    if use_cache and _cacheable(data):
        cache.put(key, data)
    return data


def post_form(url: str, data: dict[str, Any],
              use_cache: bool = True, ttl_days: float | None = None) -> Any:
    key = cache.make_key("POST", url, data)
    if use_cache:
        hit = cache.get(key, ttl_days)
        if hit is not None:
            return hit
    resp = client().post(url, data=data)
    resp.raise_for_status()
    parsed = _parse_json(resp, url)
    if use_cache and _cacheable(parsed):
        cache.put(key, parsed)
    return parsed


def _cacheable(data: Any) -> bool:
    """Never persist a failure.

    ArcGIS reports errors with HTTP 200 and an {"error": {...}} body. Caching
    one would make a single transient hiccup look like a permanently broken
    parcel for the next fourteen days -- or a year, for the sources cached
    long-term -- with no way out but clearing the cache.
    """
    if isinstance(data, dict) and "error" in data:
        return False
    return True


def _parse_json(resp: httpx.Response, url: str) -> Any:
    try:
        return resp.json()
    except json.JSONDecodeError:
        snippet = resp.text[:200].replace("\n", " ")
        raise SourceError(
            f"{url} returned non-JSON (HTTP {resp.status_code}): {snippet}"
        ) from None


# ---------------------------------------------------------------------------
# Endpoint discovery
# ---------------------------------------------------------------------------

_resolved: dict[str, str] = {}


def pick_working(candidates: Sequence[str], probe: str = "?f=json") -> str:
    """Return the first candidate URL whose metadata responds. Cached."""
    if not candidates:
        raise SourceError("No candidate URLs configured")
    cache_key = candidates[0]
    if cache_key in _resolved:
        return _resolved[cache_key]

    stored = cache.get(cache.make_key("resolved", list(candidates)), ttl_days=7)
    if stored and stored in candidates:
        _resolved[cache_key] = stored
        return stored

    errors: list[str] = []
    for url in candidates:
        ok, detail = _reachable(url)
        if ok:
            _resolved[cache_key] = url
            cache.put(cache.make_key("resolved", list(candidates)), url)
            return url
        errors.append(f"{url} -> {detail}")
    raise SourceError("All candidates failed:\n  " + "\n  ".join(errors))


def _reachable(url: str) -> tuple[bool, str]:
    """Is this endpoint usable? Metadata first, then an actual query.

    FEMA's hazards.fema.gov serves /query to ordinary clients but refuses
    metadata reads. Judging it on ?f=json alone would reject the primary
    flood source and silently fall through to the mirror.
    """
    detail = ""
    try:
        resp = client().get(url, params={"f": "json"})
        if resp.status_code == 200:
            body = resp.json()
            if not (isinstance(body, dict) and "error" in body):
                return True, "ok"
            detail = body["error"].get("message", "error")
        else:
            detail = f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 - we want the message, any type
        detail = f"{type(exc).__name__}: {exc}"

    # Metadata is blocked or broken; see whether the layer answers a query.
    try:
        resp = client().post(url.rstrip("/") + "/query", data={
            "f": "json", "where": "1=1", "returnCountOnly": "true",
        })
        if resp.status_code == 200:
            body = resp.json()
            if isinstance(body, dict) and "error" not in body:
                return True, "ok (query only; metadata blocked)"
    except Exception:  # noqa: BLE001
        pass
    return False, detail


def probe(candidates: Sequence[str]) -> dict[str, Any]:
    """Doctor helper: report on every candidate without raising."""
    out = []
    for url in candidates:
        entry: dict[str, Any] = {"url": url}
        try:
            resp = client().get(url, params={"f": "json"})
            entry["http"] = resp.status_code
            try:
                body = resp.json()
                if isinstance(body, dict) and "error" in body:
                    entry["ok"] = False
                    entry["detail"] = body["error"].get("message", "error")
                else:
                    entry["ok"] = resp.status_code == 200
                    if isinstance(body, dict):
                        # These keys can be present but null, so coalesce
                        # before slicing rather than relying on a default.
                        label = (body.get("name")
                                 or body.get("mapName")
                                 or body.get("serviceDescription")
                                 or "")
                        entry["name"] = str(label)[:60]
            except json.JSONDecodeError:
                entry["ok"] = False
                entry["detail"] = "non-JSON response"
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["detail"] = f"{type(exc).__name__}: {exc}"

        if not entry.get("ok"):
            # Metadata may be blocked while /query works (FEMA does this).
            ok, detail = _reachable(url)
            if ok:
                entry["ok"] = True
                entry["detail"] = detail
        out.append(entry)
    return {"candidates": out, "working": next(
        (c["url"] for c in out if c.get("ok")), None)}


# ---------------------------------------------------------------------------
# ArcGIS query helpers
# ---------------------------------------------------------------------------

def arcgis_query(
    layer_url: str,
    *,
    geometry: dict[str, Any] | None = None,
    geometry_type: str = "esriGeometryPolygon",
    spatial_rel: str = "esriSpatialRelIntersects",
    where: str = "1=1",
    out_fields: Iterable[str] | str = "*",
    return_geometry: bool = True,
    out_sr: int = 4326,
    result_offset: int | None = None,
    result_record_count: int | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run an ArcGIS REST /query and return the raw response."""
    fields = out_fields if isinstance(out_fields, str) else ",".join(out_fields)
    params: dict[str, Any] = {
        "f": "json",
        "where": where,
        "outFields": fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": out_sr,
    }
    if geometry is not None:
        params["geometry"] = json.dumps(geometry)
        params["geometryType"] = geometry_type
        params["spatialRel"] = spatial_rel
        params["inSR"] = 4326
    if result_offset is not None:
        params["resultOffset"] = result_offset
    if result_record_count is not None:
        params["resultRecordCount"] = result_record_count

    # POST, not GET. A real parcel outline serialises to several kilobytes of
    # JSON, and a 150-item IN() clause is not much smaller. ArcGIS Server
    # behind IIS caps query strings at 2048 bytes and answers 404.15; behind
    # nginx it answers 414. Both look like "the layer is broken" rather than
    # "your URL was too long", so every query goes as a form POST.
    url = layer_url.rstrip("/") + "/query"
    if use_cache:
        key = cache.make_key("arcgis", url, params)
        hit = cache.get(key)
        if hit is not None:
            data = hit
        else:
            data = _post_query(url, params)
            if _cacheable(data):
                cache.put(key, data)
    else:
        data = _post_query(url, params)

    if isinstance(data, dict) and "error" in data:
        msg = data["error"].get("message", "unknown")
        details = "; ".join(data["error"].get("details", []) or [])
        raise SourceError(f"{layer_url} query failed: {msg} {details}".strip())
    return data


def _post_query(url: str, params: dict[str, Any]) -> Any:
    resp = client().post(url, data=params)
    resp.raise_for_status()
    return _parse_json(resp, url)


def _object_id(feature: dict[str, Any], oid_field: str | None) -> Any:
    """A stable identity for a feature, for de-duplicating pages.

    The obvious implementation -- read OBJECTID -- is not enough. Knowing the
    layer's objectIdFieldName does not help if outFields excluded it, which is
    exactly what this project does when it asks for a named field list. With
    no identity every feature looks new, the duplicate guard never fires, and
    an offset-ignoring server pages until it hits max_records. So fall back to
    fingerprinting the feature itself.
    """
    attrs = feature.get("attributes") or {}
    if oid_field and oid_field in attrs:
        return attrs[oid_field]
    for candidate in ("OBJECTID", "OBJECTID_1", "FID", "OID", "objectid"):
        if candidate in attrs:
            return attrs[candidate]
    try:
        return hashlib.sha1(
            json.dumps(feature, sort_keys=True, default=str).encode()
        ).hexdigest()
    except (TypeError, ValueError):
        return None


# Layers that answered "Pagination is not supported". Remembered for the
# process lifetime so we only pay for the discovery once.
_NO_PAGINATION: set[str] = set()


def _no_pagination(layer_url: str) -> bool:
    return layer_url in _NO_PAGINATION


def arcgis_query_all(
    layer_url: str, *, page_size: int = 1000, max_records: int = 20000, **kwargs
) -> list[dict[str, Any]]:
    """Page through a query until exhausted or max_records is hit.

    Three failure modes this has to survive, all of them real on Tennessee
    services:

    * A layer whose maxRecordCount is below page_size returns a short page
      WITH exceededTransferLimit set. Treating "short page" as "finished"
      silently truncates the result -- 500 of 4,000 parcels, reported as
      complete.
    * An older ArcGIS Server (10.0-10.2, which is what several county
      MapServers here run) ignores resultOffset entirely and hands back the
      same first page forever. Without a guard that is an infinite loop
      producing duplicate parcels.
    * A server that returns nothing at all.

    So: keep going while the server says there is more, stop as soon as a page
    contains no record we have not already seen, and de-duplicate on OBJECTID.
    """
    features: list[dict[str, Any]] = []
    seen: set[Any] = set()
    offset = 0
    oid_field: str | None = None

    while len(features) < max_records:
        want = min(page_size, max_records - len(features))
        if _no_pagination(layer_url):
            # Some older ArcGIS Servers reject resultOffset outright with
            # "Pagination is not supported" rather than ignoring it, which
            # would otherwise fail the whole query. Ask for one unpaged page
            # and take what the server's own maxRecordCount gives us.
            data = arcgis_query(layer_url, **kwargs)
            batch = data.get("features", [])
            features.extend(batch)
            if len(batch) >= 1000:
                log.warning(
                    "%s does not support pagination; results may be capped at "
                    "its maxRecordCount.", layer_url
                )
            return features[:max_records]
        try:
            data = arcgis_query(
                layer_url, result_offset=offset, result_record_count=want, **kwargs
            )
        except SourceError as exc:
            if "pagination is not supported" in str(exc).lower():
                _NO_PAGINATION.add(layer_url)
                continue
            raise
        oid_field = oid_field or data.get("objectIdFieldName")
        batch = data.get("features", [])
        if not batch:
            break

        fresh = []
        for feature in batch:
            oid = _object_id(feature, oid_field)
            if oid is None:
                fresh.append(feature)
                continue
            if oid in seen:
                continue
            seen.add(oid)
            fresh.append(feature)

        # Every record in this page was already collected: the server is
        # ignoring resultOffset. Stop rather than loop forever.
        if not fresh:
            break

        features.extend(fresh)
        offset += len(batch)

        more = bool(data.get("exceededTransferLimit", False)) or \
            bool(data.get("properties", {}).get("exceededTransferLimit", False))
        if len(batch) < want and not more:
            break

    return features[:max_records]


def find_layer_by_name(service_url: str, name_hint: str) -> str:
    """Resolve a layer index by matching its name, not by guessing an integer.

    Deliberately raises rather than falling back to layer 0. Silently querying
    the wrong layer produces missing attributes on every parcel in the state,
    which looks like bad data rather than a broken lookup -- and the wrong
    answer would be cached for a month.
    """
    key = cache.make_key("layerlookup", service_url, name_hint)
    hit = cache.get(key, ttl_days=30)
    if hit:
        return hit
    data = get_json(service_url.rstrip("/") + "/layers", {"f": "json"})
    if isinstance(data, dict) and "error" in data:
        raise SourceError(
            f"{service_url}/layers returned an error: "
            f"{data['error'].get('message', 'unknown')}"
        )
    layers = data.get("layers", []) if isinstance(data, dict) else []
    if not layers:
        raise SourceError(f"No layers listed on {service_url}")

    hint = name_hint.lower().replace(" ", "")
    best = None
    for layer in layers:
        lname = str(layer.get("name", "")).lower().replace(" ", "")
        if hint in lname:
            best = layer
            break
    if best is None:
        available = ", ".join(
            f"{layer.get('id')}={layer.get('name')!r}" for layer in layers
        )
        raise SourceError(
            f"No layer on {service_url} matches {name_hint!r}. "
            f"Available layers: {available}. "
            "Update TN_LANDUSE_LAYER_NAME_HINT in config.py."
        )
    url = f"{service_url.rstrip('/')}/{best['id']}"
    cache.put(key, url)
    return url
