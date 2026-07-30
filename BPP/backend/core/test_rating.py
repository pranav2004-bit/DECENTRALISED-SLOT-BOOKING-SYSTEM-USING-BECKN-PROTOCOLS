"""Phase 4.5 Test Gate (livetracker2.md §4.5) pieces owned by BPP: real /rating
receipt (verifying both the BAP and the forwarding Gateway, and the request
shape) and real /on_rating dispatch — genuinely persisting every submitted
rating as a `Rating` row, including the IDOR-shaped holder_ref-mismatch case
(rating still recorded, just not FK-linked to a booking this transaction
doesn't own) and, per the phase's own Test Gate wording, rating against a
genuinely `COMPLETE` booking (reached via the real `complete_active_booking()`
trigger, not a hand-set status).
"""

import datetime as dt
import json
from decimal import Decimal
from unittest.mock import patch

import pytest
import redis
import responses
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from inventory_core.models import Booking, Rating, Resource, Slot
from inventory_core.reservation import complete_active_booking, confirm_hold, hold_slot

from core import rating_service
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
    from core import participant_keys

    participant_keys.get_signing_keys.cache_clear()
    yield settings


def _redis_client() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)


def _make_slot_and_business(*, ended=False):
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
        price_value="899.00",
    )
    if ended:
        start_time = timezone.now() - dt.timedelta(hours=2)
        end_time = timezone.now() - dt.timedelta(hours=1)
    else:
        start_time = timezone.now().replace(microsecond=0)
        end_time = start_time + dt.timedelta(minutes=30)
    slot = Slot.objects.create(
        resource=resource,
        start_time=start_time,
        end_time=end_time,
        capacity_total=1,
        capacity_remaining=1,
    )
    return resource, slot


def _make_completed_booking(*, holder_ref="txn-1"):
    """Reaches Booking.Status.COMPLETE the real, honest way (§4.5's Test Gate
    requirement) — via the same complete_active_booking() the reconciliation
    sweep uses, not a hand-set .status assignment."""
    resource, slot = _make_slot_and_business(ended=True)
    booking = hold_slot(
        slot.id, holder_ref=holder_ref, redis_client=_redis_client(), ttl_seconds=600
    )
    confirm_hold(booking.id, redis_client=_redis_client())
    assert complete_active_booking(booking.id) is True
    booking.refresh_from_db()
    assert booking.status == Booking.Status.COMPLETE
    return resource, slot, booking


def _build_rating_payload(*, ratings, bap_id="bap.example.com", transaction_id="txn-1"):
    return {
        "context": {
            "domain": "ONDC:RET13",
            "location": {"country": {"code": "IND"}},
            "action": "rating",
            "version": "1.1.0",
            "bap_id": bap_id,
            "bap_uri": f"https://{bap_id}",
            "transaction_id": transaction_id,
            "message_id": "msg-1",
            "timestamp": "2026-07-20T00:00:00Z",
        },
        "message": {"ratings": ratings},
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
def test_rating_view_acks_when_both_bap_and_gateway_signatures_are_valid(
    bpp_identity_settings, client
):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_rating_payload(
        ratings=[
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "rating_category": "Order",
                "value": "5",
            }
        ]
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

    with (
        patch("core.rating_service.dispatch_on_rating_in_background") as mock_dispatch,
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        resp = client.post(
            reverse("rating"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 200
    assert resp.json()["message"]["ack"]["status"] == "ACK"
    mock_dispatch.assert_called_once_with(payload=payload)


@pytest.mark.parametrize(
    "ratings",
    [
        [],
        "not-a-list",
        [{"rating_category": "Order", "value": "5"}],  # missing id
        [{"id": "x", "value": "5"}],  # missing rating_category
        [{"id": "x", "rating_category": "NotARealCategory", "value": "5"}],
        [{"id": "x", "rating_category": "Order"}],  # missing value
        # livetracker3.md §2.1 bullet 5: the real schema's value latitude (a bare
        # number OR a comparison/logical expression) is deliberately narrowed to a
        # plain 1-5 int for this project's own use case — each of these must be
        # cleanly rejected before ever reaching the aggregation function.
        [{"id": "x", "rating_category": "Order", "value": "gte 3"}],
        [{"id": "x", "rating_category": "Order", "value": "99"}],
        [{"id": "x", "rating_category": "Order", "value": "0"}],
        [{"id": "x", "rating_category": "Order", "value": "3.5"}],
    ],
)
@pytest.mark.django_db
def test_rating_view_rejects_a_malformed_ratings_array_before_acking(client, ratings):
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_rating_payload(ratings=ratings)
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
            reverse("rating"),
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=bap_header,
            HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )

    assert resp.status_code == 400
    assert resp.json()["message"]["ack"]["status"] == "NACK"


@pytest.mark.django_db
def test_dispatch_on_rating_records_a_real_rating_against_a_completed_booking(
    bpp_identity_settings,
):
    """§4.5's own Test Gate: rating against a genuinely COMPLETE booking."""
    resource, slot, booking = _make_completed_booking(holder_ref="txn-1")
    payload = _build_rating_payload(
        ratings=[{"id": str(booking.id), "rating_category": "Order", "value": "5"}],
        transaction_id="txn-1",
    )

    captured_requests = []

    def gateway_on_rating_callback(request):
        captured_requests.append(request)
        return (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}}))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST, "http://gateway:8000/on_rating", callback=gateway_on_rating_callback
        )
        rating_service.dispatch_on_rating(payload=payload, correlation_id="corr-rating-1")

    rating = Rating.objects.get(booking_id_text=str(booking.id))
    assert rating.booking_id == booking.id
    assert rating.rating_category == "Order"
    assert rating.value == "5"
    assert rating.correlation_id == "corr-rating-1"

    assert len(captured_requests) == 1
    forwarded = json.loads(captured_requests[0].body)
    assert "error" not in forwarded
    assert forwarded["message"] == {}


