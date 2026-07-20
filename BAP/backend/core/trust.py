"""Cross-participant trust establishment (Phase 3.4): given an inbound signed request
from another participant, look up their registered public key via Registry /lookup and
verify the signature against it. This is what makes SUBSCRIBED status actually useful —
without it, a participant's public key sitting in Registry is inert.

Deliberately does NOT cover BAP validating Registry's own identity — that's inherent
trust-on-first-use via GET /identity (see registry_client.get_registry_identity), not a
Lookup-based check, since Registry doesn't Subscribe to itself. Documented as a real,
known limitation (no PKI chain of trust for the Registry's identity itself), not silently
assumed equivalent to participant-to-participant verification.
"""

from beckn_crypto import (
    SignatureVerificationError,
    parse_authorization_header,
    verify_request_signature,
)

from . import registry_client


class TrustEstablishmentError(Exception):
    pass


def verify_participant_signature(*, authorization_header: str, body: bytes) -> bool:
    """Verifies an inbound Authorization header against the signing_public_key Registry
    has on file for that subscriber_id — the real cross-participant check (livetracker1.md
    3.4: 'Gateway can fetch and validate BAP's and BPP's public keys; BAP/BPP can validate
    Registry's identity' — this covers the participant-to-participant half). Raises
    TrustEstablishmentError if the subscriber isn't registered or the signature is invalid."""
    try:
        params = parse_authorization_header(authorization_header)
    except SignatureVerificationError as exc:
        raise TrustEstablishmentError(str(exc)) from exc

    results = registry_client.lookup({"subscriber_id": params["subscriber_id"]})
    if not results:
        raise TrustEstablishmentError(
            f"No registered participant found for subscriber_id={params['subscriber_id']!r}"
        )
    if results[0]["status"] != "SUBSCRIBED":
        raise TrustEstablishmentError(
            f"subscriber_id={params['subscriber_id']!r} is registered but not SUBSCRIBED "
            f"(status={results[0]['status']!r}) — refusing to trust an unverified identity"
        )

    try:
        return verify_request_signature(
            authorization_header=authorization_header,
            body=body,
            public_key_b64=results[0]["signing_public_key"],
        )
    except SignatureVerificationError as exc:
        raise TrustEstablishmentError(str(exc)) from exc


def verify_bpp_and_gateway(
    *, context: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> None:
    """Defense in depth for every Gateway-relayed inbound callback (on_search, and
    now on_select) — verifies BOTH the BPP's own signature (identity must match
    context.bpp_id) AND the forwarding Gateway's own X-Gateway-Authorization signature
    over the identical body. A callback reaching BAP directly — bypassing Gateway
    entirely, even with a genuine BPP signature — is rejected for missing/invalid
    X-Gateway-Authorization. Extracted from §3.1's search_service.py (originally
    `_verify_bpp_and_gateway`, private to that module) once §3.2 needed the identical
    check for on_select — a single shared version instead of two drifting copies,
    mirroring the same extraction already done on BPP's side (core/trust.py's
    verify_bap_and_gateway). Raises TrustEstablishmentError on any failure; the caller
    decides the HTTP status code."""
    if not authorization_header:
        raise TrustEstablishmentError("Missing Authorization header")

    verify_participant_signature(authorization_header=authorization_header, body=body)

    signer_subscriber_id = parse_authorization_header(authorization_header)["subscriber_id"]
    if signer_subscriber_id != context.get("bpp_id"):
        raise TrustEstablishmentError(
            f"Signature identity ({signer_subscriber_id!r}) does not match "
            f"context.bpp_id ({context.get('bpp_id')!r})"
        )

    if not gateway_authorization_header:
        raise TrustEstablishmentError("Missing X-Gateway-Authorization header")
    verify_participant_signature(
        authorization_header=gateway_authorization_header, body=body
    )
