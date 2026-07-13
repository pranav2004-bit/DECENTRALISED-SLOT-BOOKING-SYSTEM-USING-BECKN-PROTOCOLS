"""Registry Client Service — wraps the shared ResilientHttpClient (timeout + retry +
circuit breaker, per livetracker1.md Phase 1.2) for Gateway's calls to the Registry's
/lookup endpoint (used to discover BPPs — beckn_gateway_details_v1.1.md §3.1).

The HTTP resilience layer is real and tested (shared/resilient_http/tests.py). The
actual Lookup call construction and response parsing against the confirmed
Subscription[] schema is Phase 2.1/4.1 work — this establishes the seam, matching
the same scoping already applied to core/crypto.py.
"""

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


def lookup(filter_payload: dict) -> list[dict]:
    """Calls Registry /lookup with a Subscription-shaped filter, per
    protocol_compliance_notes_v1.1.md §A.1/§A.2. NOT YET IMPLEMENTED — Phase 4.1
    (End-to-End Trust Chain Verification) is where this gets exercised for real."""
    raise NotImplementedError("Real Registry /lookup integration lands in Phase 4.1")