@pytest.mark.django_db
def test_dispatch_on_rating_records_but_does_not_fk_link_a_rating_for_another_transaction(
    bpp_identity_settings,
):
    """IDOR-shaped: a rating submitted with someone else's order id is still
    genuinely recorded (real feedback, never silently dropped) — just not
    linked to a booking this transaction doesn't own."""
    resource, slot, booking = _make_completed_booking(holder_ref="txn-owner")
    payload = _build_rating_payload(
        ratings=[{"id": str(booking.id), "rating_category": "Order", "value": "1"}],
        transaction_id="txn-attacker",
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_rating",
            callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
        )
        rating_service.dispatch_on_rating(payload=payload)

    rating = Rating.objects.get(booking_id_text=str(booking.id))
    assert rating.booking is None


@pytest.mark.django_db
def test_dispatch_on_rating_records_a_rating_with_no_resolvable_booking(bpp_identity_settings):
    """A rating for an Item/Provider/Agent/Support entity this domain doesn't model
    as a separate resolvable record — still genuinely captured, just unlinked."""
    payload = _build_rating_payload(
        ratings=[{"id": "resource-xyz", "rating_category": "Provider", "value": "up"}],
    )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_rating",
            callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
        )
        rating_service.dispatch_on_rating(payload=payload)

    rating = Rating.objects.get(booking_id_text="resource-xyz")
    assert rating.booking is None
    assert rating.rating_category == "Provider"
    assert rating.value == "up"


def _dispatch_rating(*, booking, value, rating_category="Order", transaction_id="txn-1"):
    payload = _build_rating_payload(
        ratings=[{"id": str(booking.id), "rating_category": rating_category, "value": value}],
        transaction_id=transaction_id,
    )
    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_rating",
            callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
        )
        rating_service.dispatch_on_rating(payload=payload)


@pytest.mark.django_db
def test_dispatch_on_rating_aggregates_real_ratings_into_a_real_average(bpp_identity_settings):
    """livetracker3.md §2.1 Test Gate: three real ratings (4, 5, 3), each against its
    own genuinely completed booking for the same resource, from each booking's true
    owning transaction, produce average_rating == 4.0, rating_count == 3."""
    resource, first_slot = _make_slot_and_business(ended=True)
    start_time = timezone.now() - dt.timedelta(hours=2)
    for value, txn in [("4", "txn-1"), ("5", "txn-2"), ("3", "txn-3")]:
        # A fresh slot per rating — the same resource can have many real,
        # independently-completed bookings over time; reusing one capacity-1 slot
        # would leave the second/third `hold_slot()` with nothing left to hold.
        slot = Slot.objects.create(
            resource=resource,
            start_time=start_time,
            end_time=start_time + dt.timedelta(minutes=30),
            capacity_total=1,
            capacity_remaining=1,
        )
        booking = hold_slot(slot.id, holder_ref=txn, redis_client=_redis_client(), ttl_seconds=600)
        confirm_hold(booking.id, redis_client=_redis_client())
        assert complete_active_booking(booking.id) is True
        _dispatch_rating(booking=booking, value=value, transaction_id=txn)

    resource.refresh_from_db()
    assert resource.average_rating == Decimal("4.00")
    assert resource.rating_count == 3


@pytest.mark.django_db
def test_dispatch_on_rating_initializes_the_aggregate_without_dividing_by_zero(
    bpp_identity_settings,
):
    """A rating against a resource with zero prior ratings correctly initializes
    (average_rating starts null, per Resource's own honest-absence design) rather
    than erroring or fabricating a value."""
    resource, slot, booking = _make_completed_booking(holder_ref="txn-1")
    assert resource.average_rating is None
    assert resource.rating_count == 0

    _dispatch_rating(booking=booking, value="5")

    resource.refresh_from_db()
    assert resource.average_rating == Decimal("5.00")
    assert resource.rating_count == 1


