import os

import pytest

from .bus import EventBus, process_with_dlq

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6390/0")


@pytest.fixture
def bus():
    b = EventBus(redis_url=REDIS_URL, queue_name="test-queue", dlq_name="test-dlq")
    b._redis.delete(b.queue_name, b.dlq_name)  # clean slate per test
    yield b
    b._redis.delete(b.queue_name, b.dlq_name)


def test_publish_and_consume_round_trip(bus):
    event_id = bus.publish("booking.created", {"booking_id": "b1"})
    event = bus.consume_one(timeout_seconds=2)
    assert event is not None
    assert event["event_id"] == event_id
    assert event["event_type"] == "booking.created"
    assert event["payload"] == {"booking_id": "b1"}


def test_consume_times_out_on_empty_queue(bus):
    event = bus.consume_one(timeout_seconds=0.5)
    assert event is None


def test_queue_length_reflects_pending_events(bus):
    bus.publish("a", {})
    bus.publish("b", {})
    assert bus.queue_length() == 2
    bus.consume_one(timeout_seconds=1)
    assert bus.queue_length() == 1


def test_failed_event_is_routed_to_dlq_not_lost(bus):
    bus.publish("booking.created", {"booking_id": "b2"})
    event = bus.consume_one(timeout_seconds=2)

    def failing_handler(_event):
        raise ValueError("simulated processing failure")

    success = process_with_dlq(bus, event, failing_handler)
    assert success is False
    assert bus.dlq_length() == 1
    dlq_events = bus.peek_dlq()
    assert dlq_events[0]["payload"] == {"booking_id": "b2"}
    assert "simulated processing failure" in dlq_events[0]["error"]


def test_successful_event_is_not_routed_to_dlq(bus):
    bus.publish("booking.created", {"booking_id": "b3"})
    event = bus.consume_one(timeout_seconds=2)

    processed = []

    def good_handler(e):
        processed.append(e)

    success = process_with_dlq(bus, event, good_handler)
    assert success is True
    assert bus.dlq_length() == 0
    assert len(processed) == 1
