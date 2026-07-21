"""Phase 3.4 Test Gate (livetracker2.md §3.4) pieces owned by BAP: real /confirm
trigger (customer-facing, non-Beckn shape, targets the same BPP a prior successful
/init already resolved to), the result poll, and real /on_confirm receipt
(verifying both the BPP and the forwarding Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core import confirm_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.models import SearchSession


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bap_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bap-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _session_with_init(*, transaction_id="txn-1", bpp_id="bpp.example.com"):
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="haircut", domain="ONDC:RET13"
    )
    session.selected_order = {
        "provider": {"id": "biz-1"},
        "items": [{"id": "item-1"}],
        "fulfillments": [{"id": "booking-1", "stops": [{"type": "start"}]}],
        "quote": {"price": {"currency": "INR", "value": "500.00"}},
    }
    session.selected_bpp_id = bpp_id
    session.selected_bpp_uri = f"https://{bpp_id}"
    session.init_order = {
        "provider": {"id": "biz-1"},
        "items": [{"id": "item-1"}],
        "fulfillments": [{"id": "booking-1", "stops": [{"type": "start"}]}],
        "quote": {
            "price": {"currency": "INR", "value": "500.00"},
            "breakup": [
                {
                    "item": {"id": "item-1"},
                    "title": "Stylist A",
                    "price": {"currency": "INR", "value": "500.00"},
                }
            ],
            "ttl": "PT600S",
        },
    }
    session.save()
    return session


def _build_on_confirm_payload(
    *, bap_id="bap-backend.local", bpp_id="bpp.example.com", error=None
):
    payload = {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "on_confirm",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "id": "booking-1",
                "status": "ACTIVE",
                "provider": {"id": "biz-1"},
                "items": [{"id": "item-1"}],
                "fulfillments": [{"id": "booking-1"}],
                "quote": {
                    "price": {"currency": "INR", "value": "500.00"},
                    "breakup": [
                        {
                            "item": {"id": "item-1"},
                            "title": "Stylist A",
                            "price": {"currency": "INR", "value": "500.00"},
                        }
                    ],
                },
                "payments": [{"status": "NOT-PAID"}],
            }
        },
    }
    if error is not None:
        payload["error"] = error
    return payload


def _known(*, bpp_pub=None, gateway_pub=None):
    known = {}
    if bpp_pub is not None:
        known["bpp.example.com"] = {
            "subscriber_id": "bpp.example.com",
            "status": "SUBSCRIBED",
            "signing_public_key": bpp_pub,
        }
    if gateway_pub is not None:
        known["gateway.local"] = {
            "subscriber_id": "gateway.local",
            "status": "SUBSCRIBED",
            "signing_public_key": gateway_pub,
        }
    return known


def _lookup_callback(known_participants):
    def callback(request):
        filters = json.loads(request.body)
        subscriber_id = filters["subscriber_id"]
        entry = known_participants.get(subscriber_id)
        return (200, {}, json.dumps([entry] if entry else []))

    return callback


@pytest.mark.django_db
def test_trigger_confirm_targets_the_same_bpp_from_init_and_sends_a_real_signed_order(
    bap_identity_settings, client
):
    """FUNC (the core §3.4 flow, BAP's side): confirming after a real successful
    initialization reaches Gateway as a real, signed Beckn Order targeting the
    exact same BPP that /init already resolved to — the resent order drops the
    stale quote (the BPP recomputes it fresh one final time)."""
    _session_with_init()
    captured_requests = []

    def gateway_confirm_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/confirm", callback=gateway_confirm_callback
        )
        resp = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )

    assert resp.status_code == 202
    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "confirm"
    assert forwarded["context"]["bap_id"] == "bap-backend.local"
    assert forwarded["context"]["bpp_id"] == "bpp.example.com"
    assert forwarded["context"]["bpp_uri"] == "https://bpp.example.com"
    order = forwarded["message"]["order"]
    assert order["provider"]["id"] == "biz-1"
    assert order["items"][0]["id"] == "item-1"
    assert order["fulfillments"][0]["id"] == "booking-1"
    assert "quote" not in order
    assert "Authorization" in captured_requests[0].headers


@pytest.mark.django_db
def test_trigger_confirm_is_idempotent_on_repeat_with_the_same_key(bap_identity_settings, client):
    """FUNC (§3.6): a flaky-connection retry of the confirm POST, carrying the same
    real Idempotency-Key header, must not fire a second real Beckn /confirm at
    Gateway — the exact recorded response is replayed instead. Proven by asserting
    Gateway's /confirm was hit exactly once despite two identical POSTs."""
    from django.core.cache import cache

    cache.clear()
    _session_with_init()
    captured_requests = []

    def gateway_confirm_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/confirm", callback=gateway_confirm_callback
        )
        first = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="retry-key-1",
        )
        second = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="retry-key-1",
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json() == second.json()
    assert len(captured_requests) == 1


