"""livetracker6.md §2.2 Test Gate — the business-facing Orders feature: a real
confirmed booking appears live on the correct business's own Orders page via the
new per-resource `resource-{resource_id}-orders` WebSocket group, and the initial
page load's own `GET /api/v1/orders` REST endpoint, both scoped identically
(owner sees every owned resource's orders, staff sees only their one assigned
resource's own) — mirroring `test_realtime_availability.py`'s own established
fixture/session pattern rather than inventing a second one.
"""

import datetime as dt

import pytest
from channels.auth import AuthMiddlewareStack
from channels.layers import channel_layers
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import path
from django.utils import timezone
from inventory_core.models import Booking, Resource, Slot

from core.consumers import BusinessOrdersConsumer
from core.realtime import broadcast_order_confirmed

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _fresh_channel_layer_per_test():
    channel_layers._reset_backends("CHANNEL_LAYERS")
    yield
    channel_layers._reset_backends("CHANNEL_LAYERS")


application = AuthMiddlewareStack(
    URLRouter([path("ws/business/orders/", BusinessOrdersConsumer.as_asgi())])
)


async def _login(client: Client, contact: str) -> None:
    from channels.db import database_sync_to_async

    def _login_sync():
        client.post(
            "/api/v1/auth/login",
            data={"contact": contact, "password": TEST_PASSWORD},
            content_type="application/json",
        )

    await database_sync_to_async(_login_sync)()


def _session_cookie_header(client: Client) -> list:
    session_cookie = client.cookies.get("sessionid")
    if session_cookie is None:
        return []
    return [(b"cookie", f"sessionid={session_cookie.value}".encode())]


@pytest.fixture
def owner(db):
    return BusinessAccount.objects.create_user(
        contact="owner@example.com", business_name="Glow Salon", password=TEST_PASSWORD
    )


