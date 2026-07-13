"""Cryptography Service — stub for Phase 1 (per BAP_details_v1.1.md §10). BAP needs
TWO distinct key pairs (protocol_compliance_notes_v1.1.md §A.4/§B.3), not one:
- signing key pair (Ed25519) for request signing
- encryption key pair (X25519) for the on_subscribe encrypted challenge
Real key generation/signing lands in Phase 3.1 (BAP Onboarding) against the confirmed
scheme — do not implement real crypto here yet.
"""


def generate_signing_key_pair() -> tuple[str, str]:
    """Returns (public_key, private_key), both base64-encoded Ed25519.
    NOT YET IMPLEMENTED — Phase 3.1."""
    raise NotImplementedError("Real Ed25519 key generation lands in Phase 3.1")


def generate_encryption_key_pair() -> tuple[str, str]:
    """Returns (public_key, private_key), X25519, ASN.1 DER format per
    protocol_compliance_notes_v1.1.md §B.3. NOT YET IMPLEMENTED — Phase 3.1."""
    raise NotImplementedError("Real X25519 key generation lands in Phase 3.1")


def sign_outbound_request(*, body: bytes, signing_private_key: str) -> str:
    """Builds the Authorization header per protocol_compliance_notes_v1.1.md §C.2.
    NOT YET IMPLEMENTED — Phase 3.1."""
    raise NotImplementedError("Real request signing lands in Phase 3.1")
