"""livetracker6.md §2.3 Test Gate — the vendor-facing order-confirmation email's
own send logic, mirroring `BAP/backend/core/test_notifications.py`'s established
pattern (call the synchronous function directly, assert against `mail.outbox`).
Integration coverage proving `events_worker.py`'s own `DISPATCH` table actually
triggers this for a real `BookingEvent.CONFIRMED` lives in `test_events_worker.py`.
"""

import datetime as dt

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot

from core import vendor_notifications

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


def _confirmed_booking(*, owner_contact="owner@example.com", resource_name="Stylist A"):
    owner = BusinessAccount.objects.create_user(
        contact=owner_contact, business_name="Glow Salon", password=TEST_PASSWORD
    )
    resource = Resource.objects.create(owner_ref=str(owner.id), name=resource_name)
    now = timezone.now()
    slot = Slot.objects.create(
        resource=resource,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=0,
    )
    booking = Booking.objects.create(slot=slot, holder_ref="tx-1", status=Booking.Status.ACTIVE)
    return Booking.objects.select_related("slot__resource").get(pk=booking.id)


@pytest.mark.django_db
def test_notify_vendor_order_confirmed_sends_a_real_email_with_correct_details():
    booking = _confirmed_booking()

    vendor_notifications.notify_vendor_order_confirmed(booking)

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["owner@example.com"]
    assert "confirmed" in sent.subject.lower()
    assert "Stylist A" in sent.body
    assert "tx-1" in sent.body


@pytest.mark.django_db
def test_notify_vendor_order_confirmed_is_a_safe_no_op_for_a_malformed_owner_ref():
    """Real bug found live during this phase's own test/regression pass, not a
    hypothetical: `Resource.owner_ref` is documented as opaque, not a real FK
    (`shared/inventory_core/models.py`), and real data already has at least one
    non-UUID value (a "biz-debug" test resource, confirmed via direct DB query
    this same session). `BusinessAccount.objects.filter(id=owner_ref)` raises a
    real `ValidationError` for such a value — uncaught, that exception used to
    propagate out of this function into `process_event()`'s own single
    `transaction.atomic()` block, rolling back the *same event's* already-written
    audit-log entry too (confirmed live: two real subprocess-worker
    integration tests in `test_events_worker.py` started failing with zero audit
    entries once this consumer was wired in, until this was fixed)."""
    now = timezone.now()
    resource = Resource.objects.create(owner_ref="not-a-uuid", name="Stylist A")
    slot = Slot.objects.create(
        resource=resource,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=0,
    )
    booking = Booking.objects.create(slot=slot, holder_ref="tx-1", status=Booking.Status.ACTIVE)
    booking = Booking.objects.select_related("slot__resource").get(pk=booking.id)

    vendor_notifications.notify_vendor_order_confirmed(booking)  # must not raise

    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_notify_vendor_order_confirmed_skips_a_non_email_shaped_contact():
    booking = _confirmed_booking(owner_contact="+91-9876543210")

    vendor_notifications.notify_vendor_order_confirmed(booking)

    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_a_different_businesss_booking_never_notifies_this_business():
    """IDOR discipline, same as §2.2: the recipient is resolved from the
    booking's own resource's own owner, never anything caller-supplied — proven
    here by creating two businesses and confirming each only ever gets its own."""
    booking_a = _confirmed_booking(owner_contact="owner-a@example.com", resource_name="Stylist A")
    booking_b = _confirmed_booking(owner_contact="owner-b@example.com", resource_name="Stylist B")

    vendor_notifications.notify_vendor_order_confirmed(booking_a)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner-a@example.com"]
    assert booking_b.slot.resource.name not in mail.outbox[0].body


@pytest.mark.django_db
def test_notify_vendor_order_confirmed_consumer_reacts_to_a_real_event_payload():
    booking = _confirmed_booking()

    vendor_notifications.notify_vendor_order_confirmed_consumer(
        {"payload": {"booking_id": str(booking.id)}}
    )

    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_notify_vendor_order_confirmed_consumer_is_a_safe_no_op_for_an_unknown_booking():
    vendor_notifications.notify_vendor_order_confirmed_consumer(
        {"payload": {"booking_id": "00000000-0000-0000-0000-000000000000"}}
    )
    assert len(mail.outbox) == 0
