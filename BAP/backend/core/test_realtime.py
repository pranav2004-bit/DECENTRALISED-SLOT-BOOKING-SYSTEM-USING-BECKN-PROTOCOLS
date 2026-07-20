"""Phase 2.4 Test Gate (livetracker2.md §2.4) for BAP's WebSocket foundation — a real
connection opens, stays alive, and round-trips a message. `channels.testing.
WebsocketCommunicator` drives the real ASGI application in-process (a real protocol
handshake and consumer lifecycle, not a mock) — the live, real-network confirmation ("inspecting
actual socket traffic") is done separately via a running container, not by this test suite.
"""

import json

import pytest
from channels.testing import WebsocketCommunicator

from bap.asgi import application


@pytest.mark.asyncio
async def test_websocket_connects_and_sends_connected_ack():
    communicator = WebsocketCommunicator(application, "/ws/")
    connected, _ = await communicator.connect()
    assert connected is True

    message = json.loads(await communicator.receive_from())
    assert message == {"type": "connected"}

    await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_round_trips_a_ping_pong():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data=json.dumps({"type": "ping"}))
    message = json.loads(await communicator.receive_from())

    assert message == {"type": "pong"}
    await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_ignores_malformed_input_instead_of_crashing():
    communicator = WebsocketCommunicator(application, "/ws/")
    await communicator.connect()
    await communicator.receive_from()  # the initial "connected" ack

    await communicator.send_to(text_data="not valid json")

    assert await communicator.receive_nothing() is True
    await communicator.disconnect()
