"""livetracker4.md §2.1 gap-closure (2026-08-02): the `requeue_dlq` management
command — the DLQ reprocessing tool this project didn't have before (found
during the phase's own post-close gap audit)."""

import io

import pytest
from django.core.management import CommandError, call_command

from core.events import get_event_bus


@pytest.fixture
def bus():
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)


def test_peek_reports_empty_dlq_honestly(bus):
    out = io.StringIO()
    call_command("requeue_dlq", "--peek", stdout=out)
    assert "DLQ is empty" in out.getvalue()


def test_peek_lists_a_real_poisoned_entry(bus):
    event_id = bus.publish("booking.created", {"booking_id": "b1"})
    event = bus.consume_one(timeout_seconds=2)
    bus.send_to_dlq(event, error="simulated poison")

    out = io.StringIO()
    call_command("requeue_dlq", "--peek", stdout=out)

    output = out.getvalue()
    assert event_id in output
    assert "booking.created" in output
    assert "simulated poison" in output


def test_requeue_moves_the_named_event_back_onto_the_main_queue(bus):
    event_id = bus.publish("booking.created", {"booking_id": "b2"})
    event = bus.consume_one(timeout_seconds=2)
    bus.send_to_dlq(event, error="simulated poison")

    out = io.StringIO()
    call_command("requeue_dlq", "--event-id", event_id, stdout=out)

    assert "requeued" in out.getvalue()
    assert bus.dlq_length() == 0
    assert bus.queue_length() == 1


def test_requeue_without_event_id_or_peek_raises(bus):
    with pytest.raises(CommandError, match="--event-id is required"):
        call_command("requeue_dlq")


def test_requeue_an_unknown_event_id_raises(bus):
    with pytest.raises(CommandError, match="no DLQ entry found"):
        call_command("requeue_dlq", "--event-id", "no-such-id")
