"""Phase 4.5 Test Gate (livetracker2.md §4.5) pieces owned by BPP: real /support
receipt (verifying both the BAP and the forwarding Gateway, and that
message.support.ref_id is present) and real /on_support dispatch — resolving
the owning business's real contact info for a genuinely completed booking,
including the IDOR-shaped holder_ref-mismatch rejection.
"""

import datetime as dt
import json
from unittest.mock import patch

import pytest
import redis
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import complete_active_booking, confirm_hold, hold_slot

from core import support_service
from core.crypto import generate_signing_key_pair, sign_outbound_request

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bpp_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bpp-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bpp-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_slot_and_business(*, contact, ended=False):
    business = BusinessAccount.objects.create_user(
        contact=contact, business_name="Glow Salon", password=TEST_PASSWORD
    )
    resource = Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        category_id="ONDC:RET13",
        price_currency="INR",
        price_value="899.00",
    )
    if ended:
        start_time = timezone.now() - dt.timedelta(hours=2)
        end_time = timezone.now() - dt.timedelta(hours=1)
    else:
        start_time = timezone.now().replace(microsecond=0)
        end_time = start_time + dt.timedelta(minutes=30)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=end_time,
        capacity_total=1,
        capacity_remaining=1,
    )
    return business, resource, slot


def _make_completed_booking(*, holder_ref="txn-1", contact="owner@example.com"):
    business, resource, slot = _make_slot_and_business(contact=contact, ended=True)
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    confirm_hold(booking.id, redis_client=_redis_client())
    assert complete_active_booking(booking.id) is True
    booking.refresh_from_db()
    assert booking.status == Booking.Status.COMPLETE
    return business, resource, slot, booking


def _build_support_payload(*, ref_id, bap_id="bap.example.com", transaction_id="txn-1"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "support",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"support": {"ref_id": str(ref_id)}},
    }


def _lookup_callback(known_participants):
    def callback(request):
        filters = json.loads(request.body)
        subscriber_id = filters["subscriber_id"]
        entry = known_participants.get(subscriber_id)
        return (200, {}, json.dumps([entry] if entry else []))

    return callback


def _known(*, bap_pub=None, gateway_pub=None):
    known = {}
    if bap_pub is not None:
        known["bap.example.com"] = {
            "subscriber_id": "bap.example.com",
            "status": "SUBSCRIBED",
            "signing_public_key": bap_pub,
        }
    if gateway_pub is not None:
        known["gateway.local"] = {
            "subscriber_id": "gateway.local",
            "status": "SUBSCRIBED",
            "signing_public_key": gateway_pub,
        }
    return known


@pytest.mark.django_db
def test_support_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_support_payload(ref_id="11111111-1111-1111-1111-111111111111")
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with (
        patch("core.support_service.dispatch_on_support_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("support"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_support_view_rejects_a_missing_ref_id_before_acking(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {"context": _build_support_payload(ref_id="x")["context"], "message": {}}
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body,
        subscriber_id="gateway.local",
        unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("support"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_support_returns_the_owning_businesss_email_contact(bpp_identity_settings):
    business, resource, slot, booking = _make_completed_booking(
        holder_ref="txn-1", contact="owner@example.com"
    )
    payload = _build_support_payload(ref_id=booking.id, transaction_id="txn-1")

    captured_requests = []

    def gateway_on_support_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_support",
            callback=gateway_on_support_callback,
        )
        support_service.dispatch_on_support(payload=payload)

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert "error" not in forwarded
    assert forwarded["message"]["support"]["ref_id"] == str(booking.id)
    assert forwarded["message"]["support"]["email"] == "owner@example.com"
    assert "phone" not in forwarded["message"]["support"]


@pytest.mark.django_db
def test_dispatch_on_support_returns_the_owning_businesss_phone_contact(bpp_identity_settings):
    business, resource, slot, booking = _make_completed_booking(
        holder_ref="txn-1", contact="+911234567890"
    )
    payload = _build_support_payload(ref_id=booking.id, transaction_id="txn-1")

    captured_requests = []

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_support",
            callback=lambda r: (captured_requests.append(r), (200, {}, json.dumps({})))[1],
        )
        support_service.dispatch_on_support(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["message"]["support"]["phone"] == "+911234567890"
    assert "email" not in forwarded["message"]["support"]


@pytest.mark.django_db
def test_dispatch_on_support_rejects_a_booking_held_by_a_different_transaction(
    bpp_identity_settings,
):
    business, resource, slot, booking = _make_completed_booking(holder_ref="txn-owner")
    payload = _build_support_payload(ref_id=booking.id, transaction_id="txn-attacker")

    captured_requests = []

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_support",
            callback=lambda r: (captured_requests.append(r), (200, {}, json.dumps({})))[1],
        )
        support_service.dispatch_on_support(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"
