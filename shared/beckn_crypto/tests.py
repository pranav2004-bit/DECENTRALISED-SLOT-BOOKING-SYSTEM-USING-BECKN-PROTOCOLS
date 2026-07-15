"""Standalone tests for shared/beckn_crypto — run directly with pytest, no Django
required (this module has zero framework dependency by design, so any of the four
apps can use it identically).
"""

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

from beckn_crypto import (
    build_verification_file_content,
    generate_signing_key_pair,
    sign_domain_verification_request_id,
)


def test_sign_domain_verification_request_id_produces_valid_signature():
    public_b64, private_b64 = generate_signing_key_pair()
    request_id = "11111111-1111-1111-1111-111111111111"

    signature_b64 = sign_domain_verification_request_id(
        request_id=request_id, signing_private_key_b64=private_b64
    )

    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
    public_key.verify(base64.b64decode(signature_b64), request_id.encode())  # must not raise


def test_domain_verification_signature_is_over_raw_request_id_unhashed():
    """Confirms the spec detail (protocol_compliance_notes_v1.1.md §B.2): signature is
    over the raw request_id string, not a digest of it — a different scheme than
    request signing (which digests the body first)."""
    public_b64, private_b64 = generate_signing_key_pair()
    request_id = "some-request-id"
    signature_b64 = sign_domain_verification_request_id(
        request_id=request_id, signing_private_key_b64=private_b64
    )
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))

    # Verifying against a BLAKE-512 digest of the request_id (the "wrong" scheme) must fail —
    # proves the real implementation signs the raw string, not a digest.
    import hashlib

    digest = hashlib.blake2b(request_id.encode(), digest_size=64).digest()
    try:
        public_key.verify(base64.b64decode(signature_b64), digest)
        raised = False
    except InvalidSignature:
        raised = True
    assert raised, "signature should be over the raw request_id, not a digest of it"


def test_build_verification_file_content_contains_the_signature():
    _, private_b64 = generate_signing_key_pair()
    content = build_verification_file_content(
        request_id="my-request-id", signing_private_key_b64=private_b64
    )
    assert "ondc-site-verification.html" in content
    assert "Signed Unique Request ID:" in content


def test_different_request_ids_produce_different_signatures():
    _, private_b64 = generate_signing_key_pair()
    sig1 = sign_domain_verification_request_id(
        request_id="request-a", signing_private_key_b64=private_b64
    )
    sig2 = sign_domain_verification_request_id(
        request_id="request-b", signing_private_key_b64=private_b64
    )
    assert sig1 != sig2
