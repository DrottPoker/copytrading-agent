import asyncio
import json
from types import SimpleNamespace

import pytest

from app.integrations import hyperliquid_ws_client
from app.integrations.hyperliquid_ws_client import (
    HyperliquidWebSocketError,
    receive_messages,
    receive_until_stop,
    stream_user_fills,
    user_fills_subscription_wallet,
)


class BlockingWebSocket:
    def __init__(self) -> None:
        self.receive_started = asyncio.Event()
        self.receive_cancelled = False

    async def recv(self) -> str:
        self.receive_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receive_cancelled = True
            raise
        return "never"


class ScriptedWebSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = [json.dumps(message) for message in messages]
        self.sent: list[dict[str, object]] = []

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def recv(self) -> str:
        return self.messages.pop(0)


class ScriptedConnection:
    def __init__(self, websocket: ScriptedWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> ScriptedWebSocket:
        return self.websocket

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_receive_until_stop_cancels_pending_intake() -> None:
    websocket = BlockingWebSocket()
    stop_event = asyncio.Event()
    receive_task = asyncio.create_task(
        receive_until_stop(
            websocket,
            stop_event=stop_event,
            timeout_seconds=30,
        )
    )
    await websocket.receive_started.wait()

    stop_event.set()
    result = await asyncio.wait_for(receive_task, timeout=1)

    assert result is None
    assert websocket.receive_cancelled is True


def test_user_fills_subscription_wallet_requires_confirmed_subscription() -> None:
    assert (
        user_fills_subscription_wallet(
            {
                "channel": "subscriptionResponse",
                "data": {
                    "method": "subscribe",
                    "subscription": {"type": "userFills", "user": "0xABC"},
                },
            }
        )
        == "0xabc"
    )
    assert (
        user_fills_subscription_wallet(
            {
                "channel": "subscriptionResponse",
                "data": {
                    "method": "subscribe",
                    "subscription": {"type": "allMids"},
                },
            }
        )
        is None
    )


def test_user_fills_snapshot_also_proves_active_subscription() -> None:
    assert (
        user_fills_subscription_wallet(
            {
                "channel": "userFills",
                "data": {"user": "0xABC", "fills": [], "isSnapshot": True},
            }
        )
        == "0xabc"
    )


@pytest.mark.asyncio
async def test_stream_user_fills_reports_acknowledged_wallet_once(monkeypatch) -> None:
    websocket = ScriptedWebSocket(
        [
            {
                "channel": "subscriptionResponse",
                "data": {
                    "method": "subscribe",
                    "subscription": {"type": "userFills", "user": "0xABC"},
                },
            }
        ]
    )
    monkeypatch.setattr(
        hyperliquid_ws_client.websockets,
        "connect",
        lambda *_args, **_kwargs: ScriptedConnection(websocket),
    )
    stop_event = asyncio.Event()
    acknowledged: list[str] = []

    async def on_subscribed(wallet_address: str) -> None:
        acknowledged.append(wallet_address)

    async def on_message(_message: dict[str, object]) -> None:
        stop_event.set()

    await stream_user_fills(
        settings=SimpleNamespace(hyperliquid_ws_url="wss://example.test/ws"),
        wallet_addresses=["0xABC"],
        on_message=on_message,
        on_subscribed=on_subscribed,
        stop_event=stop_event,
    )

    assert acknowledged == ["0xabc"]
    assert websocket.sent[0]["subscription"] == {
        "type": "userFills",
        "user": "0xABC",
        "aggregateByTime": False,
    }


class HeartbeatWebSocket:
    """WebSocket that stays silent until pinged, answering only when configured to."""

    def __init__(self, *, answer_pings: bool) -> None:
        self.answer_pings = answer_pings
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[dict[str, object]] = []

    async def send(self, value: str) -> None:
        message = json.loads(value)
        self.sent.append(message)
        if self.answer_pings and message == {"method": "ping"}:
            self.incoming.put_nowait(json.dumps({"channel": "pong"}))

    async def recv(self) -> str:
        return await self.incoming.get()


@pytest.mark.asyncio
async def test_receive_messages_raises_when_the_heartbeat_ping_goes_unanswered() -> None:
    websocket = HeartbeatWebSocket(answer_pings=False)

    with pytest.raises(HyperliquidWebSocketError, match="heartbeat"):
        async for _message in receive_messages(
            websocket,
            stop_event=asyncio.Event(),
            heartbeat_seconds=0.01,
            pong_timeout_seconds=0.01,
        ):
            pass

    assert websocket.sent == [{"method": "ping"}]


@pytest.mark.asyncio
async def test_receive_messages_keeps_an_answering_connection_open() -> None:
    websocket = HeartbeatWebSocket(answer_pings=True)
    stop_event = asyncio.Event()
    received: list[dict[str, object]] = []

    async for message in receive_messages(
        websocket,
        stop_event=stop_event,
        heartbeat_seconds=0.01,
        pong_timeout_seconds=0.05,
    ):
        received.append(message)
        if len(received) == 3:
            stop_event.set()

    assert received == [{"channel": "pong"}] * 3
    assert websocket.sent == [{"method": "ping"}] * 3
