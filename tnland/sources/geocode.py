"""Turn a street address into a point, so it can be looked up like a click.

Two sources:

  1. Census Bureau geocoder -- free, no key, US-only, built on the same
     TIGER data the road layer uses. Primary.
  2. Nominatim (OpenStreetMap) -- fallback. Rate-limited to 1 request per
     second by policy and requires a real User-Agent.

Geocoding is deliberately preferred over matching the parcel layer's own
ADDRESS field, because that field is the assessor's situs address: often
blank on rural vacant land, and formatted inconsistently ("RD" vs "ROAD",
missing suffixes). Resolving to a coordinate and asking which parcel
contains it sidesteps all of that. The ADDRESS field is still used as a
fallback for addresses TIGER does not know.
"""

from __future__ import annotations

import time
from typing import Any

from .. import cache, config
from ..http import SourceError, client, get_json

CENSUS = ("https://geocoding.geo.census.gov/geocoder/locations/"
          "onelineaddress")
NOMINATIM = "https://nominatim.openstreetmap.org/search"

_last_nominatim = 0.0


def geocode(address: str, limit: int = 5) -> list[dict[str, Any]]:
    """Return candidate locations, best first. Never raises for a miss."""
    address = (address or "").strip()
    if len(address) < 5:
        return []

    key = cache.make_key("geocode", address.upper())
    hit = cache.get(key, ttl_days=180)
    if hit is not None:
        return hit[:limit]

    results = _census(address)
    if not results:
        results = _nominatim(address)

    if results:
        cache.put(key, results)
    return results[:limit]


def _census(address: str) -> list[dict[str, Any]]:
    try:
        data = get_json(CENSUS, {
            "address": address,
            "benchmark": "Public_AR_Current",
            "format": "json",
        }, ttl_days=180)
    except Exception:  # noqa: BLE001 - a geocoder miss is not an error
        return []

    matches = (data or {}).get("result", {}).get("addressMatches", []) or []
    out = []
    for m in matches:
        coords = m.get("coordinates") or {}
        lon, lat = coords.get("x"), coords.get("y")
        if lon is None or lat is None:
            continue
        comp = m.get("addressComponents") or {}
        out.append({
            "address": m.get("matchedAddress"),
            "lon": float(lon),
            "lat": float(lat),
            "state": comp.get("state"),
            "zip": comp.get("zip"),
            "source": "US Census geocoder",
        })
    return out


def _nominatim(address: str) -> list[dict[str, Any]]:
    global _last_nominatim
    # Nominatim's usage policy is one request per second, enforced socially
    # and technically. Sleep rather than risk a block.
    elapsed = time.time() - _last_nominatim
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)
    try:
        resp = client().get(NOMINATIM, params={
            "q": address, "format": "json", "countrycodes": "us",
            "limit": 5, "addressdetails": 1,
        }, headers={"User-Agent": config.USER_AGENT}, timeout=20.0)
        _last_nominatim = time.time()
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        return []

    out = []
    for m in data or []:
        try:
            lon, lat = float(m["lon"]), float(m["lat"])
        except (KeyError, TypeError, ValueError):
            continue
        addr = m.get("address") or {}
        if addr.get("state") and addr["state"] != "Tennessee":
            continue
        out.append({
            "address": m.get("display_name"),
            "lon": lon,
            "lat": lat,
            "state": "TN",
            "zip": addr.get("postcode"),
            "source": "OpenStreetMap Nominatim",
        })
    return out


def search_address_field(address: str, limit: int = 25) -> list[dict[str, Any]]:
    """Fallback: match the assessor's own ADDRESS field on the parcel layer.

    Used when no geocoder knows the address, which happens on new roads and
    on rural parcels whose address was assigned locally. Matching is loose --
    house number plus the distinctive part of the street name -- because
    suffix spelling is inconsistent across counties.
    """
    from .parcels import _from_statewide, _statewide_url
    from ..http import arcgis_query_all

    tokens = _street_tokens(address)
    if not tokens:
        return []
    clauses = " AND ".join(
        f"UPPER(ADDRESS) LIKE '%{t}%'" for t in tokens
    )
    try:
        feats = arcgis_query_all(
            _statewide_url(),
            where=clauses,
            out_fields=list(config.TN_PARCEL_FIELDS.values()),
            max_records=limit,
        )
    except SourceError:
        return []
    return [_from_statewide(f) for f in feats]


_SUFFIXES = {
    "RD", "ROAD", "ST", "STREET", "AVE", "AVENUE", "DR", "DRIVE", "LN",
    "LANE", "CT", "COURT", "CIR", "CIRCLE", "HWY", "HIGHWAY", "PIKE",
    "TRL", "TRAIL", "WAY", "BLVD", "PL", "PLACE", "TN", "USA",
}


def _street_tokens(address: str) -> list[str]:
    """House number plus the distinctive words of the street name.

    Drops the suffix ("RD" vs "ROAD" is the single most common mismatch),
    the city, the state and the ZIP -- none of which live in the parcel
    layer's ADDRESS field in a predictable form.
    """
    head = address.split(",")[0].upper()
    raw = [w.strip(".") for w in head.replace("-", " ").split() if w.strip(".")]
    tokens = []
    for w in raw:
        if w in _SUFFIXES or w.isdigit() and len(w) == 5:
            continue
        if w.replace("'", "").isalnum():
            tokens.append(w.replace("'", "''"))
    return tokens[:4]


# ---------------------------------------------------------------------------
# Unnumbered addresses ("0 McBroom Branch Rd")
# ---------------------------------------------------------------------------
# Listing sites use "0" as a placeholder house number when the county has not
# assigned one -- which is the normal state of affairs for vacant land. No
# geocoder can place it, because the address does not exist. The useful
# response is not an approximate point but the road itself, and every parcel
# fronting it.

import re

_UNNUMBERED = re.compile(r"^\s*(0+|tbd|lot\s*\d*|n/?a|none)\s*[-,]?\s+", re.I)


def is_unnumbered(address: str) -> bool:
    """True when the address has a placeholder house number, or none at all."""
    head = (address or "").split(",")[0].strip()
    if not head:
        return False
    if _UNNUMBERED.match(head):
        return True
    # No leading digits at all means it is a road name, not an address.
    return not head[0].isdigit()


def split_address(address: str) -> tuple[str, str]:
    """Return (street_name_without_number_or_suffix, locality)."""
    parts = [p.strip() for p in (address or "").split(",")]
    head = parts[0] if parts else ""
    locality = ", ".join(p for p in parts[1:] if p)

    head = _UNNUMBERED.sub("", head).strip()
    words = [w.strip(".") for w in head.split() if w.strip(".")]
    while words and words[0].isdigit():
        words.pop(0)
    while words and words[-1].upper() in _SUFFIXES:
        words.pop()
    return " ".join(words), locality


def locality_anchor(locality: str) -> dict[str, Any] | None:
    """Geocode a 'City, ST ZIP' string to a point to anchor a road search."""
    if not locality.strip():
        return None
    hits = geocode(locality)
    if hits:
        return hits[0]
    # The Census geocoder handles street addresses, not bare places, so a
    # city or ZIP on its own usually falls through to Nominatim.
    hits = _nominatim(locality)
    return hits[0] if hits else None
