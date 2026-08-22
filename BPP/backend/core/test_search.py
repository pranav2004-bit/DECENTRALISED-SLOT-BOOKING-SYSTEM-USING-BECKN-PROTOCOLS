"""Phase 3.1 Test Gate (livetracker2.md §3.1) pieces owned by BPP: real /search
receipt (verifying both the BAP and the forwarding Gateway) and real /on_search
dispatch (building the real Beauty catalog and sending it to Gateway).
"""

import json
from unittest.mock import patch

import pytest
import responses
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from inventory_core.models import Resource

from core import search_service
from core.crypto import generate_signing_key_pair, sign_outbound_request

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "unused-in-this-test"  # pragma: allowlist secret


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def bpp_identity_settings(settings, tmp_path):
    """Deterministic BPP identity for these tests — never depend on ambient .env
    values, which differ between local dev and CI (e.g. GATEWAY_BASE_URL is
    `http://beckn-gateway:8000` in this repo's real .env, a real Docker service name,
    not something a unit test should assume or accidentally hit)."""
    settings.SIGNING_PRIVATE_KEY_PATH = str(tmp_path / "signing.json")
    settings.SUBSCRIBER_ID = "bpp-backend.local"
    settings.UNIQUE_KEY_ID = "key-1"
    settings.SUBSCRIBER_URL = "https://bpp-backend.local"
    settings.GATEWAY_BASE_URL = "http://gateway:8000"
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _build_search_payload(*, bap_id="bap.example.com", query=None, domain="ONDC:RET13"):
    intent = {"item": {"descriptor": {"name": query}}} if query else {}
    return {
        "context": {
            "domain": domain,
            "location": {"country": {"code": "IND"}},
            "action": "search",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": "txn-1",
            "message_id": "msg-1",
            "timestamp": "2026-07-19T00:00:00Z",
        },
        "message": {"intent": intent},
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
@patch("core.search_service.dispatch_on_search_in_background")
def test_search_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    mock_dispatch, client
):
    """`dispatch_on_search_in_background` is mocked here deliberately — this test
    only checks the synchronous ACK response shape; real dispatch behavior (the
    actual catalog build + send to Gateway) is covered by
    test_dispatch_on_search_sends_the_real_catalog_to_gateway below, called directly
    and synchronously so it isn't racing a background thread. Without this mock, the
    view would fire a genuine daemon thread that outlives this test (and the whole
    pytest process), hits the ambient GATEWAY_BASE_URL for real, and can trip a
    Postgres "database is being accessed by other users" teardown warning."""
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload()
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json() == {"context": payload["context"], "message": {"ack": {"status": "ACK"}}}
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_search_view_rejects_missing_gateway_authorization(client):
    """The defense-in-depth check: a genuinely BAP-signed request with no
    X-Gateway-Authorization at all must be rejected — BPP must never accept search
    traffic that bypassed Gateway, even with a valid BAP signature."""
    bap_pub, bap_priv = generate_signing_key_pair()
    payload = _build_search_payload()
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    known = _known(bap_pub=bap_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_search_view_rejects_bap_id_impersonation(client):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload(bap_id="someone-else.example.com")
    body = json.dumps(payload).encode()
    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 401


@pytest.mark.django_db
def test_dispatch_on_search_sends_the_real_catalog_to_gateway(bpp_identity_settings):
    """Real end-to-end proof, not structural-only: a real BusinessAccount + Resource
    exist, dispatch_on_search is called directly (not through the view/thread, to
    avoid racing it), and the exact catalog data (real business/resource names) is
    confirmed present in the payload actually sent to Gateway's /on_search."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )
    Resource.objects.create(
        owner_ref=str(business.id), name="Stylist A", code="STY-A", category_id="ONDC:RET13"
    )

    payload = _build_search_payload()
    captured_requests = []

    def gateway_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_search", callback=gateway_on_search_callback
        )
        search_service.dispatch_on_search(payload=payload)

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["context"]["action"] == "on_search"
    assert forwarded["context"]["transaction_id"] == "txn-1"
    assert forwarded["context"]["message_id"] == "msg-1"
    assert forwarded["context"]["bap_id"] == "bap.example.com"
    assert forwarded["context"]["bpp_id"] == "bpp-backend.local"
    provider = forwarded["message"]["catalog"]["providers"][0]
    assert provider["descriptor"]["name"] == "Glow Salon"
    assert provider["items"][0]["descriptor"]["name"] == "Stylist A"
    assert "Authorization" in captured_requests[0].headers


@pytest.mark.django_db
def test_dispatch_on_search_filters_the_real_catalog_by_the_real_query_text(
    bpp_identity_settings,
):
    """livetracker3.md §1.1 Test Gate: a real query on the wire (not an empty intent,
    as the test above uses) narrows the catalog actually sent to Gateway down to the
    matching resource only — proving `dispatch_on_search` reads
    `message.intent.item.descriptor.name` from the real inbound payload, not just
    `context["domain"]` as before this fix."""
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )
    Resource.objects.create(owner_ref=str(business.id), name="Senior Stylist - Priya")
    Resource.objects.create(owner_ref=str(business.id), name="Junior Stylist - Anjali")

    payload = _build_search_payload(query="Priya")
    captured_requests = []

    def gateway_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_search", callback=gateway_on_search_callback
        )
        search_service.dispatch_on_search(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    provider = forwarded["message"]["catalog"]["providers"][0]
    items = provider["items"]
    assert [item["descriptor"]["name"] for item in items] == ["Senior Stylist - Priya"]


@pytest.mark.django_db
def test_dispatch_on_search_with_a_nonsense_query_returns_zero_providers_not_an_error(
    bpp_identity_settings,
):
    business = BusinessAccount.objects.create_user(
        contact="salon@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )
    Resource.objects.create(owner_ref=str(business.id), name="Stylist A")

    payload = _build_search_payload(query="xyzzy-no-such-thing")
    captured_requests = []

    def gateway_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_search", callback=gateway_on_search_callback
        )
        search_service.dispatch_on_search(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    assert forwarded["message"]["catalog"]["providers"] == []


@pytest.mark.django_db
def test_search_view_nacks_a_direct_off_domain_request_when_bpp_is_single_domain_scoped(
    settings, client
):
    """livetracker7.md §1.3 Test Gate: a BPP instance configured
    `SUPPORTED_DOMAINS=["ONDC:SRV13"]` (Healthcare only) must genuinely reject a
    direct `/search` for `"domain": "BECKN:AUTO01"` (Automotive) — a real NACK, not
    a 200 the caller could mistake for "no results happen to exist". This proves
    the BPP itself refuses the request at the request boundary, independent of
    whatever Registry/Gateway filtering would otherwise have done.

    The domain check runs before trust/signature verification (core/search_service.py
    checks context/domain first, trust second), so — as this test's own absence of a
    registered `/lookup` mock confirms — a rejected off-domain request never even
    triggers a Registry lookup for the BAP/Gateway public keys, a real efficiency
    side-benefit of failing fast on the cheapest check first."""
    settings.SUPPORTED_DOMAINS = ["ONDC:SRV13"]
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload(domain="BECKN:AUTO01")
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )

    with responses.RequestsMock(assert_all_requests_are_fired=False):
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    resp_json = resp.json()
    assert resp_json["message"]["ack"]["status"] == "NACK"
    assert resp_json["error"]["code"] == "SEARCH_ERROR"
    assert "BECKN:AUTO01" in resp_json["error"]["message"]


@pytest.mark.django_db
@patch("core.search_service.dispatch_on_search_in_background")
def test_search_view_acks_an_in_scope_domain_when_bpp_is_single_domain_scoped(
    mock_dispatch, settings, client
):
    """The other half of the same Test Gate: the identical single-domain-scoped
    instance still correctly ACKs a real, in-scope Healthcare search — narrowing
    `SUPPORTED_DOMAINS` rejects only what's genuinely out of scope, not everything."""
    settings.SUPPORTED_DOMAINS = ["ONDC:SRV13"]
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_search_payload(domain="ONDC:SRV13")
    body = json.dumps(payload).encode()

    bap_header = sign_outbound_request(
        body=body, subscriber_id="bap.example.com", unique_key_id="key-1",
        signing_private_key_b64=bap_priv,
    )
    gateway_header = sign_outbound_request(
        body=body, subscriber_id="gateway.local", unique_key_id="key-1",
        signing_private_key_b64=gateway_priv,
    )
    known = _known(bap_pub=bap_pub, gateway_pub=gateway_pub)

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("search"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.django_db
def test_dispatch_on_search_returns_real_results_for_an_in_scope_healthcare_search(
    settings, bpp_identity_settings,
):
    """livetracker7.md §1.3 Test Gate's other half, exercised at the dispatch layer
    (matching test_dispatch_on_search_sends_the_real_catalog_to_gateway's own
    pattern above): a single-domain-scoped instance's real Healthcare business is
    still found and returned, proving the new request-boundary check doesn't also
    accidentally narrow catalog *content* — only which domains are accepted at all."""
    settings.SUPPORTED_DOMAINS = ["ONDC:SRV13"]
    business = BusinessAccount.objects.create_user(
        contact="clinic@example.com", business_name="Demo Health Clinic",
        password=TEST_PASSWORD, domain_code="ONDC:SRV13",
    )
    Resource.objects.create(
        owner_ref=str(business.id), name="Demo Doctor", category_id="ONDC:SRV13"
    )

    payload = _build_search_payload(domain="ONDC:SRV13")
    captured_requests = []

    def gateway_on_search_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_search", callback=gateway_on_search_callback
        )
        search_service.dispatch_on_search(payload=payload)

    forwarded = json.loads(captured_requests[0].body)
    provider = forwarded["message"]["catalog"]["providers"][0]
    assert provider["descriptor"]["name"] == "Demo Health Clinic"
    assert provider["items"][0]["descriptor"]["name"] == "Demo Doctor"
