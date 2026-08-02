"""livetracker4.md §2.4 Test Gate — BAP-Side Reconciliation Sweep Parity.

FUNC/DR: deliberately drop a real /on_confirm callback (simulating a lost
message) and confirm the sweep detects the stale session and self-heals it —
re-triggering /confirm — within one sweep interval, without manual customer
action. Also proves the critical correctness boundary found by design audit
before implementing: a session that never triggered /confirm at all must
never be auto-confirmed.
"""

import datetime as dt
import json

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core import reconciliation
from core.models import SearchSession

Customer = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bap_identity_settings(settings, tmp_path):
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bap-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bap-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _session_with_init(
    *, transaction_id="txn-1", bpp_id="bpp.example.com", customer=None, confirm_triggered_at=None
):
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="haircut", domain="ONDC:RET13", customer=customer
    )
    session.selected_bpp_id = bpp_id
    session.selected_bpp_uri = f"https://{bpp_id}"
    session.init_order = {
        "provider": {"id": "biz-1"},
        "items": [{"id": "item-1"}],
        "fulfillments": [{"id": "booking-1", "stops": [{"type": "start"}]}],
        "quote": {
            "price": {"currency": "INR", "value": "500.00"},
            "breakup": [
                {
                    "item": {"id": "item-1"},
                    "title": "Stylist A",
                    "price": {"currency": "INR", "value": "500.00"},
                }
            ],
        },
    }
    session.confirm_triggered_at = confirm_triggered_at
    session.save()
    return session


def _mock_bpp_registry_lookup(rsps, *, bpp_id="bpp.example.com", url="https://bpp.example.com"):
    def callback(request):
        filters = json.loads(request.body)
        assert filters["subscriber_id"] == bpp_id
        body = [{"subscriber_id": bpp_id, "status": "SUBSCRIBED", "url": url}]
        return (200, {}, json.dumps(body))

    rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=callback)


def _bpp_confirm_ack_callback(captured_requests):
    def callback(request):
        captured_requests.append(request)
        body = json.loads(request.body)
        return (
            200,
            {},
            json.dumps({"context": body["context"], "message": {"ack": {"status": "ACK"}}}),
        )

    return callback


@pytest.mark.django_db
def test_sweep_resyncs_a_session_whose_confirm_callback_was_genuinely_lost(bap_identity_settings):
    """The real §2.4 Test Gate: a real /confirm was dispatched (confirm_triggered_at
    set), its callback never arrived (confirmed_order/error both still null), and
    enough time has genuinely passed. The sweep must re-trigger /confirm — the
    real, idempotent self-healing mechanism (see reconciliation.py's own
    docstring for why /status can't do this instead)."""
    stale_time = timezone.now() - dt.timedelta(seconds=600)
    session = _session_with_init(confirm_triggered_at=stale_time)
    captured_requests = []

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST,
            "https://bpp.example.com/confirm",
            callback=_bpp_confirm_ack_callback(captured_requests),
        )
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 1
    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "confirm"
    assert forwarded["context"]["transaction_id"] == session.transaction_id

    session.refresh_from_db()
    assert session.confirm_triggered_at > stale_time  # trigger_confirm() stamped a fresh attempt


@pytest.mark.django_db
def test_sweep_does_not_touch_a_session_that_never_triggered_confirm_at_all(bap_identity_settings):
    """The critical correctness boundary found by design audit before
    implementing: init_order set, confirmed_order/error both null, but
    confirm_triggered_at was *never* set — the customer simply never clicked
    confirm. Auto-triggering it here would commit/bill a booking nobody asked
    for. Zero real dispatch must happen."""
    _session_with_init(confirm_triggered_at=None)

    with responses.RequestsMock(assert_all_requests_are_fired=False):
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 0


@pytest.mark.django_db
def test_sweep_does_not_touch_a_confirm_still_genuinely_in_flight(bap_identity_settings):
    """confirm_triggered_at set, but only moments ago — not yet stale. A real
    trigger in flight must not be double-triggered."""
    recent = timezone.now() - dt.timedelta(seconds=5)
    _session_with_init(confirm_triggered_at=recent)

    with responses.RequestsMock(assert_all_requests_are_fired=False):
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 0


@pytest.mark.django_db
def test_sweep_does_not_touch_an_already_resolved_confirmation(bap_identity_settings):
    """confirm_triggered_at is old, but confirmed_order is genuinely already
    set — the callback arrived, nothing is actually stale."""
    stale_time = timezone.now() - dt.timedelta(seconds=600)
    session = _session_with_init(confirm_triggered_at=stale_time)
    session.confirmed_order = {"id": "booking-1", "status": "ACTIVE"}
    session.save()

    with responses.RequestsMock(assert_all_requests_are_fired=False):
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 0


@pytest.mark.django_db
def test_sweep_does_not_touch_a_confirmation_that_already_resolved_to_an_error(
    bap_identity_settings,
):
    """Symmetric to the success case above — confirmed_error set is also a
    genuinely resolved outcome, not a stale one."""
    stale_time = timezone.now() - dt.timedelta(seconds=600)
    session = _session_with_init(confirm_triggered_at=stale_time)
    session.confirmed_error = {"code": "SLOT_UNAVAILABLE", "message": "no longer available"}
    session.save()

    with responses.RequestsMock(assert_all_requests_are_fired=False):
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 0


@pytest.mark.django_db
def test_full_dropped_callback_scenario_end_to_end_via_the_real_http_trigger(
    bap_identity_settings, client
):
    """DR — the real Test Gate scenario, driven through the actual customer-
    facing HTTP endpoint (not a direct service-layer call), then self-healed by
    the sweep: a real customer triggers /confirm (stamping confirm_triggered_at
    via the real view), the /on_confirm callback is deliberately never sent
    (simulating a lost message), then the sweep — run after the session is
    artificially aged past the staleness threshold — re-triggers it and a
    second, real BPP dispatch happens."""
    _session_with_init()

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST,
            "https://bpp.example.com/confirm",
            callback=_bpp_confirm_ack_callback([]),
        )
        resp = client.post(
            reverse("confirm-trigger"),
            data=json.dumps({"transaction_id": "txn-1"}),
            content_type="application/json",
        )
    assert resp.status_code == 202

    session = SearchSession.objects.get(transaction_id="txn-1")
    assert session.confirm_triggered_at is not None
    assert session.confirmed_order is None
    assert session.confirmed_error is None  # the "lost callback" — never arrives

    # Age the session artificially past the staleness threshold, simulating
    # real wall-clock time passing with no callback ever received.
    session.confirm_triggered_at = timezone.now() - dt.timedelta(seconds=600)
    session.save()

    captured_requests = []
    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST,
            "https://bpp.example.com/confirm",
            callback=_bpp_confirm_ack_callback(captured_requests),
        )
        resynced = reconciliation.sweep_stale_confirmations(stale_after_seconds=300)

    assert resynced == 1
    assert len(captured_requests) == 1  # the real self-healing re-dispatch
