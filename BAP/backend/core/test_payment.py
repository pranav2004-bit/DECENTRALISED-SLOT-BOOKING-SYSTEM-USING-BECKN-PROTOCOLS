"""livetracker5.md Phase 1.2/1.3/1.6 Test Gates — real amount-of-record integrity,
idempotency (including the concurrent-request gate), and the placeholder's real
retirement (this file used to assert `PaymentNotYetImplementedError`; it now asserts
the real behavior that superseded it, per Phase 1.6's own Test Gate)."""

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch

import payment_gateway
import pytest
import responses
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse

from core import payment_service
from core.models import PaymentTransaction, SearchSession
from core.payment_service import PaymentError, get_payment_result, initiate_payment

Customer = get_user_model()


@pytest.fixture
def client():
    return Client(enforce_csrf_checks=True)


@pytest.fixture
def bap_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bap-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    yield settings


def _mock_bpp_registry_lookup(rsps, *, bpp_id="bpp.example.com", url="https://bpp.example.com"):
    def callback(request):
        filters = json.loads(request.body)
        assert filters["subscriber_id"] == bpp_id
        body = [{"subscriber_id": bpp_id, "status": "SUBSCRIBED", "url": url}]
        return (200, {}, json.dumps(body))

    rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=callback)


class _FakeAdapter(payment_gateway.PaymentGatewayAdapter):
    """Test-only adapter — never registered outside these tests. `outcome` lets a
    test choose what the "vendor" does without touching real network code."""

    vendor_code = "razorpay"

    def __init__(
        self,
        outcome=payment_gateway.PaymentStatus.SUCCEEDED,
        *,
        refund_outcome=payment_gateway.PaymentStatus.REFUNDED,
        refund_error: Exception | None = None,
    ):
        self.outcome = outcome
        self.refund_outcome = refund_outcome
        self.refund_error = refund_error
        self.charge_calls: list[str] = []
        self.refund_calls: list[dict] = []

    def charge(self, *, amount, currency, idempotency_key, metadata):
        self.charge_calls.append(idempotency_key)
        return payment_gateway.PaymentResult(
            status=self.outcome,
            vendor_txn_id=f"vendor-{idempotency_key}" if self.outcome != "FAILED" else None,
            failure_reason=None if self.outcome != "FAILED" else "DECLINED",
        )

    def refund(self, *, vendor_txn_id, amount, idempotency_key):
        self.refund_calls.append(
            {"vendor_txn_id": vendor_txn_id, "amount": amount, "idempotency_key": idempotency_key}
        )
        if self.refund_error is not None:
            raise self.refund_error
        return payment_gateway.RefundResult(
            status=self.refund_outcome,
            vendor_refund_id=f"refund-{idempotency_key}",
            refunded_amount=amount if self.refund_outcome != payment_gateway.PaymentStatus.FAILED else Decimal("0"),
            failure_reason=None if self.refund_outcome != payment_gateway.PaymentStatus.FAILED else "REFUND_DECLINED",
        )

    def verify_webhook(self, *, payload, headers):
        if headers.get("X-Fake-Signature") != "valid":
            raise payment_gateway.WebhookVerificationError("invalid signature")
        data = json.loads(payload)
        return payment_gateway.WebhookEvent(
            vendor_txn_id=data["vendor_txn_id"],
            status=payment_gateway.PaymentStatus(data["status"]),
            raw_payload=data,
        )

    def get_status(self, *, vendor_txn_id):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clean_payment_gateway_registry():
    payment_gateway.interface._REGISTRY.clear()
    yield
    payment_gateway.interface._REGISTRY.clear()


@pytest.fixture
def confirmed_session(db):
    return SearchSession.objects.create(
        transaction_id="txn-pay-1",
        query="haircut",
        domain="ONDC:RET13",
        confirmed_order={"quote": {"price": {"currency": "INR", "value": "899.00"}}},
    )


def test_initiate_payment_with_no_confirmed_order_raises_quote_unavailable(db):
    SearchSession.objects.create(transaction_id="txn-no-confirm", query="x", domain="ONDC:RET13")
    with pytest.raises(PaymentError) as exc_info:
        initiate_payment(transaction_id="txn-no-confirm")
    assert exc_info.value.code == "PAYMENT_QUOTE_UNAVAILABLE"


