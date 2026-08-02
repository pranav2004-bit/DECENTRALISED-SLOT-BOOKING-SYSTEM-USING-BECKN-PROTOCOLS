"""Phase 3.2 Test Gate (livetracker2.md §3.2) pieces owned by BPP: real /select
receipt (verifying both the BAP and the forwarding Gateway) and real /on_select
dispatch — resolving the requested item+time against live availability and
attempting the real atomic hold, including the NEG concurrent-race requirement and
the re-selection-releases-prior-hold requirement.
"""

import datetime as dt
import json
import os
import subprocess
import sys
import time as time_module
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
import responses
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django_observability.metrics import get_counter
from event_bus import EventBus
from inventory_core.events import BookingEvent, SlotEvent
from inventory_core.models import Booking, Resource, Slot

import core.events as events_module
from core import select_service
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
    settings.RESERVATION_HOLD_TTL_SECONDS = 600
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _make_resource_with_slot(*, price_value="500.00", capacity=1, start_time=None):
    business = BusinessAccount.objects.create_user(
        contact=f"salon-{Resource.objects.count()}@example.com",
        business_name="Glow Salon",
        password=TEST_PASSWORD,
    )
    resource = Resource.objects.create(
        owner_ref=str(business.id),
        name="Stylist A",
        category_id="ONDC:RET13",
        price_currency="INR",
        price_value=price_value,
    )
    start_time = start_time or timezone.now().replace(microsecond=0)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=start_time + dt.timedelta(minutes=30),
        capacity_total=capacity,
        capacity_remaining=capacity,
    )
    return resource, slot


def _build_select_payload(
    *, item_id, requested_timestamp, bap_id="bap.example.com", transaction_id="txn-1"
):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "select",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {
            "order": {
                "items": [{"id": item_id}],
                "fulfillments": [
                    {"stops": [{"type": "start", "time": {"timestamp": requested_timestamp}}]}
                ],
            }
        },
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
@patch("core.select_service.dispatch_on_select_in_background")
def test_select_view_acks_when_both_bap_and_gateway_signatures_are_valid(mock_dispatch, client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_select_payload(
        item_id="11111111-1111-1111-1111-111111111111", requested_timestamp="2026-07-25T10:00:00Z"
    )
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
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_select_view_accepts_missing_gateway_authorization_now_that_gateway_is_optional(client):
    """livetracker4.md §1.2: /select now arrives directly from the BAP with no
    Gateway hop — a missing X-Gateway-Authorization header must be accepted, not
    rejected, as long as the BAP's own signature is genuinely valid
    (require_gateway=False for this action)."""
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_select_payload(
        item_id="11111111-1111-1111-1111-111111111111", requested_timestamp="2026-07-25T10:00:00Z"
    )
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body,
        subscriber_id="bap.example.com",
        unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    known = _known(bap_pub=bap_pub)

    with (
        patch("core.select_service.dispatch_on_select_in_background"),
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"


@pytest.mark.django_db
def test_select_view_rejects_missing_bap_authorization_even_without_gateway(client):
    """NEG: require_gateway=False only makes the Gateway signature optional — the
    BAP's own signature is still mandatory."""
    payload = _build_select_payload(
        item_id="11111111-1111-1111-1111-111111111111", requested_timestamp="2026-07-25T10:00:00Z"
    )
    body = json.dumps(payload).encode()

    resp = client.post(
        reverse("select"),
        data=body,
        content_type="application/json",
    )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_select_view_rejects_a_malformed_order_before_acking(client):
    """A structurally-invalid order (no fulfillments/stops) must NACK synchronously,
    not ACK and then silently fail in the background."""
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = {
        "context": _build_select_payload(item_id="x", requested_timestamp="2026-07-25T10:00:00Z")[
            "context"
        ],
        "message": {"order": {"items": [{"id": "x"}], "fulfillments": []}},
    }
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
            reverse("select"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_select_holds_the_real_slot_and_returns_a_real_quote(bpp_identity_settings):
    resource, slot = _make_resource_with_slot(price_value="750.00")
    requested_timestamp = slot.start_time.isoformat()
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp=requested_timestamp
    )

    captured_requests = []

    def bap_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_select", callback=bap_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    booking = Booking.objects.get(slot=slot)
    assert booking.status == Booking.Status.HELD
    assert booking.holder_ref == "txn-1"

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_select"
    assert "error" not in forwarded
    order = forwarded["message"]["order"]
    assert order["items"][0]["id"] == str(resource.id)
    assert order["fulfillments"][0]["id"] == str(booking.id)
    assert order["quote"]["price"] == {"currency": "INR", "value": "750.00"}


@pytest.mark.django_db
def test_dispatch_on_select_does_not_raise_when_bap_is_unreachable(bpp_identity_settings):
    """livetracker4.md §1.4 coverage-parity replacement for beckn-gateway's retired
    test_relay_on_select_does_not_raise_when_bap_is_unreachable."""
    resource, slot = _make_resource_with_slot(price_value="750.00")
    requested_timestamp = slot.start_time.isoformat()
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp=requested_timestamp
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://bap.example.com/on_select",
            body=ConnectionError("simulated unreachable BAP"),
        )
        select_service.dispatch_on_select(payload=payload)

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0


@pytest.mark.django_db
def test_dispatch_on_select_returns_slot_unavailable_for_a_nonexistent_time(bpp_identity_settings):
    resource, _slot = _make_resource_with_slot()
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp="2099-01-01T00:00:00Z"
    )

    captured_requests = []

    def bap_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_select", callback=bap_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "SLOT_UNAVAILABLE"


