"""Exact home location from a street address via OpenStreetMap Nominatim. Owner: Dev B, ticket B11 (#38).

Privacy: the address is used once to get coordinates and then dropped. It is never logged, never stored on
the profile, and the cache is keyed by a hash of it. Coordinates are rounded to 3 decimals (about 100 m).
If the geocoder is slow or down, callers keep the neighborhood centroid; nothing here raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import urllib.parse
import urllib.request

from core.types import FamilyProfile

log = logging.getLogger("geo")

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "choices-counselor/0.1 (LA Hacks 2026; github.com/AJChaikovsky/LA-Hacks-project)"
TIMEOUT_S = 3
MIN_INTERVAL_S = 1.0                      # Nominatim usage policy: at most 1 request per second
LA_COUNTY_VIEWBOX = "-118.95,34.82,-117.65,33.70"  # left,top,right,bottom; results limited to LA County

_cache: dict[str, tuple[float, float] | None] = {}
_lock = threading.Lock()
_last_request = 0.0


def _key(address: str) -> str:
    return hashlib.sha256(" ".join(address.lower().split()).encode()).hexdigest()


def _fetch(address: str) -> tuple[float, float] | None:
    global _last_request
    query = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1, "countrycodes": "us",
                                    "viewbox": LA_COUNTY_VIEWBOX, "bounded": 1})
    with _lock:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    req = urllib.request.Request(f"{NOMINATIM}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        rows = json.load(resp)
    if not rows:
        return None
    return round(float(rows[0]["lat"]), 3), round(float(rows[0]["lon"]), 3)


def geocode(address: str | None) -> tuple[float, float] | None:
    """(lat, lng) rounded to 3 decimals, or None when blank, not found in LA County, or the geocoder fails."""
    if not address or not address.strip():
        return None
    key = _key(address)
    if key in _cache:
        return _cache[key]
    try:
        point = _fetch(address)
    except Exception as exc:  # timeout, network, bad JSON: fall back to the neighborhood centroid
        log.warning("[GEO] geocoder unavailable (%s); using the neighborhood centroid", type(exc).__name__)
        return None
    _cache[key] = point
    log.info("[GEO] address %s", "geocoded" if point else "not found in LA County")
    return point


def apply_address(profile: FamilyProfile, address: str | None) -> FamilyProfile:
    """Profile with home_lat/home_lng from the address (when it geocodes); the address itself is not kept."""
    point = geocode(address)
    if point is None:
        return profile
    return profile.model_copy(update={"home_lat": point[0], "home_lng": point[1]})
