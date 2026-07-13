"""Cryptography Service — stub for Phase 1 (per registry_details_v1.1.md §12 and
livetracker1.md Phase 1.1). Real Ed25519 signing / X25519 challenge decryption logic
lands in Phase 2.2/2.3 against the confirmed scheme in protocol_compliance_notes_v1.1.md
§A.4/§C — do not implement real crypto here yet; this only establishes the interface
shape so Phase 2 has a stable seam to fill in, per the Phase 2.0 sandbox-confirmation
gate already defined in livetracker1.md.
"""


class SignatureVerificationError(Exception):
    pass


def verify_request_signature(*, authorization_header: str, body: bytes, public_key: str) -> bool:
    """Verify an inbound request's Authorization header against the confirmed scheme:
    Ed25519 signature over a signing string containing a BLAKE-512 digest of `body`
    (protocol_compliance_notes_v1.1.md §C.1-2). NOT YET IMPLEMENTED — Phase 2.3.
    """
    raise NotImplementedError(
        "Real signature verification lands in Phase 2.3 — see protocol_compliance_notes_v1.1.md"
    )


def decrypt_challenge(
    *, encrypted_challenge: str, own_encryption_private_key: str, peer_public_key: str
) -> str:
    """Decrypt an on_subscribe challenge using the shared key derived from this
    participant's encryption private key and the peer's public key
    (protocol_compliance_notes_v1.1.md §A.1/§B.5). NOT YET IMPLEMENTED — Phase 2.2.
    """
    raise NotImplementedError(
        "Real challenge decryption lands in Phase 2.2 — see protocol_compliance_notes_v1.1.md"
    )
