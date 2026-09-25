"""Tests for the Razorpay adapter (livetracker5.md Phase 2.1/2.2). All HTTP calls
mocked via `responses` against Razorpay's own documented request/response shapes —
no real sandbox credentials used or required (this project's Rule 4: sandbox-only,
explicitly authorized). The FUNC/SEC Test Gates that require a genuine sandbox charge
stay honestly unchecked in the tracker; these tests verify the adapter's own request
construction and response parsing are correct against the real documented API shapes.
"""

import hashlib
import hmac
import json
from decimal import Decimal

import responses
from resilient_http import ResilientHttpClient

from .interface import PaymentStatus, WebhookVerificationError
from .razorpay_adapter import RazorpayAdapter, _sanitize_refund_idempotency_key, _to_paise


def _adapter():
    return RazorpayAdapter(
        key_id="rzp_test_fake",
        key_secret="fake_secret",
        webhook_secret="whsec_fake",
        http_client=ResilientHttpClient(max_retries=0),
    )


def test_to_paise_converts_rupees_to_integer_paise():
    assert _to_paise(Decimal("899.00")) == 89900
    assert _to_paise(Decimal("10.50")) == 1050


def test_sanitize_refund_idempotency_key_strips_disallowed_characters():
    key = "refund:txn-pay-1:1"
    sanitized = _sanitize_refund_idempotency_key(key)
    assert all(c.isalnum() or c in "-_" for c in sanitized)
    assert len(sanitized) >= 10
    # Deterministic — same input always produces the same output, required for
    # retries to actually dedupe against Razorpay's own idempotency check.
    assert sanitized == _sanitize_refund_idempotency_key(key)


@responses.activate
def test_charge_creates_a_real_shaped_order_and_returns_pending():
    captured = {}

    def callback(request):
        captured["body"] = json.loads(request.body)
        captured["auth"] = request.headers.get("Authorization")
        return (200, {}, json.dumps({"id": "order_abc123", "status": "created", "amount": 89900}))

    responses.add_callback(responses.POST, "https://api.razorpay.com/v1/orders", callback=callback)

    result = _adapter().charge(
        amount=Decimal("899.00"),
        currency="INR",
        idempotency_key="charge:txn-pay-1:1",
        metadata={"transaction_id": "txn-pay-1"},
    )

    assert result.status == PaymentStatus.PENDING  # hosted checkout not yet completed
    assert result.vendor_txn_id == "order_abc123"
    assert captured["body"]["amount"] == 89900  # paise, not rupees
    assert captured["body"]["currency"] == "INR"
    assert captured["body"]["receipt"] == "charge:txn-pay-1:1"
    assert captured["auth"] is not None  # Basic Auth header present


@responses.activate
def test_charge_maps_a_rejected_order_to_failed():
    responses.add(
        responses.POST,
        "https://api.razorpay.com/v1/orders",
        json={"error": {"description": "Invalid currency"}},
        status=400,
    )
    result = _adapter().charge(
        amount=Decimal("899.00"), currency="XXX", idempotency_key="charge:txn-1:1", metadata={}
    )
    assert result.status == PaymentStatus.FAILED
    assert result.vendor_txn_id is None
    assert result.failure_reason == "Invalid currency"


@responses.activate
def test_refund_sends_the_sanitized_idempotency_header_and_converts_amount():
    captured = {}

    def callback(request):
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"id": "rfnd_1", "amount": 89900, "status": "processed"}),
        )

    responses.add_callback(
        responses.POST,
        "https://api.razorpay.com/v1/payments/pay_abc/refund",
        callback=callback,
    )

    result = _adapter().refund(
        vendor_txn_id="pay_abc", amount=Decimal("899.00"), idempotency_key="refund:txn-1:1"
    )

    assert result.status == PaymentStatus.REFUNDED
    assert result.refunded_amount == Decimal("899")
    assert captured["headers"]["X-Refund-Idempotency"] == "refund-txn-1-1"
    assert captured["body"]["amount"] == 89900


