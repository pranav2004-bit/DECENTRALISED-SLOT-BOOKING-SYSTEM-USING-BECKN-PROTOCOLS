"""Validation Service — real implementation as of Phase 4.1 (End-to-End Trust Chain
Verification). Validates only the shared `context` envelope
(protocol_compliance_notes_v1.1.md §D.3) — the first point this tracker's scope
touches real search/on_search traffic. Deliberately does NOT validate `message.intent`/
`message.catalog` business payload shapes — those are business-workflow scope, out of
this foundation/trust-layer tracker (see module docstring history)."""

REQUIRED_CONTEXT_FIELDS = (
    "domain",
    "location",
    "action",
    "version",
    "bap_id",
    "bap_uri",
    "transaction_id",
    "message_id",
    "timestamp",
)


class PayloadValidationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def validate_context(context: dict) -> None:
    """Validates the shared `context` object fields required on every transaction
    API action (protocol_compliance_notes_v1.1.md §D.3): domain, location, action,
    version, bap_id, bap_uri, transaction_id, message_id, timestamp. Raises
    PayloadValidationError naming the first missing/empty field."""
    if not isinstance(context, dict):
        raise PayloadValidationError("context must be an object")
    for field in REQUIRED_CONTEXT_FIELDS:
        if not context.get(field):
            raise PayloadValidationError(f"context.{field} is required", field=field)
