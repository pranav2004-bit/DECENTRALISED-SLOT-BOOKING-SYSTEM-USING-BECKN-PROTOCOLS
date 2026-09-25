"""Real payment-initiation service (livetracker5.md Phase 1.2/1.3/1.6) — the BAP-side
amount-of-record-integrity and idempotency layer sitting in front of the vendor-agnostic
`shared/payment_gateway` interface. Retires the old `PaymentNotYetImplementedError`
placeholder this module used to be — confirmed via grep before this change that
`initiate_payment()` had zero live callers anywhere to break.

Follows the same trigger/result-poll split already established for confirm/init/select
(`confirm_service.py`, etc.): a real vendor charge is not guaranteed to resolve
synchronously (a hosted-checkout flow returns `PENDING` until the customer completes
it on the vendor's own page) — real resolution for that case arrives later via a
signature-verified webhook (Phase 2.2), not built here.
"""

import json
import logging
import threading
from decimal import Decimal, InvalidOperation

import payment_gateway
from beckn_transaction import build_context, new_message_id
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_observability.context import correlation_id_var

from . import registry_client
from .crypto import sign_outbound_request
from .models import PaymentTransaction, SearchSession
from .participant_keys import get_signing_keys
from .session_authz import SessionAccessError, resolve_owned_session

logger = logging.getLogger(__name__)

# Phase 0.1's picked vendor — the only vendor_code this BAP-side flow charges through
# today. Not hardcoded any deeper than this one constant, so adding/swapping a vendor
# (Phase 5) never requires touching this module's own logic, only what's registered
# under this code in `shared/payment_gateway`.
DEFAULT_VENDOR = "razorpay"

_MAX_CREATE_ATTEMPTS = 2  # one real attempt + one retry after losing a genuine DB race


class PaymentError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def initiate_payment(*, transaction_id: str, customer=None) -> dict:
    """Server-side amount resolution (Design Principle 2 / Phase 1.2): the charge
    amount is read directly from `SearchSession.confirmed_order`, never accepted from
    the caller — this function takes only `transaction_id`. Idempotent (Phase 1.3): a
    retry — whether a genuine network retry or a concurrent duplicate click — never
    produces a second real gateway charge; only a caller that genuinely creates a
    fresh `PENDING` row goes on to call the gateway at all.

    `customer` (IDOR protection): same contract as every other trigger/result service
    in this codebase (`confirm_service.resolve_owned_session`).
    """
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        raise PaymentError("PAYMENT_UNAVAILABLE", exc.message, exc.status_code) from exc

    amount, currency = _resolve_confirmed_amount(session)

    txn = _get_or_create_chargeable_transaction(
        session=session, transaction_id=transaction_id, amount=amount, currency=currency
    )
    if txn is None:
        # An existing transaction already covers this booking (in flight, succeeded,
        # or refunded) — never start a second charge; return its real current state.
        return _serialize(_latest_transaction(transaction_id))

    _run_charge(txn)
    return _serialize(txn)