@pytest.mark.django_db
def test_dispatch_on_select_returns_item_not_found_for_an_unknown_resource(bpp_identity_settings):
    payload = _build_select_payload(
        item_id="99999999-9999-9999-9999-999999999999", requested_timestamp="2026-07-25T10:00:00Z"
    )

    captured_requests = []

    def bap_on_select_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_select", callback=bap_on_select_callback
        )
        select_service.dispatch_on_select(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["error"]["code"] == "ITEM_NOT_FOUND"


@pytest.mark.django_db(transaction=True)
def test_concurrent_select_on_the_same_slot_yields_exactly_one_winner(bpp_identity_settings):
    """The real §3.2 Test Gate: a slot selected by someone else microseconds earlier
    is correctly rejected, not silently accepted. Two genuinely concurrent threads
    race the same capacity-1 slot via dispatch_on_select — the real hold_slot()
    atomicity (already proven in test_inventory_core_booking.py) must surface here as
    a real ITEM/SLOT rejection for exactly one of the two callers.

    One shared `responses.RequestsMock()` wraps both threads deliberately — activating
    two independent RequestsMock contexts concurrently in different threads isn't
    thread-safe (it patches the requests library's transport globally); registering
    the callback once and letting both threads' calls hit it is."""
    resource, slot = _make_resource_with_slot(capacity=1)
    requested_timestamp = slot.start_time.isoformat()

    results = {}

    def on_select_callback(request):
        forwarded = json.loads(request.body)
        transaction_id = forwarded["context"]["transaction_id"]
        results[transaction_id] = "error" not in forwarded
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    def attempt(customer_id):
        payload = _build_select_payload(
            item_id=str(resource.id),
            requested_timestamp=requested_timestamp,
            transaction_id=f"txn-{customer_id}",
        )
        select_service.dispatch_on_select(payload=payload)
        connection.close()

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(
            responses.POST, "https://bap.example.com/on_select", callback=on_select_callback
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(attempt, [1, 2]))

    assert len(results) == 2
    successes = sum(1 for won in results.values() if won)
    assert successes == 1

    slot.refresh_from_db()
    assert slot.capacity_remaining == 0
    assert Booking.objects.filter(slot=slot, status=Booking.Status.HELD).count() == 1


@pytest.mark.django_db
def test_reselecting_a_different_slot_releases_the_first_hold(bpp_identity_settings):
    resource, slot_a = _make_resource_with_slot(
        capacity=1, start_time=timezone.now().replace(microsecond=0)
    )
    slot_b = Slot.objects.create(
        resource=resource,
        start_time=slot_a.start_time + dt.timedelta(hours=1),
        end_time=slot_a.start_time + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )

    def run_select(slot):
        payload = _build_select_payload(
            item_id=str(resource.id), requested_timestamp=slot.start_time.isoformat()
        )
        with responses.RequestsMock() as rsps:
            rsps.add_callback(
                responses.POST,
                "https://bap.example.com/on_select",
                callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
            )
            select_service.dispatch_on_select(payload=payload)

    run_select(slot_a)
    slot_a.refresh_from_db()
    assert slot_a.capacity_remaining == 0
    first_booking = Booking.objects.get(slot=slot_a)
    assert first_booking.status == Booking.Status.HELD

    run_select(slot_b)

    slot_a.refresh_from_db()
    slot_b.refresh_from_db()
    first_booking.refresh_from_db()
    assert first_booking.status == Booking.Status.CANCELLED
    assert slot_a.capacity_remaining == 1
    assert slot_b.capacity_remaining == 0
    assert Booking.objects.filter(slot=slot_b, status=Booking.Status.HELD).count() == 1


# --- livetracker4.md §2.1 cutover (2026-08-02): select_service.py's own
# hold-creation path never wired event_bus at all before this cutover — a real
# gap found while removing the inline record_hold_created()/broadcast_slot_update()
# calls (see select_service.py's own comments). The tests below prove the fix:
# a real /select now genuinely publishes the events the removed inline calls
# used to substitute for. ---------------------------------------------------


@pytest.fixture
def isolated_bus():
    """A local EventBus on its own uniquely-named queue, patched in as
    core.select_service's own get_event_bus() for the duration of a test —
    avoids the shared, uncontrolled default queue every other test that
    exercises confirm/cancel/update's real event_bus= wiring also publishes
    into, matching the isolation discipline test_events_worker.py's own
    _isolated_bus_and_env() established for subprocess-based tests."""
    suffix = uuid.uuid4().hex[:12]
    bus = EventBus(
        redis_url=django_settings.EVENT_BUS_URL,
        queue_name=f"test-select-queue-{suffix}",
        dlq_name=f"test-select-dlq-{suffix}",
    )
    with patch("core.select_service.get_event_bus", return_value=bus):
        yield bus
    bus._redis.delete(bus.queue_name, bus.dlq_name, bus.processing_queue_name)


@pytest.mark.django_db
def test_dispatch_on_select_publishes_a_real_slot_reserved_event(
    bpp_identity_settings, isolated_bus
):
    resource, slot = _make_resource_with_slot(price_value="500.00")
    payload = _build_select_payload(
        item_id=str(resource.id), requested_timestamp=slot.start_time.isoformat()
    )

    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.POST,
            "https://bap.example.com/on_select",
            json={"message": {"ack": {"status": "ACK"}}},
        )
        select_service.dispatch_on_select(payload=payload)

    booking = Booking.objects.get(slot=slot)
    event = isolated_bus.consume_one(timeout_seconds=2)
    assert event is not None, "dispatch_on_select must publish a real SlotEvent.RESERVED"
    assert event["event_type"] == SlotEvent.RESERVED
    assert event["payload"]["slot_id"] == str(slot.id)
    assert event["payload"]["booking_id"] == str(booking.id)
    assert event["payload"]["holder_ref"] == "txn-1"


