"""Registry Client Service — wraps the shared ResilientHttpClient (timeout + retry +
circuit breaker, per livetracker1.md Phase 1.2) for Gateway's calls to Registry.

subscribe()/lookup() here use the `Authorization` header, NOT `X-Gateway-Authorization` —
a deliberate, documented departure from core/crypto.py's module docstring (which is
about Gateway's role forwarding OTHER participants' transaction requests, e.g.
search -> on_search, per protocol_compliance_notes_v1.1.md §C.3/§H.3). Subscribe/Lookup are
registry.yaml paths where Gateway acts as an onboarding participant itself, not a proxy
forwarding someone else's request — and §C.4 confirms "every path in registry.yaml ...
requires SubscriberAuth via a signed Authorization header", the same rule BAP/BPP follow.
X-Gateway-Authorization is for Phase 3's actual search-routing work (livetracker2.md),
not this.

The circuit breaker is Redis-backed (Phase 4.2 follow-up) only when CACHE_ENABLED. Still
gated on the flag (not hardcoded), but the flag itself now defaults to `true` and
`gateway-cache` is an always-on `docker-compose.yml` service (livetracker2.md §3.11's
explicit decision — `search`, §3.1, put Gateway on continuous customer-facing traffic,
matching BAP's/BPP's own unconditional Redis dependency instead of the old opt-in
`[BETA]`/`with-gateway-cache`-profile default from Phase 4.2). With `CACHE_ENABLED=false`
this still falls back to the in-memory `CircuitBreaker` as before — the real
cross-worker-consistency bug confirmed live in Phase 4.2 (a stopped Registry took ~19s to
fail on every request, never failing fast) is only fixed when the cache is enabled.
"""

import json

from django.conf import settings
from resilient_http import ResilientHttpClient

from core.crypto import sign_outbound_request
from core.participant_keys import get_signing_keys

_client: ResilientHttpClient | None = None
_participant_clients: dict[str, ResilientHttpClient] = {}


def get_client() -> ResilientHttpClient:
    global _client
    if _client is None:
        redis_client = None
        if settings.CACHE_ENABLED and settings.REDIS_URL:
            import redis

            redis_client = redis.Redis.from_url(settings.REDIS_URL)
        _client = ResilientHttpClient(
            timeout_seconds=settings.REGISTRY_LOOKUP_TIMEOUT_MS / 1000,
            redis_client=redis_client,
            circuit_breaker_key="gateway-registry-client",
        )
    return _client


def get_participant_client(subscriber_id: str) -> ResilientHttpClient:
    """Returns a `ResilientHttpClient` isolated per counterparty `subscriber_id` (§3.6,
    `livetracker2.md`), for Gateway's *outbound* calls to an individual BPP/BAP —
    `dispatch_X`/`relay_on_X` in `routing.py`. Deliberately separate from `get_client()`
    (Registry-only, its own `circuit_breaker_key`): before this fix, every one of those
    calls reused `get_client()`'s single breaker, so one genuinely-down BPP could trip
    fail-fast routing to every *other* healthy BPP/BAP too, and Registry's own health got
    conflated with each individual downstream participant's — the opposite of what a
    circuit breaker exists for. `RedisCircuitBreaker` only holds two auto-expiring Redis
    keys per instance, so caching one client per real `subscriber_id` ever seen is cheap,
    not an unbounded resource — the number of distinct subscriber_ids is bounded by the
    number of real onboarded participants on the network."""
    client = _participant_clients.get(subscriber_id)
    if client is None:
        redis_client = None
        if settings.CACHE_ENABLED and settings.REDIS_URL:
            import redis

            redis_client = redis.Redis.from_url(settings.REDIS_URL)
        client = ResilientHttpClient(
            timeout_seconds=settings.REGISTRY_LOOKUP_TIMEOUT_MS / 1000,
            redis_client=redis_client,
            circuit_breaker_key=f"gateway-outbound:{subscriber_id}",
        )
        _participant_clients[subscriber_id] = client
    return client


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
