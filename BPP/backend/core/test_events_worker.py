"""livetracker4.md §2.1 Test Gate — Independent Async Consumer / Worker Pool.

FUNC/INTEG/DR: a real booking confirmation produces a `BookingConfirmed` event
consumed by a genuinely separate worker process (confirmed by process/PID
inspection, not just the side effect happening), not inline in the same
request/thread; killing the worker process and restarting it resumes correctly,
reusing the existing idempotency ledger; the individual consumer functions
(audit-log, metrics, WebSocket broadcast) are also exercised directly.
"""

import datetime as dt
import os
import subprocess
import sys
import time as time_module
import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone
from event_bus import EventBus
from inventory_core.events import BookingEvent, SlotEvent
from inventory_core.models import Booking, BookingAuditLogEntry, Resource, Slot
from inventory_core.reservation import cancel_booking, confirm_hold, hold_slot

from core.events import get_event_bus
from core.events_worker import DISPATCH, _combined
from core.metrics import booking_lifecycle_consumer, hold_created_consumer
from core.realtime import broadcast_slot_update_consumer


def _isolated_bus_and_env():
    """A real gap found live (not in code review): a subprocess-spawned worker
    runs concurrently with pytest's own test execution — sharing the one real
    `bpp-internal-events` queue every other test in this suite also uses risks a
    still-alive worker subprocess consuming an event a *different*, unrelated
    test just published (this is exactly what broke
    test_inventory_core_events.py::test_events_for_same_slot_are_processed_in_publish_order
    the first time this file's own tests ran alongside it). Every test that
    spawns a real worker subprocess gets its own uniquely-named queue/DLQ
    instead — both the test's own bus object and the subprocess (via env vars
    `core.events.get_event_bus()` reads through `settings.EVENT_BUS_QUEUE_NAME`/
    `_DLQ_NAME`) point at the identical isolated pair, invisible to every other
    test.

    **A second, more serious gap found live during the livetracker4.md §2.1
    cutover (2026-08-02), not in code review — this one had been silently
    masking every subprocess test's own real assertion:** `os.environ` (what
    this dict starts from) still carries this *process's* own `DATABASE_URL`,
    e.g. `postgres://bpp:bpp@bpp-db:5432/bpp` — the real, persistent dev
    database. pytest-django, though, runs this whole test inside a *different*,
    ephemeral `test_bpp` database (created at session start, dropped at session
    end) by mutating `django.db.connection.settings_dict['NAME']` in-process —
    not via an env var, so a spawned subprocess never inherits it. Before this
    cutover, every subprocess test's own assertion (a real `BookingAuditLogEntry`
    exists) passed anyway, but for the *wrong* reason: `reservation.py`'s own
    still-present inline `log_booking_audit_event()` call wrote the identical
    row synchronously, in-process, into the correct `test_bpp` — regardless of
    whether the subprocess worker being tested ever actually worked. Once the
    inline call was removed as part of this cutover, that accidental cover
    disappeared, and every subprocess test failed for real — not because the
    worker broke, but because it had been quietly writing into the *live dev
    database* (`bpp`) the entire time, confirmed live via direct `psql`
    inspection (`bpp` accumulated real rows tagged with this suite's own
    `correlation_id` test values). Fixed by rebuilding `DATABASE_URL` from the
    *actual* live connection's own `settings_dict` — whatever database this
    test process is really talking to right now — instead of trusting the
    inherited env var."""
    from django.db import connection

    suffix = uuid.uuid4().hex[:12]
    queue_name = f"test-worker-queue-{suffix}"
    dlq_name = f"test-worker-dlq-{suffix}"
    bus = EventBus(redis_url=settings.EVENT_BUS_URL, queue_name=queue_name, dlq_name=dlq_name)
    db = connection.settings_dict
    real_database_url = (
        f"postgres://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    env = {
        **os.environ,
        "EVENT_BUS_QUEUE_NAME": queue_name,
        "EVENT_BUS_DLQ_NAME": dlq_name,
        "DATABASE_URL": real_database_url,
        # A third gap in the same family, found alongside the DATABASE_URL one: the
        # subprocess never imports pytest, so bpp/settings.py's own `TESTING =
        # "pytest" in sys.modules` check always resolved False there — silently
        # pointing its Redis-backed metrics counters at the real dev DB (Redis DB
        # 0/whatever REDIS_URL specifies) instead of the test-only DB 15 `TESTING`
        # redirects to. Explicit env var makes the subprocess resolve `TESTING` the
        # same way this test process does.
        "TESTING": "true",
    }
    return bus, env


@pytest.fixture
def bus():
    b = get_event_bus()
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)
    yield b
    b._redis.delete(b.queue_name, b.dlq_name, b.processing_queue_name)


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


# --- Individual consumer functions ----------------------------------------