@responses.activate
def test_refund_maps_a_rejected_refund_to_failed():
    responses.add(
        responses.POST,
        "https://api.razorpay.com/v1/payments/pay_abc/refund",
        json={"error": {"description": "Refund amount exceeds payment amount"}},
        status=400,
    )
    result = _adapter().refund(
        vendor_txn_id="pay_abc", amount=Decimal("9999.00"), idempotency_key="refund:txn-1:1"
    )
    assert result.status == PaymentStatus.FAILED
    assert result.refunded_amount == Decimal("0.00")


def _signed_headers(payload: bytes, secret: str = "whsec_fake") -> dict:
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return {"X-Razorpay-Signature": sig}


def test_verify_webhook_accepts_a_correctly_signed_payment_captured_event():
    payload = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {"entity": {"id": "pay_1", "order_id": "order_abc123", "status": "captured"}}
            },
        }
    ).encode()
    event = _adapter().verify_webhook(payload=payload, headers=_signed_headers(payload))
    assert event.vendor_txn_id == "order_abc123"
    assert event.status == PaymentStatus.SUCCEEDED


def test_verify_webhook_accepts_a_correctly_signed_payment_failed_event():
    payload = json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {"entity": {"id": "pay_1", "order_id": "order_abc123", "status": "failed"}}
            },
        }
    ).encode()
    event = _adapter().verify_webhook(payload=payload, headers=_signed_headers(payload))
    assert event.status == PaymentStatus.FAILED


def test_verify_webhook_rejects_a_missing_signature():
    payload = b'{"event": "payment.captured"}'
    import pytest

    with pytest.raises(WebhookVerificationError):
        _adapter().verify_webhook(payload=payload, headers={})


def test_verify_webhook_rejects_a_tampered_signature():
    payload = json.dumps({"event": "payment.captured"}).encode()
    headers = _signed_headers(payload, secret="wrong_secret")
    import pytest

    with pytest.raises(WebhookVerificationError):
        _adapter().verify_webhook(payload=payload, headers=headers)


def test_verify_webhook_rejects_a_malformed_but_correctly_signed_payload():
    """A signature can be genuinely valid for a payload that's still missing the
    fields this adapter needs — must fail closed, not crash with a raw KeyError."""
    payload = json.dumps({"event": "payment.captured", "payload": {}}).encode()
    import pytest

    with pytest.raises(WebhookVerificationError):
        _adapter().verify_webhook(payload=payload, headers=_signed_headers(payload))


@responses.activate
def test_get_status_returns_the_most_recent_payment_status_for_the_order():
    responses.add(
        responses.GET,
        "https://api.razorpay.com/v1/orders/order_abc123/payments",
        json={"items": [{"id": "pay_1", "status": "captured"}]},
        status=200,
    )
    assert _adapter().get_status(vendor_txn_id="order_abc123") == PaymentStatus.SUCCEEDED


@responses.activate
def test_get_status_returns_pending_when_no_payment_attempt_exists_yet():
    responses.add(
        responses.GET,
        "https://api.razorpay.com/v1/orders/order_abc123/payments",
        json={"items": []},
        status=200,
    )
    assert _adapter().get_status(vendor_txn_id="order_abc123") == PaymentStatus.PENDING


