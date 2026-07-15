"""Search routing — Phase 4.1 plumbing test only, per livetracker1.md: "use the
confirmed /search -> /on_search context/envelope shape as the plumbing test payload,
without implementing real intent/catalog business logic yet." This proves the trust
chain works end-to-end (signature verification + BPP discovery via Registry Lookup) —
it does NOT forward the request to a BPP or implement /on_search. Real search/on_search
business logic is deliberately out of this foundation/trust-layer tracker's scope.
"""

from . import registry_client, trust
from .validation import PayloadValidationError, validate_context


class RoutingError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def route_search(*, payload: dict, authorization_header: str, body: bytes) -> dict:
    """Validates context, verifies the caller's signature against their Registry-
    registered key, confirms the Authorization identity matches context.bap_id (defense
    against a valid signature for one participant being replayed under a different
    claimed bap_id), then looks up SUBSCRIBED sellerApp participants in the requested
    domain — the routing targets Gateway would forward to in a real search/on_search
    flow. Returns {"routed_to": [...]}; raises RoutingError on any failure, with the
    right status code for the caller."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        raise RoutingError(f"Invalid context: {exc}", status_code=400) from exc

    if not authorization_header:
        raise RoutingError("Missing Authorization header", status_code=401)

    try:
        trust.verify_participant_signature(authorization_header=authorization_header, body=body)
    except trust.TrustEstablishmentError as exc:
        raise RoutingError(f"Signature verification failed: {exc}", status_code=401) from exc

    from beckn_crypto import parse_authorization_header

    signer_subscriber_id = parse_authorization_header(authorization_header)["subscriber_id"]
    if signer_subscriber_id != context["bap_id"]:
        raise RoutingError(
            f"Signature identity ({signer_subscriber_id!r}) does not match "
            f"context.bap_id ({context['bap_id']!r})",
            status_code=401,
        )

    bpps = registry_client.lookup({"domain": context["domain"], "type": "sellerApp"})
    subscribed_bpps = [bpp for bpp in bpps if bpp["status"] == "SUBSCRIBED"]

    return {
        "routed_to": [
            {"subscriber_id": bpp["subscriber_id"], "url": bpp["url"]} for bpp in subscribed_bpps
        ]
    }
