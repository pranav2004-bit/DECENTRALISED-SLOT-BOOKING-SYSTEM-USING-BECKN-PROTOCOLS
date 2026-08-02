"""Real /select and /on_select business logic (livetracker2.md Phase 3.2). Same
synchronous-ACK/background-dispatch split as §3.1's search_service.py, for the same
reason (protocol_compliance_notes_v1.1.md §H.1: async is mandatory).

Wire shape confirmed before implementing (§I.1): both /select's request and
/on_select's response carry `message.order` (a real `Order` object), not a bare
item/fulfillment pair. `on_select`'s returned `Order` includes a real `quote`
(§I.2/§I.3) — provisional pricing starts here, not at §3.3 (Init).

Slot resolution deliberately never puts this project's own internal `Slot` primary key
on the wire (it "has no direct protocol-schema counterpart" per its own docstring) —
the customer's requested start time (a real `Time.timestamp`, §I.5) is resolved to a
`Slot` internally, and the resulting `Booking.id` is returned as `Fulfillment.id`
instead, the real schema's own mechanism for referencing one specific fulfillment
instance across select -> init -> confirm.
"""

import json
import logging
import threading

import redis
from beckn_transaction import (
    PayloadValidationError,
    build_ack_response,
    build_context,
    build_nack_response,
    validate_context,
)
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from inventory_core.domain_adapter import get_adapter
from inventory_core.models import Resource, Slot
from inventory_core.reservation import hold_multi_resource_booking, hold_slot, release_hold_now

from . import registry_client, trust
from .automotive_adapter import find_paired_resource
from .crypto import sign_outbound_request
from .events import get_event_bus
from .models import BusinessAccount
from .participant_keys import get_signing_keys

logger = logging.getLogger("bpp")


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)


def _extract_selection(order: dict) -> tuple[str, str]:
    """Pulls the item id + the customer's requested start time out of the order draft.
    Raises ValueError with a human-readable message on any missing/malformed piece —
    the caller turns that into a real NACK, never a raw 500."""
    try:
        item_id = order["items"][0]["id"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("message.order.items[0].id is required") from exc

    try:
        stops = order["fulfillments"][0]["stops"]
        start_stop = next(s for s in stops if s.get("type") == "start")
        requested_timestamp = start_stop["time"]["timestamp"]
    except (KeyError, IndexError, TypeError, StopIteration) as exc:
        raise ValueError(
            "message.order.fulfillments[0].stops[] must include a 'start' stop with time.timestamp"
        ) from exc

    return item_id, requested_timestamp


def validate_and_ack_select(
    *, payload: dict, authorization_header: str, gateway_authorization_header: str, body: bytes
) -> tuple[dict, int]:
    """Synchronous half of /select: verifies the BAP and the forwarding Gateway, and
    that the order draft is at least well-formed enough to resolve later. Does NOT
    resolve the slot or attempt the hold — that's dispatch_on_select's job, fired in
    the background after this returns, matching search's own split."""
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
        # livetracker4.md §1.2: /select now arrives directly from BAP, no Gateway
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
                context=context, error={"code": "SELECT_ERROR", "message": str(exc)}
            ),
            401,
        )

    try:
        order = payload["message"]["order"]
        _extract_selection(order)
    except (KeyError, ValueError) as exc:
        return (
            build_nack_response(
                context=context, error={"code": "SELECT_ERROR", "message": str(exc)}
            ),
            400,
        )

    return build_ack_response(context=context), 200


