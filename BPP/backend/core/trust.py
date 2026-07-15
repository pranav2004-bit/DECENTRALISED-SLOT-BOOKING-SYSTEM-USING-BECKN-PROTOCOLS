"""Cross-participant trust establishment (Phase 3.4) — see
BAP/backend/core/trust.py for the full design rationale (identical here)."""

from beckn_crypto import SignatureVerificationError, parse_authorization_header, verify_request_signature

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
