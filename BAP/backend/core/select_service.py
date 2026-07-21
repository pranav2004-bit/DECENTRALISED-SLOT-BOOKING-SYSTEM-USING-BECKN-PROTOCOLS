"""Real /select trigger and /on_select receipt (livetracker2.md Phase 3.2). Mirrors
search_service.py's split exactly:
- `trigger_select`/`get_selection_result`: customer/web-facing, non-Beckn-protocol.
- `validate_and_ack_on_select`/`record_on_select_result`: the real Beckn wire endpoint.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §I.1):
both /select's request and /on_select's response carry `message.order`, not a bare
item/fulfillment pair.
"""

import json
import logging

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    new_message_id,
    validate_context,
)
from django.conf import settings
from django.utils import timezone

from . import registry_client, trust
from .crypto import sign_outbound_request
from .models import SearchSession
from .participant_keys import get_signing_keys

logger = logging.getLogger("bap")


class SelectError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _find_item_provider(session: SearchSession, item_id: str) -> tuple[str, str, str]:
    """Searches the session's own accumulated on_search results for the provider that
    actually offers item_id. Returns (bpp_id, bpp_uri, provider_id). Raises SelectError
    if not found — the customer can only select an item a real search genuinely
    returned, never an arbitrary client-supplied id routed to an arbitrary BPP."""
    for result in session.results:
        catalog = result.get("catalog", {})
        for provider in catalog.get("providers", []):
            for item in provider.get("items", []):
                if item.get("id") == item_id:
                    return result["bpp_id"], result["bpp_uri"], provider["id"]
    raise SelectError(f"No such item {item_id!r} in this search's results", status_code=404)


def trigger_select(*, transaction_id: str, item_id: str, requested_timestamp: str) -> None:
    """Customer-facing selection trigger — builds and sends the real signed Beckn
    /select to Gateway, targeting the one specific BPP that actually offered this item.
    Clears any previous selection result on this session first, so a re-select doesn't
    briefly show stale data while the new one is in flight (the BPP-side release of
    the prior hold happens independently, server-side, per §3.2)."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        raise SelectError("No such search transaction", status_code=404) from None

    bpp_id, bpp_uri, provider_id = _find_item_provider(session, item_id)

    session.selected_order = None
    session.selected_error = None
    session.save(update_fields=["selected_order", "selected_error", "updated_at"])

    context = build_context(
        domain=session.domain,
        action="select",
        version="1.1.0",
        bap_id=settings.SUBSCRIBER_ID,
        bap_uri=settings.SUBSCRIBER_URL,
        bpp_id=bpp_id,
        bpp_uri=bpp_uri,
        transaction_id=transaction_id,
        message_id=new_message_id(),
        location={"country": {"code": "IND"}},
        timestamp=timezone.now().isoformat(),
    )
    order = {
        "provider": {"id": provider_id},
        "items": [{"id": item_id}],
        "fulfillments": [
            {"stops": [{"type": "start", "time": {"timestamp": requested_timestamp}}]}
        ],
    }
    payload = {"context": context, "message": {"order": order}}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_select_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/select"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_select_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise SelectError(f"Gateway unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise SelectError("Gateway rejected the select request (NACK)", status_code=502)


def get_selection_result(*, transaction_id: str) -> dict | None:
    """Returns the current selection outcome, or None if no such search session
    exists. Both fields are None while on_select hasn't arrived yet — a normal,
    honest in-progress state, not an error, same discipline as get_search_results."""
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        return None
    return {
        "transaction_id": session.transaction_id,
        "selected_order": session.selected_order,
        "selected_error": session.selected_error,
    }


def validate_and_ack_on_select(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /on_select: verifies the BPP and the forwarding Gateway,
    returns the ACK/NACK envelope. Does NOT record the result — that's
    record_on_select_result's job, called after this returns 200."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "SELECT_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        trust.verify_bpp_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
        )
    except trust.TrustEstablishmentError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "SELECT_ERROR", "message": str(exc)}
            ),
            401,
        )

    return build_ack_response(context=context), 200


def record_on_select_result(*, payload: dict) -> None:
    """Records the real selection outcome (a real Order+quote, or a real error like
    SLOT_UNAVAILABLE) against the matching SearchSession. A callback for a
    transaction_id this BAP has no record of is logged and dropped, same discipline as
    record_on_search_result."""
    context = payload["context"]
    transaction_id = context.get("transaction_id")
    try:
        session = SearchSession.objects.get(transaction_id=transaction_id)
    except SearchSession.DoesNotExist:
        logger.warning(
            "record_on_select_result: no SearchSession for transaction_id=%r, dropping",
            transaction_id,
        )
        return

    error = payload.get("error")
    if error is not None:
        session.selected_error = error
        session.selected_order = None
    else:
        session.selected_order = payload["message"]["order"]
        session.selected_error = None
        # Recorded so a later /init (§3.3) can target the same BPP again without
        # re-deriving it from `selected_order.provider.id`, which isn't reliably
        # unique across different BPPs.
        session.selected_bpp_id = context.get("bpp_id", "")
        session.selected_bpp_uri = context.get("bpp_uri", "")
    session.save(
        update_fields=[
            "selected_order",
            "selected_error",
            "selected_bpp_id",
            "selected_bpp_uri",
            "updated_at",
        ]
    )
