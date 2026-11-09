"""Test Gate for core/availability_service.py — the direct, signed BAP->BPP
check_availability call (see that module's own docstring for why it answers
synchronously instead of deferring to an on_check_availability callback)."""

import datetime as dt
import json
import uuid
from zoneinfo import ZoneInfo

import pytest
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Resource, Slot

from core.crypto import generate_signing_key_pair, sign_outbound_request

BusinessAccount = get_user_model()

TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret
HEALTHCARE = settings.DOMAIN_HEALTHCARE
INDIA_TZ = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def business(db):
    return BusinessAccount.objects.create_user(
        contact="hospital@example.com",
        business_name="Pavan Group Of Hospitals",
        password=TEST_PASSWORD,
        domain_code=HEALTHCARE,
        is_accepting_bookings=True,
    )


@pytest.fixture
def doctor(business):
    return Resource.objects.create(owner_ref=str(business.id), name="Dr. Anoop")


def _today_ist() -> dt.date:
    return timezone.now().astimezone(INDIA_TZ).date()


def _later_today(now, fraction):
    """A fixed `now + Nh` offset flakes whenever the suite runs within N hours of
    IST midnight — the slot rolls into tomorrow and _today_ist() no longer sees
    it (found live 2026-11-08, re-found live 2026-11-09 in sibling tests this
    helper didn't yet cover). Scaling to a fraction of whatever time actually
    remains before IST midnight keeps the slot inside "today" no matter when the
    suite runs."""
    end_of_today_ist = dt.datetime.combine(
        now.astimezone(INDIA_TZ).date(), dt.time(23, 59), tzinfo=INDIA_TZ
    )
    return now + (end_of_today_ist - now) * fraction


def _payload(*, resource_ids, date, bap_id="bap.example.com", domain=HEALTHCARE):
    return {
        "context": {
            "domain": domain,
            "location": {"country": {"code": "IND"}},
            "action": "check_availability",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-09-25T00:00:00Z",
        },
        "message": {"resource_ids": resource_ids, "date": date},
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


def _run(payload, *, bap_priv, bap_pub):
    body, header = _signed_body(payload, priv=bap_priv)
    known = _known(bap_pub=bap_pub)
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        return Client().post(
            reverse("check-availability"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )


@pytest.mark.django_db
def test_true_for_a_resource_with_a_real_open_slot_today(doctor):
    now = timezone.now()
    start_time = _later_today(now, 0.25)
    Slot.objects.create(
        resource=doctor,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    assert resp.json()["message"]["availability"] == {str(doctor.id): True}


@pytest.mark.django_db
def test_false_for_a_slot_that_already_ended_today(doctor):
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now - dt.timedelta(hours=3),
        end_time=now - dt.timedelta(hours=2),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    assert resp.json()["message"]["availability"] == {str(doctor.id): False}


@pytest.mark.django_db
def test_false_for_a_held_slot(doctor):
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=2),
        capacity_total=1,
        capacity_remaining=1,
        status=Slot.Status.HELD,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["availability"] == {str(doctor.id): False}


@pytest.mark.django_db
def test_false_for_a_fully_booked_slot_with_zero_capacity_remaining(doctor):
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=2),
        capacity_total=1,
        capacity_remaining=0,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["availability"] == {str(doctor.id): False}


@pytest.mark.django_db
def test_true_for_a_future_date_with_a_slot_that_day(doctor):
    future_day = _today_ist() + dt.timedelta(days=5)
    start_local = dt.datetime.combine(future_day, dt.time(10, 0), tzinfo=INDIA_TZ)
    Slot.objects.create(
        resource=doctor,
        start_time=start_local.astimezone(ZoneInfo("UTC")),
        end_time=(start_local + dt.timedelta(hours=1)).astimezone(ZoneInfo("UTC")),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=future_day.isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["availability"] == {str(doctor.id): True}


@pytest.mark.django_db
def test_false_for_an_unknown_resource_id_mixed_into_a_valid_batch(doctor):
    now = timezone.now()
    start_time = _later_today(now, 0.25)
    Slot.objects.create(
        resource=doctor,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    unknown_id = str(uuid.uuid4())
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id), unknown_id], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    assert resp.json()["message"]["availability"] == {str(doctor.id): True, unknown_id: False}


@pytest.mark.django_db
def test_false_for_a_resource_whose_business_is_not_accepting_bookings(business, doctor):
    business.is_accepting_bookings = False
    business.save(update_fields=["is_accepting_bookings"])
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=2),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["availability"] == {str(doctor.id): False}