def record_webhook_event(*, vendor: str, payload: bytes, headers: dict) -> None:
    """Real /webhook receipt (livetracker5.md Phase 2.2) — verifies the vendor's
    own signature scheme before trusting anything in `payload` (Design Principle
    5, "fail closed on trust"). Raises `payment_gateway.WebhookVerificationError`
    on an invalid/missing signature; the caller (the view) is responsible for
    turning that into a 401/400, never processing the payload regardless.

    A webhook for a `vendor_txn_id` this app has no record of is logged and
    dropped, same discipline as `confirm_service.record_on_confirm_result`. A
    webhook repeating a transaction's *current* terminal status (Razorpay retries
    webhook delivery with exponential backoff over 24h — genuine duplicate
    delivery is expected, not a bug) is a safe no-op, not an error — `PENDING`
    transactions are the only ones a webhook is expected to move.

    `payment_gateway.get_adapter()` raises `LookupError` for an unregistered
    vendor — deliberately not caught here; the view turns it into a clean 503."""
    adapter = payment_gateway.get_adapter(vendor)
    event = adapter.verify_webhook(payload=payload, headers=headers)

    transitioned_transaction_id: str | None = None

    with transaction.atomic():
        txn = (
            PaymentTransaction.objects.select_for_update()
            .filter(vendor_txn_id=event.vendor_txn_id, vendor=vendor)
            .first()
        )
        if txn is None:
            logger.warning(
                "record_webhook_event: no PaymentTransaction for vendor=%r vendor_txn_id=%r, dropping",
                vendor,
                event.vendor_txn_id,
            )
            return

        if txn.status == event.status:
            return  # duplicate webhook delivery — expected, not an error

        if event.status in (PaymentTransaction.Status.SUCCEEDED, PaymentTransaction.Status.FAILED):
            if txn.status != PaymentTransaction.Status.PENDING:
                # Already resolved (by a prior webhook, or a direct charge()
                # response) to something other than what this webhook claims —
                # do not silently overwrite a terminal state from a stale/
                # out-of-order delivery.
                logger.warning(
                    "record_webhook_event: ignoring %s webhook for already-%s transaction %r",
                    event.status.value,
                    txn.status,
                    txn.transaction_id_text,
                )
                return
            txn.transition_status(event.status)
            transitioned_transaction_id = txn.transaction_id_text
        # A PENDING event (e.g. an "order.created"-shaped notification, if ever
        # sent) carries no new information this transaction doesn't already
        # honestly reflect — nothing to do.

    # livetracker5.md Phase 3.3: fired after the transaction above has genuinely
    # committed (never inside the lock — a slow/failed broadcast must not hold
    # the row), and only on a real transition, same "notify after save, only on
    # success" discipline confirm_service.record_on_confirm_result already
    # established for booking-confirmed emails.
    if transitioned_transaction_id is not None:
        from .realtime import broadcast_payment_status_changed

        broadcast_payment_status_changed(
            transaction_id=transitioned_transaction_id, status=event.status.value
        )
        # livetracker5.md Phase 4.1: BPP only needs to know about a transition that
        # actually changes "is this genuinely paid for" — a FAILED charge never was
        # committed revenue from BPP's own perspective, so it's deliberately not
        # forwarded (unlike the customer's own WS broadcast above, which fires for
        # both, since the customer does care about a decline).
        if event.status == PaymentTransaction.Status.SUCCEEDED:
            notify_bpp_of_payment_status_in_background(
                transaction_id=transitioned_transaction_id, status=event.status.value
            )


def refund_if_paid(*, transaction_id: str) -> None:
    """Cancellation-triggered refund (livetracker5.md Phase 4.2) — called synchronously
    from `cancel_service.record_on_cancel_result` for both BAP- and BPP-initiated
    cancellations (both converge on the real /on_cancel wire callback regardless of
    who requested the cancellation, so this one hook covers both).

    Full refund only: no cancellation-fee or partial-refund policy is documented
    anywhere in this project (`BAP_details_v1.1.md`/`BPP_details_v1.1.md`/
    `project_details.md` — confirmed by grep across every `*.md` in the repo before
    writing this, per this task's own explicit instruction not to assume full refund
    is always correct). A `SUCCEEDED` payment's full remaining amount is refunded;
    nothing here decides *whether* to refund on principle — the caller decides that
    by only calling this on a genuine cancellation.

    Called synchronously, not backgrounded like `notify_booking_cancelled_in_background`:
    unlike a best-effort confirmation email, a lost refund attempt is a real money
    problem, and this app has no background-task durability yet (no DLQ/replay worker
    — the same accepted MVP-tier gap §4.1 already documents) to safely retry a
    fire-and-forget refund that failed mid-flight. This function never raises — a
    refund failure is logged for manual reconciliation and leaves the original
    SUCCEEDED status intact, so a downstream refund problem never blocks or reopens
    an already-genuine cancellation.

    Idempotency key is always `refund:{transaction_id}:1`, not an incrementing
    attempt counter like `charge`'s: unlike a charge (where a decline legitimately
    invites a fresh customer-initiated retry with a new key/row), a refund is one
    well-defined operation against one already-succeeded charge — retried, if ever,
    with the *same* key so the vendor's own idempotency guarantees still apply
    (Design Principle 3)."""
    try:
        _refund_if_paid(transaction_id=transaction_id)
    except Exception:
        logger.exception(
            "refund_if_paid: unexpected error refunding transaction_id=%r, left for manual reconciliation",
            transaction_id,
        )


