"""livetracker5.md Phase 3.3 Test Gate: a real payment status change reaches a
connected browser over the real WebSocket connection, proven here with
`channels.testing.WebsocketCommunicator` against `core/consumers.py`'s
`PaymentStatusConsumer` — not just by inspecting the broadcast helper in isolation.
Mirrors BPP's own `core/test_realtime_availability.py` pattern exactly.
"""

import pytest
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.layers import channel_layers
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import path

from core.consumers import PaymentStatusConsumer
from core.models import SearchSession
from core.realtime import broadcast_payment_status_changed

Customer = get_user_model()

# Test fixture value, not a real credential.
TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _fresh_channel_layer_per_test():
    """Same documented reason as BPP's identical fixture: a cached
    `RedisChannelLayer` instance's internal locks are bound to a prior test's
    now-closed event loop under strict-mode `pytest-asyncio`."""
    channel_layers._reset_backends("CHANNEL_LAYERS")
    yield
    channel_layers._reset_backends("CHANNEL_LAYERS")


application = AuthMiddlewareStack(
    URLRouter(
        [path("ws/payment/<str:transaction_id>/", PaymentStatusConsumer.as_asgi())]
    )
)


def _session_cookie_header(client: Client) -> list:
    session_cookie = client.cookies.get("sessionid")
    if session_cookie is None:
        return []
    return [(b"cookie", f"sessionid={session_cookie.value}".encode())]


@pytest.fixture
def customer(db):
    return Customer.objects.create_user(
        contact="jane@example.com", name="Jane", password=TEST_PASSWORD
    )


@pytest.fixture
def customer_client(customer):
    client = Client()
    client.post(
        "/api/v1/auth/login",
        data={"contact": customer.contact, "password": TEST_PASSWORD},
        content_type="application/json",
    )
    return client


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_connecting_to_a_nonexistent_transaction_is_rejected():
    communicator = WebsocketCommunicator(application, "/ws/payment/does-not-exist/")
    connected, _ = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_different_customers_session_is_rejected(customer_client):
    other = await database_sync_to_async(Customer.objects.create_user)(
        contact="owner@example.com", name="Owner", password=TEST_PASSWORD
    )
    await database_sync_to_async(SearchSession.objects.create)(
        transaction_id="txn-owned", query="x", domain="ONDC:RET13", customer=other
    )
    communicator = WebsocketCommunicator(
        application, "/ws/payment/txn-owned/", headers=_session_cookie_header(customer_client)
    )
    connected, _ = await communicator.connect()
    assert connected is False
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_an_anonymous_still_unowned_session_can_connect():
    await database_sync_to_async(SearchSession.objects.create)(
        transaction_id="txn-anon", query="x", domain="ONDC:RET13"
    )
    communicator = WebsocketCommunicator(application, "/ws/payment/txn-anon/")
    connected, _ = await communicator.connect()
    assert connected is True
    await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_owning_customer_receives_a_real_broadcast(customer, customer_client):
    await database_sync_to_async(SearchSession.objects.create)(
        transaction_id="txn-owned-2", query="x", domain="ONDC:RET13", customer=customer
    )
    communicator = WebsocketCommunicator(
        application, "/ws/payment/txn-owned-2/", headers=_session_cookie_header(customer_client)
    )
    connected, _ = await communicator.connect()
    assert connected is True

    await database_sync_to_async(broadcast_payment_status_changed)(
        transaction_id="txn-owned-2", status="SUCCEEDED"
    )

    message = await communicator.receive_json_from()
    assert message == {"type": "payment.status_changed", "status": "SUCCEEDED"}

    await communicator.disconnect()
