"""Test Gate for availability_service.py — the customer-facing availability-by-date
check that decides which doctor/stylist/mechanic cards the search-results UI even
renders (livetracker9.md). Mirrors test_select.py's own fixtures/mocking conventions
exactly (same direct-dispatch pattern, see availability_service.py's own docstring).
"""

import json

import pytest
import responses
from django.test import Client
from django.urls import reverse

from core.models import SearchSession


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
    yield settings


def _session_with_result(*, transaction_id="txn-1", bpp_id="bpp.example.com"):
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="", domain="ONDC:SRV13"
    )
    session.results = [
        {
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "catalog": {
                "descriptor": {"name": "Healthcare Catalog"},
                "providers": [
                    {
                        "id": "provider-1",
                        "descriptor": {"name": "Pavan Group Of Hospitals"},
                        "category_id": "ONDC:SRV13",
                        "items": [
                            {"id": "doctor-1", "descriptor": {"name": "Anoop"}},
                            {"id": "doctor-2", "descriptor": {"name": "Bhimaraju"}},
                        ],
                    }
                ],
            },
        }
    ]
    session.save()
    return session


def _mock_bpp_registry_lookup(rsps, *, bpp_id="bpp.example.com", url="https://bpp.example.com"):
    def callback(request):
        filters = json.loads(request.body)
        assert filters["subscriber_id"] == bpp_id
        body = [{"subscriber_id": bpp_id, "status": "SUBSCRIBED", "url": url}]
        return (200, {}, json.dumps(body))

    rsps.add_callback(responses.POST, "http://registry:8000/lookup", callback=callback)


def _ack(body, *, availability):
    payload = json.loads(body)
    return (
        200,
        {},
        json.dumps(
            {
                "context": payload["context"],
                "message": {"ack": {"status": "ACK"}, "availability": availability},
            }
        ),
    )


@pytest.mark.django_db
def test_posts_a_real_signed_request_for_only_this_providers_items(bap_identity_settings, client):
    """FUNC: resolves the real BPP from this session's own on_search results (the
    customer never supplies bpp_id), sends exactly the requested date, and the
    request itself carries a real Authorization signature."""
    _session_with_result()
    captured = []

    def bpp_callback(request):
        captured.append(request)
        return _ack(request.body, availability={"doctor-1": True, "doctor-2": False})

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/check_availability", callback=bpp_callback
        )
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_ids": ["doctor-1", "doctor-2"],
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.json() == {"availability": {"doctor-1": True, "doctor-2": False}}
    assert len(captured) == 1
    forwarded = json.loads(captured[0].body)
    assert forwarded["context"]["action"] == "check_availability"
    assert forwarded["context"]["bap_id"] == "bap-backend.local"
    assert forwarded["context"]["bpp_id"] == "bpp.example.com"
    assert forwarded["message"]["resource_ids"] == ["doctor-1", "doctor-2"]
    assert forwarded["message"]["date"] == "2026-11-01"
    assert "Authorization" in captured[0].headers


@pytest.mark.django_db
def test_drops_a_resource_id_that_does_not_belong_to_the_given_provider(
    bap_identity_settings, client
):
    """Anti-IDOR: a client-supplied resource_id outside this session's own real
    results for `provider_id` must never reach BPP — this endpoint is not a probe
    for arbitrary resource ids."""
    _session_with_result()
    captured = []

    def bpp_callback(request):
        captured.append(request)
        return _ack(request.body, availability={"doctor-1": True})

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/check_availability", callback=bpp_callback
        )
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_ids": ["doctor-1", "someone-elses-doctor-id"],
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    forwarded = json.loads(captured[0].body)
    assert forwarded["message"]["resource_ids"] == ["doctor-1"]


@pytest.mark.django_db
def test_never_calls_bpp_when_every_requested_id_is_invalid(bap_identity_settings, client):
    _session_with_result()

    with responses.RequestsMock() as rsps:
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_ids": ["not-a-real-item"],
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        assert len(rsps.calls) == 0

    assert resp.status_code == 200
    assert resp.json() == {"availability": {}}