def _refund_if_paid(*, transaction_id: str) -> None:
    with transaction.atomic():
        txn = (
            PaymentTransaction.objects.select_for_update()
            .filter(transaction_id_text=transaction_id)
            .order_by("-created_at")
            .first()
        )
        if txn is None or txn.status != PaymentTransaction.Status.SUCCEEDED:
            # Nothing paid (cancelled before/without payment), or already
            # refunded/refunding (duplicate /on_cancel delivery) — nothing to do.
            return
        vendor = txn.vendor
        vendor_txn_id = txn.vendor_txn_id
        remaining_amount = txn.amount - txn.refunded_amount
        txn_pk = txn.pk

    # The gateway call itself happens outside the row lock (same discipline as
    # `_run_charge`) — an external HTTP call must never hold a DB lock for its
    # own duration.
    try:
        adapter = payment_gateway.get_adapter(vendor)
    except LookupError:
        logger.error(
            "refund_if_paid: no adapter registered for vendor=%r, cannot refund transaction_id=%r",
            vendor,
            transaction_id,
        )
        return

    idempotency_key = f"refund:{transaction_id}:1"
    try:
        result = adapter.refund(
            vendor_txn_id=vendor_txn_id,
            amount=remaining_amount,
            idempotency_key=idempotency_key,
        )
    except Exception:
        logger.exception(
            "refund_if_paid: refund call raised for transaction_id=%r, leaving SUCCEEDED status intact",
            transaction_id,
        )
        return

    if result.status == payment_gateway.PaymentStatus.REFUNDED:
        new_status = PaymentTransaction.Status.REFUNDED
    elif result.status == payment_gateway.PaymentStatus.PARTIALLY_REFUNDED:
        new_status = PaymentTransaction.Status.PARTIALLY_REFUNDED
    else:
        logger.error(
            "refund_if_paid: refund call for transaction_id=%r returned %s, leaving "
            "SUCCEEDED status intact for manual reconciliation",
            transaction_id,
            result.status.value,
        )
        return

    with transaction.atomic():
        # Re-fetch under lock: a concurrent duplicate call could have reached
        # this point too (both send the same idempotency_key to the vendor,
        # which is safe by design — but only one of them may apply the local
        # status transition).
        txn = PaymentTransaction.objects.select_for_update().filter(pk=txn_pk).first()
        if txn is None or txn.status != PaymentTransaction.Status.SUCCEEDED:
            return
        txn.transition_status(new_status, refunded_amount=result.refunded_amount)

    from .realtime import broadcast_payment_status_changed

    broadcast_payment_status_changed(transaction_id=transaction_id, status=new_status.value)
    # livetracker5.md Phase 4.1: a refund genuinely un-does "is this paid for" —
    # BPP needs this one just as much as the original SUCCEEDED notification.
    notify_bpp_of_payment_status_in_background(transaction_id=transaction_id, status=new_status.value)


def get_payment_result(*, transaction_id: str, customer=None) -> dict | None:
    """Result-poll half of the trigger/result pattern (matches `confirm_service.
    get_confirm_result`). Returns None if no such session or no payment has been
    initiated yet — a normal in-progress/not-started state, not an error."""
    try:
        session = resolve_owned_session(transaction_id=transaction_id, requesting_customer=customer)
    except SessionAccessError as exc:
        if exc.status_code == 404:
            return None
        raise PaymentError("PAYMENT_UNAVAILABLE", exc.message, exc.status_code) from exc
    del session
    txn = _latest_transaction(transaction_id)
    return _serialize(txn) if txn is not None else None


def _get_or_create_chargeable_transaction(
    *, session: SearchSession, transaction_id: str, amount: Decimal, currency: str
) -> PaymentTransaction | None:
    """Returns a freshly created `PENDING` row this caller now owns and must charge,
    or `None` if an existing transaction already covers this booking. Race-safe: a
    genuinely concurrent duplicate `create()` is caught via `idempotency_key`'s DB-level
    uniqueness and retried once, so two callers can never both believe they own the
    charge (Phase 1.3's own Test Gate: N concurrent attempts, exactly 1 real charge).
    """
    for _ in range(_MAX_CREATE_ATTEMPTS):
        with transaction.atomic():
            latest = (
                PaymentTransaction.objects.select_for_update()
                .filter(transaction_id_text=transaction_id)
                .order_by("-created_at")
                .first()
            )
            if latest is not None and latest.status != PaymentTransaction.Status.FAILED:
                # PENDING (still in flight, possibly by a concurrent caller), or a
                # terminal success/refund state — never start a new attempt.
                return None

            attempt = 1 if latest is None else _attempt_number(latest.idempotency_key) + 1
            idempotency_key = f"charge:{transaction_id}:{attempt}"
            try:
                return PaymentTransaction.objects.create(
                    session=session,
                    transaction_id_text=transaction_id,
                    amount=amount,
                    currency=currency,
                    vendor=DEFAULT_VENDOR,
                    idempotency_key=idempotency_key,
                    collected_by=PaymentTransaction.CollectedBy.BAP,
                )
            except IntegrityError:
                # A genuinely concurrent caller won the race for this exact
                # (transaction_id, attempt) pair between our read above and our
                # create() — retry the read; it will now see their row.
                continue
    return None


