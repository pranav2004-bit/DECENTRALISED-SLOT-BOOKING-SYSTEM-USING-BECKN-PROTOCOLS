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
from datetime import timedelta

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
from .metrics import record_select_succeeded
from .models import PaymentTransaction, SearchSession
from .participant_keys import get_signing_keys
from .realtime import broadcast_slot_held
from .session_authz import SessionAccessError, resolve_owned_session

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


MAX_CONCURRENT_HOLDS = 3

# Matches search/page.tsx's own HOLD_SECONDS constant exactly — this is what
# the frontend actually requests on every tap-to-hold (BAP/backend/core/
# views.py's select_trigger_view just forwards/clamps whatever it's sent, it
# doesn't invent this number). Used to compute the `expires_at` broadcast to
# every *other* browser watching this provider (see realtime.py's
# `broadcast_slot_held` for why a real callback-based expiry isn't available
# here) — kept as one named constant, not copy-pasted, so the two sides
# can't silently drift apart.
HOLD_SECONDS = 180


def _count_in_flight_holds(*, session: SearchSession) -> int:
    """Reservation-hold abuse prevention (§3.7): counts this same actor's OTHER
    "in-flight" transactions — a real `selected_order` recorded, but not yet
    `confirmed_order`/`cancelled_order` — the honest proxy available at BAP for
    "may currently be holding a slot" (BAP has no direct signal of a hold
    actually releasing early at BPP — a customer backing out without cancelling,
    say — only that this transaction hasn't reached a terminal state yet).
    Keyed by `customer` when authenticated, by the session's own `client_ip`
    (captured at search time) when anonymous — a `SearchSession` with no
    customer and no client_ip has no real actor identity to cap against, so
    it's excluded rather than silently capped against an empty/wrong key.

    Real gap found and closed live 2026-11-05: this had no time bound at all —
    a `selected_order` counted forever, even long after the real BPP-side
    hold's own TTL (`HOLD_SECONDS`) had elapsed, permanently using up a
    customer's cap for slots that had long since been released back to
    everyone else. `selected_order_at` (set once, at the exact moment a real
    hold is recorded — see that field's own docstring) is what BAP actually
    knows about a hold's age; a generous `+30s` margin over `HOLD_SECONDS`
    absorbs the real callback/network latency between the customer's original
    tap and this timestamp being recorded, so a genuinely still-active hold is
    never undercounted, only a truly stale one excluded.

    Real gap found and closed live 2026-11-05: a `selected_order` dropped out of
    this count entirely the instant `/confirm` succeeded — but `/confirm` also
    permanently books the slot (HELD -> BOOKED), before payment is ever
    attempted (see `SearchSession.confirmed_order_at`'s own docstring for the
    full finding). The net effect: once confirmed, a session no longer counted
    as "in flight" at all, so a single actor could repeat select -> confirm
    without limit, permanently locking unlimited slots without ever paying for
    any of them. A confirmed session now counts the same as an ordinary hold
    for exactly as long as `reconciliation.sweep_unpaid_confirmed_bookings`
    gives it to actually pay (`PAYMENT_COMPLETION_DEADLINE_SECONDS`) — past
    that window the sweep cancels it, which is also what naturally drops it out
    of this count (`confirmed_order_at` ages out, same mechanism as a stale
    hold above). A session with a genuinely `SUCCEEDED` payment is a real,
    legitimate booking, not abuse — explicitly excluded regardless of age."""
    now = timezone.now()
    still_within_hold_window = now - timedelta(seconds=HOLD_SECONDS + 30)
    still_within_payment_window = now - timedelta(
        seconds=settings.PAYMENT_COMPLETION_DEADLINE_SECONDS
    )
    base = SearchSession.objects.exclude(transaction_id=session.transaction_id)

    in_flight_holds = base.filter(
        selected_order__isnull=False,
        confirmed_order__isnull=True,
        cancelled_order__isnull=True,
        selected_order_at__gte=still_within_hold_window,
    )
    unpaid_confirmed = base.filter(
        confirmed_order__isnull=False,
        confirmed_order_at__isnull=False,
        confirmed_order_at__gte=still_within_payment_window,
        cancelled_order__isnull=True,
    ).exclude(payment_transactions__status=PaymentTransaction.Status.SUCCEEDED)

    if session.customer_id is not None:
        in_flight_holds = in_flight_holds.filter(customer_id=session.customer_id)
        unpaid_confirmed = unpaid_confirmed.filter(customer_id=session.customer_id)
    elif session.client_ip:
        in_flight_holds = in_flight_holds.filter(customer__isnull=True, client_ip=session.client_ip)
        unpaid_confirmed = unpaid_confirmed.filter(
            customer__isnull=True, client_ip=session.client_ip
        )
    else:
        return 0
    return in_flight_holds.count() + unpaid_confirmed.count()


