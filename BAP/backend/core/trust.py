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
