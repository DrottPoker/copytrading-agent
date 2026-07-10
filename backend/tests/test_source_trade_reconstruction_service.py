from datetime import UTC, datetime
from decimal import Decimal

from app.services.source_trade_reconstruction_service import (
    ReconstructedSourceTrade,
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
