"""livetracker5.md Phase 4.1 Test Gate (`INTEG`) — the real-time half, split into
its own file for the same reason `test_business_orders.py` is separate from BPP's
other (sync) tests: an async, `transaction=True`-marked WebSocket test mixed into
a file with plain `@pytest.mark.django_db` tests is a real, previously-diagnosed
source of flaky/hanging test-isolation bugs in this codebase (see
`BAP/backend/core/test_realtime.py`'s own docstring for the identical root cause
found there) — confirmed here directly: this exact test passed reliably alone but
hung at `communicator.connect()` when run inside `test_payment_status.py` alongside
its sync tests."""

import pytest
from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import path
from inventory_core.events import BookingEvent
from inventory_core.models import Booking, Resource

from core.consumers import BusinessOrdersConsumer
from core.realtime import broadcast_payment_status_consumer
from core.test_payment_status import _make_booking

BusinessAccount = get_user_model()

TEST_PASSWORD = "a-strong-passw0rd!"  # pragma: allowlist secret

application = AuthMiddlewareStack(
    URLRouter([path("ws/business/orders/", BusinessOrdersConsumer.as_asgi())])
)


def _session_cookie_header(client: Client) -> list:
    session_cookie = client.cookies.get("sessionid")
    if session_cookie is None:
        return []
    return [(b"cookie", f"sessionid={session_cookie.value}".encode())]


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_payment_status_change_reaches_the_live_orders_dashboard():
    from channels.db import database_sync_to_async

    def _setup():
        owner = BusinessAccount.objects.create_user(
            contact="owner2@example.com", business_name="Glow Salon", password=TEST_PASSWORD
        )
        resource = Resource.objects.create(owner_ref=str(owner.id), name="Stylist A")
        booking = _make_booking(resource, holder_ref="txn-live")
        client = Client()
        client.post(
            "/api/v1/auth/login",
            data={"contact": owner.contact, "password": TEST_PASSWORD},
            content_type="application/json",
        )
        return client, booking.id

    client, booking_id = await database_sync_to_async(_setup)()

    communicator = WebsocketCommunicator(
        application, "/ws/business/orders/", headers=_session_cookie_header(client)
    )
    connected, _ = await communicator.connect()
    assert connected is True

    def _mark_paid_and_refetch():
        Booking.objects.filter(pk=booking_id).update(payment_status="SUCCEEDED")
        return Booking.objects.select_related("slot__resource").get(pk=booking_id)

    fresh = await database_sync_to_async(_mark_paid_and_refetch)()
    await database_sync_to_async(broadcast_payment_status_consumer)(
        {
            "event_id": "1",
            "event_type": BookingEvent.PAYMENT_SUCCEEDED,
            "payload": {"booking_id": str(fresh.id)},
        }
    )

    message = await communicator.receive_json_from()
    assert message["type"] == "order.payment_status_changed"
    assert message["order"]["transaction_id"] == "txn-live"
    assert message["order"]["payment_status"] == "SUCCEEDED"

    await communicator.disconnect()