def _run_charge(txn: PaymentTransaction) -> None:
    """Real gateway call, made outside any DB row lock — an external HTTP call must
    never hold a lock for its own duration, the same discipline this codebase already
    applies everywhere else a lock guards a DB decision, not a network call."""
    try:
        adapter = payment_gateway.get_adapter(txn.vendor)
    except LookupError as exc:
        txn.transition_status(PaymentTransaction.Status.FAILED)
        raise PaymentError(
            "PAYMENT_GATEWAY_UNAVAILABLE",
            "No payment vendor is configured yet.",
            503,
        ) from exc

    result = adapter.charge(
        amount=txn.amount,
        currency=txn.currency,
        idempotency_key=txn.idempotency_key,
        metadata={"transaction_id": txn.transaction_id_text},
    )
    if result.vendor_txn_id:
        txn.vendor_txn_id = result.vendor_txn_id
        txn.save(update_fields=["vendor_txn_id"])

    if result.status == payment_gateway.PaymentStatus.SUCCEEDED:
        txn.transition_status(PaymentTransaction.Status.SUCCEEDED)
        # livetracker5.md Phase 4.1: covers a vendor that resolves synchronously
        # (unlike Razorpay's own hosted-checkout PENDING-then-webhook flow) — the
        # webhook path's own equivalent call is in record_webhook_event.
        notify_bpp_of_payment_status_in_background(
            transaction_id=txn.transaction_id_text, status=PaymentTransaction.Status.SUCCEEDED.value
        )
    elif result.status == payment_gateway.PaymentStatus.FAILED:
        txn.transition_status(PaymentTransaction.Status.FAILED)
    # else: PENDING — a hosted-checkout charge genuinely isn't resolved yet; left
    # as-is, real resolution arrives later via a verified webhook (Phase 2.2), not
    # built here.


def _resolve_confirmed_amount(session: SearchSession) -> tuple[Decimal, str]:
    """Defensive parse (Phase 1.2): `confirmed_order` is untyped JSON with no schema
    enforcement at the DB layer — a malformed or missing `quote.price` shape must
    fail the request cleanly, never silently charge `0` or crash with an unhandled
    `KeyError`."""
    order = session.confirmed_order
    if not order:
        raise PaymentError(
            "PAYMENT_QUOTE_UNAVAILABLE",
            "This booking has no confirmed order yet — payment cannot be initiated "
            "before confirmation has succeeded.",
            409,
        )
    try:
        price = order["quote"]["price"]
        value = Decimal(str(price["value"]))
        currency = price["currency"]
    except (KeyError, TypeError, InvalidOperation) as exc:
        raise PaymentError(
            "PAYMENT_QUOTE_UNAVAILABLE",
            "This booking's confirmed quote is missing or malformed.",
            409,
        ) from exc
    if not currency:
        raise PaymentError(
            "PAYMENT_QUOTE_UNAVAILABLE",
            "This booking's confirmed quote is missing or malformed.",
            409,
        )
    return value, currency


def _attempt_number(idempotency_key: str) -> int:
    return int(idempotency_key.rsplit(":", 1)[-1])


def _latest_transaction(transaction_id: str) -> PaymentTransaction | None:
    return (
        PaymentTransaction.objects.filter(transaction_id_text=transaction_id)
        .order_by("-created_at")
        .first()
    )


def _serialize(txn: PaymentTransaction) -> dict:
    return {
        "transaction_id": txn.transaction_id_text,
        "status": txn.status,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "vendor_txn_id": txn.vendor_txn_id,
    }