@pytest.mark.django_db
def test_rejects_an_invalid_date_format(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _payload(resource_ids=[str(doctor.id)], date="not-a-date")

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_rejects_a_date_in_the_past(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    past_day = (_today_ist() - dt.timedelta(days=1)).isoformat()
    payload = _payload(resource_ids=[str(doctor.id)], date=past_day)

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 400


@pytest.mark.django_db
def test_rejects_too_many_resource_ids(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    too_many = [str(uuid.uuid4()) for _ in range(51)]
    payload = _payload(resource_ids=too_many, date=_today_ist().isoformat())

    resp = _run(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 400


@pytest.mark.django_db
def test_rejects_an_invalid_signature(doctor):
    payload = _payload(resource_ids=[str(doctor.id)], date=_today_ist().isoformat())
    body = json.dumps(payload).encode()

    resp = Client().post(
        reverse("check-availability"),
        data=body,
        content_type="application/json",
        HTTP_AUTHORIZATION='Signature keyId="bap.example.com|key-1",signature="bad"',
    )

    assert resp.status_code == 401


# --- list_available_slots: the real time-of-day picker for /select ---


def _slots_payload(*, resource_id, date, bap_id="bap.example.com", domain=HEALTHCARE):
    return {
        "context": {
            "domain": domain,
            "location": {"country": {"code": "IND"}},
            "action": "list_available_slots",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-09-25T00:00:00Z",
        },
        "message": {"resource_id": resource_id, "date": date},
    }


def _run_slots(payload, *, bap_priv, bap_pub):
    body, header = _signed_body(payload, priv=bap_priv)
    known = _known(bap_pub=bap_pub)
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        return Client().post(
            reverse("list-available-slots"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=header,
        )


@pytest.mark.django_db
def test_slots_lists_real_open_windows_in_order(doctor):
    now = timezone.now()
    # Fixed +1h/+4h offsets used to flake whenever this ran within ~4h of IST
    # midnight (the later slot rolled into tomorrow, so _today_ist() only ever
    # saw the earlier one) — found live 2026-11-08. Both offsets are now a
    # fraction of whatever time actually remains before IST midnight, so they
    # always land inside "today" no matter when the suite runs.
    end_of_today_ist = dt.datetime.combine(
        now.astimezone(INDIA_TZ).date(), dt.time(23, 59), tzinfo=INDIA_TZ
    )
    remaining = end_of_today_ist - now
    later = Slot.objects.create(
        resource=doctor,
        start_time=now + remaining / 2,
        end_time=now + remaining / 2 + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    earlier = Slot.objects.create(
        resource=doctor,
        start_time=now + remaining / 4,
        end_time=now + remaining / 4 + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    slots = resp.json()["message"]["slots"]
    assert len(slots) == 2
    assert slots[0]["start_time"] == earlier.start_time.isoformat()
    assert slots[0]["status"] == "available"
    assert slots[1]["start_time"] == later.start_time.isoformat()
    assert slots[1]["status"] == "available"


@pytest.mark.django_db
def test_slots_excludes_a_slot_that_already_ended(doctor):
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now - dt.timedelta(hours=2),
        end_time=now - dt.timedelta(hours=1),
        capacity_total=1,
        capacity_remaining=1,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["slots"] == []


@pytest.mark.django_db
def test_slots_includes_a_held_slot_marked_booked_not_excluded(doctor):
    """Real gap found and closed live 2026-11-05: a HELD slot (another customer's
    in-progress hold) used to vanish from the response entirely, identical to a
    slot that never existed. BAP's own time-picker UI wants to render every real
    slot and grey out the ones it can't offer — so this must come back, just
    marked unavailable, not be silently dropped."""
    now = timezone.now()
    start_time = _later_today(now, 0.25)
    held = Slot.objects.create(
        resource=doctor,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=1,
        status=Slot.Status.HELD,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    slots = resp.json()["message"]["slots"]
    assert len(slots) == 1
    assert slots[0]["start_time"] == held.start_time.isoformat()
    assert slots[0]["status"] == "booked"


@pytest.mark.django_db
def test_slots_includes_a_booked_slot_marked_booked(doctor):
    now = timezone.now()
    start_time = _later_today(now, 0.25)
    booked = Slot.objects.create(
        resource=doctor,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=1,
        capacity_remaining=0,
        status=Slot.Status.BOOKED,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    slots = resp.json()["message"]["slots"]
    assert len(slots) == 1
    assert slots[0]["start_time"] == booked.start_time.isoformat()
    assert slots[0]["status"] == "booked"


@pytest.mark.django_db
def test_slots_excludes_a_cancelled_slot(doctor):
    """A CANCELLED slot was pulled from the provider's own schedule entirely —
    there is no real time window left to show, grey or otherwise, unlike a
    HELD/BOOKED one."""
    now = timezone.now()
    Slot.objects.create(
        resource=doctor,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=1,
        status=Slot.Status.CANCELLED,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.json()["message"]["slots"] == []


@pytest.mark.django_db
def test_slots_marks_a_zero_capacity_available_slot_as_booked(doctor):
    """A multi-capacity slot whose status is still nominally AVAILABLE but whose
    capacity has been fully consumed is, for display purposes, exactly as taken
    as an explicitly BOOKED one — must not be reported as selectable."""
    now = timezone.now()
    start_time = _later_today(now, 0.25)
    full = Slot.objects.create(
        resource=doctor,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=3,
        capacity_remaining=0,
        status=Slot.Status.AVAILABLE,
    )
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    slots = resp.json()["message"]["slots"]
    assert len(slots) == 1
    assert slots[0]["start_time"] == full.start_time.isoformat()
    assert slots[0]["status"] == "booked"


@pytest.mark.django_db
def test_slots_empty_list_for_an_unknown_resource_id(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(uuid.uuid4()), date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    assert resp.json()["message"]["slots"] == []


@pytest.mark.django_db
def test_slots_empty_list_for_a_malformed_resource_id(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id="not-a-uuid", date=_today_ist().isoformat())

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 200
    assert resp.json()["message"]["slots"] == []


@pytest.mark.django_db
def test_slots_rejects_an_invalid_date(doctor):
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _slots_payload(resource_id=str(doctor.id), date="not-a-date")

    resp = _run_slots(payload, bap_priv=bap_priv, bap_pub=bap_pub)

    assert resp.status_code == 400
