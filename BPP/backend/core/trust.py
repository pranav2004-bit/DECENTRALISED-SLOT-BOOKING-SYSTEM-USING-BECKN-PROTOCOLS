"""Cross-participant trust establishment (Phase 3.4) — see
BAP/backend/core/trust.py for the full design rationale (identical here)."""

from beckn_crypto import (
    SignatureVerificationError,
    parse_authorization_header,
    verify_request_signature,
)

from . import registry_client


class TrustEstablishmentError(Exception):
    pass


def verify_participant_signature(*, authorization_header: str, body: bytes) -> bool:
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


def verify_bap_and_gateway(
    *, context: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> None:
    """Defense in depth for every Gateway-forwarded inbound action (search, select, and
    every later one) — verifies BOTH the original BAP's signature (identity must match
    context.bap_id) AND the forwarding Gateway's own X-Gateway-Authorization signature
    over the identical body. A request reaching BPP directly — bypassing Gateway
    entirely, even with a genuine BAP signature — is rejected for missing/invalid
    X-Gateway-Authorization; BPP only trusts traffic that actually came through a real,
    SUBSCRIBED Gateway. Extracted from §3.1's search_service.py (originally
    `_verify_bap_and_gateway`, private to that module) once §3.2 needed the identical
    check for select — a single shared version instead of two drifting copies. Raises
    `TrustEstablishmentError` on any failure; the caller decides the HTTP status code."""
    if not authorization_header:
        raise TrustEstablishmentError("Missing Authorization header")

    verify_participant_signature(authorization_header=authorization_header, body=body)

    signer_subscriber_id = parse_authorization_header(authorization_header)["subscriber_id"]
    if signer_subscriber_id != context.get("bap_id"):
        raise TrustEstablishmentError(
            f"Signature identity ({signer_subscriber_id!r}) does not match "
            f"context.bap_id ({context.get('bap_id')!r})"
        )

    if not gateway_authorization_header:
        raise TrustEstablishmentError("Missing X-Gateway-Authorization header")
    verify_participant_signature(
        authorization_header=gateway_authorization_header, body=body
    )
