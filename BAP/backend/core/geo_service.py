"""Search page's "Detect my location" — reverse-geocodes GPS coordinates (from the
browser's own Geolocation API) to a pincode, server-side, so the third-party call
and its required User-Agent identification never has to live in the browser —
same reasoning registry_client.py already applies to every other outbound call
this project makes. The pincode -> division -> state lookup that follows is a
plain `postal_directory` read (see postal_lookup_by_pincode_view); this module
only does the one step that genuinely needs an external service.
"""

import redis
from django.conf import settings
from resilient_http import ResilientHttpClient

_client: ResilientHttpClient | None = None


def _get_client() -> ResilientHttpClient:
    global _client
    if _client is None:
        _client = ResilientHttpClient(
            timeout_seconds=settings.HTTP_CLIENT_TIMEOUT_MS / 1000,
            max_retries=settings.HTTP_CLIENT_MAX_RETRIES,
            circuit_breaker_threshold=settings.HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD,
            redis_client=redis.Redis.from_url(
                settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
            ),
            circuit_breaker_key="bap-geocoding-client",
        )
    return _client


class GeocodingError(Exception):
    pass


def reverse_geocode_to_pincode(*, lat: float, lon: float) -> str | None:
    """Real reverse-geocode call to Nominatim. Returns the matched postcode, or
    None when the response has no postcode for this location — a real, honest
    "couldn't resolve" outcome (e.g. coordinates over water or outside any mapped
    area), not an error. Raises GeocodingError only for an actual request failure
    (timeout, non-2xx, unreachable)."""
    try:
        response = _get_client().get(
            settings.GEOCODING_BASE_URL.rstrip("/") + "/reverse",
            params={"format": "json", "lat": lat, "lon": lon, "zoom": 18, "addressdetails": 1},
            headers={"User-Agent": settings.GEOCODING_USER_AGENT},
        )
        response.raise_for_status()
    except Exception as exc:
        raise GeocodingError(f"reverse geocoding failed: {exc}") from exc
    return response.json().get("address", {}).get("postcode")
