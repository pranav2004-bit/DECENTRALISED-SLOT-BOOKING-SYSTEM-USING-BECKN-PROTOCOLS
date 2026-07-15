"""Registry Client Service — wraps the shared ResilientHttpClient, per
BAP_details_v1.1.md §3 ("Registry communication is handled through the Registry
Client Service, which internally uses the HTTP Client Service"). Real Subscribe/
on_subscribe integration landed in Phase 3.1 (BAP Onboarding).

Every call signs a real Authorization header (protocol_compliance_notes_v1.1.md §C.4:
"signing is not optional on any endpoint, including /lookup") using BAP's own signing
key. NOTE — a genuine, documented gap carried into Phase 4 security-hardening, not
silently assumed complete: Registry's /subscribe and /lookup endpoints do not yet verify
this header server-side (see livetracker1.md Phase 3 notes). For /subscribe specifically
this is a real bootstrapping question — a first-time participant has no key registered
with Registry yet for it to verify against — that a future pass needs to resolve, not
guess at here.
"""

import json

from core.crypto import sign_outbound_request
from core.participant_keys import get_signing_keys
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