def _on_select_context(*, request_context: dict) -> dict:
    return build_context(
        domain=request_context["domain"],
        action="on_select",
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


def _hold_multi_resource_selection(
    *, resource: Resource, slot: Slot, requested_time, requested_timestamp: str, transaction_id: str
) -> tuple[dict | None, dict | None]:
    """§4.2's multi-resource selection path — Automotive's own case, and the reason
    `required_resource_count()` (Phase 1.5's own hook, unused until this phase) now
    has a real caller. Finds the paired resource (bay<->mechanic, same business, same
    time) and holds both slots together via `hold_multi_resource_booking()`. Returns
    `(error, resolved_order)`, exactly one of which is non-`None` — the same contract
    `dispatch_on_select`'s single-resource branch already follows, so both branches
    slot into the same response-building code below unchanged."""
    paired = find_paired_resource(resource, requested_time)
    if paired is None:
        return (
            {
                "code": "SLOT_UNAVAILABLE",
                "message": "The other required resource is not available at this time",
            },
            None,
        )
    paired_resource, paired_slot = paired

    bookings = hold_multi_resource_booking(
        [slot.id, paired_slot.id],
        holder_ref=transaction_id,
        redis_client=_redis_client(),
        ttl_seconds=settings.RESERVATION_HOLD_TTL_SECONDS,
        event_bus=get_event_bus(),
    )
    if bookings is None:
        return (
            {"code": "SLOT_UNAVAILABLE", "message": "Slot no longer available"},
            None,
        )

    # livetracker4.md §2.1 cutover (2026-08-02): hold-created metrics and the live
    # dashboard broadcast used to fire inline, right here. Both are now driven by the
    # real event worker consuming the `SlotEvent.RESERVED` `hold_multi_resource_booking`
    # just published above (one per resource) — see `core/events_worker.py`'s
    # `DISPATCH[SlotEvent.RESERVED]`. This is a deliberate metric-semantics decision,
    # confirmed with the project owner: a multi-resource booking (e.g. Automotive's
    # bay+mechanic pair) now counts as 2 holds created, not 1 — one per real `Booking`
    # row, matching what the event bus naturally publishes, not the prior "one count per
    # customer action" behavior.
    resources = [resource, paired_resource]
    total_value = resource.price_value + paired_resource.price_value
    resolved_order = {
        "provider": {"id": resource.owner_ref},
        "items": [{"id": str(r.id)} for r in resources],
        "fulfillments": [
            {
                "id": str(booking.id),
                "stops": [{"type": "start", "time": {"timestamp": requested_timestamp}}],
            }
            for booking in bookings
        ],
        "quote": {
            "price": {"currency": resource.price_currency, "value": str(total_value)},
            "breakup": [
                {
                    "item": {"id": str(r.id)},
                    "title": r.name,
                    "price": {"currency": r.price_currency, "value": str(r.price_value)},
                }
                for r in resources
            ],
        },
    }
    return None, resolved_order


def dispatch_on_select(*, payload: dict) -> None:
    """Resolves the requested item + time against **live** availability (Source-of-
    Truth rule, §3.2's own Test Gate) and attempts the real, already-proven atomic hold
    (`hold_slot`, §1.2/§1.3) — the exact mechanism that makes "selected by someone else
    microseconds earlier is correctly rejected" true, not a new concurrency primitive.
    Sends the resulting /on_select (success or a real, specific error) to Gateway.
    Fire-and-forget: failures are logged, not raised, same discipline as
    dispatch_on_search."""
    context = payload["context"]
    order = payload["message"]["order"]
    item_id, requested_timestamp = _extract_selection(order)

    error = None
    resolved_order = None

    try:
        resource = Resource.objects.get(pk=item_id)
    except (Resource.DoesNotExist, ValueError):
        error = {"code": "ITEM_NOT_FOUND", "message": f"No such item {item_id!r}"}
    else:
        requested_time = parse_datetime(requested_timestamp)
        if requested_time is None:
            error = {
                "code": "VALIDATION_ERROR",
                "message": f"Malformed time.timestamp: {requested_timestamp!r}",
            }
        else:
            try:
                slot = Slot.objects.get(resource=resource, start_time=requested_time)
            except Slot.DoesNotExist:
                error = {
                    "code": "SLOT_UNAVAILABLE",
                    "message": "No matching slot for the requested time",
                }
            else:
                # A customer selecting a different slot after already holding one from
                # an earlier /select in this same transaction must not leak the first
                # hold until its TTL eventually expires on its own — release it now,
                # before attempting the new one. Already generalizes correctly to a
                # prior *multi*-resource hold (§4.2) too: it releases every still-HELD
                # booking under this transaction_id, not just one.
                release_prior_hold_for_transaction(transaction_id=context["transaction_id"])

                # §4.2: how many resources this domain's own adapter says a booking
                # genuinely needs (Automotive's bay+mechanic pair = 2; every domain
                # before it = 1) — the real consumer `required_resource_count()` was
                # missing since Phase 1.5 first defined it.
                business = BusinessAccount.objects.get(id=resource.owner_ref)
                adapter = get_adapter(business.domain_code)
                required_count = adapter.required_resource_count({})

                if required_count > 1:
                    error, resolved_order = _hold_multi_resource_selection(
                        resource=resource,
                        slot=slot,
                        requested_time=requested_time,
                        requested_timestamp=requested_timestamp,
                        transaction_id=context["transaction_id"],
                    )
                else:
                    booking = hold_slot(
                        slot.id,
                        # transaction_id, not bap_id: bap_id identifies the BAP
                        # *application*, shared across every one of its customers —
                        # using it as holder_ref would make one customer's
                        # re-selection release an unrelated customer's hold.
                        # transaction_id is unique per real Beckn transaction, i.e.
                        # per individual customer's shopping session.
                        holder_ref=context["transaction_id"],
                        redis_client=_redis_client(),
                        ttl_seconds=settings.RESERVATION_HOLD_TTL_SECONDS,
                        event_bus=get_event_bus(),
                    )
                    if booking is None:
                        error = {
                            "code": "SLOT_UNAVAILABLE",
                            "message": "Slot no longer available",
                        }
                    else:
                        # livetracker4.md §2.1 cutover: see the multi-resource branch's
                        # own comment above — hold-created metrics and the availability
                        # broadcast are now driven by the real event worker consuming
                        # the `SlotEvent.RESERVED` `hold_slot` just published, not fired
                        # inline here.
                        resolved_order = {
                            "provider": {"id": resource.owner_ref},
                            "items": [{"id": str(resource.id)}],
                            "fulfillments": [
                                {
                                    "id": str(booking.id),
                                    "stops": [
                                        {
                                            "type": "start",
                                            "time": {"timestamp": requested_timestamp},
                                        }
                                    ],
                                }
                            ],
                            "quote": {
                                "price": {
                                    "currency": resource.price_currency,
                                    "value": str(resource.price_value),
                                }
                            },
                        }

    on_select_context = _on_select_context(request_context=context)
    on_select_message: dict = {}
    if resolved_order is not None:
        on_select_message["order"] = resolved_order
    else:
        on_select_message["order"] = order
    on_select_payload = {"context": on_select_context, "message": on_select_message}
    if error is not None:
        on_select_payload["error"] = error
    body = json.dumps(on_select_payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    # livetracker4.md §1.2: sent directly to the calling BAP's own subscriber_url
    # (already known from the request's own context) instead of Gateway's relay —
    # /select is one of the 9 post-search actions the real protocol never routes
    # through Gateway once discovery has happened.
    bap_on_select_url = context["bap_uri"].rstrip("/") + "/on_select"
    try:
        response = registry_client.get_bap_client(context["bap_id"]).post(
            bap_on_select_url,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception:
        logger.exception(
            "dispatch_on_select: sending on_select to %s failed", bap_on_select_url
        )


def dispatch_on_select_in_background(*, payload: dict) -> None:
    """Fires dispatch_on_select on a daemon thread — the actual fire-and-forget entry
    point the view uses. Kept separate so tests can call dispatch_on_select directly
    and synchronously without racing a thread."""
    thread = threading.Thread(target=dispatch_on_select, kwargs={"payload": payload}, daemon=True)
    thread.start()


def release_prior_hold_for_transaction(*, transaction_id: str) -> None:
    """§3.2's re-selection case: a customer selecting a different slot after already
    holding one from an earlier /select in the same transaction must not leak the first
    hold until its TTL eventually expires on its own. Finds any other still-HELD
    booking held under this transaction_id (a customer only ever has one live
    selection in flight per transaction in this minimal flow) and releases it
    immediately via release_hold_now."""
    from inventory_core.models import Booking

    for booking in Booking.objects.filter(holder_ref=transaction_id, status=Booking.Status.HELD):
        # livetracker4.md §2.1 cutover: the dashboard broadcast for this release used to
        # fire inline here. It's now driven by the real event worker consuming the
        # `SlotEvent.RELEASED` `release_hold_now` publishes below — see
        # `core/events_worker.py`'s `DISPATCH[SlotEvent.RELEASED]`. This also closes a
        # real, pre-existing gap: `event_bus` was never wired through this call site
        # before, so a re-selection's own release was never audit-logged either — it now
        # is, via the worker's `audit_log_consumer` on the `BookingEvent.CANCELLED`
        # (`reason="superseded_by_reselect"`) `release_hold_now` also publishes.
        release_hold_now(booking.id, redis_client=_redis_client(), event_bus=get_event_bus())
