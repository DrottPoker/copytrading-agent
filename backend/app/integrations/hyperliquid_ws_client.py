import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from app.core.config import Settings

WebSocketMessageHandler = Callable[[dict[str, Any]], Awaitable[None]]
WebSocketSubscriptionHandler = Callable[[str], Awaitable[None]]


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
            while not stop_event.is_set():
                try:
                    raw_message = await receive_until_stop(
                        websocket,
                        stop_event=stop_event,
                        timeout_seconds=30,
                    )
                except TimeoutError:
                    await websocket.send(json.dumps({"method": "ping"}))
                    continue
                if raw_message is None:
                    return

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    raise HyperliquidWebSocketError("Received invalid WebSocket JSON.") from exc

                if isinstance(message, dict):
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

            while not stop_event.is_set():
                try:
                    raw_message = await receive_until_stop(
                        websocket,
                        stop_event=stop_event,
                        timeout_seconds=30,
                    )
                except TimeoutError:
                    await websocket.send(json.dumps({"method": "ping"}))
                    continue
                if raw_message is None:
                    return

                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError as exc:
                    raise HyperliquidWebSocketError("Received invalid WebSocket JSON.") from exc

                if isinstance(message, dict):
                    await on_message(message)
    except HyperliquidWebSocketError:
        raise
    except Exception as exc:
        raise HyperliquidWebSocketError(str(exc)) from exc


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
