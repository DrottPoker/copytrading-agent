import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import websockets

from app.core.config import Settings

WebSocketMessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
WebSocketSubscriptionHandler = Callable[[str], Awaitable[None]]
# Ping after this much silence, and treat the connection as dead when nothing,
# not even the pong, arrives within the pong timeout.
WEBSOCKET_HEARTBEAT_SECONDS = 30.0
WEBSOCKET_PONG_TIMEOUT_SECONDS = 10.0


class HyperliquidWebSocketError(RuntimeError):
    pass


async def stream_user_fills(
    *,
    settings: Settings,
    wallet_addresses: list[str],
    on_message: WebSocketMessageHandler,
    on_subscribed: WebSocketSubscriptionHandler | None = None,
    stop_event: asyncio.Event,
) -> None:
    if not wallet_addresses:
        return

    try:
        async with websockets.connect(settings.hyperliquid_ws_url, ping_interval=None) as websocket:
            for address in wallet_addresses:
                await websocket.send(
                    json.dumps(
                        {
                            "method": "subscribe",
                            "subscription": {
                                "type": "userFills",
                                "user": address,
                                "aggregateByTime": False,
                            },
                        }
                    )
                )

            requested_wallets = {address.lower() for address in wallet_addresses}
            acknowledged_wallets: set[str] = set()
            async for message in receive_messages(websocket, stop_event=stop_event):
                acknowledged_wallet = user_fills_subscription_wallet(message)
                if (
                    on_subscribed is not None
                    and acknowledged_wallet in requested_wallets
                    and acknowledged_wallet not in acknowledged_wallets
                ):
                    acknowledged_wallets.add(acknowledged_wallet)
                    await on_subscribed(acknowledged_wallet)
                await on_message(message)
    except HyperliquidWebSocketError:
        raise
    except Exception as exc:
        raise HyperliquidWebSocketError(str(exc)) from exc


async def stream_all_mids(
    *,
    settings: Settings,
    dex: str,
    on_message: WebSocketMessageHandler,
    stop_event: asyncio.Event,
) -> None:
    subscription = {"type": "allMids"}
    if dex:
        subscription["dex"] = dex

    try:
        async with websockets.connect(settings.hyperliquid_ws_url, ping_interval=None) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "method": "subscribe",
                        "subscription": subscription,
                    }
                )
            )

            async for message in receive_messages(websocket, stop_event=stop_event):
                await on_message(message)
    except HyperliquidWebSocketError:
        raise
    except Exception as exc:
        raise HyperliquidWebSocketError(str(exc)) from exc


async def receive_messages(
    websocket: Any,
    *,
    stop_event: asyncio.Event,
    heartbeat_seconds: float = WEBSOCKET_HEARTBEAT_SECONDS,
    pong_timeout_seconds: float = WEBSOCKET_PONG_TIMEOUT_SECONDS,
) -> AsyncIterator[dict[str, Any]]:
    """Yield JSON object messages until stop, detecting silently dead connections.

    A ping goes out after a quiet heartbeat interval. Hyperliquid answers with a
    pong message, so any message proves the connection alive; a missing answer
    raises and lets the caller reconnect instead of waiting on a dead socket.
    """

    awaiting_pong = False
    while not stop_event.is_set():
        try:
            raw_message = await receive_until_stop(
                websocket,
                stop_event=stop_event,
                timeout_seconds=pong_timeout_seconds if awaiting_pong else heartbeat_seconds,
            )
        except TimeoutError as exc:
            if awaiting_pong:
                raise HyperliquidWebSocketError(
                    "Hyperliquid WebSocket did not answer the heartbeat ping."
                ) from exc
            await websocket.send(json.dumps({"method": "ping"}))
            awaiting_pong = True
            continue
        if raw_message is None:
            return
        awaiting_pong = False
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as exc:
            raise HyperliquidWebSocketError("Received invalid WebSocket JSON.") from exc
        if isinstance(message, dict):
            yield message


async def receive_until_stop(
    websocket: Any,
    *,
    stop_event: asyncio.Event,
    timeout_seconds: float,
) -> Any | None:
    receive_task = asyncio.create_task(websocket.recv())
    stop_task = asyncio.create_task(stop_event.wait())
    tasks = (receive_task, stop_task)
    try:
        done, _pending = await asyncio.wait(
            tasks,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receive_task in done:
            return await receive_task
        if stop_task in done:
            return None
        raise TimeoutError
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def user_fills_subscription_wallet(message: dict[str, Any]) -> str | None:
    channel = message.get("channel")
    data = message.get("data")
    if not isinstance(data, dict):
        return None
    if channel == "subscriptionResponse":
        if data.get("method") != "subscribe":
            return None
        subscription = data.get("subscription")
        if not isinstance(subscription, dict) or subscription.get("type") != "userFills":
            return None
        return normalized_wallet(subscription.get("user"))
    if channel == "userFills":
        return normalized_wallet(data.get("user"))
    return None


def normalized_wallet(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    wallet = value.strip().lower()
    return wallet or None
