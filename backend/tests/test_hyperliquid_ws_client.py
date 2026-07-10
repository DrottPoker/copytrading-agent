import asyncio

import pytest

from app.integrations.hyperliquid_ws_client import receive_until_stop


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