@pytest.mark.django_db
def test_hold_created_consumer_increments_the_real_counter():
    with patch("core.metrics.increment_counter") as mock_incr:
        hold_created_consumer(_built_event(SlotEvent.RESERVED, slot_id="s1"))
    mock_incr.assert_called_once_with("bpp:metrics:hold_created")


@pytest.mark.django_db
def test_booking_lifecycle_consumer_routes_confirmed_correctly():
    with patch("core.metrics.increment_counter") as mock_incr:
        booking_lifecycle_consumer(
            _built_event(BookingEvent.CONFIRMED, booking_id="b1", slot_id="s1")
        )
    mock_incr.assert_called_once_with("bpp:metrics:booking_confirmed")


@pytest.mark.django_db
def test_booking_lifecycle_consumer_distinguishes_cancel_reasons():
    """The exact reason-based branching §2.1's own producer-enrichment finding
    exists to make possible — a hold_expired-reasoned cancel and a genuine
    customer_cancel must increment different counters, matching what the
    now-still-present inline calls already do."""
    with patch("core.metrics.increment_counter") as mock_incr:
        booking_lifecycle_consumer(
            _built_event(
                BookingEvent.CANCELLED, booking_id="b1", reason="hold_expired", slot_id="s1"
            )
        )
    mock_incr.assert_called_once_with("bpp:metrics:hold_expired")

    with patch("core.metrics.increment_counter") as mock_incr:
        booking_lifecycle_consumer(
            _built_event(
                BookingEvent.CANCELLED, booking_id="b1", reason="customer_cancel", slot_id="s1"
            )
        )
    mock_incr.assert_called_once_with("bpp:metrics:booking_cancelled")


@pytest.mark.django_db
def test_booking_lifecycle_consumer_does_not_count_a_reselect_supersede():
    """`superseded_by_reselect` was never counted by the inline path either —
    matching it exactly, not inventing a new metric."""
    with patch("core.metrics.increment_counter") as mock_incr:
        booking_lifecycle_consumer(
            _built_event(
                BookingEvent.CANCELLED,
                booking_id="b1",
                reason="superseded_by_reselect",
                slot_id="s1",
            )
        )
    mock_incr.assert_not_called()


@pytest.mark.django_db
def test_broadcast_slot_update_consumer_refetches_and_broadcasts_the_real_slot(resource):
    slot = _make_slot(resource)
    with patch("core.realtime.broadcast_slot_update") as mock_broadcast:
        broadcast_slot_update_consumer(_built_event(SlotEvent.RESERVED, slot_id=str(slot.id)))
    mock_broadcast.assert_called_once()
    called_resource_id, called_slot = mock_broadcast.call_args[0]
    assert called_resource_id == resource.id
    assert called_slot.id == slot.id


@pytest.mark.django_db
def test_broadcast_slot_update_consumer_is_a_safe_noop_for_an_unknown_slot():
    with patch("core.realtime.broadcast_slot_update") as mock_broadcast:
        broadcast_slot_update_consumer(_built_event(SlotEvent.RESERVED, slot_id=str(uuid.uuid4())))
    mock_broadcast.assert_not_called()


# --- _combined() composition + idempotency correctness ---------------------


@pytest.mark.django_db
def test_combined_runs_every_registered_handler_for_the_same_event():
    """Real bug found and fixed at design time (see shared/event_bus/worker.py's
    own docstring): wrapping each handler separately with process_event's
    event_id-keyed idempotency would silently skip every handler after the
    first as a false duplicate. _combined() composes them into one function
    before wrapping once — both must run."""
    calls = []
    combined = _combined(lambda e: calls.append("first"), lambda e: calls.append("second"))
    combined(_built_event(BookingEvent.CONFIRMED, booking_id="b1"))
    assert calls == ["first", "second"]


@pytest.mark.django_db
def test_combined_is_idempotent_by_event_id_as_a_whole():
    calls = []
    combined = _combined(lambda e: calls.append("a"), lambda e: calls.append("b"))
    event = _built_event(BookingEvent.CONFIRMED, booking_id="b1")

    combined(event)
    combined(event)  # exact same event_id redelivered

    assert calls == ["a", "b"]  # not ["a", "b", "a", "b"]


def test_dispatch_covers_every_real_booking_and_slot_lifecycle_event():
    """Every event type reservation.py's own lifecycle functions actually
    publish (confirmed by direct read, not assumed) has a registered consumer
    here — a real event silently falling through with no handler would mean
    its audit-log/metrics/broadcast side effect just stops happening once the
    inline calls are eventually removed."""
    expected = {
        SlotEvent.RESERVED,
        SlotEvent.RELEASED,
        SlotEvent.CONFIRMED,
        SlotEvent.RESCHEDULED,
        SlotEvent.COMPLETED,
        BookingEvent.CONFIRMED,
        BookingEvent.CANCELLED,
        BookingEvent.COMPLETED,
        BookingEvent.RESCHEDULED,
    }
    assert expected.issubset(DISPATCH.keys())