def _notify_bpp_of_payment_status(*, transaction_id: str, status: str) -> None:
    """Cross-app payment-status visibility (livetracker5.md Phase 4.1) — sends a
    real signed BAP->BPP HTTP notification, mirroring `cancel_service.trigger_
    cancel`'s own direct-dispatch pattern exactly (a real registry lookup + `crypto.
    sign_outbound_request`, not a bespoke auth scheme). Deliberately NOT `shared/
    event_bus`: that module's own docstring scopes it to "internal EDA between
    business modules within one app... not a distributed message broker", and
    `shared/inventory_core/events.py`'s own docstring says the same thing from the
    other side ("never substitutes for the external Beckn protocol calls between
    BAP <-> Gateway <-> BPP... which stay strictly signed HTTP") — BAP and BPP
    communicate only over signed HTTP everywhere else in this codebase, and this is
    no exception. BPP's own local event_bus only comes into play after this signed
    payload lands on BPP's side (`BPP/backend/core/payment_status_service.py`).

    Meant to be called only via `notify_bpp_of_payment_status_in_background` below,
    never directly — an unreachable BPP must never block or fail an already-genuine
    payment-status transition on BAP's own side. The entire body runs under one
    try/except (matching `notifications.py::_send`'s own "single code path, nothing
    escapes the background thread uncaught" discipline) — a failure building the
    signed request is exactly as harmless-to-the-caller as a failure sending it.
    Known, accepted MVP-tier limitation (recorded, not silently absent): no
    DLQ/replay for this specific notification if BPP is briefly unreachable — BAP
    has no working event worker/DLQ yet (Phase 6.2/6.3 close this gap); BPP's own
    dashboard would go stale until a later payment-status change corrects it."""
    try:
        try:
            session = SearchSession.objects.get(transaction_id=transaction_id)
        except SearchSession.DoesNotExist:
            logger.warning(
                "_notify_bpp_of_payment_status: no SearchSession for transaction_id=%r",
                transaction_id,
            )
            return
        if not session.selected_bpp_id or not session.selected_bpp_uri:
            # No confirmed BPP on this transaction — can't happen for a real charge
            # (payment only follows a real /confirm), but never crash on it regardless.
            logger.warning(
                "_notify_bpp_of_payment_status: no selected BPP for transaction_id=%r",
                transaction_id,
            )
            return

        context = build_context(
            domain=session.domain,
            action="payment_status",
            version="1.1.0",
            bap_id=settings.SUBSCRIBER_ID,
            bap_uri=settings.SUBSCRIBER_URL,
            bpp_id=session.selected_bpp_id,
            bpp_uri=session.selected_bpp_uri,
            transaction_id=transaction_id,
            message_id=new_message_id(),
            location={"country": {"code": "IND"}},
            timestamp=timezone.now().isoformat(),
        )
        payload = {"context": context, "message": {"payment_status": status}}
        body = json.dumps(payload).encode()

        _, signing_priv = get_signing_keys()
        auth_header = sign_outbound_request(
            body=body,
            subscriber_id=settings.SUBSCRIBER_ID,
            unique_key_id=settings.UNIQUE_KEY_ID,
            signing_private_key_b64=signing_priv,
        )

        bpp_url = registry_client.resolve_subscribed_bpp(session.selected_bpp_id)
        response = registry_client.get_bpp_client(session.selected_bpp_id).post(
            bpp_url.rstrip("/") + "/payment_status",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": auth_header,
                "X-Correlation-Id": correlation_id_var.get() or "",
            },
        )
        response.raise_for_status()

        ack_status = response.json().get("message", {}).get("ack", {}).get("status")
        if ack_status != "ACK":
            logger.error(
                "_notify_bpp_of_payment_status: BPP NACKed the payment-status "
                "notification for transaction_id=%r",
                transaction_id,
            )
    except Exception:
        logger.exception(
            "_notify_bpp_of_payment_status: failed to notify BPP for transaction_id=%r",
            transaction_id,
        )


def notify_bpp_of_payment_status_in_background(*, transaction_id: str, status: str) -> None:
    from .notifications import _run_then_close_connection

    thread = threading.Thread(
        target=_run_then_close_connection,
        kwargs={
            "target": _notify_bpp_of_payment_status,
            "kwargs": {"transaction_id": transaction_id, "status": status},
        },
        daemon=True,
    )
    thread.start()