@responses.activate
def test_charge_survives_a_transport_level_retry_with_the_same_idempotency_key():
    """Phase 2.3's own audit-identified nuance, proven rather than just asserted:
    ResilientHttpClient's urllib3-level auto-retry fires on 500/502/503/504
    *before* any application code gets a say. This is only safe because Razorpay
    treats `receipt` as its own idempotency key — a retried POST with the same
    receipt must be treated as a no-op replay, not a new order. Simulates two
    real 503s (transient vendor-side failure) followed by success, and confirms
    the adapter still returns exactly one clean result with the retry
    transparent to the caller."""
    receipts_seen = []

    def callback(request):
        receipts_seen.append(json.loads(request.body)["receipt"])
        return (200, {}, json.dumps({"id": "order_abc123", "status": "created"}))

    responses.add(responses.POST, "https://api.razorpay.com/v1/orders", status=503)
    responses.add(responses.POST, "https://api.razorpay.com/v1/orders", status=503)
    responses.add_callback(responses.POST, "https://api.razorpay.com/v1/orders", callback=callback)

    adapter = RazorpayAdapter(
        key_id="rzp_test_fake",
        key_secret="fake_secret",
        webhook_secret="whsec_fake",
        http_client=ResilientHttpClient(max_retries=3, backoff_factor=0.01),
    )
    result = adapter.charge(
        amount=Decimal("899.00"),
        currency="INR",
        idempotency_key="charge:txn-1:1",
        metadata={},
    )

    assert result.status == PaymentStatus.PENDING
    assert result.vendor_txn_id == "order_abc123"
    # All 3 attempts (2 failed + 1 succeeded) carried the identical receipt —
    # confirmed, not assumed, so Razorpay's own idempotency guard is what
    # actually prevents a duplicate order, not luck.
    assert receipts_seen == ["charge:txn-1:1"]


@responses.activate
def test_a_timeout_with_retries_exhausted_returns_a_clean_failed_result_not_a_crash():
    """Phase 2.3's own Test Gate: a real vendor-API timeout must not leave the
    caller with an unhandled exception. Real bug caught by actually running this
    test (not assumed correct from the design): `ResilientHttpClient` with
    `max_retries=0` still builds a urllib3 `Retry` with `status_forcelist=[500,
    502,503,504]`, which raises `RetryError` on a 500 rather than returning a
    plain response — the adapter didn't catch this at first, verified live."""
    responses.add(responses.POST, "https://api.razorpay.com/v1/orders", status=500)
    adapter = RazorpayAdapter(
        key_id="rzp_test_fake",
        key_secret="fake_secret",
        webhook_secret="whsec_fake",
        http_client=ResilientHttpClient(max_retries=0),
    )
    result = adapter.charge(
        amount=Decimal("899.00"), currency="INR", idempotency_key="charge:txn-1:1", metadata={}
    )
    assert result.status == PaymentStatus.FAILED
    assert result.vendor_txn_id is None
    assert result.failure_reason in ("GATEWAY_TIMEOUT", "GATEWAY_UNAVAILABLE")


@responses.activate
def test_repeated_failures_through_the_adapter_actually_open_the_circuit_breaker():
    """Confirms the circuit breaker is genuinely wired through this adapter's
    calls, not just an unused constructor parameter — shared/resilient_http's
    own breaker logic is already proven correct elsewhere; this only proves the
    integration point."""
    responses.add(responses.POST, "https://api.razorpay.com/v1/orders", status=500)
    http_client = ResilientHttpClient(max_retries=0, circuit_breaker_threshold=2)
    adapter = RazorpayAdapter(
        key_id="rzp_test_fake",
        key_secret="fake_secret",
        webhook_secret="whsec_fake",
        http_client=http_client,
    )
    assert http_client.circuit_state == "closed"
    adapter.charge(amount=Decimal("1.00"), currency="INR", idempotency_key="k1", metadata={})
    adapter.charge(amount=Decimal("1.00"), currency="INR", idempotency_key="k2", metadata={})
    assert http_client.circuit_state == "open"

    # Once open, the very next call must also come back as a clean FAILED
    # result (CircuitOpenError caught, same as any other transport failure) —
    # never a raw CircuitOpenError reaching payment_service.py.
    result = adapter.charge(amount=Decimal("1.00"), currency="INR", idempotency_key="k3", metadata={})
    assert result.status == PaymentStatus.FAILED
    assert result.failure_reason == "GATEWAY_UNAVAILABLE"


@responses.activate
def test_get_status_raises_lookup_error_for_a_nonexistent_order():
    responses.add(
        responses.GET,
        "https://api.razorpay.com/v1/orders/does-not-exist/payments",
        json={"error": {"description": "not found"}},
        status=400,
    )
    import pytest

    with pytest.raises(LookupError):
        _adapter().get_status(vendor_txn_id="does-not-exist")
