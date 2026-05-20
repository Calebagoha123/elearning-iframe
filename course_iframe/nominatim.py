"""Server-side verification helpers for OpenStreetMap Nominatim place lookups.

We only contact Nominatim to confirm that a place picked from the autocomplete
suggestions actually exists. The frontend handles search/autocomplete directly
against Nominatim; the server merely re-fetches the chosen OSM object to make
sure the submission references a real entity.

Successful verifications are cached (Django cache framework, default 7 days)
so the same (osm_type, osm_id) doesn't get re-fetched on every submit.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)

_ALLOWED_OSM_TYPES = {"node", "way", "relation"}
_OSM_TYPE_TO_LOOKUP = {"node": "N", "way": "W", "relation": "R"}


class PlaceVerificationError(Exception):
    """Raised when Nominatim cannot verify a submitted place."""


def _nominatim_base():
    return getattr(settings, "NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org").rstrip("/")


def _user_agent():
    return getattr(
        settings,
        "NOMINATIM_USER_AGENT",
        "elearning-iframe/1.0 (housing data entry)",
    )


def verification_enabled():
    return bool(getattr(settings, "NOMINATIM_VERIFY", True))


def _cache_key(osm_type, osm_id):
    return f"nominatim:lookup:{osm_type}:{osm_id}"


def _cache_seconds():
    return int(getattr(settings, "NOMINATIM_CACHE_SECONDS", 604800))


def verify_place(osm_type, osm_id):
    """Look up a place by OSM type+id and return a normalized dict.

    Raises ``PlaceVerificationError`` if the place cannot be verified. Successful
    verifications are cached.
    """
    osm_type = (osm_type or "").lower().strip()
    osm_id = str(osm_id or "").strip()
    if osm_type not in _ALLOWED_OSM_TYPES or not osm_id.isdigit():
        raise PlaceVerificationError("Pick a location from the suggestions.")

    if not verification_enabled():
        return {
            "osm_type": osm_type,
            "osm_id": osm_id,
            "display_name": "",
            "lat": "",
            "lon": "",
        }

    cache_key = _cache_key(osm_type, osm_id)
    cached = cache.get(cache_key)
    if cached:
        return cached

    osm_ids = f"{_OSM_TYPE_TO_LOOKUP[osm_type]}{osm_id}"
    query = urllib.parse.urlencode({"osm_ids": osm_ids, "format": "json"})
    url = f"{_nominatim_base()}/lookup?{query}"
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        logger.warning("Nominatim verification failed for %s%s: %s", osm_type, osm_id, exc)
        raise PlaceVerificationError("Could not verify that location right now. Try again.") from exc

    try:
        results = json.loads(body)
    except json.JSONDecodeError as exc:
        raise PlaceVerificationError("Could not verify that location right now. Try again.") from exc

    if not isinstance(results, list) or not results:
        raise PlaceVerificationError("That location was not found on the map.")

    first = results[0]
    verified = {
        "osm_type": osm_type,
        "osm_id": osm_id,
        "display_name": str(first.get("display_name") or ""),
        "lat": str(first.get("lat") or ""),
        "lon": str(first.get("lon") or ""),
    }
    cache.set(cache_key, verified, _cache_seconds())
    return verified
