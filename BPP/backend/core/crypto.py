"""Cryptography Service — stub for Phase 1 (per BPP_details_v1.1.md §10). BPP needs
TWO distinct key pairs, same as BAP (protocol_compliance_notes_v1.1.md §A.4/§B.3):
signing (Ed25519) and encryption (X25519). Real key generation/signing lands in
Phase 3.2 (BPP Onboarding).
"""


def generate_signing_key_pair() -> tuple[str, str]:
    """Returns (public_key, private_key), Ed25519. NOT YET IMPLEMENTED — Phase 3.2."""
    raise NotImplementedError("Real Ed25519 key generation lands in Phase 3.2")


def generate_encryption_key_pair() -> tuple[str, str]:
    """Returns (public_key, private_key), X25519. NOT YET IMPLEMENTED — Phase 3.2."""
    raise NotImplementedError("Real X25519 key generation lands in Phase 3.2")


def sign_outbound_request(*, body: bytes, signing_private_key: str) -> str:
    """Builds the Authorization header per protocol_compliance_notes_v1.1.md §C.2.
    NOT YET IMPLEMENTED — Phase 3.2."""
    raise NotImplementedError("Real request signing lands in Phase 3.2")