def test_initiate_payment_with_malformed_quote_raises_quote_unavailable(db):
    SearchSession.objects.create(
        transaction_id="txn-malformed",
        query="x",
        domain="ONDC:RET13",
        confirmed_order={"quote": {}},  # missing "price" entirely
    )
    with pytest.raises(PaymentError) as exc_info:
        initiate_payment(transaction_id="txn-malformed")
    assert exc_info.value.code == "PAYMENT_QUOTE_UNAVAILABLE"


def test_initiate_payment_with_no_vendor_registered_marks_failed_and_raises(confirmed_session):
    with pytest.raises(PaymentError) as exc_info:
        initiate_payment(transaction_id="txn-pay-1")
    assert exc_info.value.code == "PAYMENT_GATEWAY_UNAVAILABLE"

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.FAILED


def test_initiate_payment_never_accepts_amount_from_the_caller(confirmed_session):
    """Design Principle 2 / Phase 1.2's own Test Gate: the real charge amount always
    equals the session's stored confirmed quote — there is no `amount` parameter for
    a caller to even supply. This test documents that by construction: the function
    signature itself takes only `transaction_id`."""
    import inspect

    params = inspect.signature(initiate_payment).parameters
    assert set(params) == {"transaction_id", "customer"}


def test_initiate_payment_charges_successfully_and_records_the_real_amount(confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    result = initiate_payment(transaction_id="txn-pay-1")

    assert result["status"] == "SUCCEEDED"
    assert result["amount"] == "899.00"
    assert result["currency"] == "INR"
    assert result["vendor_txn_id"]

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.SUCCEEDED
    assert txn.succeeded_at is not None
    assert txn.amount == Decimal("899.00")


def test_initiate_payment_twice_does_not_double_charge(confirmed_session):
    adapter = _FakeAdapter()
    payment_gateway.register_adapter("razorpay", adapter)

    first = initiate_payment(transaction_id="txn-pay-1")
    second = initiate_payment(transaction_id="txn-pay-1")

    assert first == second
    assert len(adapter.charge_calls) == 1  # the second call never reached the gateway
    assert PaymentTransaction.objects.filter(transaction_id_text="txn-pay-1").count() == 1


def test_a_declined_charge_can_be_retried_as_a_genuinely_new_attempt(confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.FAILED))
    first = initiate_payment(transaction_id="txn-pay-1")
    assert first["status"] == "FAILED"

    # A genuine retry (not a network-level replay) must be able to charge again —
    # not be silently swallowed as "already resolved."
    payment_gateway.register_adapter("razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.SUCCEEDED))
    second = initiate_payment(transaction_id="txn-pay-1")
    assert second["status"] == "SUCCEEDED"

    rows = PaymentTransaction.objects.filter(transaction_id_text="txn-pay-1").order_by("created_at")
    assert rows.count() == 2
    assert rows[0].idempotency_key != rows[1].idempotency_key


def test_get_payment_result_returns_none_for_a_nonexistent_transaction(db):
    """Distinct code path from 'session exists but no payment yet' below — this one
    exercises resolve_owned_session's SessionAccessError(404) branch specifically,
    which was previously never actually exercised by a test."""
    assert get_payment_result(transaction_id="txn-does-not-exist") is None


def test_get_payment_result_returns_none_when_nothing_initiated_yet(confirmed_session):
    assert get_payment_result(transaction_id="txn-pay-1") is None


def test_get_payment_result_returns_the_current_state(confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    initiate_payment(transaction_id="txn-pay-1")
    result = get_payment_result(transaction_id="txn-pay-1")
    assert result["status"] == "SUCCEEDED"


# --- Webhook receipt (Phase 2.2) ---


def _webhook_payload(vendor_txn_id: str, status: str) -> bytes:
    return json.dumps({"vendor_txn_id": vendor_txn_id, "status": status}).encode()


def test_record_webhook_event_rejects_an_invalid_signature(confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    with pytest.raises(payment_gateway.WebhookVerificationError):
        payment_service.record_webhook_event(
            vendor="razorpay",
            payload=_webhook_payload("order-1", "SUCCEEDED"),
            headers={},
        )


def test_record_webhook_event_transitions_a_pending_transaction_to_succeeded(confirmed_session):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.PENDING  # hosted checkout not yet completed

    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "SUCCEEDED"),
        headers={"X-Fake-Signature": "valid"},
    )
    txn.refresh_from_db()
    assert txn.status == PaymentTransaction.Status.SUCCEEDED
    assert txn.succeeded_at is not None


def test_record_webhook_event_transitions_a_pending_transaction_to_failed(confirmed_session):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "FAILED"),
        headers={"X-Fake-Signature": "valid"},
    )
    txn.refresh_from_db()
    assert txn.status == PaymentTransaction.Status.FAILED
    assert txn.failed_at is not None


