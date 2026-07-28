"""Phase 4.4 Test Gate (livetracker2.md §4.4): "a second browser tab sees a slot go
from available to booked without a manual refresh, over the real WebSocket connection
established in §2.4" — proven here over the real `channels.testing.WebsocketCommunicator`
against `core/consumers.py`'s `ResourceAvailabilityConsumer`, not just by inspecting the
broadcast helper in isolation. Also covers the interactive `block_slot` client -> server
signal (the "not just a one-way dashboard feed" half of §4.4's own wording).
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
from inventory_core.models import Resource, Slot

from core.consumers import ResourceAvailabilityConsumer
from core.realtime import broadcast_slot_update

BusinessAccount = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _fresh_channel_layer_per_test():
    """`channels.layers.get_channel_layer()` caches one `RedisChannelLayer` instance
    (and its internal `asyncio.Lock`s) process-wide, but `pytest-asyncio` (strict mode,
    the project default) gives every test function its own fresh event loop — a lock
    created while serving an earlier test is bound to *that* test's now-closed loop, so
    reusing the same cached layer instance in a later test raises `RuntimeError: ...
    is bound to a different event loop`. `_reset_backends()` is Channels' own documented
    mechanism for exactly this (used by its own test suite) — forces a brand new
    instance, with fresh locks bound to the current test's loop, on the next
    `get_channel_layer()`/`self.channel_layer` access."""
    channel_layers._reset_backends("CHANNEL_LAYERS")
    yield
    channel_layers._reset_backends("CHANNEL_LAYERS")

application = AuthMiddlewareStack(
    URLRouter(
        [
            path(
                "ws/resources/<uuid:resource_id>/availability",
                ResourceAvailabilityConsumer.as_asgi(),
            )
        ]
    )
)


def _login_sync(client: Client, contact: str) -> None:
    client.post(
        "/api/v1/auth/login",
        data={"contact": contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )


async def _login(client: Client, contact: str) -> None:
    """`Client.post()` does real synchronous ORM work (session lookup, `authenticate()`)
    — calling it directly inside an `async def` test body trips Django's own
    `SynchronousOnlyOperation` guard, unlike calling it from a plain sync pytest fixture
    (no event loop running yet at that point). Wrapped the same way as any other
    synchronous ORM access from these async tests."""
    from channels.db import database_sync_to_async

    await database_sync_to_async(_login_sync)(client, contact)


def _session_cookie_header(client: Client) -> list:
    """Real Django session auth, not a scope shortcut — logs in via the exact same
    `business-login` endpoint the browser uses, then hands the resulting `sessionid`
    cookie to `WebsocketCommunicator` so `AuthMiddlewareStack` resolves the same real
    `request.user` the REST endpoints already trust."""
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
def owner_session(owner):
    client = Client()
    client.post(
        "/api/v1/auth/login",
        data={"contact": owner.contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )
    return client


@pytest.fixture
def resource(db, owner):
    return Resource.objects.create(
        owner_ref=str(owner.id), name="Stylist A", domain_data={"resource_type": "stylist"}
    )


@pytest.fixture
def slot(db, resource):
    now = timezone.now()
    return Slot.objects.create(
        resource=resource,
        start_time=now + dt.timedelta(hours=1),
        end_time=now + dt.timedelta(hours=1, minutes=30),
        capacity_total=1,
        capacity_remaining=1,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_unauthenticated_connection_is_rejected(resource):
    communicator = WebsocketCommunicator(
        application, f"/ws/resources/{resource.id}/availability"
    )
    connected, subprotocol_or_close = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_different_businesss_owner_is_rejected(resource):
    from channels.db import database_sync_to_async

    await database_sync_to_async(BusinessAccount.objects.create_user)(
        contact="other-owner@example.com", business_name="Other Salon", password=TEST_PASSWORD
    )
    other_client = Client()
    await _login(other_client, "other-owner@example.com")
    communicator = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(other_client),
    )
    connected, _ = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_owner_can_connect_and_receives_a_broadcast_slot_update(
    owner_session, resource, slot
):
    communicator = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(owner_session),
    )
    connected, _ = await communicator.connect()
    assert connected is True

    from channels.db import database_sync_to_async

    await database_sync_to_async(broadcast_slot_update)(resource.id, slot)

    message = await communicator.receive_json_from()
    assert message["type"] == "slot.update"
    assert message["slot"]["id"] == str(slot.id)
    assert message["slot"]["status"] == "AVAILABLE"

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_staff_assigned_to_this_resource_can_connect(owner_session, resource):
    from channels.db import database_sync_to_async

    owner = await database_sync_to_async(BusinessAccount.objects.get)(contact="owner@example.com")
    staff = await database_sync_to_async(BusinessAccount.objects.create_user)(
        contact="staff@example.com",
        business_name="Stylist One",
        password=TEST_PASSWORD,
        role=BusinessAccount.Role.STAFF,
        managed_by=owner,
    )

    def _assign():
        resource.domain_data = {**resource.domain_data, "assigned_staff_id": str(staff.id)}
        resource.save(update_fields=["domain_data", "updated_at"])

    await database_sync_to_async(_assign)()

    staff_client = Client()
    await _login(staff_client, "staff@example.com")
    communicator = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(staff_client),
    )
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_staff_not_assigned_to_this_resource_is_rejected(owner_session, resource):
    from channels.db import database_sync_to_async

    owner = await database_sync_to_async(BusinessAccount.objects.get)(contact="owner@example.com")
    await database_sync_to_async(BusinessAccount.objects.create_user)(
        contact="staff@example.com",
        business_name="Stylist One",
        password=TEST_PASSWORD,
        role=BusinessAccount.Role.STAFF,
        managed_by=owner,
    )
    # Note: never assigned to `resource` via domain_data["assigned_staff_id"].

    staff_client = Client()
    await _login(staff_client, "staff@example.com")
    communicator = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(staff_client),
    )
    connected, _ = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_block_slot_over_the_socket_blocks_and_acks_and_broadcasts(
    owner_session, resource, slot
):
    communicator = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(owner_session),
    )
    connected, _ = await communicator.connect()
    assert connected is True

    await communicator.send_json_to({"type": "block_slot", "slot_id": str(slot.id)})

    ack = await communicator.receive_json_from()
    assert ack == {"type": "block_result", "blocked": [str(slot.id)], "skipped": []}

    broadcast = await communicator.receive_json_from()
    assert broadcast["type"] == "slot.update"
    assert broadcast["slot"]["id"] == str(slot.id)
    assert broadcast["slot"]["status"] == "CANCELLED"

    from channels.db import database_sync_to_async

    refreshed = await database_sync_to_async(Slot.objects.get)(pk=slot.id)
    assert refreshed.status == Slot.Status.CANCELLED

    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_two_connected_tabs_both_see_the_same_slot_go_from_available_to_booked(
    owner_session, resource, slot
):
    """The Test Gate's own scenario, literally: two independent connections (two real
    browser tabs) watching the same resource; one real mutation broadcasts to both."""
    tab_one = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(owner_session),
    )
    tab_two = WebsocketCommunicator(
        application,
        f"/ws/resources/{resource.id}/availability",
        headers=_session_cookie_header(owner_session),
    )
    assert (await tab_one.connect())[0] is True
    assert (await tab_two.connect())[0] is True

    from channels.db import database_sync_to_async
    from django.db import transaction

    def _hold():
        with transaction.atomic():
            s = Slot.objects.select_for_update().get(pk=slot.id)
            s.capacity_remaining = 0
            s.status = Slot.Status.HELD
            s.save(update_fields=["capacity_remaining", "status", "updated_at"])
            return s

    held_slot = await database_sync_to_async(_hold)()
    await database_sync_to_async(broadcast_slot_update)(resource.id, held_slot)

    for communicator in (tab_one, tab_two):
        message = await communicator.receive_json_from()
        assert message["type"] == "slot.update"
        assert message["slot"]["id"] == str(slot.id)
        assert message["slot"]["status"] == "HELD"

    await tab_one.disconnect()
    await tab_two.disconnect()
