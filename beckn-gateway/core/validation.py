"""Validation Service — stub for Phase 1 (per beckn_gateway_details_v1.1.md §9).
Real validators for inbound `search` requests and `on_search` responses against the
confirmed transaction API context shape (protocol_compliance_notes_v1.1.md §D) land
in Phase 4.1 (End-to-End Trust Chain Verification) — that's the first point this
tracker's scope touches real search/on_search traffic. Do not implement business
payload validation here yet.
"""


class PayloadValidationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def validate_context(context: dict) -> None:
    """Validates the shared `context` object fields required on every transaction
    API action (protocol_compliance_notes_v1.1.md §D.3): domain, location, action,
    version, bap_id, bap_uri, transaction_id, message_id, timestamp.
    NOT YET IMPLEMENTED — Phase 4.1."""
    raise NotImplementedError(
        "Real context validation lands in Phase 4.1 — see protocol_compliance_notes_v1.1.md"
    )