# --- Cancellation-triggered refund (Phase 4.2) ---


def test_refund_if_paid_is_a_no_op_when_nothing_was_ever_charged(confirmed_session):
    payment_service.refund_if_paid(transaction_id="txn-pay-1")
    assert PaymentTransaction.objects.filter(transaction_id_text="txn-pay-1").count() == 0


def test_refund_if_paid_is_a_no_op_for_a_failed_payment(confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.FAILED))
    initiate_payment(transaction_id="txn-pay-1")

    payment_service.refund_if_paid(transaction_id="txn-pay-1")

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.FAILED  # unchanged — nothing to refund


def test_refund_if_paid_refunds_a_succeeded_payment_in_full(confirmed_session):
    adapter = _FakeAdapter()
    payment_gateway.register_adapter("razorpay", adapter)
    initiate_payment(transaction_id="txn-pay-1")

    payment_service.refund_if_paid(transaction_id="txn-pay-1")

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.REFUNDED
    assert txn.refunded_amount == Decimal("899.00")
    assert txn.refunded_at is not None
    assert len(adapter.refund_calls) == 1
    assert adapter.refund_calls[0]["amount"] == Decimal("899.00")
    assert adapter.refund_calls[0]["idempotency_key"] == "refund:txn-pay-1:1"


def test_refund_if_paid_is_a_safe_no_op_when_called_twice(confirmed_session):
    """Duplicate /on_cancel delivery must never attempt (or double-count) a second
    real refund — the same 'duplicate is a safe no-op' discipline record_webhook_event
    already applies to duplicate webhook delivery."""
    adapter = _FakeAdapter()
    payment_gateway.register_adapter("razorpay", adapter)
    initiate_payment(transaction_id="txn-pay-1")

    payment_service.refund_if_paid(transaction_id="txn-pay-1")
    payment_service.refund_if_paid(transaction_id="txn-pay-1")

    assert len(adapter.refund_calls) == 1
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.REFUNDED


def test_refund_if_paid_leaves_succeeded_status_intact_when_the_vendor_declines_the_refund(confirmed_session):
    adapter = _FakeAdapter(refund_outcome=payment_gateway.PaymentStatus.FAILED)
    payment_gateway.register_adapter("razorpay", adapter)
    initiate_payment(transaction_id="txn-pay-1")

    payment_service.refund_if_paid(transaction_id="txn-pay-1")

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.SUCCEEDED  # left for manual reconciliation
    assert txn.refunded_amount == Decimal("0.00")


def test_refund_if_paid_leaves_succeeded_status_intact_when_the_refund_call_raises(confirmed_session):
    adapter = _FakeAdapter(refund_error=ConnectionError("vendor unreachable"))
    payment_gateway.register_adapter("razorpay", adapter)
    initiate_payment(transaction_id="txn-pay-1")

    payment_service.refund_if_paid(transaction_id="txn-pay-1")  # must not raise

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.status == PaymentTransaction.Status.SUCCEEDED