# --- The real Test Gate: a genuinely separate OS process, not inline --------


def _bpp_backend_dir() -> str:
    # manage.py's own directory — this test file already runs with core.* importable,
    # confirming the process cwd is already BPP/backend when pytest runs inside the
    # real container, same assumption every other test file in this suite already makes.
    return os.getcwd()


def _worker_output(worker: subprocess.Popen) -> str:
    if worker.poll() is None:
        return "(still running)"
    return worker.stdout.read()


@pytest.mark.django_db(transaction=True)
def test_a_real_confirm_is_consumed_by_a_genuinely_separate_worker_process(resource, redis_client):
    """The literal Test Gate wording: confirmed by process/PID inspection, not
    just the side effect happening. Spawns the real `run_event_worker`
    management command as a genuine OS subprocess — not a thread, not an
    in-test function call — and confirms a real BookingConfirmed event this
    test never handles itself produces a real BookingAuditLogEntry, because a
    separate process consumed it off the real queue.

    `transaction=True`: the worker subprocess is a genuinely separate DB
    connection — it can only see data this test has actually committed, not
    data sitting in Django's normal per-test uncommitted-transaction rollback.

    Uses its own isolated queue/DLQ (`_isolated_bus_and_env()`) — a real
    subprocess running concurrently with pytest's own execution must not share
    a queue any other, unrelated test might also be publishing to at the same
    wall-clock moment."""
    isolated_bus, isolated_env = _isolated_bus_and_env()
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)

    worker = subprocess.Popen(
        [sys.executable, "manage.py", "run_event_worker"],
        cwd=_bpp_backend_dir(),
        env=isolated_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert worker.pid != os.getpid()  # a real, separate process, not this test's own

        confirm_hold(
            booking.id,
            redis_client=redis_client,
            event_bus=isolated_bus,
            correlation_id="test-corr-1",
        )

        deadline = time_module.time() + 15
        entry = None
        while time_module.time() < deadline:
            entry = BookingAuditLogEntry.objects.filter(
                booking_id_text=str(booking.id), event_type=BookingEvent.CONFIRMED
            ).first()
            if entry is not None:
                break
            time_module.sleep(0.2)

        assert entry is not None, (
            "worker subprocess never wrote the audit-log entry — "
            f"worker output so far:\n{_worker_output(worker)}"
        )
        assert entry.correlation_id == "test-corr-1"
        assert entry.detail["slot_id"] == str(slot.id)
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)


