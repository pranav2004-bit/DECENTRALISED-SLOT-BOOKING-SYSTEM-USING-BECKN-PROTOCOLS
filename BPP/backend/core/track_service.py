"""Real /track and /on_track business logic (livetracker2.md Phase 3.5, deepened
in §4.2). Same synchronous-ACK/background-dispatch split as every other action.

Wire shape confirmed before implementing (§L.4/§L.5): /track's REQUEST carries
`message.order_id` + `callback_url` (accepted but not acted on differently —
this project keeps the existing Gateway-relay pattern for every action, rather
than a direct push to `callback_url`). **`/on_track`'s message carries
`tracking` ($ref `Tracking.yaml`), NOT an `Order`** — a real, material
correction from Phase 3.5's own research: `Tracking.status`'s real enum is
`active`/`inactive` (whether a *live position feed* exists), not the
`SCHEDULED`/`IN_PROGRESS`/`COMPLETED` fulfillment-progress values §3.5's
original wording assumed. Genuine live-location tracking is not meaningful for
a walk-in Beauty appointment or a Healthcare consultation, so those domains
always, honestly, report `"inactive"`.

§4.2's real depth for Automotive (technician dispatch, the case §3.5
deliberately deferred): `status` reflects the real, already-tracked
`Booking.fulfillment_status` — `"active"` once a technician is genuinely
`IN_PROGRESS` on this booking, `"inactive"` otherwise (`SCHEDULED`/`COMPLETED`/
`NO_SHOW`). No `location`/`url` are populated even for Automotive — this
project has no real GPS/live-position data source, and inventing coordinates
would be dishonest fabrication, not "depth"; `status` alone, backed by a real
existing field, is the genuine signal available today. Real fulfillment-
progress detail beyond that remains `/status`'s job (`status_service.py`), not
duplicated here.
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

from . import registry_client, trust
from .crypto import sign_outbound_request
from .models import BusinessAccount
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def validate_and_ack_track(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /track: verifies the BAP and the forwarding Gateway,
    and that `message.order_id` is present. Does NOT resolve the booking —
    that's dispatch_on_track's job, fired in the background after this returns."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "TRACK_ERROR", "message": f"Invalid context: {exc}"},
            ),
            400,
        )

    try:
        # livetracker4.md §1.2: /track now arrives directly from BAP, no Gateway
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
                context=context, error={"code": "TRACK_ERROR", "message": str(exc)}
            ),
            401,
        )

    if not payload.get("message", {}).get("order_id"):
        return (
            build_nack_response(
                context=context,
                error={"code": "TRACK_ERROR", "message": "message.order_id is required"},
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_track_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_track",
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


def dispatch_on_track(*, payload: dict) -> None:
    """Resolves the real Booking referenced by `message.order_id`, verifies it's
    held by *this* transaction, then returns a real `Tracking` object via
    /on_track — read-only, no state mutation. Fire-and-forget: failures are
    logged, not raised.

    §4.2: `status` is domain-aware — Beauty/Healthcare always report
    `"inactive"` (unchanged, honest — no live feed exists for either), while
    Automotive reports `"active"`/`"inactive"` off the booking's own real
    `fulfillment_status` (see module docstring)."""
    context = payload["context"]
    order_id = payload["message"]["order_id"]

    error = None
    resolved_tracking = None

    try:
        booking = Booking.objects.select_related("slot__resource").get(pk=order_id)
    except (Booking.DoesNotExist, ValueError):
        booking = None

    if booking is None:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    elif booking.holder_ref != context["transaction_id"]:
        error = {"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    else:
        business = BusinessAccount.objects.get(id=booking.slot.resource.owner_ref)
        if business.domain_code == settings.DOMAIN_AUTOMOTIVE:
            is_active = booking.fulfillment_status == Booking.FulfillmentStatus.IN_PROGRESS
            resolved_tracking = {"status": "active" if is_active else "inactive"}
        else:
            resolved_tracking = {"status": "inactive"}

    on_track_context = _on_track_context(request_context=context)
    on_track_message: dict = {
        "tracking": resolved_tracking if resolved_tracking is not None else {"status": "inactive"}
    }
    on_track_payload = {"context": on_track_context, "message": on_track_message}
    if error is not None:
        on_track_payload["error"] = error
    body = json.dumps(on_track_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    # livetracker4.md §1.2: sent directly to the calling BAP's own subscriber_url
    # (already known from the request's own context) instead of Gateway's relay —
    # /track is one of the 9 post-search actions the real protocol never routes
    # through Gateway once discovery has happened.
    bap_on_track_url = context["bap_uri"].rstrip("/") + "/on_track"
    try:
        response = registry_client.get_bap_client(context["bap_id"]).post(
            bap_on_track_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_track: sending on_track to %s failed", bap_on_track_url
        )


def dispatch_on_track_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_track on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_track
    directly and synchronously without racing a thread."""
    thread = threading.Thread(target=dispatch_on_track, kwargs={"payload": payload}, daemon=True)
    thread.start()