@pytest.mark.django_db
def test_rejects_an_unknown_provider_id(bap_identity_settings, client):
    _session_with_result()

    resp = client.post(
        reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
        data=json.dumps(
            {"provider_id": "no-such-provider", "resource_ids": ["doctor-1"], "date": "2026-11-01"}
        ),
        content_type="application/json",
    )

    assert resp.status_code == 404


@pytest.mark.django_db
def test_rejects_an_unknown_transaction_id(bap_identity_settings, client):
    resp = client.post(
        reverse("check-availability", kwargs={"transaction_id": "no-such-txn"}),
        data=json.dumps(
            {"provider_id": "provider-1", "resource_ids": ["doctor-1"], "date": "2026-11-01"}
        ),
        content_type="application/json",
    )

    assert resp.status_code == 404


@pytest.mark.django_db
def test_rejects_an_invalid_date_format(bap_identity_settings, client):
    _session_with_result()

    resp = client.post(
        reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
        data=json.dumps(
            {"provider_id": "provider-1", "resource_ids": ["doctor-1"], "date": "tomorrow"}
        ),
        content_type="application/json",
    )

    assert resp.status_code == 400


@pytest.mark.django_db
def test_propagates_a_bpp_nack_as_a_502(bap_identity_settings, client):
    _session_with_result()

    def bpp_callback(request):
        payload = json.loads(request.body)
        return (
            400,
            {},
            json.dumps(
                {
                    "context": payload["context"],
                    "message": {"ack": {"status": "NACK"}},
                    "error": {"code": "AVAILABILITY_ERROR", "message": "bad batch"},
                }
            ),
        )

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/check_availability", callback=bpp_callback
        )
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {"provider_id": "provider-1", "resource_ids": ["doctor-1"], "date": "2026-11-01"}
            ),
            content_type="application/json",
        )

    assert resp.status_code == 502


# --- check_doctor_availability batching (more ids than one BPP call's own cap) ---


def _session_with_many_items(n, *, transaction_id="txn-many", bpp_id="bpp.example.com"):
    items = [{"id": f"doctor-{i}", "descriptor": {"name": f"Doctor {i}"}} for i in range(n)]
    session = SearchSession.objects.create(
        transaction_id=transaction_id, query="", domain="ONDC:SRV13"
    )
    session.results = [
        {
            "bpp_id": bpp_id,
            "bpp_uri": f"https://{bpp_id}",
            "catalog": {
                "descriptor": {"name": "Healthcare Catalog"},
                "providers": [
                    {
                        "id": "provider-1",
                        "descriptor": {"name": "Pavan Group Of Hospitals"},
                        "category_id": "ONDC:SRV13",
                        "items": items,
                    }
                ],
            },
        }
    ]
    session.save()
    return session


@pytest.mark.django_db
def test_more_than_the_per_call_cap_is_split_into_several_signed_calls_not_truncated(
    bap_identity_settings, client
):
    """A provider with more items than one BPP call can carry (MAX_RESOURCE_IDS=50
    on both sides) must still get a real answer for every one of them — not a
    silent false for whatever didn't fit in the first batch."""
    _session_with_many_items(75, transaction_id="txn-many")
    calls = []

    def bpp_callback(request):
        calls.append(request)
        payload = json.loads(request.body)
        ids = payload["message"]["resource_ids"]
        return _ack(request.body, availability={rid: True for rid in ids})

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/check_availability", callback=bpp_callback
        )
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-many"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_ids": [f"doctor-{i}" for i in range(75)],
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert len(calls) == 2
    assert len(json.loads(calls[0].body)["message"]["resource_ids"]) == 50
    assert len(json.loads(calls[1].body)["message"]["resource_ids"]) == 25
    availability = resp.json()["availability"]
    assert len(availability) == 75
    assert all(availability[f"doctor-{i}"] is True for i in range(75))


