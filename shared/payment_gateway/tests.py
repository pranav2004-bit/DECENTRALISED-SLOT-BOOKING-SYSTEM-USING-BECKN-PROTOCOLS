"""Tests for the vendor-agnostic payment gateway interface (livetracker5.md Phase 1.1).
"""

import re
from decimal import Decimal
from pathlib import Path

import pytest

from .interface import (
    PaymentGatewayAdapter,
    PaymentResult,
    PaymentStatus,
    RefundResult,
    WebhookEvent,
    WebhookVerificationError,
    _REGISTRY,
    get_adapter,
    register_adapter,
    registered_vendors,
)


class _FakeAdapter(PaymentGatewayAdapter):
    """Minimal concrete implementation used only to exercise the interface/registry
    contract in these tests — not a real vendor, never registered outside a test."""

    vendor_code = "fake"

    def charge(self, *, amount, currency, idempotency_key, metadata):
        return PaymentResult(status=PaymentStatus.SUCCEEDED, vendor_txn_id="fake-txn-1")

    def refund(self, *, vendor_txn_id, amount, idempotency_key):
        return RefundResult(
            status=PaymentStatus.REFUNDED, vendor_refund_id="fake-refund-1", refunded_amount=amount
        )

    def verify_webhook(self, *, payload, headers):
        if headers.get("X-Fake-Signature") != "valid":
            raise WebhookVerificationError("invalid signature")
        return WebhookEvent(vendor_txn_id="fake-txn-1", status=PaymentStatus.SUCCEEDED)

    def get_status(self, *, vendor_txn_id):
        return PaymentStatus.SUCCEEDED


@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test starts and ends with an empty registry, so no test's registration
    leaks into another — the module-level `_REGISTRY` dict is process-global state."""
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()


def test_module_leaks_no_vendor_sdk_import():
    """Mechanical Test Gate from Phase 1.1: grep the module's own source for any
    known vendor SDK name — must find none. A real, automated check, not a
    code-review claim."""
    source = (Path(__file__).parent / "interface.py").read_text()
    vendor_names = ["razorpay", "cashfree", "payu", "stripe"]
    for name in vendor_names:
        assert not re.search(name, source, re.IGNORECASE), (
            f"interface.py must stay vendor-agnostic — found {name!r}"
        )


def test_cannot_instantiate_adapter_missing_a_method():
    """Abstract — a vendor adapter that skips part of the contract fails at
    class-definition time, not silently at runtime."""

    class _Incomplete(PaymentGatewayAdapter):
        vendor_code = "incomplete"

        def charge(self, *, amount, currency, idempotency_key, metadata):
            return None

        # refund/verify_webhook/get_status deliberately not implemented

    with pytest.raises(TypeError):
        _Incomplete()


def test_get_adapter_raises_lookup_error_for_unregistered_vendor():
    with pytest.raises(LookupError):
        get_adapter("razorpay")


def test_register_then_get_returns_the_same_instance():
    adapter = _FakeAdapter()
    register_adapter("fake", adapter)
    assert get_adapter("fake") is adapter


def test_register_overwrites_a_previous_registration_for_the_same_code():
    first, second = _FakeAdapter(), _FakeAdapter()
    register_adapter("fake", first)
    register_adapter("fake", second)
    assert get_adapter("fake") is second


def test_registered_vendors_lists_every_registered_code_sorted():
    register_adapter("zeta", _FakeAdapter())
    register_adapter("alpha", _FakeAdapter())
    assert registered_vendors() == ["alpha", "zeta"]


def test_charge_and_refund_round_trip_through_a_real_adapter_instance():
    adapter = _FakeAdapter()
    charge = adapter.charge(
        amount=Decimal("100.00"), currency="INR", idempotency_key="charge:booking-1", metadata={}
    )
    assert charge.status == PaymentStatus.SUCCEEDED
    assert charge.vendor_txn_id == "fake-txn-1"

    refund = adapter.refund(
        vendor_txn_id=charge.vendor_txn_id,
        amount=Decimal("100.00"),
        idempotency_key="refund:booking-1:1",
    )
    assert refund.status == PaymentStatus.REFUNDED
    assert refund.refunded_amount == Decimal("100.00")


def test_verify_webhook_rejects_a_missing_or_invalid_signature():
    adapter = _FakeAdapter()
    with pytest.raises(WebhookVerificationError):
        adapter.verify_webhook(payload=b"{}", headers={})
    with pytest.raises(WebhookVerificationError):
        adapter.verify_webhook(payload=b"{}", headers={"X-Fake-Signature": "tampered"})


def test_verify_webhook_accepts_a_correctly_signed_payload():
    adapter = _FakeAdapter()
    event = adapter.verify_webhook(payload=b"{}", headers={"X-Fake-Signature": "valid"})
    assert event.status == PaymentStatus.SUCCEEDED
    assert event.vendor_txn_id == "fake-txn-1"
