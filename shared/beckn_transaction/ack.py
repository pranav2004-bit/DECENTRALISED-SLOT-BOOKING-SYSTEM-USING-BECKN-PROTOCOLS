"""ACK/NACK response envelope — the immediate, same-session response every real
`/action` and `/on_action` endpoint returns per the confirmed protocol mandate
(protocol_compliance_notes_v1.1.md §H.1/§H.2): async is not optional, and the actual
business response arrives later as a separate signed callback, not in this envelope.
"""


def build_ack_response(*, context: dict) -> dict:
    """`{"context": ..., "message": {"ack": {"status": "ACK"}}}` — confirmed shape
    from `schema/Ack.yaml` (protocol_compliance_notes_v1.1.md §H.2)."""
    return {"context": context, "message": {"ack": {"status": "ACK"}}}


def build_nack_response(*, context: dict, error: dict) -> dict:
    """Same envelope, `status: NACK`, plus a top-level `error` object — used when
    the request fails synchronous validation (bad context, signature failure) before
    any async dispatch is even attempted."""
    return {"context": context, "message": {"ack": {"status": "NACK"}}, "error": error}
