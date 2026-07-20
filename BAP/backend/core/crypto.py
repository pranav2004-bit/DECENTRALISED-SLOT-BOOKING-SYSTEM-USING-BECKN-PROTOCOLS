"""Cryptography Service — real implementation, per BAP_details_v1.1.md §10 and the
confirmed scheme in protocol_compliance_notes_v1.1.md §A.4/§C. Thin wrapper around
shared/beckn_crypto (the actual Ed25519/X25519/BLAKE-512 implementation lives there,
shared by Registry/BAP/BPP/Gateway — see that module's docstring for why).
"""

import time

from beckn_crypto import (
    ChallengeDecryptionError,
    SignatureVerificationError,
    build_authorization_header,
    build_signing_string,
    build_verification_file_content,
    compute_blake512_digest,
    decrypt_challenge,
    generate_encryption_key_pair,
    generate_signing_key_pair,
    sign_domain_verification_request_id,
    sign_request,
)

__all__ = [
    "ChallengeDecryptionError",
    "SignatureVerificationError",
    "build_verification_file_content",
    "decrypt_challenge",
    "generate_encryption_key_pair",
    "generate_signing_key_pair",
    "sign_domain_verification_request_id",
    "sign_outbound_request",
]


def sign_outbound_request(
    *, body: bytes, subscriber_id: str, unique_key_id: str, signing_private_key_b64: str
) -> str:
    """Builds the full `Authorization` header value for a BAP-originated request, per
    the confirmed scheme (protocol_compliance_notes_v1.1.md §C.2): Ed25519 signature
    over a signing string containing a BLAKE-512 digest of `body`, TTL-bound
    created/expires. Set this as the `Authorization` header — BAP uses that header
    name, not Gateway's `X-Gateway-Authorization` (§C.3/§H.3)."""
    created = int(time.time())
    expires = created + 30
    digest_b64 = compute_blake512_digest(body)
    signing_string = build_signing_string(created=created, expires=expires, digest_b64=digest_b64)
    signature_b64 = sign_request(
        signing_string=signing_string, private_key_b64=signing_private_key_b64
    )
    return build_authorization_header(
        subscriber_id=subscriber_id,
        unique_key_id=unique_key_id,
        algorithm="ed25519",
        created=created,
        expires=expires,
        signature_b64=signature_b64,
    )
