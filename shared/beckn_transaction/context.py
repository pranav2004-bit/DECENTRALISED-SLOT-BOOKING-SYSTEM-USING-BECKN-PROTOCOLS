"""Transaction-layer `context` object — shared across BAP, Gateway, and BPP, since all
three build/validate the identical shape (protocol_compliance_notes_v1.1.md §D.3).
Moved here from beckn-gateway/core/validation.py (Phase 4.1 of livetracker1.md), which
was the only place this existed before livetracker2.md Phase 3.1 needed the same
validator on the BAP/BPP side too — a single source of truth instead of three drifting
copies, matching the reuse pattern already established for shared/beckn_crypto.
"""

import uuid

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


def build_context(
    *,
    domain: str,
    action: str,
    version: str,
    bap_id: str,
    bap_uri: str,
    transaction_id: str,
    message_id: str,
    location: dict,
    bpp_id: str | None = None,
    bpp_uri: str | None = None,
    timestamp: str,
) -> dict:
    """Builds a real, validated `context` object. `message_id` and `timestamp` are
    deliberately required, not defaulted here — a fresh `search` gets a new
    `message_id`, but `on_search`/`on_select`/etc. must echo the SAME `message_id` as
    the action they're responding to (protocol semantics, not this function's call to
    make), so silently generating one here would be an easy way to build a wire-invalid
    response without noticing. `new_transaction_id()`/`new_message_id()` below are
    provided separately for the one genuine case each is freshly minted: starting a new
    transaction, or starting a new action within one."""
    context = {
        "domain": domain,
        "location": location,
        "action": action,
        "version": version,
        "bap_id": bap_id,
        "bap_uri": bap_uri,
        "transaction_id": transaction_id,
        "message_id": message_id,
        "timestamp": timestamp,
    }
    if bpp_id is not None:
        context["bpp_id"] = bpp_id
    if bpp_uri is not None:
        context["bpp_uri"] = bpp_uri
    validate_context(context)
    return context


def new_transaction_id() -> str:
    return str(uuid.uuid4())


def new_message_id() -> str:
    return str(uuid.uuid4())
