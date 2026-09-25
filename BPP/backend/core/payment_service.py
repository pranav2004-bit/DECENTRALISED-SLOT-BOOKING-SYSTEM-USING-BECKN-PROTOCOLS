"""BPP-side payment awareness (livetracker5.md Phase 1.6 retirement of the old
`PaymentNotYetImplementedError` placeholder). BPP does not collect payment itself at
`[MVP]` — `livetracker5.md` §0.2 decided BAP collects by default for all 3 domains,
with BPP-side collection (`collected_by="bpp"`) designed into the shared interface
but not required to have a working call path until a real domain needs it.

Phase 4.3 built that call path (`charge_via_bpp`) for real, proving Design Principle 1
holds in this direction too — not just theoretically supported. No real domain calls
it live yet, so it deliberately has no `PaymentTransaction`-equivalent persistence:
the day a real domain needs BPP-initiated collection, that domain wraps this same
call with its own record-keeping, exactly the way `BAP/backend/core/payment_service.py`
wraps this identical interface with its own `PaymentTransaction`.
"""

import payment_gateway
from inventory_core.models import Booking


class BppInitiatedPaymentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BppInitiatedPaymentNotConfiguredError(BppInitiatedPaymentError):
    """No vendor adapter is registered for BPP-initiated collection yet — the
    interface supports this direction (Phase 4.3), nothing has enabled it."""

    def __init__(self):
        super().__init__(
            "NOT_YET_CONFIGURED",
            "No payment vendor is registered for BPP-initiated collection.",
        )


def charge_via_bpp(*, booking_id: str, vendor: str) -> payment_gateway.PaymentResult:
    """Real BPP-initiated charge call path (livetracker5.md Phase 4.3) — proves
    Design Principle 1 holds in this direction too: the exact same
    `shared/payment_gateway` interface, zero interface changes, zero BAP-specific
    code involved.

    Amount-of-record: `Booking.confirmed_total_value`/`confirmed_total_currency`
    (Phase 0.4 option (a), written once at confirm time by
    `confirm_service.dispatch_on_confirm`) — never `SearchSession.confirmed_order`,
    which lives in BAP's own database and would make BPP depend on BAP being up,
    breaking the "BPP doesn't need BAP up to operate" independence this project's
    two-app split otherwise guarantees.

    Idempotency key is deterministic (`bpp-charge:{booking_id}:1`, Design Principle
    3) but not attempt-numbered like BAP's own `payment_service._get_or_create_
    chargeable_transaction`: without a persistence layer to track prior attempts
    (see module docstring — none exists yet, by design, since nothing calls this
    live), a single well-defined key per booking is the honest scope of this proof;
    a real caller adding retry-after-decline semantics adds its own attempt
    tracking around this call, the same way BAP's does.
    """
    try:
        booking = Booking.objects.get(pk=booking_id)
    except (Booking.DoesNotExist, ValueError, TypeError) as exc:
        raise BppInitiatedPaymentError(
            "BOOKING_NOT_FOUND", f"No booking found for booking_id={booking_id!r}."
        ) from exc

    if booking.confirmed_total_value is None or not booking.confirmed_total_currency:
        raise BppInitiatedPaymentError(
            "PAYMENT_AMOUNT_UNAVAILABLE",
            "This booking has no confirmed total value yet — it must be confirmed "
            "before a BPP-initiated charge can be made.",
        )

    try:
        adapter = payment_gateway.get_adapter(vendor)
    except LookupError as exc:
        raise BppInitiatedPaymentNotConfiguredError() from exc

    return adapter.charge(
        amount=booking.confirmed_total_value,
        currency=booking.confirmed_total_currency,
        idempotency_key=f"bpp-charge:{booking_id}:1",
        metadata={"booking_id": str(booking_id)},
    )
