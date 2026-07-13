"""Cryptography Service — stub for Phase 1 (per beckn_gateway_details_v1.1.md §9).
Real Ed25519 signing lands in Phase 3 (Gateway Onboarding). Note the real protocol
distinction already confirmed and documented: Gateway signs its own outbound calls
via `Proxy-Authorization`, NOT `Authorization` (protocol_compliance_notes_v1.1.md
§C.3) — do not reuse Registry/BAP/BPP's Authorization-header signing code unmodified
when this gets implemented.
"""


def sign_outbound_request(*, body: bytes, private_key_path: str) -> str:
    """Builds the Proxy-Authorization header value for a Gateway-originated request.
    NOT YET IMPLEMENTED — Phase 3.3 (Gateway Onboarding)."""
    raise NotImplementedError("Real Proxy-Authorization signing lands in Phase 3.3")