@pytest.mark.django_db
def test_reselecting_publishes_released_and_superseded_cancelled_events(
    bpp_identity_settings, isolated_bus
):
    """Real, previously-nonexistent capability closed by this cutover: before
    event_bus was wired into release_prior_hold_for_transaction, a re-selection's
    own release was never observable on the bus at all — not audit-logged, not
    broadcast via the worker path. Now it publishes both SlotEvent.RELEASED and
    BookingEvent.CANCELLED(reason=superseded_by_reselect), matching every other
    real release path."""
    resource, slot_a = _make_resource_with_slot(
        capacity=1, start_time=timezone.now().replace(microsecond=0)
    )
    slot_b = Slot.objects.create(
        resource=resource,
        start_time=slot_a.start_time + dt.timedelta(hours=1),
        end_time=slot_a.start_time + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )

    def run_select(slot):
        payload = _build_select_payload(
            item_id=str(resource.id), requested_timestamp=slot.start_time.isoformat()
        )
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://bap.example.com/on_select",
                json={"message": {"ack": {"status": "ACK"}}},
            )
            select_service.dispatch_on_select(payload=payload)

    run_select(slot_a)
    first_booking = Booking.objects.get(slot=slot_a)
    # Drain slot_a's own SlotEvent.RESERVED before re-selecting, so the queue
    # only holds the release's events by the time we assert on them below.
    reserved = isolated_bus.consume_one(timeout_seconds=2)
    assert reserved["event_type"] == SlotEvent.RESERVED

    run_select(slot_b)

    events_by_type = {}
    for _ in range(3):
        event = isolated_bus.consume_one(timeout_seconds=2)
        if event is not None:
            events_by_type[event["event_type"]] = event

    assert SlotEvent.RELEASED in events_by_type
    assert events_by_type[SlotEvent.RELEASED]["payload"]["slot_id"] == str(slot_a.id)

    assert BookingEvent.CANCELLED in events_by_type
    cancelled_payload = events_by_type[BookingEvent.CANCELLED]["payload"]
    assert cancelled_payload["booking_id"] == str(first_booking.id)
    assert cancelled_payload["reason"] == "superseded_by_reselect"

    assert SlotEvent.RESERVED in events_by_type


