"""Exposure, loss guards, and target-position sizing against a real database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    PaperCopyFill,
    PaperPosition,
    PaperTradingAccount,
    TradingAccount,
    TradingAccountPerformanceSnapshot,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WatchedWallet,
)
from app.services.copy_signal_service import (
    ExecutionMarketPrices,
    SourceFillPart,
)
from app.services.live_trading_service import (
    LIVE_EXCHANGE_SOURCE,
    live_account_loss_state,
    live_source_net_loss_usd,
    load_live_copy_exposure,
)
from app.services.paper_trading_service import (
    PaperSourceAllocation,
    apply_open_part,
)

pytestmark = pytest.mark.integration

ACCOUNT_KEY = "live_sizing"
SOURCE = "0xsizing-source"
OTHER_SOURCE = "0xsizing-other"


def live_account() -> TradingAccount:
    return TradingAccount(
        key=ACCOUNT_KEY,
        account_type="live",
        label="Sizing account",
        status="enabled",
        network="testnet",
        equity_usd=Decimal("1000"),
        config_payload={"lastReconciliation": {"unifiedEquityUsd": "1000"}},
    )


def watched(address: str) -> WatchedWallet:
    return WatchedWallet(address=address, enabled=True)


def snapshot(*, index: str, recorded_at: datetime, started_at: datetime):
    return TradingAccountPerformanceSnapshot(
        account_key=ACCOUNT_KEY,
        account_type="live",
        equity_usd=Decimal("1000") * Decimal(index),
        performance_index=Decimal(index),
        time_weighted_return_pct=Decimal(index) - Decimal("1"),
        tracking_started_at=started_at,
        recorded_at=recorded_at,
        is_baseline=recorded_at == started_at,
    )


def position(*, source_wallet: str, notional: str, unrealized: str | None = None):
    return TradingPosition(
        account_key=ACCOUNT_KEY,
        account_type="live",
        source_wallet=source_wallet,
        coin="ETH" if source_wallet == OTHER_SOURCE else "BTC",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal(notional),
        notional_usd=Decimal(notional),
        leverage=Decimal("2"),
        margin_mode="cross",
        margin_usd=Decimal(notional) / Decimal("2"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload=({"position": {"unrealizedPnl": unrealized}} if unrealized is not None else {}),
        opened_at=datetime.now(UTC),
    )


def fill(*, fill_id: str, realized: str, fee: str, filled_at: datetime) -> TradingFill:
    return TradingFill(
        account_key=ACCOUNT_KEY,
        account_type="live",
        source_wallet=SOURCE,
        source_fill_id=fill_id,
        sequence_index=0,
        exchange_fill_id=f"exchange-{fill_id}",
        coin="BTC",
        action="close",
        side="long",
        price=Decimal("100"),
        size=Decimal("1"),
        notional_usd=Decimal("100"),
        fee_usd=Decimal(fee),
        realized_pnl_usd=Decimal(realized),
        filled_at=filled_at,
    )


@pytest.mark.asyncio
async def test_loss_state_uses_the_rolling_performance_index(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    started = now - timedelta(days=8)
    async with integration_sessionmaker() as session:
        session.add(live_account())
        await session.flush()
        session.add_all(
            [
                snapshot(index="1.0", recorded_at=started, started_at=started),
                snapshot(index="1.2", recorded_at=now - timedelta(days=3), started_at=started),
                snapshot(index="1.1", recorded_at=now - timedelta(hours=23), started_at=started),
                snapshot(index="0.99", recorded_at=now, started_at=started),
            ]
        )
        await session.flush()

        state = await live_account_loss_state(session, account_key=ACCOUNT_KEY, now=now)

    assert state is not None
    # 24 hours ago the index was 1.2; seven days ago it was the 1.0 baseline.
    assert state.daily_loss_pct == Decimal("1") - Decimal("0.99") / Decimal("1.2")
    assert state.weekly_loss_pct == Decimal("0.01")
    assert state.drawdown_pct == Decimal("1") - Decimal("0.99") / Decimal("1.2")


@pytest.mark.asyncio
async def test_loss_state_is_unknown_without_performance_history(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add(live_account())
        await session.flush()

        state = await live_account_loss_state(
            session,
            account_key=ACCOUNT_KEY,
            now=datetime.now(UTC),
        )

    assert state is None


@pytest.mark.asyncio
async def test_source_loss_combines_recent_realized_and_open_unrealized_losses(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all([live_account(), watched(SOURCE)])
        await session.flush()
        session.add_all(
            [
                fill(fill_id="recent", realized="-30", fee="2", filled_at=now - timedelta(days=1)),
                fill(fill_id="old", realized="-100", fee="1", filled_at=now - timedelta(days=8)),
                position(source_wallet=SOURCE, notional="100", unrealized="-20"),
            ]
        )
        await session.flush()

        loss = await live_source_net_loss_usd(
            session,
            account_key=ACCOUNT_KEY,
            source_wallet=SOURCE,
            now=now,
        )

    assert loss == Decimal("52")


@pytest.mark.asyncio
async def test_exposure_counts_positions_and_unfilled_entry_orders(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add_all([live_account(), watched(SOURCE), watched(OTHER_SOURCE)])
        await session.flush()
        session.add_all(
            [
                position(source_wallet=SOURCE, notional="100"),
                position(source_wallet=OTHER_SOURCE, notional="200"),
                position(source_wallet=LIVE_EXCHANGE_SOURCE, notional="250"),
                TradingOrder(
                    account_key=ACCOUNT_KEY,
                    account_type="live",
                    source_wallet=SOURCE,
                    source_fill_id="pending",
                    sequence_index=0,
                    client_order_id="pending-entry",
                    coin="BTC",
                    action="add",
                    side="long",
                    is_buy=True,
                    reduce_only=False,
                    order_type="ioc",
                    status="ready",
                    requested_size=Decimal("1"),
                    requested_notional_usd=Decimal("50"),
                    filled_size=Decimal("0"),
                    filled_notional_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                ),
            ]
        )
        await session.flush()
        account = await session.get(TradingAccount, ACCOUNT_KEY)
        assert account is not None

        exposure = await load_live_copy_exposure(
            session,
            account=account,
            source_wallet=SOURCE,
            coin="BTC",
            settings=Settings(),
        )
        excluding_pending = await load_live_copy_exposure(
            session,
            account=account,
            source_wallet=SOURCE,
            coin="BTC",
            settings=Settings(),
            exclude_client_order_id="pending-entry",
        )

    assert exposure.source_usd == Decimal("150")
    # Attributed positions (300) exceed the exchange snapshot (250); plus the open order.
    assert exposure.account_usd == Decimal("350")
    assert excluding_pending.source_usd == Decimal("100")
    assert excluding_pending.account_usd == Decimal("300")


PAPER_ACCOUNT_KEY = "paper_sizing"


def paper_account() -> PaperTradingAccount:
    return PaperTradingAccount(
        key=PAPER_ACCOUNT_KEY,
        label="Paper sizing",
        starting_balance_usd=Decimal("1000"),
        cash_balance_usd=Decimal("1000"),
        equity_usd=Decimal("1000"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        enabled=True,
    )


def paper_allocation(source_wallet: str = SOURCE) -> PaperSourceAllocation:
    return PaperSourceAllocation(
        source_wallet=source_wallet,
        source_label="Sizing source",
        rank=1,
        pool_rank=1,
        score=Decimal("90"),
        allocation_pct=Decimal("0.2"),
        active=True,
        has_realtime_slot=True,
        status_reason="copy_candidate",
    )


def paper_fill(*, fill_id: str, coin: str = "HYPE") -> dict:
    return {
        "externalFillId": fill_id,
        "coin": coin,
        "price": "100",
        "timestampMs": int(datetime.now(UTC).timestamp() * 1000),
    }


def paper_part(*, start_position: str, size: str, side: str = "long") -> SourceFillPart:
    return SourceFillPart(
        action="open",
        side=side,
        source_size=Decimal(size),
        source_notional_usd=Decimal(size) * Decimal("100"),
        sequence_index=0,
        start_position=Decimal(start_position),
    )


@pytest.mark.asyncio
async def test_paper_copy_rounds_the_opening_and_then_tracks_the_target(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(paper_copy_slippage_bps=Decimal("0"))
    allocation = paper_allocation()
    prices = ExecutionMarketPrices(prices={"HYPE": Decimal("100")}, sources={"HYPE": "test"})

    async def copy(session: AsyncSession, *, fill_id: str, start: str, size: str) -> None:
        account = await session.get(PaperTradingAccount, PAPER_ACCOUNT_KEY)
        assert account is not None
        await apply_open_part(
            session,
            account=account,
            allocation=allocation,
            fill=paper_fill(fill_id=fill_id),
            part=paper_part(start_position=start, size=size),
            source_perp_equity=Decimal("1000000"),
            source_leverages={"HYPE": Decimal("3")},
            market_prices=prices,
            settings=settings,
        )
        await session.flush()

    async with integration_sessionmaker() as session:
        session.add_all([watched(SOURCE), paper_account()])
        await session.flush()

        # Copy ratio 200 / 1,000,000: one source coin is $0.02 of target at price 100.
        # The opening rounds $0.20 up to $10, the 210 coin target ($4.20) is already
        # covered, and the 760 coin target ($15.20) is $5.20 short, which rounds up to $10.
        await copy(session, fill_id="open", start="0", size="10")
        await copy(session, fill_id="small-add", start="10", size="200")
        await copy(session, fill_id="crossing-add", start="210", size="550")

        held = await session.scalar(
            select(PaperPosition).where(PaperPosition.account_key == PAPER_ACCOUNT_KEY)
        )
        fills = list(
            (
                await session.scalars(
                    select(PaperCopyFill)
                    .where(PaperCopyFill.account_key == PAPER_ACCOUNT_KEY)
                    .order_by(PaperCopyFill.source_fill_id)
                )
            ).all()
        )

    assert held is not None
    # $10 opening, then a $10 rounded-up add once the target passed $15.
    assert held.notional_usd == Decimal("20")
    by_fill = {item.source_fill_id: item for item in fills}
    assert by_fill["open"].notional_usd == Decimal("10")
    assert by_fill["small-add"].skipped_reason == "target_position_reached"
    assert by_fill["crossing-add"].notional_usd == Decimal("10")


@pytest.mark.asyncio
async def test_paper_copy_gives_each_account_coin_to_one_source(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    settings = Settings(paper_copy_slippage_bps=Decimal("0"))
    prices = ExecutionMarketPrices(
        prices={"HYPE": Decimal("100"), "ETH": Decimal("100")},
        sources={"HYPE": "test", "ETH": "test"},
    )

    async def copy(
        session: AsyncSession,
        *,
        source_wallet: str,
        fill_id: str,
        coin: str,
        side: str = "long",
    ) -> None:
        account = await session.get(PaperTradingAccount, PAPER_ACCOUNT_KEY)
        assert account is not None
        await apply_open_part(
            session,
            account=account,
            allocation=paper_allocation(source_wallet),
            fill=paper_fill(fill_id=fill_id, coin=coin),
            part=paper_part(start_position="0", size="10", side=side),
            source_perp_equity=Decimal("1000000"),
            source_leverages={coin: Decimal("3")},
            market_prices=prices,
            settings=settings,
        )
        await session.flush()

    async def open_markets(session: AsyncSession) -> set[tuple[str, str]]:
        positions = await session.scalars(
            select(PaperPosition).where(PaperPosition.account_key == PAPER_ACCOUNT_KEY)
        )
        return {(item.source_wallet, item.coin) for item in positions.all()}

    async with integration_sessionmaker() as session:
        session.add_all([watched(SOURCE), watched(OTHER_SOURCE), paper_account()])
        await session.flush()

        await copy(session, source_wallet=SOURCE, fill_id="first-hype", coin="HYPE")
        # Hyperliquid nets one position per account and coin, whatever the side.
        await copy(session, source_wallet=OTHER_SOURCE, fill_id="other-long", coin="HYPE")
        await copy(
            session,
            source_wallet=OTHER_SOURCE,
            fill_id="other-short",
            coin="HYPE",
            side="short",
        )
        await copy(session, source_wallet=OTHER_SOURCE, fill_id="other-eth", coin="ETH")
        markets_while_reserved = await open_markets(session)

        # Once the owning source is flat, the coin is free for another source.
        owner = await session.scalar(
            select(PaperPosition).where(
                PaperPosition.source_wallet == SOURCE,
                PaperPosition.coin == "HYPE",
            )
        )
        assert owner is not None
        await session.delete(owner)
        await session.flush()
        await copy(session, source_wallet=OTHER_SOURCE, fill_id="other-hype-later", coin="HYPE")
        markets_after_release = await open_markets(session)

        skips = (
            await session.scalars(
                select(PaperCopyFill).where(
                    PaperCopyFill.account_key == PAPER_ACCOUNT_KEY,
                    PaperCopyFill.action == "skip",
                )
            )
        ).all()

    assert markets_while_reserved == {(SOURCE, "HYPE"), (OTHER_SOURCE, "ETH")}
    assert markets_after_release == {(OTHER_SOURCE, "HYPE"), (OTHER_SOURCE, "ETH")}
    assert {item.source_fill_id: item.skipped_reason for item in skips} == {
        "other-long": "paper_market_reserved_by_other_source",
        "other-short": "paper_market_reserved_by_other_source",
    }