def trigger_select(
    *,
    transaction_id: str,
    item_id: str,
    requested_timestamp: str,
    customer=None,
    customer_name: str | None = None,
    customer_email: str | None = None,
    customer_phone: str | None = None,
    hold_seconds: int | None = None,
    slot_id: str | None = None,
) -> None:
    """Customer-facing selection trigger — builds and sends the real signed Beckn
    /select to Gateway, targeting the one specific BPP that actually offered this item.
    Clears any previous selection result on this session first, so a re-select doesn't
    briefly show stale data while the new one is in flight (the BPP-side release of
    the prior hold happens independently, server-side, per §3.2).

    `customer` (§3.7): IDOR protection — raises `SelectError(401/403)` if this
    transaction belongs to a different customer than the caller. Also enforces
    `MAX_CONCURRENT_HOLDS` — a real, live-exploitable DoS otherwise: a malicious
    actor could mass-hold every available slot across many different
    `transaction_id`s without ever confirming, denying service to real customers
    for up to a full hold-TTL window (§1.3).

    `customer_name`/`customer_email`/`customer_phone`: who this appointment is
    actually for — carried on `fulfillments[0].stops[0].person`/`.contact`, the
    real confirmed schema location for exactly this (protocol_compliance_notes_v1.1.md
    §J.1's `Stop.yaml` confirmation: `person`/`contact` are real Stop fields, not
    invented ones). Sent once, at select time, since /init and /confirm both resolve
    the same already-created Booking by `fulfillments[0].id` rather than re-reading
    the order body — there is no later point where re-sending this would do anything.

    `slot_id` (project-defined, same "no real Select field for it" territory as
    `hold_seconds` above): disambiguates which *specific* Slot a customer picked
    when more than one shares the identical `requested_timestamp` — AutoCare's own
    token/queue mode (BPP/backend/core/select_service.py's own handling of it is
    the authoritative side; this is just the pass-through). `None` for every other
    domain, same as `hold_seconds` being 0/unset for most callers today."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise SelectError(exc.message, status_code=exc.status_code) from exc

    if _count_in_flight_holds(session=session) >= MAX_CONCURRENT_HOLDS:
        raise SelectError(
            "Too many concurrent reservation holds — confirm or let an existing "
            "one expire before selecting another",
            status_code=429,
        )

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
    stop = {"type": "start", "time": {"timestamp": requested_timestamp}}
    if customer_name:
        stop["person"] = {"name": customer_name}
    if customer_email or customer_phone:
        contact = {}
        if customer_phone:
            contact["phone"] = customer_phone
        if customer_email:
            contact["email"] = customer_email
        stop["contact"] = contact
    order = {
        "provider": {"id": provider_id},
        "items": [{"id": item_id}],
        "fulfillments": [{"stops": [stop]}],
    }
    message: dict = {"order": order}
    if hold_seconds:
        message["hold_seconds"] = hold_seconds
    if slot_id:
        message["slot_id"] = slot_id
    payload = {"context": context, "message": message}
    body = json.dumps(payload).encode()

    _, signing_priv = get_signing_keys()
    auth_header = sign_outbound_request(
        body=body,
        subscriber_id=settings.SUBSCRIBER_ID,
        unique_key_id=settings.UNIQUE_KEY_ID,
        signing_private_key_b64=signing_priv,
    )

    # livetracker4.md §1.1: direct BAP->BPP dispatch — /select is one of the 9
    # post-search actions the real protocol never routes through Gateway once the
    # BPP's address is known (protocol_compliance_notes_v1.1.md §P). Unlike the
    # other 8 actions, select has no `session.selected_bpp_id` yet (that field is
    # what /on_select itself sets, later, in record_on_select_result) — the
    # target here is the `bpp_id` just resolved fresh above by
    # `_find_item_provider`, from this session's own on_search results, not a
    # session field. A fresh Registry lookup immediately before dispatch both
    # re-confirms this BPP is still SUBSCRIBED and resolves its real, current
    # url — not the possibly-stale one from the on_search result itself.
    try:
        bpp_url = registry_client.resolve_subscribed_bpp(bpp_id)
    except registry_client.RegistryLookupError as exc:
        raise SelectError(str(exc), status_code=502) from exc

    try:
        response = registry_client.get_bpp_client(bpp_id).post(
            bpp_url.rstrip("/") + "/select",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": auth_header},
        )
        response.raise_for_status()
    except Exception as exc:
        raise SelectError(f"BPP unreachable: {exc}", status_code=502) from exc

    ack_status = response.json().get("message", {}).get("ack", {}).get("status")
    if ack_status != "ACK":
        raise SelectError("BPP rejected the select request (NACK)", status_code=502)


def get_selection_result(*, transaction_id: str, customer=None) -> dict | None:
    """Returns the current selection outcome, or None if no such search session
    exists. Both fields are None while on_select hasn't arrived yet — a normal,
    honest in-progress state, not an error, same discipline as get_search_results.
    `customer` (§3.7): IDOR protection, same contract as get_search_results."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        if exc.status_code == 404:
            return None
        raise SelectError(exc.message, status_code=exc.status_code) from exc
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
        # livetracker4.md §1.2: on_select now arrives directly from BPP, with no
        # Gateway hop — require_gateway=False (see trust.py's own docstring for
        # why this doesn't weaken the check, only makes the Gateway signature
        # optional rather than removed).
        trust.verify_bpp_and_gateway(
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
        session.selected_order_at = timezone.now()
        # Recorded so a later /init (§3.3) can target the same BPP again without
        # re-deriving it from `selected_order.provider.id`, which isn't reliably
        # unique across different BPPs.
        session.selected_bpp_id = context.get("bpp_id", "")
        session.selected_bpp_uri = context.get("bpp_uri", "")
        record_select_succeeded()
        try:
            order = session.selected_order
            start_stop = order["fulfillments"][0]["stops"][0]
            broadcast_slot_held(
                provider_id=order["provider"]["id"],
                item_id=order["items"][0]["id"],
                start_time=start_stop["time"]["timestamp"],
                expires_at=(timezone.now() + timedelta(seconds=HOLD_SECONDS)).isoformat(),
            )
        except (KeyError, IndexError, TypeError):
            # The real hold itself already succeeded above regardless — this
            # push is a pure UX improvement for *other* browsers, never
            # something this customer's own successful select depends on, so
            # a malformed/unexpected order shape here is logged and dropped,
            # not raised.
            logger.warning(
                "record_on_select_result: could not derive slot-held broadcast fields for "
                "transaction_id=%r",
                transaction_id,
            )
    session.save(
        update_fields=[
            "selected_order",
            "selected_error",
            "selected_order_at",
            "selected_bpp_id",
            "selected_bpp_uri",
            "updated_at",
        ]
    )
