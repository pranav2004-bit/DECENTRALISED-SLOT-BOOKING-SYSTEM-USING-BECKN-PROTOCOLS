"""Real /rating and /on_rating business logic (livetracker2.md Phase 4.5). Same
synchronous-ACK/background-dispatch split as every other action.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §O,
`schema/Rating.yaml`): the REQUEST carries `message.ratings[]` (required, non-empty
array), each entry an `id` (the entity being rated), a `rating_category`
(`Item`/`Order`/`Fulfillment`/`Provider`/`Agent`/`Support`) and a `value`
(deliberately a string in the real schema, not narrowed to int — never validated as
numeric here either, for the same reason). `/on_rating`'s response wraps
`message.feedback_form` ($ref `XInput`), confirmed genuinely optional (only
`context`/`message` are required at the top level) — deliberately omitted: a full
dynamic-form schema this project has no other use for, not a shortcut around a
required field.

This project has no separate `Item`/`Provider`/`Agent`/`Support`-desk models —
`Order` and `Fulfillment` both map onto the one `Booking` record everywhere else in
this codebase (see `status_service.py`'s `order.id`/`fulfillments[].id`, both
`booking.id`). So for every rating entry this resolves `id` as a `Booking` primary
key on a best-effort basis, regardless of the entry's own `rating_category` — a
match is expected for `Order`/`Fulfillment` entries and simply won't occur for the
others, which is fine: the rating is still captured (`Rating.booking` stays unset),
just not FK-linked. IDOR-safe the same way as every other post-booking action: a
resolved `Booking` only gets linked if its `holder_ref` also matches this
transaction — a rating submitted against someone else's order is still recorded
(never silently dropped — it's real submitted feedback), just not attached to a
booking this transaction doesn't own.
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
from django_observability.context import correlation_id_var
from inventory_core.models import Booking, Rating

from . import registry_client, trust
from .crypto import sign_outbound_request
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def validate_and_ack_rating(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /rating: verifies the BAP and the forwarding Gateway, and
    that `message.ratings` is a non-empty array whose entries each carry `id`,
    `rating_category` (a real category), and `value`. Does NOT record anything yet
    — that's dispatch_on_rating's job, fired in the background after this returns."""
    try:
        context = payload["context"]
        validate_context(context)
    except (KeyError, PayloadValidationError) as exc:
        return (
            build_nack_response(
                context=payload.get("context", {}),
                error={"code": "RATING_ERROR", "message": f"Invalid context: {exc}"},
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
                context=context, error={"code": "RATING_ERROR", "message": str(exc)}
            ),
            401,
        )

    ratings = payload.get("message", {}).get("ratings")
    if not isinstance(ratings, list) or not ratings:
        return (
            build_nack_response(
                context=context,
                error={
                    "code": "RATING_ERROR",
                    "message": "message.ratings must be a non-empty array",
                },
            ),
            400,
        )

    valid_categories = set(Rating.RatingCategory.values)
    for entry in ratings:
        if not isinstance(entry, dict):
            missing = "entry"
        elif not entry.get("id"):
            missing = "id"
        elif entry.get("rating_category") not in valid_categories:
            missing = "rating_category"
        elif not entry.get("value"):
            missing = "value"
        else:
            missing = None
        if missing:
            return (
                build_nack_response(
                    context=context,
                    error={
                        "code": "RATING_ERROR",
                        "message": f"each ratings[] entry requires a valid {missing}",
                    },
                ),
                400,
            )

    return build_ack_response(context=context), 200


def _on_rating_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_rating",
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


def dispatch_on_rating(*, payload: dict, correlation_id: str | None = None) -> None:
    """Records every submitted `ratings[]` entry as a real `Rating` row — genuine
    capture, not just an ack-and-discard. Fire-and-forget: failures are logged, not
    raised, same discipline as every other dispatch_on_X."""
    context = payload["context"]
    transaction_id = context["transaction_id"]

    for entry in payload["message"]["ratings"]:
        entity_id = entry["id"]
        try:
            booking = Booking.objects.get(pk=entity_id)
        except (Booking.DoesNotExist, ValueError, ValidationError):
            # ValidationError: Booking.id is a UUID column, and unlike Order/Fulfillment
            # entries (where id is always a real booking id), an Item/Provider/Agent/
            # Support entry's id is expected, common, real input that often won't even
            # look like a UUID — Django's own UUIDField.get_prep_value raises its own
            # ValidationError for those, not ValueError.
            booking = None
        if booking is not None and booking.holder_ref != transaction_id:
            booking = None

        Rating.objects.create(
            booking=booking,
            booking_id_text=entity_id,
            rating_category=entry["rating_category"],
            entity_id=entity_id,
            value=entry["value"],
            correlation_id=correlation_id,
        )

    on_rating_context = _on_rating_context(request_context=context)
    on_rating_payload = {"context": on_rating_context, "message": {}}
    body = json.dumps(on_rating_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    gateway_on_rating_url = settings.GATEWAY_BASE_URL.rstrip("/") + "/on_rating"
    try:
        response = registry_client.get_gateway_client().post(
            gateway_on_rating_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_rating: sending on_rating to %s failed", gateway_on_rating_url
        )


def dispatch_on_rating_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_rating on a daemon thread — the actual fire-and-forget
    entry point the view uses. Kept separate so tests can call dispatch_on_rating
    directly and synchronously without racing a thread.

    Captures `correlation_id_var` here, in the real request-handling thread, before
    spawning the background thread (§3.10) — same pattern as every other
    dispatch_on_X_in_background."""
    correlation_id = correlation_id_var.get()
    thread = threading.Thread(
        target=dispatch_on_rating,
        kwargs={"payload": payload, "correlation_id": correlation_id},
        daemon=True,
    )
    thread.start()