@pytest.fixture
def owner_client(owner):
    client = Client()
    client.post(
        "/api/v1/auth/login",
        data={"contact": owner.contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )
    return client


@pytest.fixture
def resource_a(db, owner):
    return Resource.objects.create(
        owner_ref=str(owner.id), name="Stylist A", domain_data={"resource_type": "stylist"}
    )


@pytest.fixture
def resource_b(db, owner):
    return Resource.objects.create(
        owner_ref=str(owner.id), name="Stylist B", domain_data={"resource_type": "stylist"}
    )


def _make_confirmed_booking(resource, *, holder_ref="tx-1", minutes_from_now=60):
    now = timezone.now()
    slot = Slot.objects.create(
        resource=resource,
        start_time=now + dt.timedelta(minutes=minutes_from_now),
        end_time=now + dt.timedelta(minutes=minutes_from_now + 30),
        capacity_total=1,
        capacity_remaining=0,
    )
    return Booking.objects.create(
        slot=slot, holder_ref=holder_ref, status=Booking.Status.ACTIVE
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unauthenticated_connection_is_rejected():
    communicator = WebsocketCommunicator(application, "/ws/business/orders/")
    connected, _ = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_with_two_resources_receives_both_resources_own_broadcasts(
    owner_client, resource_a, resource_b
):
    from channels.db import database_sync_to_async

    booking_a = await database_sync_to_async(_make_confirmed_booking)(resource_a, holder_ref="tx-a")
    booking_b = await database_sync_to_async(_make_confirmed_booking)(resource_b, holder_ref="tx-b")

    communicator = WebsocketCommunicator(
        application, "/ws/business/orders/", headers=_session_cookie_header(owner_client)
    )
    connected, _ = await communicator.connect()
    assert connected is True

    def _refetch(booking_id):
        return Booking.objects.select_related("slot__resource").get(pk=booking_id)

    fresh_a = await database_sync_to_async(_refetch)(booking_a.id)
    fresh_b = await database_sync_to_async(_refetch)(booking_b.id)
    await database_sync_to_async(broadcast_order_confirmed)(fresh_a)
    await database_sync_to_async(broadcast_order_confirmed)(fresh_b)

    first = await communicator.receive_json_from()
    second = await communicator.receive_json_from()
    transaction_ids = {first["order"]["transaction_id"], second["order"]["transaction_id"]}
    assert transaction_ids == {"tx-a", "tx-b"}

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_staff_assigned_to_resource_a_never_receives_resource_bs_broadcast(
    owner_client, resource_a, resource_b, owner
):
    from channels.db import database_sync_to_async

    staff = await database_sync_to_async(BusinessAccount.objects.create_user)(
        contact="staff@example.com",
        business_name="Stylist One",
        password=TEST_PASSWORD,
        role=BusinessAccount.Role.STAFF,
        managed_by=owner,
    )

    def _assign():
        resource_a.domain_data = {**resource_a.domain_data, "assigned_staff_id": str(staff.id)}
        resource_a.save(update_fields=["domain_data", "updated_at"])

    await database_sync_to_async(_assign)()

    staff_client = Client()
    await _login(staff_client, "staff@example.com")
    communicator = WebsocketCommunicator(
        application, "/ws/business/orders/", headers=_session_cookie_header(staff_client)
    )
    connected, _ = await communicator.connect()
    assert connected is True

    booking_b = await database_sync_to_async(_make_confirmed_booking)(resource_b, holder_ref="tx-b")

    def _refetch(booking_id):
        return Booking.objects.select_related("slot__resource").get(pk=booking_id)

    fresh_b = await database_sync_to_async(_refetch)(booking_b.id)
    await database_sync_to_async(broadcast_order_confirmed)(fresh_b)

    # Never joined resource_b's own group at all — not merely filtered after
    # delivery — so nothing should ever arrive on this connection.
    assert await communicator.receive_nothing(timeout=0.3) is True

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_different_businesss_owner_never_receives_this_businesss_broadcast(
    resource_a,
):
    from channels.db import database_sync_to_async

    await database_sync_to_async(BusinessAccount.objects.create_user)(
        contact="other-owner@example.com", business_name="Other Salon", password=TEST_PASSWORD
    )
    other_client = Client()
    await _login(other_client, "other-owner@example.com")
    communicator = WebsocketCommunicator(
        application, "/ws/business/orders/", headers=_session_cookie_header(other_client)
    )
    connected, _ = await communicator.connect()
    assert connected is True

    booking_a = await database_sync_to_async(_make_confirmed_booking)(resource_a, holder_ref="tx-a")

    def _refetch(booking_id):
        return Booking.objects.select_related("slot__resource").get(pk=booking_id)

    fresh_a = await database_sync_to_async(_refetch)(booking_a.id)
    await database_sync_to_async(broadcast_order_confirmed)(fresh_a)

    assert await communicator.receive_nothing(timeout=0.3) is True
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_availability_consumer_sees_no_errors_when_an_order_is_confirmed(
    owner_client, resource_a
):
    """The sixth self-audit pass's own regression proof: the order broadcast and
    the availability broadcast genuinely never share a group, so an open
    `ResourceAvailabilityConsumer` connection sees zero unexpected messages when
    an order is confirmed on the same resource."""
    from channels.db import database_sync_to_async

    from core.consumers import ResourceAvailabilityConsumer

    availability_app = AuthMiddlewareStack(
        URLRouter(
            [
                path(
                    "ws/resources/<uuid:resource_id>/availability",
                    ResourceAvailabilityConsumer.as_asgi(),
                )
            ]
        )
    )
    availability_communicator = WebsocketCommunicator(
        availability_app,
        f"/ws/resources/{resource_a.id}/availability",
        headers=_session_cookie_header(owner_client),
    )
    connected, _ = await availability_communicator.connect()
    assert connected is True

    booking_a = await database_sync_to_async(_make_confirmed_booking)(resource_a, holder_ref="tx-a")

    def _refetch(booking_id):
        return Booking.objects.select_related("slot__resource").get(pk=booking_id)

    fresh_a = await database_sync_to_async(_refetch)(booking_a.id)
    await database_sync_to_async(broadcast_order_confirmed)(fresh_a)

    assert await availability_communicator.receive_nothing(timeout=0.3) is True
    await availability_communicator.disconnect()


@pytest.mark.django_db
def test_orders_list_view_owner_sees_every_owned_resources_orders(
    owner_client, resource_a, resource_b
):
    _make_confirmed_booking(resource_a, holder_ref="tx-a")
    _make_confirmed_booking(resource_b, holder_ref="tx-b")

    resp = owner_client.get("/api/v1/orders")
    assert resp.status_code == 200
    transaction_ids = {row["transaction_id"] for row in resp.json()["orders"]}
    assert transaction_ids == {"tx-a", "tx-b"}


@pytest.mark.django_db
def test_orders_list_view_excludes_still_held_bookings(owner_client, resource_a):
    now = timezone.now()
    held_slot = Slot.objects.create(
        resource=resource_a,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=0,
    )
    Booking.objects.create(slot=held_slot, holder_ref="tx-held", status=Booking.Status.HELD)
    _make_confirmed_booking(resource_a, holder_ref="tx-confirmed")

    resp = owner_client.get("/api/v1/orders")
    transaction_ids = {row["transaction_id"] for row in resp.json()["orders"]}
    assert transaction_ids == {"tx-confirmed"}


@pytest.mark.django_db
def test_orders_list_view_a_different_businesss_orders_are_absent(owner_client, resource_a):
    other_owner = BusinessAccount.objects.create_user(
        contact="other-owner@example.com", business_name="Other Salon", password=TEST_PASSWORD
    )
    other_resource = Resource.objects.create(owner_ref=str(other_owner.id), name="Other Stylist")
    _make_confirmed_booking(other_resource, holder_ref="tx-other")
    _make_confirmed_booking(resource_a, holder_ref="tx-mine")

    resp = owner_client.get("/api/v1/orders")
    transaction_ids = {row["transaction_id"] for row in resp.json()["orders"]}
    assert transaction_ids == {"tx-mine"}


@pytest.mark.django_db
def test_orders_list_view_staff_sees_only_their_assigned_resources_orders(
    owner_client, resource_a, resource_b, owner
):
    staff = BusinessAccount.objects.create_user(
        contact="staff@example.com",
        business_name="Stylist One",
        password=TEST_PASSWORD,
        role=BusinessAccount.Role.STAFF,
        managed_by=owner,
    )
    resource_a.domain_data = {**resource_a.domain_data, "assigned_staff_id": str(staff.id)}
    resource_a.save(update_fields=["domain_data", "updated_at"])
    _make_confirmed_booking(resource_a, holder_ref="tx-a")
    _make_confirmed_booking(resource_b, holder_ref="tx-b")

    staff_client = Client()
    staff_client.post(
        "/api/v1/auth/login",
        data={"contact": "staff@example.com", "password": TEST_PASSWORD},
        content_type="application/json",
    )
    resp = staff_client.get("/api/v1/orders")
    transaction_ids = {row["transaction_id"] for row in resp.json()["orders"]}
    assert transaction_ids == {"tx-a"}


@pytest.mark.django_db
def test_orders_list_view_requires_login(client):
    resp = client.get("/api/v1/orders")
    assert resp.status_code == 401


@pytest.mark.django_db
def test_orders_list_view_paginates_with_a_real_cursor(owner_client, resource_a):
    """Real bug found live in this test itself, not the view: `next_cursor` embeds
    a raw `+00:00`/`|` — building the second request's URL by simple string
    interpolation (rather than through Django's own `data=` query-param encoding,
    which the real frontend's `encodeURIComponent()` already gets right) silently
    corrupts the `+` to a space per standard query-string decoding, tripping
    `orders_list_view`'s own `except ValueError: pass` fallback and re-running the
    *unfiltered* first page instead of erroring loudly. Fixed by using the test
    client's own `data=` kwarg, which encodes correctly, not by loosening the view."""
    for i in range(3):
        _make_confirmed_booking(resource_a, holder_ref=f"tx-{i}", minutes_from_now=60 + i)

    first_page = owner_client.get("/api/v1/orders", data={"limit": "2"}).json()
    assert len(first_page["orders"]) == 2
    assert first_page["next_cursor"] is not None

    second_page = owner_client.get(
        "/api/v1/orders", data={"limit": "2", "cursor": first_page["next_cursor"]}
    ).json()
    assert len(second_page["orders"]) == 1
    assert second_page["next_cursor"] is None

    all_ids = {row["transaction_id"] for row in first_page["orders"] + second_page["orders"]}
    assert all_ids == {"tx-0", "tx-1", "tx-2"}
