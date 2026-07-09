from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    SourceTrade,
    SourceTradeIgnoredFill,
    SourceTradeSyncState,
    TradingPosition,
    WalletMonitoringStat,
    WatchedWallet,
)
from app.services.wallet_cleanup_service import (
    delete_wallet_data_rows,
    load_zero_fill_wallet_candidates,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="Phase 4 must delete all derived wallet rows through one dependency policy.",
)
async def test_wallet_data_cleanup_removes_all_derived_rows(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "b" * 40
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                WatchedWallet(address=address),
                WalletMonitoringStat(wallet_address=address, first_monitored_at=now),
                SourceTradeSyncState(wallet_address=address),
                SourceTradeIgnoredFill(
                    wallet_address=address,
                    external_fill_id="fill-1",
                    timestamp_ms=1_725_000_000_000,
                    reason="unmatched_close",
                ),
                SourceTrade(
                    trade_key=f"{address}:BTC:1",
                    wallet_address=address,
                    coin="BTC",
                    side="long",
                    status="closed",
                    opened_at_ms=1_725_000_000_000,
                    closed_at_ms=1_725_000_001_000,
                    duration_ms=1000,
                    entry_size=Decimal("1"),
                    closed_size=Decimal("1"),
                    remaining_size=Decimal("0"),
                    entry_notional_usd=Decimal("100"),
                    close_notional_usd=Decimal("101"),
                    average_entry_price=Decimal("100"),
                    average_exit_price=Decimal("101"),
                    realized_pnl_usd=Decimal("1"),
                    fee_usd=Decimal("0.01"),
                    net_pnl_usd=Decimal("0.99"),
                    entry_fill_count=1,
                    close_fill_count=1,
                ),
            ]
        )
        await session.commit()
        await delete_wallet_data_rows(session, addresses=[address])
        await session.commit()

    async with integration_sessionmaker() as session:
        remaining = {
            model.__tablename__: await session.scalar(
                select(func.count()).select_from(model).where(model.wallet_address == address)
            )
            for model in (
                WalletMonitoringStat,
                SourceTradeSyncState,
                SourceTradeIgnoredFill,
                SourceTrade,
            )
        }

    assert remaining == {
        "wallet_monitoring_stats": 0,
        "source_trade_sync_states": 0,
        "source_trade_ignored_fills": 0,
        "source_trades": 0,
    }


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="Phase 4 must protect source wallets while live exposure remains open.",
)
async def test_zero_fill_prune_excludes_open_live_position_sources(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "c" * 40
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                WatchedWallet(
                    address=address,
                    enabled=True,
                    eligible=False,
                    copy_enabled=False,
                    polling_tier="pool",
                    last_polled_at=now,
                ),
                TradingPosition(
                    account_key="live_integration",
                    account_type="live",
                    source_wallet=address,
                    coin="BTC",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("100"),
                    notional_usd=Decimal("100"),
                    leverage=Decimal("1"),
                    margin_usd=Decimal("100"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=now,
                ),
            ]
        )
        await session.commit()
        candidates = await load_zero_fill_wallet_candidates(session, limit=10)

    assert candidates == []
