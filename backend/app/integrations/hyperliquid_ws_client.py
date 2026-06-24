import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from app.core.config import Settings

WebSocketMessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


class HyperliquidWebSocketError(RuntimeError):
    pass


async def stream_user_fills(
    *,
    settings: Settings,
    wallet_addresses: list[str],
    on_message: WebSocketMessageHandler,
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

            while not stop_event.is_set():
                try:
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=30)
                except TimeoutError:
                    await websocket.send(json.dumps({"method": "ping"}))
                    continue

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
                    raw_message = await asyncio.wait_for(websocket.recv(), timeout=30)
                except TimeoutError:
                    await websocket.send(json.dumps({"method": "ping"}))
                    continue

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
