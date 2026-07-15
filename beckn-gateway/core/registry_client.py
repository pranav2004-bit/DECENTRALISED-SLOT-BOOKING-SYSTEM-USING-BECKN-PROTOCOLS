"""Registry Client Service — wraps the shared ResilientHttpClient (timeout + retry +
circuit breaker, per livetracker1.md Phase 1.2) for Gateway's calls to Registry.

subscribe()/lookup() here use the `Authorization` header, NOT `Proxy-Authorization` —
a deliberate, documented departure from core/crypto.py's module docstring (which is
about Gateway's role forwarding OTHER participants' transaction requests, e.g.
search -> on_search, per protocol_compliance_notes_v1.1.md §C.3). Subscribe/Lookup are
registry.yaml paths where Gateway acts as an onboarding participant itself, not a proxy
forwarding someone else's request — and §C.4 confirms "every path in registry.yaml ...
requires SubscriberAuth via a signed Authorization header", the same rule BAP/BPP follow.
Proxy-Authorization is for Phase 4's actual search-routing work, not this.
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
            timeout_seconds=settings.REGISTRY_LOOKUP_TIMEOUT_MS / 1000,
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
    """Calls Registry /subscribe — Gateway onboards itself as a network participant too
    (livetracker1.md Phase 3.3), with the confirmed nested payload shape
    (protocol_compliance_notes_v1.1.md §B.3)."""
    return _signed_post("/subscribe", payload)


def lookup(filter_payload: dict) -> list[dict]:
    """Calls Registry /lookup with a Subscription-shaped filter, per
    protocol_compliance_notes_v1.1.md §A.1/§A.2 — used both for onboarding-status polling
    (Phase 3.3) and BPP discovery (beckn_gateway_details_v1.1.md §3.1, exercised for real
    in Phase 4.1)."""
    return _signed_post("/lookup", filter_payload)


def get_registry_identity() -> dict:
    """Fetches the Registry's own public keys — needed to decrypt on_subscribe
    challenges."""
    response = get_client().get(settings.REGISTRY_BASE_URL.rstrip("/") + "/identity")
    response.raise_for_status()
    return response.json()
