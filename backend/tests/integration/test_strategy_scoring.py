"""Equity history, archiving, re-admission, snapshots, and backtests on a real database."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    DiscoveryImportRun,
    SourceTrade,
    TradingAccount,
    WalletEquityMetric,
    WalletFill,
    WalletScore,
    WalletScoreSnapshot,
    WatchedWallet,
)
from app.services.discovery_service import (
    DiscoveryCandidate,
    readmit_archived_wallets,
    upsert_discovery_candidates,
)
from app.services.score_backtest_service import BacktestConfig, run_score_backtest
from app.services.source_trade_reconstruction_service import load_source_trade_refresh_candidates
from app.services.wallet_cleanup_service import prune_min_closed_trades_wallets
from app.services.wallet_equity_service import (
    load_wallet_equity_metrics,
    refresh_wallet_equity_metrics,
)
from app.services.wallet_score_service import (
    recalculate_wallet_scores,
    resolve_copy_reference_equity,
    upsert_score_snapshots,
)
from app.services.wallet_service import list_wallets

pytestmark = pytest.mark.integration

WALLET = "0x" + "1" * 40
OTHER_WALLET = "0x" + "2" * 40
PROTECTED_WALLET = "0x" + "3" * 40
DAY_MS = 86_400_000


def polled_wallet(address: str, **overrides: object) -> WatchedWallet:
    values: dict = {
        "address": address,
        "enabled": True,
        "last_polled_at": datetime.now(UTC) - timedelta(hours=1),
    }
    values.update(overrides)
    return WatchedWallet(**values)


def closed_trade(address: str, index: int, *, closed_at_ms: int, net_pnl: str) -> SourceTrade:
    return SourceTrade(
        trade_key=f"{address}:BTC:long:{index}",
        wallet_address=address,
        coin="BTC",
        side="long",
        status="closed",
        opened_at_ms=closed_at_ms - 3_600_000,
        closed_at_ms=closed_at_ms,
        duration_ms=3_600_000,
        entry_size=Decimal("1"),
        closed_size=Decimal("1"),
        remaining_size=Decimal("0"),
        entry_notional_usd=Decimal("1000"),
        close_notional_usd=Decimal("1000") + Decimal(net_pnl),
        average_entry_price=Decimal("1000"),
        average_exit_price=Decimal("1000") + Decimal(net_pnl),
        realized_pnl_usd=Decimal(net_pnl),
        fee_usd=Decimal("0"),
        net_pnl_usd=Decimal(net_pnl),
        entry_fill_count=1,
        close_fill_count=1,
    )


class FakePortfolioClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def portfolio(self, *, user: str) -> list:
        self.calls.append(user)
        start = 1_780_000_000_000
        points = [(start + index * DAY_MS, 1000 + 10 * index, 10 * index) for index in range(10)]
        history = {
            "accountValueHistory": [[time_ms, str(value)] for time_ms, value, _ in points],
            "pnlHistory": [[time_ms, str(pnl)] for time_ms, _, pnl in points],
        }
        return [["perpMonth", history]]


@pytest.mark.asyncio
async def test_equity_history_is_stored_and_reused_until_stale(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(_env_file=None)
    client = FakePortfolioClient()
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add(polled_wallet(WALLET))
        await session.commit()

        first = await refresh_wallet_equity_metrics(
            session,
            addresses_by_priority=[WALLET],
            settings=settings,
            now=now,
            client=client,  # type: ignore[arg-type]
        )
        second = await refresh_wallet_equity_metrics(
            session,
            addresses_by_priority=[WALLET],
            settings=settings,
            now=now + timedelta(hours=1),
            client=client,  # type: ignore[arg-type]
        )
        stored = await load_wallet_equity_metrics(session, addresses=[WALLET])

    assert (first, second) == (1, 0)
    assert client.calls == [WALLET]
    metrics, _fetched_at = stored[WALLET]
    assert metrics.status == "ok"
    assert metrics.return_pct is not None
    assert metrics.return_pct > Decimal("0")


@pytest.mark.asyncio
async def test_prune_archives_wallets_and_keeps_their_history(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                polled_wallet(WALLET),
                polled_wallet(PROTECTED_WALLET, copy_enabled=True),
                WalletScore(wallet_address=WALLET, score=Decimal("10"), trade_count=3),
                WalletScore(wallet_address=PROTECTED_WALLET, score=Decimal("10"), trade_count=3),
                closed_trade(WALLET, 1, closed_at_ms=1_780_000_000_000, net_pnl="-20"),
                WalletScoreSnapshot(
                    wallet_address=WALLET,
                    snapshot_date=date(2026, 10, 1),
                    score=Decimal("10"),
                    score_payload={},
                ),
                WalletEquityMetric(wallet_address=WALLET, status="ok", point_count=10),
            ]
        )
        await session.commit()

        result = await prune_min_closed_trades_wallets(
            session,
            dry_run=False,
            min_closed_trades=20,
            use_lock=False,
        )
        repeat = await prune_min_closed_trades_wallets(
            session,
            dry_run=False,
            min_closed_trades=20,
            use_lock=False,
        )
        archived = await session.scalar(
            select(WatchedWallet).where(WatchedWallet.address == WALLET)
        )
        protected = await session.scalar(
            select(WatchedWallet).where(WatchedWallet.address == PROTECTED_WALLET)
        )
        kept_trades = await session.scalar(
            select(func.count())
            .select_from(SourceTrade)
            .where(SourceTrade.wallet_address == WALLET)
        )
        kept_snapshots = await session.scalar(
            select(func.count())
            .select_from(WalletScoreSnapshot)
            .where(WalletScoreSnapshot.wallet_address == WALLET)
        )
        kept_equity = await session.scalar(
            select(func.count())
            .select_from(WalletEquityMetric)
            .where(WalletEquityMetric.wallet_address == WALLET)
        )
        remaining_score = await session.scalar(
            select(WalletScore).where(WalletScore.wallet_address == WALLET)
        )
        active_wallets, _ = await list_wallets(session)
        archived_wallets, _ = await list_wallets(session, archived=True)
        refresh_candidates = await load_source_trade_refresh_candidates(
            session,
            include_disabled=True,
            wallet_address=None,
        )

    assert result.archived_wallets == 1
    assert result.deleted_wallets == 0
    assert repeat.candidate_wallets == 0
    assert archived is not None
    assert archived.archived_at is not None
    assert archived.archive_reason == "min_closed_trades_prune"
    assert archived.enabled is False
    assert protected is not None
    assert protected.archived_at is None
    assert (kept_trades, kept_snapshots, kept_equity) == (1, 1, 1)
    assert remaining_score is None
    assert [wallet.address for wallet in active_wallets] == [PROTECTED_WALLET]
    assert [wallet.address for wallet in archived_wallets] == [WALLET]
    assert WALLET not in {str(row["wallet_address"]) for row in refresh_candidates}


@pytest.mark.asyncio
async def test_archived_wallets_reenter_discovery_only_after_the_cooldown(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        run = DiscoveryImportRun(source="hyperliquid_leaderboard_week", requested_limit=10)
        session.add_all(
            [
                run,
                polled_wallet(
                    WALLET,
                    enabled=False,
                    archived_at=now - timedelta(days=40),
                    archive_reason="low_score_prune",
                ),
                polled_wallet(
                    OTHER_WALLET,
                    enabled=False,
                    archived_at=now - timedelta(days=5),
                    archive_reason="low_score_prune",
                ),
            ]
        )
        await session.flush()

        result = await upsert_discovery_candidates(
            session,
            run_id=run.id,
            candidates=[
                DiscoveryCandidate(wallet_address=WALLET, source="hyperliquid_leaderboard_week"),
                DiscoveryCandidate(
                    wallet_address=OTHER_WALLET,
                    source="hyperliquid_leaderboard_week",
                ),
            ],
            readmit_archived_after_days=30,
        )
        readmitted = await readmit_archived_wallets(session, addresses=[WALLET])
        restored = await session.scalar(
            select(WatchedWallet).where(WatchedWallet.address == WALLET)
        )
        await session.commit()

    assert result.inserted == 1
    assert result.skip_reasons.get("archived_cooldown") == 1
    assert readmitted == {WALLET}
    assert restored is not None
    assert restored.archived_at is None
    assert restored.enabled is True


@pytest.mark.asyncio
async def test_score_snapshots_keep_one_row_per_wallet_and_day(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    day = date(2026, 10, 10)
    async with integration_sessionmaker() as session:
        for score in ("40", "55"):
            await upsert_score_snapshots(
                session,
                records=[
                    {
                        "wallet_address": WALLET,
                        "snapshot_date": day,
                        "score": Decimal(score),
                        "score_payload": {"tradeCount": 25},
                    }
                ],
            )
            await session.commit()
        rows = (await session.execute(select(WalletScoreSnapshot))).scalars().all()

    assert len(rows) == 1
    assert rows[0].score == Decimal("55")


@pytest.mark.asyncio
async def test_copy_reference_equity_follows_the_live_account(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key="live_reference",
                account_type="live",
                label="Live",
                status="enabled",
                network="testnet",
                equity_usd=Decimal("2500"),
                config_payload={"lastReconciliation": {"unifiedEquityUsd": "2500"}},
            )
        )
        await session.commit()

        # Environment variables win over init arguments, so toggle live mode on a copy.
        base = Settings(_env_file=None)
        live = await resolve_copy_reference_equity(
            session,
            settings=base.model_copy(update={"live_trading_enabled": True}),
        )
        paper = await resolve_copy_reference_equity(
            session,
            settings=base.model_copy(update={"live_trading_enabled": False}),
        )

    assert live == Decimal("2500")
    assert paper == Decimal("1000")


@pytest.mark.asyncio
async def test_backtest_recomputes_scores_without_lookahead(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    start_ms = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp() * 1000)
    trades: list[SourceTrade] = []
    for index in range(100):
        closed_at_ms = start_ms + index * DAY_MS
        trades.append(closed_trade(WALLET, index, closed_at_ms=closed_at_ms, net_pnl="30"))
        trades.append(closed_trade(OTHER_WALLET, index, closed_at_ms=closed_at_ms, net_pnl="-30"))
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                polled_wallet(WALLET),
                polled_wallet(OTHER_WALLET),
                WalletEquityMetric(
                    wallet_address=WALLET,
                    status="ok",
                    average_account_value_usd=Decimal("10000"),
                    point_count=10,
                ),
                WalletEquityMetric(
                    wallet_address=OTHER_WALLET,
                    status="ok",
                    average_account_value_usd=Decimal("10000"),
                    point_count=10,
                ),
                *trades,
            ]
        )
        await session.commit()

        report = await run_score_backtest(
            session,
            settings=Settings(_env_file=None),
            config=BacktestConfig(
                horizon_days=14,
                step_days=7,
                top_k=1,
                random_samples=50,
                source="recompute",
            ),
        )

    assert report.cutoffs
    assert all(cutoff.source == "recompute" for cutoff in report.cutoffs)
    assert all(cutoff.top_wallets == [WALLET] for cutoff in report.cutoffs)
    assert report.mean_top_return is not None
    assert report.mean_top_return > Decimal("0")


def wallet_fill(
    address: str,
    index: int,
    *,
    direction: str,
    price: str,
    timestamp_ms: int,
) -> WalletFill:
    closing = direction.startswith("Close")
    return WalletFill(
        wallet_address=address,
        external_fill_id=f"{address}-{index}",
        coin="BTC",
        side="sell" if closing else "buy",
        price=Decimal(price),
        size=Decimal("1"),
        notional_usd=Decimal(price),
        fee_usd=Decimal("0"),
        pnl_usd=Decimal(price) - Decimal("1000") if closing else Decimal("0"),
        timestamp_ms=timestamp_ms,
        raw_json={"dir": direction, "startPosition": "1" if closing else "0"},
    )


@pytest.mark.asyncio
async def test_scoring_run_persists_scores_snapshots_and_new_columns(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    fills = []
    for index in range(25):
        opened_at = now_ms - (30 - index) * DAY_MS
        fills.append(
            wallet_fill(
                WALLET, 2 * index, direction="Open Long", price="1000", timestamp_ms=opened_at
            )
        )
        fills.append(
            wallet_fill(
                WALLET,
                2 * index + 1,
                direction="Close Long",
                price="1010",
                timestamp_ms=opened_at + 3_600_000,
            )
        )
    settings = Settings(
        _env_file=None,
        scoring_equity_enabled=False,
        scoring_current_drawdown_enabled=False,
    )
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                polled_wallet(WALLET),
                polled_wallet(OTHER_WALLET, enabled=False, archived_at=datetime.now(UTC)),
                *fills,
            ]
        )
        await session.commit()

        result = await recalculate_wallet_scores(session, settings=settings, use_lock=False)
        score = await session.scalar(
            select(WalletScore).where(WalletScore.wallet_address == WALLET)
        )
        snapshots = (await session.execute(select(WalletScoreSnapshot))).scalars().all()

    assert result.scored_wallets == 1
    assert score is not None
    assert score.trade_count == 25
    assert score.realized_drawdown_pct is not None
    assert score.max_drawdown_pct is None
    assert score.underwater_add_ratio == Decimal("0")
    assert [snapshot.wallet_address for snapshot in snapshots] == [WALLET]
    assert snapshots[0].score_payload["tradeCount"] == 25
    assert snapshots[0].score_payload["equityStatus"] == "missing"