def test_refund_if_paid_is_a_no_op_when_no_adapter_is_registered(db):
    """Proves the missing-adapter branch logs and returns cleanly rather than raising
    into the webhook caller (cancel_service.record_on_cancel_result has no try/except
    around this call by design)."""
    SearchSession.objects.create(
        transaction_id="txn-pay-no-adapter",
        query="haircut",
        domain="ONDC:RET13",
        confirmed_order={"quote": {"price": {"currency": "INR", "value": "500.00"}}},
    )
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    initiate_payment(transaction_id="txn-pay-no-adapter")
    payment_gateway.interface._REGISTRY.clear()  # simulate the adapter going away

    payment_service.refund_if_paid(transaction_id="txn-pay-no-adapter")  # must not raise

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-no-adapter")
    assert txn.status == PaymentTransaction.Status.SUCCEEDED


# --- PaymentTransaction.transition_status's refunded_amount guard (Phase 4.2) ---


def test_transition_status_to_refunded_requires_a_refunded_amount(confirmed_session):
    txn = PaymentTransaction.objects.create(
        session=confirmed_session,
        transaction_id_text="txn-guard-1",
        amount=Decimal("100.00"),
        currency="INR",
        vendor="razorpay",
        idempotency_key="charge:txn-guard-1:1",
        status=PaymentTransaction.Status.SUCCEEDED,
    )
    with pytest.raises(ValueError):
        txn.transition_status(PaymentTransaction.Status.REFUNDED)


def test_transition_status_rejects_a_refunded_amount_for_a_non_refund_transition(confirmed_session):
    txn = PaymentTransaction.objects.create(
        session=confirmed_session,
        transaction_id_text="txn-guard-2",
        amount=Decimal("100.00"),
        currency="INR",
        vendor="razorpay",
        idempotency_key="charge:txn-guard-2:1",
        status=PaymentTransaction.Status.PENDING,
    )
    with pytest.raises(ValueError):
        txn.transition_status(PaymentTransaction.Status.SUCCEEDED, refunded_amount=Decimal("50.00"))


def test_payment_trigger_view_deduplicates_a_repeated_idempotency_key(client, confirmed_session):
    """livetracker5.md Phase 3.1's own explicit requirement: `@idempotent_view()`
    guards the browser's own POST from ever reaching the view logic twice — the
    web-layer twin of Phase 1.3's own PaymentTransaction-based guarantee, proven
    the same way `confirm_trigger_view`'s identical test already does."""
    adapter = _FakeAdapter()
    payment_gateway.register_adapter("razorpay", adapter)
    client.get(reverse("csrf-token"))
    csrf_token = client.cookies["csrftoken"].value

    first = client.post(
        reverse("payment-trigger"),
        data=json.dumps({"transaction_id": "txn-pay-1"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_IDEMPOTENCY_KEY="web-retry-key-1",
    )
    second = client.post(
        reverse("payment-trigger"),
        data=json.dumps({"transaction_id": "txn-pay-1"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
        HTTP_IDEMPOTENCY_KEY="web-retry-key-1",
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    assert len(adapter.charge_calls) == 1


def test_record_webhook_event_drops_a_webhook_for_an_unknown_transaction(db):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    # Must not raise — same "log and drop" discipline as record_on_confirm_result
    # for an unrecognized transaction_id.
    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload("order-does-not-exist", "SUCCEEDED"),
        headers={"X-Fake-Signature": "valid"},
    )


def test_record_webhook_event_is_a_safe_no_op_on_duplicate_delivery(confirmed_session):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    headers = {"X-Fake-Signature": "valid"}
    payload = _webhook_payload(txn.vendor_txn_id, "SUCCEEDED")
    payment_service.record_webhook_event(vendor="razorpay", payload=payload, headers=headers)
    # Razorpay's own documented retry-with-backoff policy means genuine duplicate
    # delivery is expected — a second identical webhook must not raise (it would,
    # if this naively called transition_status again: SUCCEEDED -> SUCCEEDED is
    # not a valid edge).
    payment_service.record_webhook_event(vendor="razorpay", payload=payload, headers=headers)

    txn.refresh_from_db()
    assert txn.status == PaymentTransaction.Status.SUCCEEDED


def test_record_webhook_event_ignores_a_stale_webhook_against_a_resolved_transaction(
    confirmed_session,
):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "SUCCEEDED"),
        headers={"X-Fake-Signature": "valid"},
    )
    # A late/out-of-order FAILED webhook arriving after SUCCEEDED must not
    # silently overwrite the already-resolved state.
    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "FAILED"),
        headers={"X-Fake-Signature": "valid"},
    )
    txn.refresh_from_db()
    assert txn.status == PaymentTransaction.Status.SUCCEEDED


