"""Paper round trips, per-account source PnL, and equity drawdown on a real database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    PaperAccountEquitySnapshot,
    PaperCopyFill,
    PaperFundingPayment,
    PaperTradingAccount,
    WatchedWallet,
)
from app.services.paper_performance_service import (
    load_paper_round_trips,
    load_paper_source_performance_rows,
)
from app.services.paper_trading_service import (
    get_paper_account_performance,
    get_paper_trading_summary,
    reset_paper_trading_account_balance,
)

pytestmark = pytest.mark.integration

ACCOUNT = "paper_perf"
OTHER_ACCOUNT = "paper_other"
SOURCE_A = "0x" + "a" * 40
SOURCE_B = "0x" + "b" * 40
RESET_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)


def settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def account(key: str = ACCOUNT, **overrides: object) -> PaperTradingAccount:
    values: dict = {
        "key": key,
        "label": key,
        "starting_balance_usd": Decimal("1000"),
        "cash_balance_usd": Decimal("1000"),
        "equity_usd": Decimal("1000"),
        "realized_pnl_usd": Decimal("0"),
        "fee_usd": Decimal("0"),
        "enabled": False,
    }
    values.update(overrides)
    return PaperTradingAccount(**values)


def fill(
    action: str,
    *,
    minutes: int,
    source: str = SOURCE_A,
    coin: str = "BTC",
    side: str | None = "long",
    size: str = "0",
    notional: str = "0",
    fee: str = "0",
    realized: str = "0",
    sequence_index: int = 0,
    account_key: str = ACCOUNT,
) -> PaperCopyFill:
    return PaperCopyFill(
        account_key=account_key,
        source_wallet=source,
        source_fill_id=f"{coin}-{action}-{minutes}",
        sequence_index=sequence_index,
        coin=coin,
        action=action,
        side=side,
        size=Decimal(size),
        notional_usd=Decimal(notional),
        leverage=Decimal("5"),
        margin_usd=Decimal(notional) / Decimal("5"),
        fee_usd=Decimal(fee),
        realized_pnl_usd=Decimal(realized),
        filled_at=RESET_AT + timedelta(minutes=minutes),
    )


def funding(
    *, minutes: int, amount: str, source: str = SOURCE_A, coin: str = "BTC"
) -> PaperFundingPayment:
    return PaperFundingPayment(
        position_id=uuid4(),
        account_key=ACCOUNT,
        source_wallet=source,
        coin=coin,
        side="long",
        funding_time=RESET_AT + timedelta(minutes=minutes),
        funding_rate=Decimal("0.0001"),
        size=Decimal("1"),
        price=Decimal("100"),
        amount_usd=Decimal(amount),
    )


async def seed_history(session: AsyncSession) -> None:
    session.add_all(
        [
            WatchedWallet(address=SOURCE_A, label="Source A", enabled=True),
            WatchedWallet(address=SOURCE_B, label="Source B", enabled=True),
            account(balance_reset_at=RESET_AT),
        ]
    )
    await session.flush()
    session.add_all(
        [
            # Before the reset: excluded everywhere.
            fill("open", minutes=-120, size="1", notional="100", fee="0.1"),
            fill("close", minutes=-60, size="1", notional="200", fee="0.1", realized="100"),
            funding(minutes=-90, amount="50"),
            # BTC long from source A: open, add, partial reduce, close.
            fill("open", minutes=10, size="1", notional="100", fee="0.1"),
            fill("add", minutes=20, size="0.5", notional="50", fee="0.05"),
            funding(minutes=30, amount="-0.2"),
            fill("reduce", minutes=40, size="0.5", notional="55", fee="0.05", realized="5"),
            fill("close", minutes=50, size="1", notional="112", fee="0.1", realized="12"),
            # ETH from source A: a short that flips into a long, which stays open.
            fill("open", minutes=60, coin="ETH", side="short", size="1", notional="10", fee="0.1"),
            fill(
                "flip_close",
                minutes=70,
                coin="ETH",
                side="short",
                size="1",
                notional="13",
                fee="0.1",
                realized="-3",
            ),
            fill(
                "flip_open",
                minutes=70,
                coin="ETH",
                side="long",
                size="1",
                notional="13",
                fee="0.1",
                sequence_index=1,
            ),
            fill(
                "reduce",
                minutes=80,
                coin="ETH",
                side="long",
                size="0.5",
                notional="7",
                fee="0.02",
                realized="1",
            ),
            # BTC long from source B ends in a paper liquidation.
            fill("open", minutes=90, source=SOURCE_B, size="1", notional="100", fee="0.1"),
            fill("skip", minutes=95, source=SOURCE_B, side=None),
            fill(
                "liquidation",
                minutes=100,
                source=SOURCE_B,
                size="1",
                notional="80",
                fee="0.2",
                realized="-20",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_round_trips_group_a_position_lifecycle_after_the_reset(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed_history(session)
        trades = await load_paper_round_trips(session, account_key=ACCOUNT, since=RESET_AT)

    assert [(trade.coin, trade.side, trade.close_type) for trade in trades] == [
        ("BTC", "long", "liquidation"),
        ("ETH", "short", "flip_close"),
        ("BTC", "long", "close"),
    ]
    btc = trades[2]
    assert btc.source_wallet == SOURCE_A
    assert btc.opened_at == RESET_AT + timedelta(minutes=10)
    assert btc.closed_at == RESET_AT + timedelta(minutes=50)
    assert btc.entry_size == Decimal("1.5")
    assert btc.entry_notional_usd == Decimal("150")
    assert btc.exit_size == Decimal("1.5")
    assert btc.exit_notional_usd == Decimal("167")
    assert btc.realized_pnl_usd == Decimal("17")
    assert btc.fee_usd == Decimal("0.30")
    assert btc.funding_usd == Decimal("-0.2")
    assert btc.net_pnl_usd == Decimal("16.50")
    assert (btc.open_fill_count, btc.close_fill_count) == (2, 2)
    eth = trades[1]
    assert eth.net_pnl_usd == Decimal("-3.2")


@pytest.mark.asyncio
async def test_source_performance_is_per_account_and_after_the_reset(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed_history(session)
        session.add(account(OTHER_ACCOUNT))
        await session.flush()
        session.add(
            fill("open", minutes=5, account_key=OTHER_ACCOUNT, size="1", notional="10", fee="1")
        )
        await session.commit()
        rows = await load_paper_source_performance_rows(session)

    by_key = {(row["account_key"], row["source_wallet"]): row for row in rows}
    source_a = by_key[(ACCOUNT, SOURCE_A)]
    assert source_a["realized_pnl_usd"] == Decimal("15")
    assert source_a["fee_usd"] == Decimal("0.62")
    assert source_a["funding_usd"] == Decimal("-0.2")
    assert source_a["copied_fill_count"] == 8
    assert source_a["closed_trade_count"] == 2
    source_b = by_key[(ACCOUNT, SOURCE_B)]
    assert source_b["realized_pnl_usd"] == Decimal("-20")
    assert source_b["fee_usd"] == Decimal("0.3")
    assert source_b["skipped_fill_count"] == 1
    assert source_b["closed_trade_count"] == 1
    assert by_key[(OTHER_ACCOUNT, SOURCE_A)]["fee_usd"] == Decimal("1")


@pytest.mark.asyncio
async def test_account_performance_reports_equity_drawdown_and_all_round_trips(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed_history(session)
        equity = ["1000", "1020", "990", "1010", "1030"]
        session.add_all(
            PaperAccountEquitySnapshot(
                account_key=ACCOUNT,
                equity_usd=Decimal(value),
                recorded_at=RESET_AT + timedelta(minutes=5 * index),
            )
            for index, value in enumerate(equity)
        )
        await session.commit()
        performance = await get_paper_account_performance(
            session,
            account_key=ACCOUNT,
            settings=settings(),
            closed_trade_limit=2,
        )

    assert performance.stats_started_at == RESET_AT
    assert [point.equity_usd for point in performance.equity_points] == [
        Decimal(value) for value in equity
    ]
    assert performance.peak_equity_usd == Decimal("1030")
    assert performance.max_drawdown_usd == Decimal("30")
    assert performance.max_drawdown_pct is not None
    assert round(performance.max_drawdown_pct, 6) == round(Decimal("30") / Decimal("1020"), 6)
    # Statistics cover every round trip, the list only the newest ones.
    assert performance.trade_stats.trade_count == 3
    assert performance.trade_stats.win_count == 1
    assert performance.trade_stats.loss_count == 2
    assert len(performance.closed_trades) == 2
    assert performance.closed_trades[0].is_paper_liquidation is True
    assert performance.closed_trades[0].source_label == "Source B"


@pytest.mark.asyncio
async def test_summary_limits_recent_fills_per_account(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed_history(session)
        session.add(account(OTHER_ACCOUNT))
        await session.flush()
        session.add(
            fill("open", minutes=-500, account_key=OTHER_ACCOUNT, size="1", notional="10", fee="1")
        )
        await session.commit()
        summary = await get_paper_trading_summary(
            session,
            settings=settings(),
            recent_fill_limit=3,
            include_market_prices=False,
        )

    fills_by_account: dict[str, int] = {}
    for recent in summary.recent_fills:
        fills_by_account[recent.account_key] = fills_by_account.get(recent.account_key, 0) + 1
    # The other account's only fill is the oldest one overall, and it still shows up.
    assert fills_by_account == {ACCOUNT: 3, OTHER_ACCOUNT: 1}
    performance = {(row.account_key, row.source_wallet): row for row in summary.wallet_performance}
    assert performance[(ACCOUNT, SOURCE_A)].net_realized_pnl_usd == Decimal("14.18")
    assert performance[(OTHER_ACCOUNT, SOURCE_A)].net_realized_pnl_usd == Decimal("-1")


@pytest.mark.asyncio
async def test_balance_reset_starts_the_statistics_window(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add(account())
        await session.commit()
        reset_account = await reset_paper_trading_account_balance(
            session,
            account_key=ACCOUNT,
            settings=settings(),
        )
        await session.commit()

    assert reset_account.balance_reset_at is not None
