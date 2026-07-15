"""Cryptography Service — real implementation, per beckn_gateway_details_v1.1.md §9.
Thin wrapper around shared/beckn_crypto. Confirmed real protocol distinction
(protocol_compliance_notes_v1.1.md §C.3): Gateway signs its own outbound calls via
`Proxy-Authorization`, NOT `Authorization` — the header VALUE format is identical to
Registry/BAP/BPP's signing scheme, only the HTTP header NAME differs, so
`build_proxy_authorization_header` below reuses the same underlying value builder and
just documents which header to set it on. Do not set this value under the
`Authorization` header name.
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
    """Builds the header VALUE for a Gateway-originated request. The caller MUST set
    this on the `Proxy-Authorization` header, not `Authorization` — see module
    docstring and protocol_compliance_notes_v1.1.md §C.3."""
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
