from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.services import source_trade_reconstruction_service
from app.services.source_trade_reconstruction_service import (
    ReconstructedSourceTrade,
    load_source_trade_refresh_candidates,
    source_trade_windows,
)


def test_source_trade_windows_use_reconstructed_trades_only() -> None:
    now = datetime.fromtimestamp(1_800_000_000, UTC)
    recent_closed = source_trade(
        status="closed",
        opened_at_ms=1_799_900_000_000,
        closed_at_ms=1_799_950_000_000,
        entry_notional_usd=Decimal("1000"),
        realized_pnl_usd=Decimal("120"),
        fee_usd=Decimal("20"),
    )
    old_closed = source_trade(
        status="closed",
        opened_at_ms=1_792_000_000_000,
        closed_at_ms=1_792_010_000_000,
        entry_notional_usd=Decimal("500"),
        realized_pnl_usd=Decimal("-40"),
        fee_usd=Decimal("5"),
    )
    open_trade = source_trade(
        status="open",
        opened_at_ms=1_799_990_000_000,
        closed_at_ms=None,
        entry_notional_usd=Decimal("250"),
        realized_pnl_usd=Decimal("10"),
        fee_usd=Decimal("1"),
    )

    windows = {
        window.label: window
        for window in source_trade_windows(
            [recent_closed, old_closed, open_trade],
            now=now,
        )
    }

    assert windows["60d score window"].closed_trade_count == 1
    assert windows["60d score window"].open_trade_count == 1
    assert windows["60d score window"].net_pnl_usd == Decimal("109")
    assert windows["60d score window"].win_rate == Decimal("1")
    assert windows["All time"].closed_trade_count == 2
    assert windows["All time"].open_trade_count == 1
    assert windows["All time"].net_pnl_usd == Decimal("64")


@pytest.mark.asyncio
async def test_refresh_candidates_filter_by_fill_revision_before_scanning_fills() -> None:
    session = CaptureSession([])

    result = await load_source_trade_refresh_candidates(
        session,  # type: ignore[arg-type]
        include_disabled=False,
        wallet_address=None,
    )

    assert result == []
    sql = str(session.statement)
    assert "candidates as materialized" in sql
    assert "sts.fill_revision <> tw.fill_revision" in sql
    assert "group by candidates.address, candidates.fill_revision" in sql
    assert "group by tw.address" not in sql


@pytest.mark.asyncio
async def test_reconstruct_wallet_trades_streams_rows_and_reports_progress() -> None:
    progress_updates: list[int] = []
    session = StreamSession(
        [
            {
                "wallet_address": "0xabc",
                "coin": "HYPE",
                "external_fill_id": "fill-1",
                "timestamp_ms": 1_800_000_000_000,
                "notional_usd": Decimal("100"),
                "fee_usd": Decimal("0.10"),
                "pnl_usd": Decimal("0"),
                "size": Decimal("1"),
                "direction": "Open Long",
                "start_position": "0",
                "is_liquidation": False,
            }
        ]
    )

    async def record_progress(processed_fills: int) -> None:
        progress_updates.append(processed_fills)

    trades = await source_trade_reconstruction_service.reconstruct_wallet_trades(
        session,  # type: ignore[arg-type]
        window_start_ms=0,
        start_24h_ms=0,
        start_7d_ms=0,
        include_disabled=False,
        wallet_address="0xabc",
        progress_callback=record_progress,
    )

    assert session.execution_options == {
        "yield_per": source_trade_reconstruction_service.SOURCE_TRADE_STREAM_BATCH_SIZE
    }
    assert progress_updates == [1]
    assert trades["0xabc"].open_trade_count == 1


@pytest.mark.asyncio
async def test_materialized_trade_sync_persists_candidate_fill_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []
    progress_updates: list[dict[str, Any]] = []
    session = CommitSession()

    async def fake_candidates(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "wallet_address": "0xabc",
                "fill_revision": 42,
                "fill_count": 100,
                "last_fill_timestamp_ms": 1_800_000_000_000,
            }
        ]

    async def fake_refresh(*_args: Any, **kwargs: Any) -> None:
        captured.append(kwargs)
        await kwargs["progress_callback"](50)

    async def fake_progress(progress: dict[str, Any]) -> None:
        progress_updates.append(progress)

    monkeypatch.setattr(
        source_trade_reconstruction_service,
        "load_source_trade_refresh_candidates",
        fake_candidates,
    )
    monkeypatch.setattr(
        source_trade_reconstruction_service,
        "refresh_materialized_source_trades_for_wallet",
        fake_refresh,
    )

    refreshed = await source_trade_reconstruction_service.sync_materialized_source_trades(
        session,  # type: ignore[arg-type]
        include_disabled=False,
        progress_callback=fake_progress,
    )

    assert refreshed == 1
    assert session.commits == 1
    assert captured[0]["wallet_address"] == "0xabc"
    assert captured[0]["fill_revision"] == 42
    assert captured[0]["fill_count"] == 100
    assert captured[0]["last_fill_timestamp_ms"] == 1_800_000_000_000
    assert callable(captured[0]["progress_callback"])
    assert [update["processedFills"] for update in progress_updates] == [0, 50, 100]
    assert progress_updates[-1]["walletComplete"] is True


class StreamSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.execution_options: dict[str, Any] | None = None

    async def stream(
        self,
        _statement: Any,
        _params: dict[str, Any],
        *,
        execution_options: dict[str, Any],
    ) -> "AsyncCaptureResult":
        self.execution_options = execution_options
        return AsyncCaptureResult(self.rows)


class AsyncCaptureResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "AsyncCaptureResult":
        return self

    def __aiter__(self):
        async def iterate_rows():
            for row in self.rows:
                yield row

        return iterate_rows()


class CommitSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class CaptureSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.statement: Any = None

    async def execute(self, statement: Any, _params: Any) -> "CaptureResult":
        self.statement = statement
        return CaptureResult(self.rows)


class CaptureResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "CaptureResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


def source_trade(
    *,
    status: str,
    opened_at_ms: int,
    closed_at_ms: int | None,
    entry_notional_usd: Decimal,
    realized_pnl_usd: Decimal,
    fee_usd: Decimal,
) -> ReconstructedSourceTrade:
    return ReconstructedSourceTrade(
        id=f"{status}-{opened_at_ms}",
        wallet_address="0xabc",
        coin="HYPE",
        side="long",
        status=status,
        opened_at_ms=opened_at_ms,
        closed_at_ms=closed_at_ms,
        duration_ms=(closed_at_ms - opened_at_ms if closed_at_ms is not None else None),
        entry_size=Decimal("1"),
        closed_size=Decimal("1") if status == "closed" else Decimal("0"),
        remaining_size=Decimal("0") if status == "closed" else Decimal("1"),
        entry_notional_usd=entry_notional_usd,
        close_notional_usd=entry_notional_usd,
        average_entry_price=entry_notional_usd,
        average_exit_price=entry_notional_usd,
        realized_pnl_usd=realized_pnl_usd,
        fee_usd=fee_usd,
        net_pnl_usd=realized_pnl_usd - fee_usd,
        entry_fill_count=1,
        close_fill_count=1 if status == "closed" else 0,
    )
