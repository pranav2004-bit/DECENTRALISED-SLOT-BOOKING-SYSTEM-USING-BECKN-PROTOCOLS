"""BPP-side domain-scoping enforcement (livetracker7.md Phase 1).

Registry (`registry/core/models.py`'s per-domain `Participant` rows) and Gateway
(`beckn-gateway/core/routing.py`'s `dispatch_search` domain filter) already keep an
out-of-scope request from being routed to a single-domain BPP under normal network
traffic. That is only one layer, not two: nothing inside the BPP itself previously
checked the incoming `context.domain` against what this instance is actually
configured to serve, so a request that bypassed Gateway and reached this BPP
directly would still be genuinely processed. This module is the second, independent
layer — every `validate_and_ack_*` entry point calls `validate_domain_supported`
right after `validate_context` succeeds, so an out-of-scope request gets a real
NACK instead of silently falling through to (at best) an empty-result ACK.
"""

from django.conf import settings


class DomainNotSupportedError(Exception):
    """Raised when a request's `context.domain` is not in this BPP instance's own
    `settings.SUPPORTED_DOMAINS`. Caught by each `validate_and_ack_*` function and
    turned into a real NACK — never allowed to surface as a raw 500."""

    def __init__(self, domain: str | None):
        super().__init__(
            f"domain '{domain}' is not supported by this BPP instance "
            f"(supported: {', '.join(settings.SUPPORTED_DOMAINS)})"
        )
        self.domain = domain


def validate_domain_supported(context: dict) -> None:
    """`context["domain"]` is guaranteed present by this point — `validate_context`
    (called immediately before this, in every caller) already rejected a
    missing/empty `domain` field, so a bare `context["domain"]` here is correct, not
    a KeyError risk this function needs to guard against separately."""
    domain = context["domain"]
    if domain not in settings.SUPPORTED_DOMAINS:
        raise DomainNotSupportedError(domain)