def test_razorpay_webhook_view_rejects_an_invalid_signature(client, confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    response = client.post(
        reverse("razorpay-webhook"),
        data=_webhook_payload("order-1", "SUCCEEDED"),
        content_type="application/json",
    )
    assert response.status_code == 401


def test_razorpay_webhook_view_accepts_a_valid_signature_and_updates_the_transaction(
    client, confirmed_session
):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    response = client.post(
        reverse("razorpay-webhook"),
        data=_webhook_payload(txn.vendor_txn_id, "SUCCEEDED"),
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE="valid",
    )
    assert response.status_code == 200
    txn.refresh_from_db()
    assert txn.status == PaymentTransaction.Status.SUCCEEDED


def test_razorpay_webhook_view_returns_503_when_no_adapter_is_registered(client):
    response = client.post(
        reverse("razorpay-webhook"),
        data=_webhook_payload("order-1", "SUCCEEDED"),
        content_type="application/json",
    )
    assert response.status_code == 503


@pytest.mark.django_db(transaction=True)
def test_concurrent_initiate_payment_for_the_same_transaction_produces_exactly_one_charge(
    django_db_blocker,
):
    """Phase 1.3's own Test Gate: N simultaneous charge attempts for the same
    transaction must produce exactly 1 real gateway charge, not N."""
    with django_db_blocker.unblock():
        SearchSession.objects.create(
            transaction_id="txn-concurrent",
            query="x",
            domain="ONDC:RET13",
            confirmed_order={"quote": {"price": {"currency": "INR", "value": "500.00"}}},
        )
        adapter = _FakeAdapter()
        payment_gateway.register_adapter("razorpay", adapter)

    n_attempts = 10
    results = []

    def attempt():
        try:
            results.append(initiate_payment(transaction_id="txn-concurrent"))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=n_attempts) as executor:
        futures = [executor.submit(attempt) for _ in range(n_attempts)]
        for future in futures:
            future.result()

    assert len(adapter.charge_calls) == 1
    with django_db_blocker.unblock():
        assert PaymentTransaction.objects.filter(transaction_id_text="txn-concurrent").count() == 1


# --- HTTP-level tests through the real view/URL layer (Phase 1.2's own Test Gate:
# "a crafted request that includes an amount field ... confirm the actual charge
# amount sent to the adapter always equals SearchSession.confirmed_order's stored
# quote, regardless of what the request body contained") — every test above calls
# payment_service directly, which structurally can't accept a spoofed amount at all;
# this proves the same holds true through the real HTTP request path, not just the
# service function's own signature.


def test_payment_trigger_view_ignores_a_spoofed_amount_field(client, confirmed_session):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    client.get(reverse("csrf-token"))
    csrf_token = client.cookies["csrftoken"].value

    response = client.post(
        reverse("payment-trigger"),
        data=json.dumps({"transaction_id": "txn-pay-1", "amount": "1.00", "currency": "USD"}),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf_token,
    )
    assert response.status_code == 202
    body = response.json()

    # The real confirmed quote (899.00 INR), never the crafted 1.00 USD.
    assert body["amount"] == "899.00"
    assert body["currency"] == "INR"

    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")
    assert txn.amount == Decimal("899.00")
    assert txn.currency == "INR"


def test_payment_trigger_view_rejects_a_request_with_no_csrf_token(client, confirmed_session):
    """Phase 1.2's own audit-identified correction: this endpoint is deliberately
    NOT @csrf_exempt (unlike the Beckn network-callback views), since it's a real
    same-origin browser POST that moves money. A request with no CSRF token at all
    must be rejected by Django's own CsrfViewMiddleware, not silently accepted."""
    response = client.post(
        reverse("payment-trigger"),
        data=json.dumps({"transaction_id": "txn-pay-1"}),
        content_type="application/json",
    )
    assert response.status_code == 403
    assert PaymentTransaction.objects.filter(transaction_id_text="txn-pay-1").exists() is False


