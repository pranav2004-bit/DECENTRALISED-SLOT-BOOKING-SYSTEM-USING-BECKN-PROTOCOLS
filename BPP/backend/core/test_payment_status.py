"""livetracker5.md Phase 4.1 Test Gate (`INTEG`) — BPP-side payment-status
visibility: real /payment_status receipt (verified exactly like a real Beckn
action, though it isn't one — see core/payment_status_service.py's module
docstring), the real Booking.payment_status write (including the multi-resource
group case), and a real payment-status event genuinely reaching BPP's own live
Orders dashboard over the same WebSocket `BusinessOrdersConsumer` already proven
for `order.confirmed` (`test_business_orders.py`)."""

import datetime as dt
import json
import uuid

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.consumers import audit_log_consumer
from inventory_core.events import BookingEvent
from inventory_core.models import Booking, BookingAuditLogEntry, Resource, Slot

from core import payment_status_service
from core.crypto import generate_signing_key_pair, sign_outbound_request
from core.events import get_event_bus

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bus():
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


@pytest.fixture
def resource(db):
    owner = BusinessAccount.objects.create_user(
        contact="owner@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )
    return Resource.objects.create(owner_ref=str(owner.id), name="Stylist A")


def _make_booking(resource, *, holder_ref="txn-1", status=Booking.Status.ACTIVE):
    now = timezone.now()
    slot = Slot.objects.create(
        resource=resource,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=0,
    )
    return Booking.objects.create(slot=slot, holder_ref=holder_ref, status=status)


def _payload(*, transaction_id="txn-1", payment_status="SUCCEEDED", bap_id="bap.example.com"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "payment_status",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-09-25T00:00:00Z",
        },
        "message": {"payment_status": payment_status},
    }


def _lookup_callback(known_participants):
    def callback(request):
        filters = json.loads(request.body)
        entry = known_participants.get(filters["subscriber_id"])
        return (200, {}, json.dumps([entry] if entry else []))

    return callback


def _known(*, bap_pub):
    return {
        "bap.example.com": {
            "subscriber_id": "bap.example.com",
            "status": "SUBSCRIBED",
            "signing_public_key": bap_pub,
        }
    }


def _signed_body(payload, *, priv):
    body = json.dumps(payload).encode()
    header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=priv,
    )
    return body, header


# --- View: signature verification + ack/nack ---


@pytest.mark.django_db
def test_payment_status_view_acks_a_valid_signature_and_records_the_status(resource):
    booking = _make_booking(resource)
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(transaction_id=booking.holder_ref)
    body, header = _signed_body(payload, priv=bap_priv)
    known = _known(bap_pub=bap_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = Client().post(
            reverse("payment-status"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    booking.refresh_from_db()
    assert booking.payment_status == "SUCCEEDED"


@pytest.mark.django_db
def test_payment_status_view_rejects_an_invalid_signature(resource):
    booking = _make_booking(resource)
    payload = _payload(transaction_id=booking.holder_ref)
    body = json.dumps(payload).encode()

    resp = Client().post(
        reverse("payment-status"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION="Signature keyId=\"bap.example.com|key-1\",signature=\"bad\"",
    )

    assert resp.status_code == 401
    booking.refresh_from_db()
    assert booking.payment_status == ""


@pytest.mark.django_db
def test_payment_status_view_rejects_an_unrecognized_status_before_acking(resource):
    booking = _make_booking(resource)
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(transaction_id=booking.holder_ref, payment_status="PENDING")
    body, header = _signed_body(payload, priv=bap_priv)
    known = _known(bap_pub=bap_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = Client().post(
            reverse("payment-status"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"
    booking.refresh_from_db()
    assert booking.payment_status == ""


# --- record_payment_status: the real DB write + event publish ---


def test_record_payment_status_updates_every_booking_in_the_holder_ref_group(resource, bus):
    """A multi-resource confirm group (e.g. Automotive's bay+mechanic pair) shares
    one holder_ref — the same bulk-update precedent as `confirmed_total_value`."""
    booking_a = _make_booking(resource, holder_ref="txn-group")
    booking_b = _make_booking(resource, holder_ref="txn-group")

    payment_status_service.record_payment_status(payload=_payload(transaction_id="txn-group"))

    booking_a.refresh_from_db()
    booking_b.refresh_from_db()
    assert booking_a.payment_status == "SUCCEEDED"
    assert booking_b.payment_status == "SUCCEEDED"

    event = bus.consume_one(timeout_seconds=1)
    assert event is not None
    assert event["event_type"] == BookingEvent.PAYMENT_SUCCEEDED


def test_record_payment_status_drops_a_notification_for_an_unknown_transaction(db, bus):
    # Must not raise:
    payment_status_service.record_payment_status(payload=_payload(transaction_id="unknown-txn"))
    assert bus.consume_one(timeout_seconds=0.5) is None


def test_record_payment_status_publishes_the_refunded_event_type(resource, bus):
    booking = _make_booking(resource)
    payment_status_service.record_payment_status(
        payload=_payload(transaction_id=booking.holder_ref, payment_status="REFUNDED")
    )
    booking.refresh_from_db()
    assert booking.payment_status == "REFUNDED"
    event = bus.consume_one(timeout_seconds=1)
    assert event["event_type"] == BookingEvent.PAYMENT_REFUNDED


# --- Audit trail ---


def test_audit_log_consumer_records_a_payment_succeeded_event(resource):
    booking = _make_booking(resource)
    audit_log_consumer(
        {
            "event_id": str(uuid.uuid4()),
            "event_type": BookingEvent.PAYMENT_SUCCEEDED,
            "payload": {"version": 1, "booking_id": str(booking.id), "payment_status": "SUCCEEDED"},
        }
    )
    entry = BookingAuditLogEntry.objects.get(booking_id_text=str(booking.id))
    assert entry.event_type == BookingEvent.PAYMENT_SUCCEEDED
    assert entry.detail["payment_status"] == "SUCCEEDED"
