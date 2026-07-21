"""Registry Client Service — wraps the shared ResilientHttpClient, per
BAP_details_v1.1.md §3 ("Registry communication is handled through the Registry
Client Service, which internally uses the HTTP Client Service"). Real Subscribe/
on_subscribe integration landed in Phase 3.1 (BAP Onboarding).

Every call signs a real Authorization header (protocol_compliance_notes_v1.1.md §C.4:
"signing is not optional on any endpoint, including /lookup") using BAP's own signing
key. Registry verifies this header server-side as of Phase 4.3 — see
registry/core/registry_service.py's verify_subscribe_authorization/
verify_lookup_authorization docstrings for the first-time-vs-rotation verification
design.

The HTTP client is Redis-backed for circuit-breaker state (Phase 4.2 follow-up): with
gunicorn running multiple worker processes, a purely in-memory circuit breaker never
tripped network-wide — confirmed live when a stopped Registry still took ~19s to fail on
every single request, because each worker held its own independent failure count. BAP's
Redis (bap-cache) is a required service, always available, so this has no fallback path.
"""

import json

import redis
from django.conf import settings
from resilient_http import ResilientHttpClient

from core.crypto import sign_outbound_request
from core.participant_keys import get_signing_keys

_client: ResilientHttpClient | None = None
_gateway_client: ResilientHttpClient | None = None


def get_client() -> ResilientHttpClient:
    global _client
    if _client is None:
        _client = ResilientHttpClient(
            timeout_seconds=settings.HTTP_CLIENT_TIMEOUT_MS / 1000,
            max_retries=settings.HTTP_CLIENT_MAX_RETRIES,
            circuit_breaker_threshold=settings.HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD,
            redis_client=redis.Redis.from_url(settings.REDIS_URL),
            circuit_breaker_key="bap-registry-client",
        )
    return _client


def get_gateway_client() -> ResilientHttpClient:
    """Isolated from `get_client()` (§3.6, `livetracker2.md`) — that client's
    `circuit_breaker_key` was scoped for Registry calls only (`livetracker1.md` Phase
    4.2), but every `*_service.py` module's Gateway-bound call (`search`/`select`/
    `init`/`confirm`/`status`/`cancel`/`update`/`track`) reused it, so a Gateway outage
    would trip the shared breaker and then also fail-fast genuine Registry calls (e.g.
    trust-verification `lookup()` calls during an incoming `on_X` callback), even though
    Registry itself was never unhealthy. Separate breaker key, same underlying HTTP
    client behavior."""
    global _gateway_client
    if _gateway_client is None:
        _gateway_client = ResilientHttpClient(
            timeout_seconds=settings.HTTP_CLIENT_TIMEOUT_MS / 1000,
            max_retries=settings.HTTP_CLIENT_MAX_RETRIES,
            circuit_breaker_threshold=settings.HTTP_CLIENT_CIRCUIT_BREAKER_THRESHOLD,
            redis_client=redis.Redis.from_url(settings.REDIS_URL),
            circuit_breaker_key="bap-gateway-client",
        )
    return _gateway_client


def _signed_post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    signing_pub, signing_priv = get_signing_keys()
    headers = {
        "Content-Type": "application/json",
        "Authorization": sign_outbound_request(
            body=body,
            subscriber_id=settings.SUBSCRIBER_ID,
            unique_key_id=settings.UNIQUE_KEY_ID,
            signing_private_key_b64=signing_priv,
        ),
    }
    response = get_client().post(
        settings.REGISTRY_BASE_URL.rstrip("/") + path, data=body, headers=headers
    )
    response.raise_for_status()
    return response.json()


def subscribe(payload: dict) -> dict:
    """Calls Registry /subscribe with the confirmed nested payload shape
    (protocol_compliance_notes_v1.1.md §B.3). Returns the parsed JSON response
    (e.g. {"status": "UNDER_SUBSCRIPTION"}); raises requests.HTTPError on a non-2xx
    response (Registry's error body carries the specific reason, e.g.
    DOMAIN_VERIFICATION_FAILED)."""
    return _signed_post("/subscribe", payload)


def lookup(filters: dict) -> list[dict]:
    """Calls Registry /lookup — used for trust establishment (Phase 3.4): fetching
    another participant's public keys to verify their signatures, or polling this BAP's
    own subscription status."""
    return _signed_post("/lookup", filters)


def get_registry_identity() -> dict:
    """Fetches the Registry's own public keys (unauthenticated — see
    registry/core/views.py identity_view docstring for why this endpoint exists at all).
    Needed to decrypt on_subscribe challenges, which are encrypted with a key derived
    from Registry's encryption key + this BAP's encryption key."""
    response = get_client().get(settings.REGISTRY_BASE_URL.rstrip("/") + "/identity")
    response.raise_for_status()
    return response.json()
