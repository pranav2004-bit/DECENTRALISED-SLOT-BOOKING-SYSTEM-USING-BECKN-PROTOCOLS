"""Phase 1.4 Test Gate (livetracker2.md §1.4) for shared/inventory_core's event vocabulary,
versioning, and idempotent processing wired to the real shared/event_bus. Exercised here for the
same reason as Phase 1.1-1.3's tests — a Django app's tests need a real settings module + real
database, and BPP is its only current consumer.

FUNC/NEG: the exact same event delivered twice produces one business effect, not two; a
deliberately poisoned event lands in the DLQ instead of retrying forever; a consumer given an
unexpected event version fails safely (logs and skips) instead of crashing.
"""

import datetime as dt
import uuid

import pytest
from django.utils import timezone
from event_bus import EventBus, process_with_dlq
from inventory_core.events import (
    BookingEvent,
    ProcessedEvent,
    SlotEvent,
    process_event,
    publish_event,
)
from inventory_core.models import AvailabilityCalendar, Resource, Slot
from inventory_core.reservation import confirm_hold, hold_slot

from core.events import get_event_bus


@pytest.fixture
def bus() -> EventBus:
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


@pytest.fixture
def redis_client(bus):
    return bus._redis


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


def _built_event(event_type: str, **payload) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": {"version": 1, **payload},
        "published_at": timezone.now().isoformat(),
    }


# --- Idempotency: same event delivered twice -> one business effect ---------------------------


@pytest.mark.django_db
def test_duplicate_delivery_of_same_event_runs_handler_only_once():
    event = _built_event(SlotEvent.RESERVED, slot_id="s1")
    calls = []

    result_1 = process_event(event, calls.append)
    result_2 = process_event(event, calls.append)

    assert result_1 == "processed"
    assert result_2 == "duplicate"
    assert len(calls) == 1
    assert ProcessedEvent.objects.filter(event_id=event["event_id"]).count() == 1


@pytest.mark.django_db
def test_a_failed_first_attempt_does_not_block_a_legitimate_retry():
    """If `handler` raises, the idempotency marker rolls back too — a genuine retry (e.g. after
    `process_with_dlq` catches the failure) must still be allowed to run, not be treated as a
    false duplicate."""
    event = _built_event(SlotEvent.RESERVED, slot_id="s1")

    with pytest.raises(RuntimeError):
        process_event(event, lambda e: (_ for _ in ()).throw(RuntimeError("boom")))

    assert ProcessedEvent.objects.filter(event_id=event["event_id"]).count() == 0

    calls = []
    result = process_event(event, calls.append)
    assert result == "processed"
    assert len(calls) == 1


# --- DLQ: a genuinely poisoned event lands in the DLQ, not an infinite retry loop --------------


@pytest.mark.django_db
def test_poisoned_event_lands_in_dlq_via_process_with_dlq(bus):
    bus.publish(SlotEvent.RESERVED, {"version": 1, "slot_id": "s1"})
    event = bus.consume_one(timeout_seconds=2)
    assert event is not None

    def poisoned_handler(_e):
        raise RuntimeError("deliberately poisoned event")

    success = process_with_dlq(bus, event, lambda e: process_event(e, poisoned_handler))

    assert success is False
    assert bus.dlq_length() == 1


# --- Version safety: an unsupported version fails safely, not a crash --------------------------


@pytest.mark.django_db
def test_unsupported_event_version_logs_and_skips_instead_of_crashing():
    event = _built_event(SlotEvent.RESERVED, slot_id="s1")
    event["payload"]["version"] = 999
    calls = []

    result = process_event(event, calls.append, supported_versions=(1,))

    assert result == "unsupported_version"
    assert calls == []
    assert ProcessedEvent.objects.filter(event_id=event["event_id"]).count() == 0


# --- Real wiring into shared/event_bus, real payload/version shape -----------------------------


@pytest.mark.django_db
def test_publish_event_always_carries_a_version_field(bus):
    event_id = publish_event(bus, SlotEvent.CREATED, slot_id="s1", resource_id="r1")

    event = bus.consume_one(timeout_seconds=2)
    assert event["event_id"] == event_id
    assert event["event_type"] == SlotEvent.CREATED
    assert event["payload"]["version"] == 1
    assert event["payload"]["slot_id"] == "s1"


@pytest.mark.django_db
def test_generate_slots_publishes_slot_created_events(resource, bus):
    range_start = timezone.now()
    calendar = AvailabilityCalendar.objects.create(
        resource=resource,
        frequency=dt.timedelta(days=1),
        range_start=range_start,
        range_end=range_start + dt.timedelta(hours=1),
        times=["09:00"],
        slot_duration=dt.timedelta(minutes=30),
        slot_capacity=1,
    )

    slots = calendar.generate_slots(event_bus=bus)

    assert len(slots) == 1
    event = bus.consume_one(timeout_seconds=2)
    assert event["event_type"] == SlotEvent.CREATED
    assert event["payload"]["slot_id"] == str(slots[0].id)


@pytest.mark.django_db
def test_hold_slot_publishes_slot_reserved_event(resource, bus, redis_client):
    slot = _make_slot(resource)

    booking = hold_slot(
        slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30, event_bus=bus
    )

    event = bus.consume_one(timeout_seconds=2)
    assert event["event_type"] == SlotEvent.RESERVED
    assert event["payload"]["booking_id"] == str(booking.id)


@pytest.mark.django_db
def test_confirm_hold_publishes_slot_and_booking_confirmed_events(resource, bus, redis_client):
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    confirm_hold(booking.id, redis_client=redis_client, event_bus=bus)

    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)
    event_types = {first["event_type"], second["event_type"]}
    assert event_types == {SlotEvent.CONFIRMED, BookingEvent.CONFIRMED}


# --- Per-entity ordering ------------------------------------------------------------------------


@pytest.mark.django_db
def test_events_for_same_slot_are_processed_in_publish_order(bus):
    """The bus is a single Redis list (RPUSH/BLPOP) with one consumer draining it in this test —
    per-entity ordering (§1.4) is a direct consequence of that, not a separate mechanism. Publish
    SlotReserved then SlotConfirmed for the same slot in quick succession and confirm they are
    consumed in that exact order."""
    slot_id = "s1"
    bus.publish(SlotEvent.RESERVED, {"version": 1, "slot_id": slot_id})
    bus.publish(SlotEvent.CONFIRMED, {"version": 1, "slot_id": slot_id})

    first = bus.consume_one(timeout_seconds=2)
    second = bus.consume_one(timeout_seconds=2)

    assert first["event_type"] == SlotEvent.RESERVED
    assert second["event_type"] == SlotEvent.CONFIRMED
