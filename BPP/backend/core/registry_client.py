"""Registry Client Service — wraps the shared ResilientHttpClient, per
BPP_details_v1.1.md §10. Real Subscribe/on_subscribe integration landed in Phase 3.2
(BPP Onboarding). See BAP/backend/core/registry_client.py for the full docstring on the
signing convention and the documented server-side-verification gap this carries forward.
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
    (protocol_compliance_notes_v1.1.md §B.3)."""
    return _signed_post("/subscribe", payload)


def lookup(filters: dict) -> list[dict]:
    """Calls Registry /lookup — used for trust establishment (Phase 3.4)."""
    return _signed_post("/lookup", filters)


def get_registry_identity() -> dict:
    """Fetches the Registry's own public keys — needed to decrypt on_subscribe
    challenges."""
    response = get_client().get(settings.REGISTRY_BASE_URL.rstrip("/") + "/identity")
    response.raise_for_status()
    return response.json()
