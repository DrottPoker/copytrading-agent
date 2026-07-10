from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ActiveCopyWallet,
    CopySignal,
    CopyTrade,
    DiscoveryWalletCandidate,
    PaperCopyAllocation,
    PaperCopyFill,
    SourceTrade,
    SourceTradeIgnoredFill,
    SourceTradeLink,
    SourceTradeSyncState,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletMonitoringStat,
    WalletPosition,
    WalletScore,
    WalletScoreSnapshot,
    WatchedWallet,
)
from app.services.fill_retention_service import cleanup_wallet_fill_retention
from app.services.wallet_cleanup_service import (
    WalletDataProtectedError,
    delete_wallet_data_rows,
    delete_wallet_related_rows,
    load_current_drawdown_scan_wallets,
    load_low_score_wallet_candidates,
    load_max_drawdown_wallet_candidates,
    load_min_closed_trades_wallet_candidates,
    load_orphan_fill_wallet_candidates,
    load_stale_fill_wallet_candidates,
    load_zero_fill_wallet_candidates,
)
from app.services.wallet_data_policy import wallet_owned_dependencies

pytestmark = pytest.mark.integration


def live_trading_account(key: str) -> TradingAccount:
    return TradingAccount(
        key=key,
        account_type="live",
        label=key,
        status="exit_only",
        network="testnet",
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_wallet_data_cleanup_removes_all_derived_rows(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "b" * 40
    dependent_address = "0x" + "a" * 40
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        signal = CopySignal(
            source_wallet=address,
            coin="BTC",
            action="close",
            side="long",
            decision="observe",
            mode="monitor",
        )
        session.add(signal)
        await session.flush()
        copy_trade = CopyTrade(
            mode="paper",
            source_wallet=address,
            coin="BTC",
            side="long",
            status="closed",
            size_usd=Decimal("100"),
            entry_signal_id=signal.id,
            exit_signal_id=signal.id,
            opened_at=now,
            closed_at=now,
        )
        session.add(copy_trade)
        await session.flush()
        session.add_all(
            [
                live_trading_account("live_integration"),
                WatchedWallet(address=address),
                WalletFill(
                    wallet_address=address,
                    external_fill_id="wallet-fill-1",
                    coin="BTC",
                    side="buy",
                    price=Decimal("100"),
                    size=Decimal("1"),
                    timestamp_ms=1_725_000_000_000,
                    raw_json={},
                ),
                WalletPosition(
                    wallet_address=address,
                    coin="BTC",
                    side="flat",
                    size=Decimal("0"),
                ),
                WalletScore(wallet_address=address, score=Decimal("10")),
                WalletScoreSnapshot(
                    wallet_address=address,
                    score=Decimal("10"),
                    score_payload={},
                ),
                WalletMonitoringStat(wallet_address=address, first_monitored_at=now),
                ActiveCopyWallet(
                    wallet_address=address,
                    rank=1,
                    score=Decimal("10"),
                    status="inactive",
                    has_realtime_slot=False,
                ),
                ActiveCopyWallet(
                    wallet_address=dependent_address,
                    rank=2,
                    score=Decimal("9"),
                    status="inactive",
                    has_realtime_slot=False,
                    blocked_by_wallet_address=address,
                ),
                PaperCopyAllocation(
                    account_key="paper_integration",
                    source_wallet=address,
                    rank=1,
                    allocation_pct=Decimal("0"),
                    allocation_usd=Decimal("0"),
                    max_total_allocation_pct=Decimal("1"),
                    active=False,
                ),
                SourceTradeLink(
                    source_wallet=address,
                    source_fill_id="wallet-fill-1",
                    copy_trade_id=copy_trade.id,
                    coin="BTC",
                    side="long",
                    link_type="close",
                ),
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
                DiscoveryWalletCandidate(
                    wallet_address=address,
                    source="integration",
                ),
                TradingFill(
                    account_key="live_integration",
                    account_type="live",
                    source_wallet=address,
                    source_fill_id="source-audit-fill",
                    sequence_index=0,
                    exchange_fill_id="exchange-audit-fill",
                    coin="BTC",
                    action="close",
                    side="long",
                    price=Decimal("101"),
                    size=Decimal("1"),
                    notional_usd=Decimal("101"),
                    filled_at=now,
                ),
                PaperCopyFill(
                    account_key="paper_integration",
                    source_wallet=address,
                    source_fill_id="paper-audit-fill",
                    sequence_index=0,
                    coin="BTC",
                    action="skip",
                    side="long",
                    filled_at=now,
                ),
            ]
        )
        await session.commit()
        await delete_wallet_data_rows(session, addresses=[address])
        await session.commit()

    async with integration_sessionmaker() as session:
        remaining = {}
        for dependency in wallet_owned_dependencies():
            remaining[dependency.table_name] = await session.scalar(
                text(
                    f"select count(*) from {dependency.table_name} "
                    f"where {dependency.address_column} = :address"
                ),
                {"address": address},
            )
        preserved = {
            "watched_wallets": await session.scalar(
                select(func.count())
                .select_from(WatchedWallet)
                .where(WatchedWallet.address == address)
            ),
            "discovery_wallet_candidates": await session.scalar(
                select(func.count())
                .select_from(DiscoveryWalletCandidate)
                .where(DiscoveryWalletCandidate.wallet_address == address)
            ),
            "trading_fills": await session.scalar(
                select(func.count())
                .select_from(TradingFill)
                .where(TradingFill.source_wallet == address)
            ),
            "paper_copy_fills": await session.scalar(
                select(func.count())
                .select_from(PaperCopyFill)
                .where(PaperCopyFill.source_wallet == address)
            ),
        }
        cleared_blocker = await session.scalar(
            select(ActiveCopyWallet.blocked_by_wallet_address).where(
                ActiveCopyWallet.wallet_address == dependent_address
            )
        )

    assert set(remaining.values()) == {0}
    assert preserved == {
        "watched_wallets": 1,
        "discovery_wallet_candidates": 1,
        "trading_fills": 1,
        "paper_copy_fills": 1,
    }
    assert cleared_blocker is None


@pytest.mark.asyncio
async def test_zero_fill_prune_excludes_open_live_position_sources(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "c" * 40
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                live_trading_account("live_integration"),
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


@pytest.mark.asyncio
async def test_wallet_delete_rechecks_protection_before_mutation(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "d" * 40
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                live_trading_account("live_integration"),
                WatchedWallet(address=address),
                TradingPosition(
                    account_key="live_integration",
                    account_type="live",
                    source_wallet=address,
                    coin="ETH",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("100"),
                    notional_usd=Decimal("100"),
                    leverage=Decimal("1"),
                    margin_usd=Decimal("100"),
                    opened_at=now,
                ),
            ]
        )
        await session.commit()

        deleted_fills, deleted_wallets = await delete_wallet_related_rows(
            session,
            addresses=[address],
        )
        assert (deleted_fills, deleted_wallets) == (0, 0)

        with pytest.raises(WalletDataProtectedError, match="open_trading_position"):
            await delete_wallet_related_rows(
                session,
                addresses=[address],
                strict_protection=True,
            )

        remaining_wallets = await session.scalar(
            select(func.count()).select_from(WatchedWallet).where(WatchedWallet.address == address)
        )

    assert remaining_wallets == 1


@pytest.mark.asyncio
async def test_every_wallet_prune_query_excludes_open_trading_exposure(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    watched_address = "0x" + "e" * 40
    orphan_address = "0x" + "f" * 40
    now = datetime.now(UTC)
    stale_at = now - timedelta(days=90)
    stale_timestamp_ms = int(stale_at.timestamp() * 1000)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                live_trading_account("live_watched"),
                live_trading_account("live_orphan"),
                WatchedWallet(
                    address=watched_address,
                    enabled=True,
                    eligible=False,
                    copy_enabled=False,
                    polling_tier="pool",
                    last_polled_at=now,
                    last_seen_fill_at=stale_at,
                ),
                WalletFill(
                    wallet_address=watched_address,
                    external_fill_id="watched-stale-fill",
                    coin="BTC",
                    side="buy",
                    price=Decimal("100"),
                    size=Decimal("1"),
                    timestamp_ms=stale_timestamp_ms,
                    raw_json={},
                ),
                WalletScore(
                    wallet_address=watched_address,
                    score=Decimal("10"),
                    trade_count=2,
                    max_drawdown_pct=Decimal("0.90"),
                ),
                TradingPosition(
                    account_key="live_watched",
                    account_type="live",
                    source_wallet=watched_address,
                    coin="BTC",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("100"),
                    notional_usd=Decimal("100"),
                    leverage=Decimal("1"),
                    margin_usd=Decimal("100"),
                    opened_at=now,
                ),
                WalletFill(
                    wallet_address=orphan_address,
                    external_fill_id="orphan-fill",
                    coin="ETH",
                    side="buy",
                    price=Decimal("100"),
                    size=Decimal("1"),
                    timestamp_ms=stale_timestamp_ms,
                    raw_json={},
                ),
                TradingPosition(
                    account_key="live_orphan",
                    account_type="live",
                    source_wallet=orphan_address,
                    coin="ETH",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("100"),
                    notional_usd=Decimal("100"),
                    leverage=Decimal("1"),
                    margin_usd=Decimal("100"),
                    opened_at=now,
                ),
            ]
        )
        await session.commit()

        stale_candidates = await load_stale_fill_wallet_candidates(
            session,
            min_days_without_fill=30,
            limit=10,
        )
        min_trade_candidates = await load_min_closed_trades_wallet_candidates(
            session,
            min_closed_trades=5,
            limit=10,
        )
        drawdown_candidates = await load_max_drawdown_wallet_candidates(
            session,
            threshold_pct=Decimal("0.60"),
            limit=10,
        )
        low_score_candidates = await load_low_score_wallet_candidates(
            session,
            min_closed_trades=1,
            score_threshold=Decimal("30"),
            score_operator="lt",
            limit=10,
        )
        current_drawdown_wallets = await load_current_drawdown_scan_wallets(
            session,
            limit=10,
        )
        orphan_candidates = await load_orphan_fill_wallet_candidates(session, limit=10)

    assert watched_address not in {candidate.address for candidate in stale_candidates}
    assert watched_address not in {candidate.address for candidate in min_trade_candidates}
    assert watched_address not in {candidate.address for candidate in drawdown_candidates}
    assert watched_address not in {candidate.address for candidate in low_score_candidates}
    assert watched_address not in {wallet.address for wallet in current_drawdown_wallets}
    assert orphan_address not in {candidate.address for candidate in orphan_candidates}


@pytest.mark.asyncio
async def test_retention_protects_open_positions_and_in_flight_orders(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    position_address = "0x" + "1" * 40
    order_address = "0x" + "2" * 40
    now = datetime.now(UTC)
    old_timestamp_ms = int((now - timedelta(days=90)).timestamp() * 1000)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                live_trading_account("live_position_retention"),
                live_trading_account("live_order_retention"),
                WalletFill(
                    wallet_address=position_address,
                    external_fill_id="position-retention-fill",
                    coin="BTC",
                    side="buy",
                    price=Decimal("100"),
                    size=Decimal("1"),
                    timestamp_ms=old_timestamp_ms,
                    raw_json={},
                ),
                TradingPosition(
                    account_key="live_position_retention",
                    account_type="live",
                    source_wallet=position_address,
                    coin="BTC",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("100"),
                    notional_usd=Decimal("100"),
                    leverage=Decimal("1"),
                    margin_usd=Decimal("100"),
                    opened_at=now,
                ),
                WalletFill(
                    wallet_address=order_address,
                    external_fill_id="order-retention-fill",
                    coin="ETH",
                    side="buy",
                    price=Decimal("100"),
                    size=Decimal("1"),
                    timestamp_ms=old_timestamp_ms,
                    raw_json={},
                ),
                TradingOrder(
                    account_key="live_order_retention",
                    account_type="live",
                    source_wallet=order_address,
                    source_fill_id="source-order-retention",
                    sequence_index=0,
                    client_order_id="client-order-retention",
                    coin="ETH",
                    action="open",
                    side="long",
                    is_buy=True,
                    reduce_only=False,
                    status="ready",
                    requested_size=Decimal("1"),
                    requested_notional_usd=Decimal("100"),
                ),
            ]
        )
        await session.commit()

        result = await cleanup_wallet_fill_retention(
            session,
            dry_run=True,
            retention_days=61,
            protect_top_score_wallets=0,
            use_lock=False,
        )

    assert result.protected_wallets == 2
    assert result.candidate_fills == 0