# --- BPP payment-status visibility (livetracker5.md Phase 4.1) ---


@pytest.fixture
def confirmed_session_with_bpp(db):
    return SearchSession.objects.create(
        transaction_id="txn-pay-1",
        query="haircut",
        domain="ONDC:RET13",
        confirmed_order={"quote": {"price": {"currency": "INR", "value": "899.00"}}},
        selected_bpp_id="bpp.example.com",
        selected_bpp_uri="https://bpp.example.com",
    )


def test_notify_bpp_of_payment_status_sends_a_real_signed_notification(
    bap_identity_settings, confirmed_session_with_bpp
):
    captured_requests = []

    def bpp_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/payment_status", callback=bpp_callback
        )
        payment_service._notify_bpp_of_payment_status(transaction_id="txn-pay-1", status="SUCCEEDED")

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "payment_status"
    assert forwarded["context"]["transaction_id"] == "txn-pay-1"
    assert forwarded["message"]["payment_status"] == "SUCCEEDED"
    assert "Authorization" in captured_requests[0].headers


def test_notify_bpp_of_payment_status_is_a_safe_no_op_without_a_selected_bpp(confirmed_session):
    """`confirmed_session` (unlike `confirmed_session_with_bpp`) has no selected_bpp_id
    set — must not raise, must not attempt any network call."""
    payment_service._notify_bpp_of_payment_status(transaction_id="txn-pay-1", status="SUCCEEDED")


def test_notify_bpp_of_payment_status_is_a_safe_no_op_for_an_unknown_transaction(db):
    payment_service._notify_bpp_of_payment_status(transaction_id="txn-does-not-exist", status="SUCCEEDED")


def test_notify_bpp_of_payment_status_swallows_a_network_failure(
    bap_identity_settings, confirmed_session_with_bpp
):
    """The whole body runs under one try/except (matching notifications.py's own
    discipline) — an unreachable BPP must never raise into the caller."""
    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add(responses.POST, "https://bpp.example.com/payment_status", status=502)
        payment_service._notify_bpp_of_payment_status(transaction_id="txn-pay-1", status="SUCCEEDED")


@patch("core.payment_service.notify_bpp_of_payment_status_in_background")
def test_record_webhook_event_does_not_notify_bpp_of_a_failed_webhook(
    mock_notify, confirmed_session_with_bpp
):
    """BPP doesn't need to know about a FAILED charge — it was never committed
    revenue from BPP's own perspective (unlike the customer's own WS broadcast,
    which fires for both)."""
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "FAILED"),
        headers={"X-Fake-Signature": "valid"},
    )
    mock_notify.assert_not_called()


@patch("core.payment_service.notify_bpp_of_payment_status_in_background")
def test_record_webhook_event_notifies_bpp_of_a_succeeded_webhook(
    mock_notify, confirmed_session_with_bpp
):
    payment_gateway.register_adapter(
        "razorpay", _FakeAdapter(outcome=payment_gateway.PaymentStatus.PENDING)
    )
    initiate_payment(transaction_id="txn-pay-1")
    txn = PaymentTransaction.objects.get(transaction_id_text="txn-pay-1")

    payment_service.record_webhook_event(
        vendor="razorpay",
        payload=_webhook_payload(txn.vendor_txn_id, "SUCCEEDED"),
        headers={"X-Fake-Signature": "valid"},
    )
    mock_notify.assert_called_once_with(transaction_id="txn-pay-1", status="SUCCEEDED")


@patch("core.payment_service.notify_bpp_of_payment_status_in_background")
def test_refund_if_paid_notifies_bpp_of_the_refund(mock_notify, confirmed_session_with_bpp):
    payment_gateway.register_adapter("razorpay", _FakeAdapter())
    initiate_payment(transaction_id="txn-pay-1")
    mock_notify.reset_mock()  # initiate_payment's own SUCCEEDED notify isn't what this test covers

    payment_service.refund_if_paid(transaction_id="txn-pay-1")

    mock_notify.assert_called_once_with(transaction_id="txn-pay-1", status="REFUNDED")