@pytest.mark.django_db
def test_trigger_confirm_without_idempotency_key_is_not_deduplicated(
    bap_identity_settings, client
):
    """The header is opt-in (API_CONVENTIONS.md) — two requests with no key at all
    are two independent real attempts, each forwarded to Gateway, same as before
    this phase."""
    from django.core.cache import cache

    cache.clear()
    _session_with_init()
    captured_requests = []

    def gateway_confirm_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/confirm", callback=gateway_confirm_callback
        )
        client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )
        client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )

    assert len(captured_requests) == 2


@pytest.mark.django_db
def test_trigger_confirm_retryable_failure_is_not_cached_so_a_real_retry_can_succeed(
    bap_identity_settings, client
):
    """NEG (§3.6): a transient 502 (Gateway unreachable) must NOT be cached under
    the Idempotency-Key — that response is `retryable: true`, so a genuine retry
    with the same key must actually re-attempt the real call, not be stuck
    replaying the stale failure. Proven by having the first call fail (Gateway
    connection refused, no mock registered) and the second, same-key call succeed
    once Gateway is reachable."""
    from django.core.cache import cache

    cache.clear()
    _session_with_init()

    with responses.RequestsMock():
        # No callback registered for /confirm -> requests raises ConnectionError,
        # trigger_confirm wraps it as ConfirmError(status_code=502).
        first = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="retry-key-2",
        )
    assert first.status_code == 502
    assert first.json()["error"]["retryable"] is True

    captured_requests = []

    def gateway_confirm_callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/confirm", callback=gateway_confirm_callback
        )
        second = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="retry-key-2",
        )

    assert second.status_code == 202
    assert len(captured_requests) == 1


@pytest.mark.django_db
def test_trigger_confirm_view_rejects_a_transaction_with_no_successful_init(
    bap_identity_settings, client
):
    """NEG: a transaction that never had a successful /init has nothing real to
    confirm — rejected, not silently forwarded with empty/garbage fields."""
    SearchSession.objects.create(transaction_id="txn-1", query="haircut", domain="ONDC:RET13")
    resp = client.post(
        reverse("confirm-trigger"),
        data=json.dumps({"transaction_id": "txn-1"}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_trigger_confirm_view_rejects_missing_fields(client):
    resp = client.post(
        reverse("confirm-trigger"),
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_confirm_result_view_returns_404_for_an_unknown_transaction(client):
    resp = client.get(reverse("confirm-result", args=["nonexistent-txn"]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_confirm_result_view_returns_the_recorded_order(client):
    session = _session_with_init()
    session.confirmed_order = {"status": "ACTIVE"}
    session.save()

    resp = client.get(reverse("confirm-result", args=["txn-1"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmed_order"] == session.confirmed_order
    assert body["confirmed_error"] is None


@pytest.mark.django_db
@patch("core.confirm_service.record_on_confirm_result")
def test_on_confirm_view_acks_when_both_bpp_and_gateway_signatures_are_valid(mock_record, client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_on_confirm_payload()
    body = json.dumps(payload).encode()

    bpp_header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bpp_pub=bpp_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_record.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_on_confirm_view_rejects_missing_gateway_authorization(client):
    bpp_pub, bpp_priv = generate_signing_key_pair()
    payload = _build_on_confirm_payload()
    body = json.dumps(payload).encode()
    bpp_header = sign_outbound_request(
        body=body,
        subscriber_id="bpp.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bpp_priv,
    )
    known = _known(bpp_pub=bpp_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("on_confirm"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bpp_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_record_on_confirm_result_stores_the_real_confirmed_order():
    _session_with_init()
    payload = _build_on_confirm_payload()

    confirm_service.record_on_confirm_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.confirmed_order["status"] == "ACTIVE"
    assert session.confirmed_order["payments"] == [{"status": "NOT-PAID"}]
    assert session.confirmed_error is None


@pytest.mark.django_db
def test_record_on_confirm_result_stores_a_real_error_instead_of_an_order():
    """NEG: the §3.4 Test Gate's rejection case — a real SLOT_UNAVAILABLE error
    from the BPP (an expired hold, or the IDOR-shaped rejection) is recorded as an
    error, not silently swallowed or misread as a success."""
    _session_with_init()
    payload = _build_on_confirm_payload(
        error={"code": "SLOT_UNAVAILABLE", "message": "No matching booking for this order"}
    )

    confirm_service.record_on_confirm_result(payload=payload)

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.confirmed_error == {
        "code": "SLOT_UNAVAILABLE",
        "message": "No matching booking for this order",
    }
    assert session.confirmed_order is None


@pytest.mark.django_db
def test_record_on_confirm_result_drops_a_callback_for_an_unknown_transaction():
    payload = _build_on_confirm_payload()
    payload["context"]["transaction_id"] = "unknown-txn"
    # must not raise:
    confirm_service.record_on_confirm_result(payload=payload)
