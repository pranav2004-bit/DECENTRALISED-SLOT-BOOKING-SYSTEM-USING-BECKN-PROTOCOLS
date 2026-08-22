"""Real /status and /on_status business logic (livetracker2.md Phase 3.5). Same
synchronous-ACK/background-dispatch split as every other action, for the same
reason (protocol_compliance_notes_v1.1.md §H.1: async is mandatory).

Wire shape confirmed before implementing (§L.1): unlike /select, /init, /confirm,
/status's REQUEST carries only `message.order_id` (a bare id, not a full `Order`)
— /on_status's request is the one that carries the full `message.order`, same as
every other `on_` callback.

This is where a booking's real, live `fulfillment_status`
(`SCHEDULED`/`IN_PROGRESS`/`COMPLETED`/`NO_SHOW`) is exposed on the wire for the
first time — via `Fulfillment.state.descriptor.code` (confirmed project-defined
territory, §F/§J) — not via /track, which the real `Tracking.yaml` schema
confines to reporting whether a *live position feed* exists (§L.5), a genuinely
different concept.
"""

import json
import logging
import threading

from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    validate_context,
)
from django.conf import settings
from django.utils import timezone
from inventory_core.models import Booking
from inventory_core.reservation import find_group_bookings

from . import registry_client, trust
from .crypto import sign_outbound_request
from .domain_scope import DomainNotSupportedError, validate_domain_supported
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def validate_and_ack_status(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /status: verifies the BAP and the forwarding Gateway,
    and that `message.order_id` is present. Does NOT resolve the booking — that's
    dispatch_on_status's job, fired in the background after this returns."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "STATUS_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        validate_domain_supported(context)
    except DomainNotSupportedError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "STATUS_ERROR", "message": str(exc)}
            ),
            400,
        )

    try:
        # livetracker4.md §1.2: /status now arrives directly from BAP, no Gateway
        # hop — require_gateway=False (trust.py's own docstring explains why this
        # only makes the Gateway signature optional, not weaker when present).
        trust.verify_bap_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
            require_gateway=False,
        )
    except trust.TrustEstablishmentError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "STATUS_ERROR", "message": str(exc)}
            ),
            401,
        )

    if not payload.get("message", {}).get("order_id"):
        return (
            build_nack_response(
                context=context,
                error={"code": "STATUS_ERROR", "message": "message.order_id is required"},
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_status_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_status",
        version=request_context["version"],
        bap_id=request_context["bap_id"],
        bap_uri=request_context["bap_uri"],
        bpp_id=settings.SUBSCRIBER_ID,
        bpp_uri=settings.SUBSCRIBER_URL,
        transaction_id=request_context["transaction_id"],
        message_id=request_context["message_id"],
        location=request_context["location"],
        timestamp=timezone.now().isoformat(),
    )


def dispatch_on_status(*, payload: dict) -> None:
    """Resolves the real Booking referenced by `message.order_id`, verifies it's
    held by *this* transaction (same IDOR-safe check as every other post-booking
    action), then returns its real, live current state via /on_status —
    read-only, no state mutation. Fire-and-forget: failures are logged, not
    raised, same discipline as dispatch_on_confirm."""
    context = payload["context"]
    order_id = payload["message"]["order_id"]

    error = None
    resolved_order = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=order_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        # Never distinguish "wrong owner" from "genuinely gone" — same IDOR-safety
        # reasoning as /init and /confirm (protocol_compliance_notes_v1.1.md §J/§L).
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        # §4.2: report every resource in the group (Automotive's bay+mechanic pair),
        # not just the one this booking_id itself names — a customer checking status
        # on a multi-resource booking must see every resource actually reserved for
        # them, not just one. `find_group_bookings` returns just `[booking]` unchanged
        # for every domain before this phase, so this is a no-op widening for them.
        group = find_group_bookings(booking)
        resources = [b.slot.resource for b in group]
        resolved_order = {
            "id": str(booking.id),
            "status": booking.status,
            "provider": {"id": resources[0].owner_ref},
            "items": [{"id": str(r.id)} for r in resources],
            "fulfillments": [
                {
                    "id": str(b.id),
                    "state": {"descriptor": {"code": b.fulfillment_status}},
                    "stops": [
                        {
                            "type": "start",
                            "time": {"timestamp": b.slot.start_time.isoformat()},
                        }
                    ],
                }
                for b in group
            ],
        }

    on_status_context = _on_status_context(request_context=context)
    on_status_message: dict = {
        "order": resolved_order if resolved_order is not None else {"id": order_id}
    }
    on_status_payload = {"context": on_status_context, "message": on_status_message}
    if error is not None:
        on_status_payload["error"] = error
    body = json.dumps(on_status_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    # livetracker4.md §1.2: sent directly to the calling BAP's own subscriber_url
    # (already known from the request's own context) instead of Gateway's relay —
    # /status is one of the 9 post-search actions the real protocol never routes
    # through Gateway once discovery has happened.
    bap_on_status_url = context["bap_uri"].rstrip("/") + "/on_status"
    try:
        response = registry_client.get_bap_client(context["bap_id"]).post(
            bap_on_status_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_status: sending on_status to %s failed", bap_on_status_url
        )


def dispatch_on_status_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_status on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_status
    directly and synchronously without racing a thread."""
    thread = threading.Thread(
        target=dispatch_on_status, kwargs={"payload": payload}, daemon=True
    )
    thread.start()