@pytest.mark.django_db
def test_dispatch_on_rating_idor_mismatched_rating_never_moves_the_public_aggregate(
    bpp_identity_settings,
):
    """livetracker3.md §2.1's own security correction: a rating against a booking the
    caller doesn't own is stored (real feedback, unchanged §4.5 behavior) but provably
    does not move that resource's public average_rating/rating_count — comparing the
    aggregate before and after, not just asserting it's absent."""
    resource, slot, booking = _make_completed_booking(holder_ref="txn-owner")
    before_average, before_count = resource.average_rating, resource.rating_count

    _dispatch_rating(booking=booking, value="1", transaction_id="txn-attacker")

    resource.refresh_from_db()
    assert resource.average_rating == before_average
    assert resource.rating_count == before_count
    rating = Rating.objects.get(booking_id_text=str(booking.id))
    assert rating.booking is None
    assert rating.value == "1"


@pytest.mark.django_db
def test_dispatch_on_rating_resubmission_updates_the_existing_row_in_place(
    bpp_identity_settings,
):
    """A second real rating from the true owner against the same booking corrects the
    existing Rating row (an honest "I meant 5, not 3") rather than creating a second
    row or double-counting in the aggregate — livetracker3.md §2.1's duplicate-
    handling requirement, enforced by the real DB uniqueness constraint."""
    resource, slot, booking = _make_completed_booking(holder_ref="txn-1")

    _dispatch_rating(booking=booking, value="3")
    resource.refresh_from_db()
    assert resource.average_rating == Decimal("3.00")
    assert resource.rating_count == 1

    _dispatch_rating(booking=booking, value="5")
    resource.refresh_from_db()
    assert resource.average_rating == Decimal("5.00")
    assert resource.rating_count == 1  # corrected in place, not double-counted

    assert Rating.objects.filter(booking=booking, rating_category="Order").count() == 1


@pytest.mark.django_db(transaction=True)
def test_dispatch_on_rating_concurrent_resubmission_never_overcounts(bpp_identity_settings):
    """Real race found and fixed 2026-07-29: two genuinely near-simultaneous submissions
    for the same (booking, rating_category) — a real, plausible case (a double-click, a
    client retry) — must never leave rating_count/average_rating reflecting two ratings
    when only one Rating row actually exists. `transaction=True` (not the default
    `django_db`) is required for real independently-committing threads, same reasoning
    already established in TESTING.md for this exact concurrency-testing pattern."""
    import threading

    resource, slot, booking = _make_completed_booking(holder_ref="txn-race")

    def submit(value):
        payload = _build_rating_payload(
            ratings=[{"id": str(booking.id), "rating_category": "Order", "value": value}],
            transaction_id="txn-race",
        )
        rating_service.dispatch_on_rating(payload=payload)

    # One shared mock covering both threads' outbound /on_rating relay — `responses`
    # patches the HTTP layer process-wide, not per-thread, so each thread opening its
    # own `RequestsMock()` context (as `_dispatch_rating` does elsewhere in this file)
    # races the other's teardown and flakes. `assert_all_requests_are_fired=False`
    # because this test's real subject is the aggregate math, not the relay call.
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(
            responses.POST,
            "http://gateway:8000/on_rating",
            callback=lambda r: (200, {}, json.dumps({"message": {"ack": {"status": "ACK"}}})),
        )
        t1 = threading.Thread(target=submit, args=("2",))
        t2 = threading.Thread(target=submit, args=("5",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    row_count = Rating.objects.filter(booking=booking, rating_category="Order").count()
    assert row_count == 1

    resource.refresh_from_db()
    assert resource.rating_count == 1
    assert resource.average_rating in (Decimal("2.00"), Decimal("5.00"))


@pytest.mark.django_db
def test_rating_view_is_rate_limited(client):
    """livetracker3.md §2.1 bullet 4: a real, live gap found on audit — BPP's own
    Gateway-receiving /rating endpoint had no rate limiting at all, unlike the
    original 8 wire actions' own BAP-side coverage assumed. A rapid-fire burst is
    now throttled the same way, against the real Redis-backed limiter (not mocked)."""
    from django.core.cache import cache

    cache.clear()
    bap_pub, bap_priv = generate_signing_key_pair()
    gateway_pub, gateway_priv = generate_signing_key_pair()
    payload = _build_rating_payload(
        ratings=[
            {"id": "11111111-1111-1111-1111-111111111111", "rating_category": "Order", "value": "5"}
        ]
    )
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

    with (
        patch("core.rating_service.dispatch_on_rating_in_background"),
        responses.RequestsMock() as rsps,
    ):
        rsps.add_callback(
            responses.POST, "http://registry:8000/lookup", callback=_lookup_callback(known)
        )
        for _ in range(30):
            resp = client.post(
                reverse("rating"), data=body, content_type="application/json",
                HTTP_AUTHORIZATION=bap_header, HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
            )
            assert resp.status_code == 200
        resp = client.post(
            reverse("rating"), data=body, content_type="application/json",
            HTTP_AUTHORIZATION=bap_header, HTTP_X_GATEWAY_AUTHORIZATION=gateway_header,
        )
    assert resp.status_code == 429
    cache.clear()