@pytest.mark.django_db
def test_a_later_batchs_failure_does_not_erase_an_earlier_batchs_real_answers(
    bap_identity_settings, client
):
    _session_with_many_items(75, transaction_id="txn-many")

    def bpp_callback(request):
        payload = json.loads(request.body)
        ids = payload["message"]["resource_ids"]
        if len(ids) == 50:
            return _ack(request.body, availability={rid: True for rid in ids})
        return (500, {}, json.dumps({"error": "boom"}))

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/check_availability", callback=bpp_callback
        )
        resp = client.post(
            reverse("check-availability", kwargs={"transaction_id": "txn-many"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_ids": [f"doctor-{i}" for i in range(75)],
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    availability = resp.json()["availability"]
    assert len(availability) == 50
    assert all(availability[f"doctor-{i}"] is True for i in range(50))


# --- list_doctor_slots: the real time-of-day picker for /select ---


@pytest.mark.django_db
def test_slots_posts_a_real_signed_request_for_this_one_resource(bap_identity_settings, client):
    _session_with_result()
    captured = []

    def bpp_callback(request):
        captured.append(request)
        payload = json.loads(request.body)
        return (
            200,
            {},
            json.dumps(
                {
                    "context": payload["context"],
                    "message": {
                        "ack": {"status": "ACK"},
                        "slots": [
                            {
                                "start_time": "2026-11-01T10:00:00+00:00",
                                "end_time": "2026-11-01T10:30:00+00:00",
                            }
                        ],
                    },
                }
            ),
        )

    with responses.RequestsMock() as rsps:
        _mock_bpp_registry_lookup(rsps)
        rsps.add_callback(
            responses.POST, "https://bpp.example.com/list_available_slots", callback=bpp_callback
        )
        resp = client.post(
            reverse("list-available-slots", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {"provider_id": "provider-1", "resource_id": "doctor-1", "date": "2026-11-01"}
            ),
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert resp.json()["slots"] == [
        {"start_time": "2026-11-01T10:00:00+00:00", "end_time": "2026-11-01T10:30:00+00:00"}
    ]
    assert len(captured) == 1
    forwarded = json.loads(captured[0].body)
    assert forwarded["context"]["action"] == "list_available_slots"
    assert forwarded["message"]["resource_id"] == "doctor-1"
    assert forwarded["message"]["date"] == "2026-11-01"


@pytest.mark.django_db
def test_slots_never_calls_bpp_for_a_resource_outside_this_provider(bap_identity_settings, client):
    """Anti-IDOR: same discipline as check_doctor_availability."""
    _session_with_result()

    with responses.RequestsMock() as rsps:
        resp = client.post(
            reverse("list-available-slots", kwargs={"transaction_id": "txn-1"}),
            data=json.dumps(
                {
                    "provider_id": "provider-1",
                    "resource_id": "someone-elses-doctor-id",
                    "date": "2026-11-01",
                }
            ),
            content_type="application/json",
        )
        assert len(rsps.calls) == 0

    assert resp.status_code == 200
    assert resp.json()["slots"] == []


@pytest.mark.django_db
def test_slots_rejects_an_unknown_provider_id(bap_identity_settings, client):
    _session_with_result()

    resp = client.post(
        reverse("list-available-slots", kwargs={"transaction_id": "txn-1"}),
        data=json.dumps(
            {"provider_id": "no-such-provider", "resource_id": "doctor-1", "date": "2026-11-01"}
        ),
        content_type="application/json",
    )

    assert resp.status_code == 404


@pytest.mark.django_db
def test_slots_rejects_an_invalid_date_format(bap_identity_settings, client):
    _session_with_result()

    resp = client.post(
        reverse("list-available-slots", kwargs={"transaction_id": "txn-1"}),
        data=json.dumps(
            {"provider_id": "provider-1", "resource_id": "doctor-1", "date": "tomorrow"}
        ),
        content_type="application/json",
    )

    assert resp.status_code == 400
