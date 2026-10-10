"""Paper risk limits, market simulation, and shared exit rules on a real database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    PaperAccountEquitySnapshot,
    PaperCopyFill,
    PaperFundingPayment,
    PaperPosition,
    PaperTradingAccount,
    WatchedWallet,
)
from app.services.copy_signal_service import (
    ExecutionMarketPrices,
    SourceAccountState,
    SourceCurrentPosition,
    SourceFillPart,
)
from app.services.paper_execution_simulator import parse_order_book
from app.services.paper_risk_service import (
    apply_paper_funding,
    paper_entry_risk_reason,
    record_paper_equity_snapshots,
    run_paper_liquidations,
)
from app.services.paper_trading_service import (
    PaperSourceAllocation,
    apply_close_part,
    apply_open_part,
    reset_paper_trading_account_balance,
    set_paper_trading_account_enabled,
)

pytestmark = pytest.mark.integration

ACCOUNT = "paper_sim"
SOURCE = "0x" + "5" * 40
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PRICES = ExecutionMarketPrices(prices={"BTC": Decimal("100")}, sources={"BTC": "test"})


def settings(**overrides: object) -> Settings:
    values: dict = {
        "_env_file": None,
        "paper_copy_slippage_bps": Decimal("0"),
        "paper_copy_latency_ms": 0,
        "paper_copy_fee_rate": Decimal("0"),
        "trading_copy_max_entry_age_seconds": 0,
        "live_trading_limit_slippage_bps": Decimal("20"),
    }
    values.update(overrides)
    return Settings(**values)


def account(**overrides: object) -> PaperTradingAccount:
    values: dict = {
        "key": ACCOUNT,
        "label": "Paper simulation",
        "starting_balance_usd": Decimal("1000"),
        "cash_balance_usd": Decimal("1000"),
        "equity_usd": Decimal("1000"),
        "realized_pnl_usd": Decimal("0"),
        "fee_usd": Decimal("0"),
        "enabled": True,
    }
    values.update(overrides)
    return PaperTradingAccount(**values)


def allocation() -> PaperSourceAllocation:
    return PaperSourceAllocation(
        source_wallet=SOURCE,
        source_label="Source",
        rank=1,
        pool_rank=1,
        score=Decimal("90"),
        allocation_pct=Decimal("0.2"),
        active=True,
        has_realtime_slot=True,
        status_reason="copy_candidate",
    )


def position(**overrides: object) -> PaperPosition:
    values: dict = {
        "account_key": ACCOUNT,
        "source_wallet": SOURCE,
        "coin": "BTC",
        "side": "long",
        "size": Decimal("1"),
        "entry_price": Decimal("100"),
        "notional_usd": Decimal("100"),
        "leverage": Decimal("10"),
        "margin_usd": Decimal("10"),
        "margin_mode": "isolated",
        "opened_at": NOW - timedelta(hours=3),
    }
    values.update(overrides)
    return PaperPosition(**values)


def source_fill(fill_id: str, *, timestamp: datetime | None = None) -> dict:
    return {
        "externalFillId": fill_id,
        "coin": "BTC",
        "price": "100",
        "timestampMs": int((timestamp or datetime.now(UTC)).timestamp() * 1000),
    }


def open_part(*, size: str = "100") -> SourceFillPart:
    return SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal(size),
        source_notional_usd=Decimal(size) * Decimal("100"),
        sequence_index=0,
        start_position=Decimal("0"),
    )


def close_part(close_ratio: str | None) -> SourceFillPart:
    return SourceFillPart(
        action="close",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=0,
        close_ratio=Decimal(close_ratio) if close_ratio is not None else None,
    )


def source_state(*, holds: bool, margin_mode: str = "isolated") -> SourceAccountState:
    return SourceAccountState(
        dex="",
        perp_equity=Decimal("10000"),
        leverage_by_coin={"BTC": Decimal("10")},
        positions_by_coin=(
            {"BTC": SourceCurrentPosition(coin="BTC", side="long", size=Decimal("100"))}
            if holds
            else {}
        ),
        margin_mode_by_coin={"BTC": margin_mode},  # type: ignore[dict-item]
    )


async def seed(session: AsyncSession, *items: object) -> None:
    session.add_all([WatchedWallet(address=SOURCE, enabled=True), account()])
    await session.flush()
    session.add_all(list(items))
    await session.commit()


@pytest.mark.asyncio
async def test_a_breached_loss_limit_trips_the_paper_account_until_it_is_restarted(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    config = settings()
    async with integration_sessionmaker() as session:
        await seed(
            session,
            PaperAccountEquitySnapshot(
                account_key=ACCOUNT,
                equity_usd=Decimal("1000"),
                recorded_at=NOW - timedelta(hours=30),
            ),
            PaperAccountEquitySnapshot(
                account_key=ACCOUNT,
                equity_usd=Decimal("850"),
                recorded_at=NOW - timedelta(minutes=5),
            ),
        )
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        assert paper_account is not None

        reason = await paper_entry_risk_reason(
            session,
            account=paper_account,
            source_wallet=SOURCE,
            mark_prices={},
            settings=config,
            now=NOW,
        )
        repeat = await paper_entry_risk_reason(
            session,
            account=paper_account,
            source_wallet=SOURCE,
            mark_prices={},
            settings=config,
            now=NOW,
        )
        await session.commit()
        restarted = await set_paper_trading_account_enabled(
            session,
            account_key=ACCOUNT,
            enabled=True,
            settings=config,
        )

    assert reason == "paper_max_daily_loss"
    assert repeat == "paper_account_exit_only"
    assert restarted.exit_only_at is None


@pytest.mark.asyncio
async def test_the_source_loss_limit_blocks_new_entries_from_a_losing_source(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed(session, position(margin_mode="cross", size=Decimal("1")))
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        assert paper_account is not None

        # 5% of 1000 USD equity is 50 USD; the open position is 60 USD under water.
        reason = await paper_entry_risk_reason(
            session,
            account=paper_account,
            source_wallet=SOURCE,
            mark_prices={"BTC": Decimal("40")},
            settings=settings(),
            now=NOW,
        )

    assert reason == "source_loss_limit"


@pytest.mark.asyncio
async def test_isolated_positions_are_liquidated_and_lose_at_most_their_margin(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed(session, position())
        liquidated = await run_paper_liquidations(
            session,
            mark_prices={"BTC": Decimal("85")},
            max_leverages={"BTC": 40},
            settings=settings(),
            now=NOW,
        )
        await session.commit()
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        remaining = await session.scalar(select(func.count()).select_from(PaperPosition))
        record = await session.scalar(
            select(PaperCopyFill).where(PaperCopyFill.action == "liquidation")
        )

    assert liquidated == 1
    assert remaining == 0
    assert record is not None
    assert record.realized_pnl_usd == Decimal("-10")
    assert paper_account is not None
    assert paper_account.cash_balance_usd == Decimal("990")


@pytest.mark.asyncio
async def test_cross_positions_liquidate_only_when_the_account_runs_out(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed(session, position(margin_mode="cross"))
        survived = await run_paper_liquidations(
            session,
            mark_prices={"BTC": Decimal("85")},
            max_leverages={"BTC": 40},
            settings=settings(),
            now=NOW,
        )
        await session.commit()

    assert survived == 0


@pytest.mark.asyncio
async def test_funding_is_charged_once_per_position_and_hour(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    class FundingClient:
        async def funding_history(self, *, coin: str, start_time_ms: int) -> list[dict]:
            return [
                {
                    "coin": coin,
                    "fundingRate": "0.0001",
                    "time": int((NOW - timedelta(hours=hours)).timestamp() * 1000) + 40,
                }
                for hours in (2, 1)
            ]

    async with integration_sessionmaker() as session:
        await seed(session, position(margin_mode="cross", size=Decimal("2")))
        first = await apply_paper_funding(
            session,
            client=FundingClient(),  # type: ignore[arg-type]
            mark_prices={"BTC": Decimal("100")},
            settings=settings(),
            now=NOW,
        )
        await session.commit()
        second = await apply_paper_funding(
            session,
            client=FundingClient(),  # type: ignore[arg-type]
            mark_prices={"BTC": Decimal("100")},
            settings=settings(),
            now=NOW,
        )
        await session.commit()
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        payments = await session.scalar(select(func.count()).select_from(PaperFundingPayment))

    assert (first, second, payments) == (2, 0, 2)
    assert paper_account is not None
    # A long pays 2 * 100 * 0.0001 = 0.02 USD each hour.
    assert paper_account.funding_usd == Decimal("-0.04")
    assert paper_account.cash_balance_usd == Decimal("999.96")


@pytest.mark.asyncio
async def test_equity_snapshots_mark_positions_to_market_and_reset_clears_them(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    config = settings()
    async with integration_sessionmaker() as session:
        await seed(session)
        recorded = await record_paper_equity_snapshots(session, mark_prices={}, now=NOW)
        await session.commit()
        await set_paper_trading_account_enabled(
            session,
            account_key=ACCOUNT,
            enabled=False,
            settings=config,
        )
        await reset_paper_trading_account_balance(session, account_key=ACCOUNT, settings=config)
        await session.commit()
        left = await session.scalar(select(func.count()).select_from(PaperAccountEquitySnapshot))

    assert recorded == 1
    assert left == 0


@pytest.mark.asyncio
async def test_paper_entries_fill_against_the_order_book_like_an_ioc_order(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    thin_book = parse_order_book(
        {
            "levels": [
                [{"px": "99", "sz": "5"}],
                [{"px": "100", "sz": "0.5"}, {"px": "105", "sz": "9"}],
            ]
        }
    )
    empty_book = parse_order_book(
        {"levels": [[{"px": "99", "sz": "5"}], [{"px": "110", "sz": "9"}]]}
    )
    async with integration_sessionmaker() as session:
        await seed(session)
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        assert paper_account is not None
        partial = await apply_open_part(
            session,
            account=paper_account,
            allocation=allocation(),
            fill=source_fill("partial"),
            part=open_part(),
            source_perp_equity=Decimal("10000"),
            source_leverages={"BTC": Decimal("10")},
            market_prices=PRICES,
            settings=settings(),
            source_account_state=source_state(holds=True),
            order_book=thin_book,
        )
        await session.flush()
        held = await session.scalar(select(PaperPosition))
        unfilled = await apply_open_part(
            session,
            account=paper_account,
            allocation=allocation(),
            fill=source_fill("unfilled"),
            part=SourceFillPart(
                action="open",
                side="long",
                source_size=Decimal("100"),
                source_notional_usd=Decimal("10000"),
                sequence_index=1,
                start_position=Decimal("100"),
            ),
            source_perp_equity=Decimal("10000"),
            source_leverages={"BTC": Decimal("10")},
            market_prices=PRICES,
            settings=settings(),
            source_account_state=source_state(holds=True),
            order_book=empty_book,
        )

    # The 2 BTC target order only finds 0.5 BTC within the 20 bps IOC limit.
    assert partial.processed_fills == 1
    assert held is not None
    assert held.size == Decimal("0.5")
    assert held.margin_mode == "isolated"
    assert unfilled.skip_reasons == {"paper_ioc_unfilled": 1}


@pytest.mark.asyncio
async def test_paper_exits_close_fully_on_a_final_source_close_and_combine_small_closes(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await seed(session, position(margin_mode="cross", opened_at=datetime.now(UTC)))
        paper_account = await session.get(PaperTradingAccount, ACCOUNT)
        assert paper_account is not None

        async def close(fill_id: str, ratio: str | None, *, holds: bool) -> object:
            result = await apply_close_part(
                session,
                account=paper_account,
                allocation=allocation(),
                fill=source_fill(fill_id),
                part=close_part(ratio),
                source_perp_equity=Decimal("10000"),
                source_leverages={"BTC": Decimal("10")},
                market_prices=PRICES,
                settings=settings(),
                source_account_state=source_state(holds=holds, margin_mode="cross"),
            )
            await session.flush()
            return result

        small = await close("small-1", "0.05", holds=True)
        combined = await close("small-2", "0.06", holds=True)
        held = await session.scalar(select(PaperPosition))
        final = await close("final", None, holds=False)
        remaining = await session.scalar(select(func.count()).select_from(PaperPosition))
        aggregated = await session.scalar(
            select(PaperCopyFill).where(PaperCopyFill.source_fill_id == "small-1")
        )

    assert small.skip_reasons == {"close_below_min_order_notional": 1}  # type: ignore[attr-defined]
    assert combined.processed_fills == 1  # type: ignore[attr-defined]
    assert held is not None
    assert held.size == Decimal("0.89")
    assert final.processed_fills == 1  # type: ignore[attr-defined]
    assert remaining == 0
    assert aggregated is not None
    assert aggregated.skipped_reason == "close_aggregated"
