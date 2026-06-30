from types import SimpleNamespace
from typing import Any

import pytest

from app.services.paper_trading_service import PaperCopyBatchResult
from app.services.realtime_fill_service import StoredRealtimeFills
from app.workers import monitor_worker


class DummySession:
    async def __aenter__(self) -> "DummySession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def dummy_sessionmaker() -> DummySession:
    return DummySession()


@pytest.mark.asyncio
async def test_handle_websocket_message_prioritizes_live_copy(monkeypatch) -> None:
    calls: list[str] = []
    published_events: list[str] = []
    stored_fill = {
        "coin": "HYPE",
        "externalFillId": "fill-1",
        "price": "60",
        "side": "buy",
    }

    async def fake_store_realtime_fills(*_args: object, **_kwargs: object) -> StoredRealtimeFills:
        return StoredRealtimeFills(
            wallet_address="0xsource",
            fetched=1,
            inserted=1,
            duplicate=0,
            is_snapshot=False,
            latest_fill_time_ms=1,
            inserted_rows=[stored_fill],
        )

    async def fake_process_live_copy_fills(
        *_args: object,
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_process_paper_copy_fills(
        *_args: object,
        **_kwargs: object,
    ) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult(processed_fills=1)

    async def fake_publish_event(
        *_args: object,
        event_type: str,
        **_kwargs: object,
    ) -> None:
        published_events.append(event_type)

    monkeypatch.setattr(monitor_worker, "store_realtime_fills", fake_store_realtime_fills)
    monkeypatch.setattr(monitor_worker, "process_live_copy_fills", fake_process_live_copy_fills)
    monkeypatch.setattr(monitor_worker, "process_paper_copy_fills", fake_process_paper_copy_fills)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish_event)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        live_trading_copy_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )
    message: dict[str, Any] = {
        "channel": "userFills",
        "data": {
            "user": "0xsource",
            "fills": [stored_fill],
            "isSnapshot": False,
        },
    }

    await monitor_worker.handle_websocket_message(
        message,
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        wallet_addresses=["0xsource"],
        settings=settings,
    )

    assert calls == ["live", "paper"]
    assert published_events == ["fill", "live_copy", "paper_copy"]


@pytest.mark.asyncio
async def test_handle_websocket_snapshot_prioritizes_live_recovery(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_store_realtime_fills(*_args: object, **_kwargs: object) -> StoredRealtimeFills:
        return StoredRealtimeFills(
            wallet_address="0xsource",
            fetched=1,
            inserted=0,
            duplicate=1,
            is_snapshot=True,
            latest_fill_time_ms=1,
            inserted_rows=[],
        )

    async def fake_live_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult()

    async def fake_paper_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult()

    async def fake_publish_event(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(monitor_worker, "store_realtime_fills", fake_store_realtime_fills)
    monkeypatch.setattr(monitor_worker, "run_live_copy_recovery_once", fake_live_recovery_once)
    monkeypatch.setattr(monitor_worker, "run_paper_copy_recovery_once", fake_paper_recovery_once)
    monkeypatch.setattr(monitor_worker, "publish_event", fake_publish_event)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        live_trading_copy_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )
    message: dict[str, Any] = {
        "channel": "userFills",
        "data": {
            "user": "0xsource",
            "fills": [{"coin": "HYPE", "externalFillId": "fill-1"}],
            "isSnapshot": True,
        },
    }

    await monitor_worker.handle_websocket_message(
        message,
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        wallet_addresses=["0xsource"],
        settings=settings,
    )

    assert calls == ["live", "paper"]


@pytest.mark.asyncio
async def test_startup_copy_recovery_prioritizes_live_recovery(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_live_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("live")
        return PaperCopyBatchResult()

    async def fake_paper_recovery_once(*_args: object, **_kwargs: object) -> PaperCopyBatchResult:
        calls.append("paper")
        return PaperCopyBatchResult()

    monkeypatch.setattr(monitor_worker, "run_live_copy_recovery_once", fake_live_recovery_once)
    monkeypatch.setattr(monitor_worker, "run_paper_copy_recovery_once", fake_paper_recovery_once)

    settings = SimpleNamespace(
        live_trading_enabled=True,
        live_trading_copy_enabled=True,
        paper_trading_enabled=True,
        paper_copy_enabled=True,
    )

    await monitor_worker.run_startup_copy_recovery_once(
        sessionmaker=dummy_sessionmaker,
        redis=object(),
        settings=settings,
    )

    assert calls == ["live", "paper"]
