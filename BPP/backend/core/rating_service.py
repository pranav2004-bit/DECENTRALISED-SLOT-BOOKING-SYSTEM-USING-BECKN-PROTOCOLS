"""Real /rating and /on_rating business logic (livetracker2.md Phase 4.5). Same
synchronous-ACK/background-dispatch split as every other action.

Wire shape confirmed before implementing (protocol_compliance_notes_v1.1.md §O,
`schema/Rating.yaml`): the REQUEST carries `message.ratings[]` (required, non-empty
array), each entry an `id` (the entity being rated), a `rating_category`
(`Item`/`Order`/`Fulfillment`/`Provider`/`Agent`/`Support`) and a `value`
(deliberately a string in the real schema, not narrowed to int at the model layer —
`Rating.value` itself stays a free-form string). `/on_rating`'s response wraps
`message.feedback_form` ($ref `XInput`), confirmed genuinely optional (only
`context`/`message` are required at the top level) — deliberately omitted: a full
dynamic-form schema this project has no other use for, not a shortcut around a
required field.

livetracker3.md §2.1: `validate_and_ack_rating` narrows `value` to a plain 1-5 int
before ACKing — this project's own use case never needs the real schema's wider
comparison/logical-expression latitude, and an unvalidated value could otherwise
crash or silently corrupt the real aggregate `dispatch_on_rating` now maintains on
`Resource.average_rating`/`rating_count` for FK-linked `Order`/`Fulfillment`
ratings. See `dispatch_on_rating`'s own docstring for the aggregation rules.

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
from django.db import transaction
from django.utils import timezone
from django_observability.context import correlation_id_var
from inventory_core.models import Booking, Rating, Resource

from . import registry_client, trust
from .crypto import sign_outbound_request
from .domain_scope import DomainNotSupportedError, validate_domain_supported
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")

# livetracker3.md §2.1 bullet 5: the real schema's `Rating.value` allows a bare number OR a
# comparison/logical expression (protocol_compliance_notes_v1.1.md §O) — but this project's
# own use case only ever needs a plain 1-5 star score, so `/rating` narrows that latitude
# explicitly rather than aggregating (or crashing on) an expression string. Applied uniformly
# to every entry, matching this function's existing uniform id/rating_category/value-presence
# checks — this project's own real flow never produces a non-Order/Fulfillment rating either
# (§2 objective), so there is no legitimate category-specific carve-out to preserve.
_MIN_RATING_VALUE = 1
_MAX_RATING_VALUE = 5

# The two categories this project's own booking flow actually produces (§2.1 bullet 2) — an
# Item/Provider/Agent/Support-category rating is still real, stored feedback, just never
# rolled into a Resource's public aggregate (nothing in this codebase resolves those
# categories to a Resource today).
_AGGREGATE_ELIGIBLE_CATEGORIES = {Rating.RatingCategory.ORDER, Rating.RatingCategory.FULFILLMENT}


def _parse_rating_value(value: str) -> int | None:
    """Returns the clean 1-5 int a `Rating.value` string represents, or `None` if it's
    anything else (a comparison/logical expression, out-of-range, non-numeric) — used both
    by request-time validation and, defensively, by the aggregation step itself, so a value
    that somehow reaches `dispatch_on_rating` without going through validation still can't
    crash or corrupt the aggregate (§2.1 bullet 5's "excluded from aggregation" fallback)."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if _MIN_RATING_VALUE <= parsed <= _MAX_RATING_VALUE:
        return parsed
    return None


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
        validate_domain_supported(context)
    except DomainNotSupportedError as exc:
        return (
            build_nack_response(
                context=context, error={"code": "RATING_ERROR", "message": str(exc)}
            ),
            400,
        )

    try:
        # livetracker4.md §1.2: /rating now arrives directly from BAP, no Gateway
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
        if _parse_rating_value(entry["value"]) is None:
            return (
                build_nack_response(
                    context=context,
                    error={
                        "code": "RATING_ERROR",
                        "message": (
                            f"ratings[].value must be a plain integer "
                            f"{_MIN_RATING_VALUE}-{_MAX_RATING_VALUE}, "
                            f"got {entry['value']!r}"
                        ),
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


def _adjust_resource_rating_aggregate(
    *, resource_id, old_value: int | None, new_value: int | None
) -> None:
    """livetracker3.md §2.1: the on-write incremental aggregate update. Locks the one real
    `Resource` row being changed (`select_for_update`, inside its own short transaction) so
    two near-simultaneous ratings against the same resource can't lose an update to a race —
    the same real-concurrency discipline this project already applies to `hold_slot()`'s
    atomic capacity decrement, not a hypothetical concern for a real, concurrently-reachable
    wire action.

    `old_value is None` means this is a brand-new aggregate-eligible rating (increments
    `rating_count`); a non-`None` `old_value` means an existing rating is being corrected in
    place (count stays the same, only the weighted sum shifts). `new_value is None` means the
    incoming value isn't aggregate-eligible (defensive-only path — request-time validation
    already rejects this for real traffic) and the resource's numbers are left untouched."""
    if old_value is None and new_value is None:
        return
    with transaction.atomic():
        resource = Resource.objects.select_for_update().get(pk=resource_id)
        current_total = (
            (resource.average_rating * resource.rating_count)
            if resource.average_rating is not None
            else 0
        )
        if old_value is not None:
            current_total -= old_value
        else:
            resource.rating_count += 1
        if new_value is not None:
            current_total += new_value
        elif old_value is not None and resource.rating_count > 0:
            # A previously aggregate-eligible rating is being corrected to a
            # non-eligible value — defensive-only (validation already blocks this on
            # the real request path), but still don't leave rating_count counting a
            # contribution current_total no longer includes.
            resource.rating_count -= 1
        resource.average_rating = (
            round(current_total / resource.rating_count, 2) if resource.rating_count > 0 else None
        )
        resource.save(update_fields=["average_rating", "rating_count"])


def dispatch_on_rating(*, payload: dict, correlation_id: str | None = None) -> None:
    """Records every submitted `ratings[]` entry as a real `Rating` row — genuine
    capture, not just an ack-and-discard. Fire-and-forget: failures are logged, not
    raised, same discipline as every other dispatch_on_X.

    livetracker3.md §2.1: also updates the owning `Resource`'s real `average_rating`/
    `rating_count` aggregate, on write — but only for a rating that is both FK-linked to a
    `Booking` this transaction genuinely owns (the existing IDOR check below) *and* whose
    `rating_category` is one this project's own flow actually produces against a `Resource`
    (`Order`/`Fulfillment` — bullet 2's scoping decision). An IDOR-mismatched or
    category-mismatched rating is still recorded (unchanged §4.5 behavior, real feedback,
    never silently dropped) — it just never moves the public number, closing the
    review-bombing/self-inflation hole an unrestricted aggregate would open."""
    context = payload["context"]
    transaction_id = context["transaction_id"]

    for entry in payload["message"]["ratings"]:
        entity_id = entry["id"]
        rating_category = entry["rating_category"]
        value = entry["value"]
        try:
            booking = Booking.objects.select_related("slot__resource").get(pk=entity_id)
        except (Booking.DoesNotExist, ValueError, ValidationError):
            # ValidationError: Booking.id is a UUID column, and unlike Order/Fulfillment
            # entries (where id is always a real booking id), an Item/Provider/Agent/
            # Support entry's id is expected, common, real input that often won't even
            # look like a UUID — Django's own UUIDField.get_prep_value raises its own
            # ValidationError for those, not ValueError.
            booking = None
        if booking is not None and booking.holder_ref != transaction_id:
            booking = None

        aggregate_eligible = (
            booking is not None and rating_category in _AGGREGATE_ELIGIBLE_CATEGORIES
        )

        if aggregate_eligible:
            # Real race found and fixed 2026-07-29: reading the "old value" via a separate
            # pre-fetch before calling update_or_create() is not race-safe — under two
            # genuinely near-simultaneous submissions for the same (booking, rating_category)
            # (a real, plausible case: a double-click, a client retry), the losing thread's
            # separate read can be stale, making it treat a real *update* as a *new* rating
            # and double-count it in rating_count/average_rating even though the DB
            # uniqueness constraint correctly prevented a second row from ever existing.
            # `select_for_update()` locks the one real row (existing or, once created,
            # contended) so the read-old/write-new step is atomic per (booking,
            # rating_category), the same real-concurrency discipline already used for the
            # `Resource` row itself in `_adjust_resource_rating_aggregate()`.
            new_numeric = _parse_rating_value(value)
            with transaction.atomic():
                rating, created = Rating.objects.select_for_update().get_or_create(
                    booking=booking,
                    rating_category=rating_category,
                    defaults={
                        "booking_id_text": entity_id,
                        "entity_id": entity_id,
                        "value": value,
                        "correlation_id": correlation_id,
                    },
                )
                if created:
                    old_numeric = None
                else:
                    old_numeric = _parse_rating_value(rating.value)
                    rating.booking_id_text = entity_id
                    rating.entity_id = entity_id
                    rating.value = value
                    rating.correlation_id = correlation_id
                    rating.save(
                        update_fields=["booking_id_text", "entity_id", "value", "correlation_id"]
                    )
            if old_numeric != new_numeric:
                _adjust_resource_rating_aggregate(
                    resource_id=booking.slot.resource_id,
                    old_value=old_numeric,
                    new_value=new_numeric,
                )
        else:
            Rating.objects.create(
                booking=booking,
                booking_id_text=entity_id,
                rating_category=rating_category,
                entity_id=entity_id,
                value=value,
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

    # livetracker4.md §1.2: sent directly to the calling BAP's own subscriber_url
    # (already known from the request's own context) instead of Gateway's relay —
    # /rating is one of the 9 post-search actions the real protocol never routes
    # through Gateway once discovery has happened.
    bap_on_rating_url = context["bap_uri"].rstrip("/") + "/on_rating"
    try:
        response = registry_client.get_bap_client(context["bap_id"]).post(
            bap_on_rating_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_rating: sending on_rating to %s failed", bap_on_rating_url
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