def _bpp_backend_dir() -> str:
    return os.getcwd()


def _worker_output(worker: subprocess.Popen) -> str:
    if worker.poll() is None:
        return "(still running)"
    return worker.stdout.read()


@pytest.mark.django_db(transaction=True)
def test_a_real_select_hold_is_consumed_by_a_genuinely_separate_worker_process(
    bpp_identity_settings, settings
):
    """The full §2.1 Test Gate, applied to /select specifically: proves the
    newly-wired event_bus= publish (added as part of this cutover) is actually
    consumed by a genuinely separate OS process, incrementing the real
    hold_created counter — not just that the event gets published (the tests
    above), and not the process-wide core.events.get_event_bus() singleton's
    default queue (shared, uncontrolled by this test), but the same isolated
    queue/DLQ this subprocess is told about via env vars, matching
    test_events_worker.py's own _isolated_bus_and_env() technique. Resets the
    module-level singleton so the in-process publish and the subprocess consume
    from the identical isolated pair.

    Also carries the two env-propagation fixes `_isolated_bus_and_env()` needed
    (see that function's own docstring for the full story, found live while first
    writing this test): `DATABASE_URL` rebuilt from the actual live `test_bpp`
    connection (not the inherited dev-DB env var), and `TESTING=true` so the
    subprocess's own `bpp/settings.py` redirects its Redis-backed metrics
    counters to the test-only DB 15 this test's own `get_counter()` reads from —
    without it, the subprocess silently increments the real dev counter instead,
    and this test's `before`/`after` comparison never observes it."""
    from django.db import connection

    suffix = uuid.uuid4().hex[:12]
    queue_name = f"test-select-worker-queue-{suffix}"
    dlq_name = f"test-select-worker-dlq-{suffix}"
    db = connection.settings_dict
    real_database_url = (
        f"postgres://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )
    isolated_env = {
        **os.environ,
        "EVENT_BUS_QUEUE_NAME": queue_name,
        "EVENT_BUS_DLQ_NAME": dlq_name,
        "DATABASE_URL": real_database_url,
        "TESTING": "true",
    }
    settings.EVENT_BUS_QUEUE_NAME = queue_name
    settings.EVENT_BUS_DLQ_NAME = dlq_name
    events_module._bus = None  # force get_event_bus() to rebuild against the isolated queue
    try:
        resource, slot = _make_resource_with_slot(price_value="500.00")
        payload = _build_select_payload(
            item_id=str(resource.id), requested_timestamp=slot.start_time.isoformat()
        )

        before = get_counter("bpp:metrics:hold_created")

        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.POST,
                "https://bap.example.com/on_select",
                json={"message": {"ack": {"status": "ACK"}}},
            )
            select_service.dispatch_on_select(payload=payload)

        worker = subprocess.Popen(
            [sys.executable, "manage.py", "run_event_worker"],
            cwd=_bpp_backend_dir(),
            env=isolated_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            assert worker.pid != os.getpid()

            deadline = time_module.time() + 15
            after = before
            while time_module.time() < deadline:
                after = get_counter("bpp:metrics:hold_created")
                if after > before:
                    break
                time_module.sleep(0.2)

            assert after == before + 1, (
                "expected hold_created to increment by exactly 1 via the real, "
                f"separate worker process — before={before} after={after} — "
                f"worker output so far:\n{_worker_output(worker)}"
            )
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=10)
    finally:
        events_module._bus = None  # don't leak the isolated bus into later tests