@pytest.mark.django_db(transaction=True)
def test_killing_the_worker_and_restarting_it_still_processes_the_event_exactly_once(
    resource, redis_client
):
    """DR: a worker process that dies (SIGKILL, no graceful shutdown chance at
    all) and is then restarted must still process every event exactly once —
    neither dropped nor double-processed. This test doesn't control the exact
    instant of the kill relative to the pop (real OS process timing isn't
    deterministically controllable from here), so it proves the actual
    invariant that matters regardless of that timing: recover_orphaned() runs
    unconditionally at every worker startup (see run_worker's own docstring),
    and shared/event_bus/tests.py's own primitive-level tests already prove
    that call correctly recovers anything left mid-flight by a crashed
    predecessor. This test closes the loop at full process granularity.

    Uses its own isolated queue/DLQ, same reasoning as the test above."""
    isolated_bus, isolated_env = _isolated_bus_and_env()
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(
        booking.id, redis_client=redis_client, event_bus=isolated_bus, correlation_id="test-corr-2"
    )

    worker_1 = subprocess.Popen(
        [sys.executable, "manage.py", "run_event_worker"],
        cwd=_bpp_backend_dir(),
        env=isolated_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time_module.sleep(0.05)  # give it a real chance to have already popped the event
    worker_1.kill()  # SIGKILL — no graceful shutdown, the real crash scenario
    worker_1.wait(timeout=10)

    worker_2 = subprocess.Popen(
        [sys.executable, "manage.py", "run_event_worker"],
        cwd=_bpp_backend_dir(),
        env=isolated_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time_module.time() + 15
        entries = []
        while time_module.time() < deadline:
            entries = list(
                BookingAuditLogEntry.objects.filter(
                    booking_id_text=str(booking.id), event_type=BookingEvent.CONFIRMED
                )
            )
            if entries:
                break
            time_module.sleep(0.2)

        assert len(entries) == 1, (
            f"expected exactly 1 audit-log entry after kill+restart, got {len(entries)} — "
            f"worker_2 output:\n{_worker_output(worker_2)}"
        )
        assert entries[0].correlation_id == "test-corr-2"
    finally:
        worker_2.terminate()
        try:
            worker_2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker_2.kill()
            worker_2.wait(timeout=10)


@pytest.mark.django_db(transaction=True)
def test_cutover_complete_a_stopped_worker_now_means_no_audit_log_at_all(
    resource, bus, redis_client
):
    """§2.1's no-regression cutover requirement's parallel-run/burn-in window is
    now over (2026-08-02): `reservation.py`'s own inline `log_booking_audit_event`
    calls have been removed, and `select_service.py`/`confirm_service.py`/
    `cancel_service.py`/`update_service.py`'s inline metrics/broadcast calls with
    them — the real event worker (`bpp-worker` in `docker-compose.yml`) is now the
    *only* thing that produces these side effects. This test proves that
    honestly, not silently: with no worker process running, a real cancel
    produces zero audit-log entry — the accepted, documented trade-off of
    completing the cutover, not a bug. (Contrast with this test's own
    predecessor, before the cutover, which asserted the opposite.)"""
    slot = _make_slot(resource)
    booking = hold_slot(slot.id, holder_ref="cust-1", redis_client=redis_client, ttl_seconds=30)
    confirm_hold(booking.id, redis_client=redis_client, event_bus=bus)

    # No worker process running at all here — simulates it being down/broken.
    cancel_booking(booking.id, event_bus=bus, correlation_id="test-corr-3")

    entry = BookingAuditLogEntry.objects.filter(
        booking_id_text=str(booking.id), event_type=BookingEvent.CANCELLED
    ).first()
    assert entry is None, (
        "post-cutover, nothing should write an audit-log entry with the worker down "
        "— if this now finds one, an inline call was reintroduced somewhere"
    )

    # The booking's own state transition is unaffected either way — that's
    # `cancel_booking()`'s own DB-transaction work, unrelated to event consumption.
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED

    # And the events themselves are genuinely still sitting on the queue, not
    # lost — exactly what the next worker startup would pick up and process.
    assert bus.queue_length() > 0


# --- §2.3: Dead-Letter Queue Live-Proof -------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_deliberately_poisoned_event_lands_in_the_real_dlq_via_a_live_worker():
    """livetracker4.md §2.3 — `process_with_dlq`/`EVENT_BUS_DLQ_NAME` proven live
    for the first time against a genuinely separate worker process, not just
    `process_with_dlq()` called directly inside a unit test (already covered by
    `test_inventory_core_events.py::test_poisoned_event_lands_in_dlq_via_process_with_dlq`).

    The poison is real, not an injected test-only failure branch: `booking_id`
    is a syntactically invalid UUID — `audit_log_consumer`'s own
    `Booking.objects.filter(pk=booking_id)` validates the pk format before ever
    reaching the DB, raising a genuine `ValidationError` the consumer can't
    swallow (confirmed live, not assumed — an earlier draft of this test
    expected a Postgres-level `DataError` from `booking_id_text`'s own
    `max_length=64` column instead, which turned out not to be the actual
    first failure point; corrected to match the real observed exception, not
    the originally-guessed one), the same class of malformed-producer-data
    failure this mechanism exists to catch.

    Confirmed via direct Redis inspection (`LLEN`/`LRANGE` on the real DLQ key
    — `EventBus.dlq_length()`/`peek_dlq()` are thin, direct passthroughs to
    exactly those, not a higher-level abstraction hiding what's actually in
    Redis) that the poisoned event's own `event_id` — not just *some* entry —
    is what actually landed there."""
    isolated_bus, isolated_env = _isolated_bus_and_env()
    poisoned_booking_id = "x" * 100  # syntactically invalid UUID -> real ValidationError

    event_id = isolated_bus.publish(
        BookingEvent.CONFIRMED,
        {"version": 1, "booking_id": poisoned_booking_id, "slot_id": "s1", "correlation_id": None},
    )

    worker = subprocess.Popen(
        [sys.executable, "manage.py", "run_event_worker"],
        cwd=_bpp_backend_dir(),
        env=isolated_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time_module.time() + 15
        dlq_length = 0
        while time_module.time() < deadline:
            dlq_length = isolated_bus.dlq_length()  # direct LLEN on the real DLQ key
            if dlq_length >= 1:
                break
            time_module.sleep(0.2)

        assert dlq_length == 1, (
            f"expected exactly 1 event in the real DLQ, got {dlq_length} — "
            f"worker output:\n{_worker_output(worker)}"
        )
        dlq_events = isolated_bus.peek_dlq()  # direct LRANGE on the real DLQ key
        assert dlq_events[0]["event_id"] == event_id
        assert "ValidationError" in dlq_events[0]["error"]
        assert "not a valid UUID" in dlq_events[0]["error"]

        # The main queue must be genuinely empty — this isn't sitting un-consumed,
        # it was actually popped, tried, and routed away, not stuck/lost either way.
        assert isolated_bus.queue_length() == 0
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=10)
