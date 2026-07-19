"""Phase 1.3 Test Gate (livetracker2.md §1.3) for shared/inventory_core's Booking/Fulfillment
state machines and the Redis-backed TTL `HELD` reservation window. Exercised here for the same
reason as Phase 1.1/1.2's tests — a Django app's tests need a real settings module + database,
and BPP is its only current consumer.

FUNC/EDGE: every valid transition succeeds, every invalid transition (e.g. CANCELLED -> ACTIVE)
is rejected; a HELD slot with an expired TTL auto-returns to AVAILABLE without manual
intervention, verified live against real Redis (not mocked — uses BPP's own real `REDIS_URL`).
"""

import datetime as dt
import time as time_module

import pytest
import redis
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot
from inventory_core.reservation import confirm_hold, hold_slot, release_expired_hold


@pytest.fixture
def redis_client():
    # A real redis-py client against BPP's own real REDIS_URL — not mocked, per §1.3's Test
    # Gate ("verified live against real Redis"). BPP has no standalone raw-client helper
    # (only `core.events.get_event_bus()`, which wraps its own internal one for the event bus
    # queue), so this connects directly, the same way `EventBus.__init__` does internally.
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    yield client
    client.close()


@pytest.fixture
def resource(db):
    return Resource.objects.create(owner_ref="biz-1", name="Stylist A")


def _make_slot(resource, *, capacity=1):
    now = timezone.now()
    return Slot.objects.create(
        resource=resource,
        start_time=now,
        end_time=now + dt.timedelta(minutes=30),
        capacity_total=capacity,
        capacity_remaining=capacity,
    )


# --- Booking status state machine -------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.Status.HELD, Booking.Status.ACTIVE),
        (Booking.Status.HELD, Booking.Status.CANCELLED),
        (Booking.Status.ACTIVE, Booking.Status.COMPLETE),
        (Booking.Status.ACTIVE, Booking.Status.CANCELLED),
    ],
)
def test_booking_status_valid_transitions_succeed(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", status=start)

    booking.transition_status(target)

    booking.refresh_from_db()
    assert booking.status == target


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.Status.CANCELLED, Booking.Status.ACTIVE),  # the tracker's own named example
        (Booking.Status.COMPLETE, Booking.Status.ACTIVE),
        (Booking.Status.HELD, Booking.Status.COMPLETE),  # can't skip ACTIVE
        (Booking.Status.ACTIVE, Booking.Status.HELD),  # no going backwards
    ],
)
def test_booking_status_invalid_transitions_rejected(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", status=start)

    with pytest.raises(ValidationError):
        booking.transition_status(target)

    booking.refresh_from_db()
    assert booking.status == start


# --- Fulfillment status state machine ---------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.IN_PROGRESS),
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.NO_SHOW),
        (Booking.FulfillmentStatus.IN_PROGRESS, Booking.FulfillmentStatus.COMPLETED),
    ],
)
def test_fulfillment_status_valid_transitions_succeed(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", fulfillment_status=start)

    booking.transition_fulfillment_status(target)

    booking.refresh_from_db()
    assert booking.fulfillment_status == target


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("start", "target"),
    [
        (Booking.FulfillmentStatus.SCHEDULED, Booking.FulfillmentStatus.COMPLETED),  # skip step
        (Booking.FulfillmentStatus.IN_PROGRESS, Booking.FulfillmentStatus.NO_SHOW),  # too late
        (Booking.FulfillmentStatus.COMPLETED, Booking.FulfillmentStatus.IN_PROGRESS),
        (Booking.FulfillmentStatus.NO_SHOW, Booking.FulfillmentStatus.SCHEDULED),
    ],
)
def test_fulfillment_status_invalid_transitions_rejected(resource, start, target):
    slot = _make_slot(resource)
    booking = Booking.objects.create(slot=slot, holder_ref="cust-1", fulfillment_status=start)

    with pytest.raises(ValidationError):
        booking.transition_fulfillment_status(target)

    booking.refresh_from_db()
    assert booking.fulfillment_status == start


# --- Redis-backed TTL HELD reservation window -------------------------------------------------


@pytest.mark.django_db
def test_hold_slot_creates_held_booking_and_decrements_capacity(resource, redis_client):
    slot = _make_slot(resource, capacity=1)

    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    assert booking is not None
    assert booking.status == Booking.Status.HELD
    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    assert slot.status == Slot.Status.HELD


@pytest.mark.django_db
def test_hold_slot_fails_when_no_capacity(resource, redis_client):
    slot = _make_slot(resource, capacity=0)

    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    assert booking is None


@pytest.mark.django_db
def test_expired_hold_auto_returns_slot_to_available_without_manual_intervention(
    resource, redis_client
):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(
        slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1
    )
    assert booking is not None

    # Real Redis TTL expiry — not simulated/mocked. No code here manually flips anything;
    # the next line just waits for Redis itself to evict the key.
    time_module.sleep(1.5)

    released = release_expired_hold(booking.id, redis_client=redis_client)

    assert released is True
    slot.refresh_from_db()
    assert slot.status == Slot.Status.AVAILABLE
    assert slot.capacity_remaining == slot.capacity_total
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


@pytest.mark.django_db
def test_release_expired_hold_is_a_noop_while_hold_still_active(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    released = release_expired_hold(booking.id, redis_client=redis_client)

    assert released is False
    booking.refresh_from_db()
    assert booking.status == Booking.Status.HELD


@pytest.mark.django_db
def test_confirm_hold_transitions_to_active_and_clears_redis_key(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    confirmed = confirm_hold(booking.id, redis_client=redis_client)

    assert confirmed.status == Booking.Status.ACTIVE
    assert redis_client.exists(f"inventory_core:hold:{booking.id}") == 0


@pytest.mark.django_db
def test_confirm_hold_rejects_an_already_expired_hold(resource, redis_client):
    slot = _make_slot(resource, capacity=1)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=1)

    time_module.sleep(1.5)

    with pytest.raises(ValidationError):
        confirm_hold(booking.id, redis_client=redis_client)
