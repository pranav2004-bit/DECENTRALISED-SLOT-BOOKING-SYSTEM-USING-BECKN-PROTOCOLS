"""Registry Client Service — wraps the shared ResilientHttpClient, per
BPP_details_v1.1.md §10. Real Subscribe/on_subscribe integration lands in
Phase 3.2 (BPP Onboarding).
"""

from django.conf import settings
from resilient_http import ResilientHttpClient

_client: ResilientHttpClient | None = None


def get_client() -> ResilientHttpClient:
    global _client
    if _client is None:
        _client = ResilientHttpClient(
            timeout_seconds=settings.HTTP_CLIENT_TIMEOUT_MS / 1000,
            max_retries=settings.HTTP_CLIENT_MAX_RETRIES,
            circuit_breaker_threshold=settings.HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD,
        )
    return _client


def subscribe(payload: dict) -> dict:
    """Calls Registry /subscribe. NOT YET IMPLEMENTED — Phase 3.2."""
    raise NotImplementedError("Real Registry /subscribe integration lands in Phase 3.2")
