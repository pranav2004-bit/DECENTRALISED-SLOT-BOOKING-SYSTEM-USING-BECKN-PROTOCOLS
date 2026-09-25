"""livetracker5.md Phase 1.6 / Phase 4.3 Test Gates.

Phase 1.6: retired the old always-raises placeholder.
Phase 4.3 (`SANITY`): a real BPP-initiated `charge()` call, through the exact same
`shared/payment_gateway` interface BAP uses, with no BAP-specific code or
cross-service DB read involved — `charge_via_bpp` reads its amount only from
`Booking.confirmed_total_value`/`confirmed_total_currency`, never `SearchSession`
(which doesn't even exist in this app's database)."""

import datetime as dt
from decimal import Decimal

import payment_gateway
import pytest
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot

from core.payment_service import (
    BppInitiatedPaymentError,
    BppInitiatedPaymentNotConfiguredError,
    charge_via_bpp,
)


class _FakeAdapter(payment_gateway.PaymentGatewayAdapter):
    vendor_code = "razorpay"

    def __init__(self, outcome=payment_gateway.PaymentStatus.SUCCEEDED):
        self.outcome = outcome
        self.charge_calls: list[dict] = []

    def charge(self, *, amount, currency, idempotency_key, metadata):
        self.charge_calls.append(
            {
                "amount": amount,
                "currency": currency,
                "idempotency_key": idempotency_key,
                "metadata": metadata,
            }
        )
        return payment_gateway.PaymentResult(
            status=self.outcome, vendor_txn_id=f"vendor-{idempotency_key}"
        )

    def refund(self, *, vendor_txn_id, amount, idempotency_key):
        raise NotImplementedError

    def verify_webhook(self, *, payload, headers):
        raise NotImplementedError

    def get_status(self, *, vendor_txn_id):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clean_payment_gateway_registry():
    payment_gateway.interface._REGISTRY.clear()
    yield
    payment_gateway.interface._REGISTRY.clear()


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


def _confirmed_booking(resource, *, total_value="899.00", total_currency="INR"):
    now = timezone.now()
    slot = Slot.objects.create(
        resource=resource,
        start_time=now,
        end_time=now + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=0,
    )
    return Booking.objects.create(
        slot=slot,
        holder_ref="tx-1",
        status=Booking.Status.ACTIVE,
        confirmed_total_value=Decimal(total_value) if total_value is not None else None,
        confirmed_total_currency=total_currency,
    )


def test_charge_via_bpp_raises_not_configured_when_no_adapter_is_registered(resource):
    booking = _confirmed_booking(resource)
    with pytest.raises(BppInitiatedPaymentNotConfiguredError) as exc_info:
        charge_via_bpp(booking_id=str(booking.id), vendor="razorpay")
    assert exc_info.value.code == "NOT_YET_CONFIGURED"


def test_charge_via_bpp_raises_for_an_unknown_booking(db):
    with pytest.raises(BppInitiatedPaymentError) as exc_info:
        charge_via_bpp(booking_id="00000000-0000-0000-0000-000000000000", vendor="razorpay")
    assert exc_info.value.code == "BOOKING_NOT_FOUND"


def test_charge_via_bpp_raises_when_the_booking_has_no_confirmed_amount_yet(resource):
    booking = _confirmed_booking(resource, total_value=None, total_currency="")
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    with pytest.raises(BppInitiatedPaymentError) as exc_info:
        charge_via_bpp(booking_id=str(booking.id), vendor="razorpay")
    assert exc_info.value.code == "PAYMENT_AMOUNT_UNAVAILABLE"


def test_charge_via_bpp_charges_through_the_real_shared_interface(resource):
    """The actual Phase 4.3 `SANITY` proof: a real `PaymentGatewayAdapter.charge()`
    call, sourced entirely from `Booking.confirmed_total_value`, no BAP code or
    `SearchSession` involved anywhere in this call path."""
    booking = _confirmed_booking(resource, total_value="1250.50", total_currency="INR")
    adapter = _FakeAdapter()
    payment_gateway.register_adapter("razorpay", adapter)

    result = charge_via_bpp(booking_id=str(booking.id), vendor="razorpay")

    assert result.status == payment_gateway.PaymentStatus.SUCCEEDED
    assert result.vendor_txn_id
    assert len(adapter.charge_calls) == 1
    call = adapter.charge_calls[0]
    assert call["amount"] == Decimal("1250.50")
    assert call["currency"] == "INR"
    assert call["idempotency_key"] == f"bpp-charge:{booking.id}:1"
    assert call["metadata"] == {"booking_id": str(booking.id)}
