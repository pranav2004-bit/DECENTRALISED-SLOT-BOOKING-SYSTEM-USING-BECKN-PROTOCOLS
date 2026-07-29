"""Real /support and /on_support business logic (livetracker2.md Phase 4.5). Same
synchronous-ACK/background-dispatch split as every other action.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §O,
`schema/Support.yaml`): both the REQUEST and the `/on_support` RESPONSE wrap
`message.support` — the same real `Support` schema (`ref_id`/`callback_phone`/
`phone`/`email`/`url`, all optional strings, no enums/$refs) in both directions,
just with different fields populated per direction: the request only ever supplies
`ref_id`, the response supplies the real contact channels.

`ref_id` is genuinely optional in the real schema, but this BPP hosts many
businesses behind one shared endpoint (§2.2) — without a transaction to resolve,
there is no single business's contact info to honestly return. Requiring it here
is an honest implementation-specific tightening, not a silent mis-implementation
of the spec: documented, not hidden.

`BusinessAccount` (§2.2) has one free-form `contact` field ("email or phone" per
its own manager docstring), not separate typed columns — this maps it into the
real schema's `email`/`phone` fields with a plain `"@" in contact` heuristic, the
cheapest honest classification available given the single ambiguous source field.
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
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory_core.models import Booking

from . import registry_client, trust
from .crypto import sign_outbound_request
from .models import BusinessAccount
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def validate_and_ack_support(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /support: verifies the BAP and the forwarding Gateway,
    and that `message.support.ref_id` is present. Does NOT resolve the business —
    that's dispatch_on_support's job, fired in the background after this returns."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "SUPPORT_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        trust.verify_bap_and_gateway(
            context=context,
            authorization_header=authorization_header,
            gateway_authorization_header=gateway_authorization_header,
            body=body,
        )
    except trust.TrustEstablishmentError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "SUPPORT_ERROR", "message": str(exc)}
            ),
            401,
        )

    if not payload.get("message", {}).get("support", {}).get("ref_id"):
        return (
            build_nack_response(
                context=context,
                error={
                    "code": "SUPPORT_ERROR",
                    "message": "message.support.ref_id is required",
                },
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_support_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_support",
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


def dispatch_on_support(*, payload: dict) -> None:
    """Resolves the real Booking referenced by `message.support.ref_id`, verifies
    it's held by *this* transaction (same IDOR-safe check as every other
    post-booking action), then returns the owning business's real contact info via
    /on_support. Fire-and-forget: failures are logged, not raised."""
    context = payload["context"]
    ref_id = payload["message"]["support"]["ref_id"]

    error = None
    resolved_support = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=ref_id)
    except (Booking.DoesNotExist, ValueError, ValidationError):
        # ValidationError: `ref_id` is genuinely optional/free-form per the real
        # Support schema — a non-UUID value is real, expected input, not just a
        # malicious fuzz case, and Django's own UUIDField lookup raises its own
        # ValidationError for it, not ValueError.
        booking = None

    if booking is None or booking.holder_ref != context["transaction_id"]:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        try:
            business = BusinessAccount.objects.get(id=booking.slot.resource.owner_ref)
        except (BusinessAccount.DoesNotExist, ValueError):
            business = None

        resolved_support = {"ref_id": ref_id}
        if business is not None and business.contact:
            if "@" in business.contact:
                resolved_support["email"] = business.contact
            else:
                resolved_support["phone"] = business.contact

    on_support_context = _on_support_context(request_context=context)
    on_support_message: dict = {"support": resolved_support or {"ref_id": ref_id}}
    on_support_payload = {"context": on_support_context, "message": on_support_message}
    if error is not None:
        on_support_payload["error"] = error
    body = json.dumps(on_support_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_support_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_support"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_support_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_support: sending on_support to %s failed", gateway_on_support_url
        )


def dispatch_on_support_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_support on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_support
    directly and synchronously without racing a thread."""
    thread = threading.Thread(
        target=dispatch_on_support, kwargs={"payload": payload}, daemon=True
    )
    thread.start()
