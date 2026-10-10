"""Live-equivalent risk and market handling for paper accounts.

Paper accounts follow the same account and source loss limits as live accounts,
pay or receive hourly funding, and are liquidated when margin runs out, so
paper results include the outcomes live trading would have had.

Locking: the risk loop locks paper accounts in key order before touching their
positions, the same order the copy engine uses, so both never deadlock.
"""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    AuditLog,
    PaperAccountEquitySnapshot,
    PaperCopyFill,
    PaperFundingPayment,
    PaperPosition,
    PaperTradingAccount,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.copy_risk_rules import (
    AccountLossState,
    account_loss_breach,
    performance_loss_pct,
    source_loss_limit_usd,
)
from app.services.copy_signal_service import dex_from_coin, load_mark_prices
from app.services.market_price_cache import MarketPriceCache
from app.services.paper_execution_simulator import (
    SimulatedPosition,
    cross_liquidation_due,
    funding_payment,
    isolated_liquidation_due,
    liquidation_realized_pnl,
    maintenance_margin_usd,
    position_pnl,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
# Same window as the live per-source loss limit.
PAPER_SOURCE_LOSS_WINDOW = timedelta(days=7)
PAPER_EQUITY_SNAPSHOT_RETENTION = timedelta(days=35)
# Missed funding is backfilled at most this far after a worker outage.
PAPER_FUNDING_LOOKBACK = timedelta(hours=24)
PAPER_MAX_LEVERAGE_REFRESH = timedelta(hours=1)


def apply_account_fee(account: PaperTradingAccount, fee: Decimal) -> None:
    account.fee_usd += fee
    account.cash_balance_usd -= fee
    account.equity_usd = account.cash_balance_usd


def apply_account_realized_result(
    account: PaperTradingAccount,
    *,
    pnl_usd: Decimal,
    fee_usd: Decimal,
) -> None:
    account.realized_pnl_usd += pnl_usd
    account.fee_usd += fee_usd
    account.cash_balance_usd += pnl_usd - fee_usd
    account.equity_usd = account.cash_balance_usd


def apply_account_funding(account: PaperTradingAccount, amount_usd: Decimal) -> None:
    account.funding_usd += amount_usd
    account.cash_balance_usd += amount_usd
    account.equity_usd = account.cash_balance_usd


def clear_paper_account_exit_only(account: PaperTradingAccount) -> None:
    account.exit_only_at = None
    account.exit_only_reason = None


def trip_paper_account_exit_only(
    session: AsyncSession,
    account: PaperTradingAccount,
    *,
    rule: str,
    observed: Decimal,
    limit: Decimal,
    now: datetime,
) -> None:
    """Stop new paper entries after a loss limit, as a live account goes exit-only."""

    account.exit_only_at = now
    account.exit_only_reason = rule
    session.add(
        AuditLog(
            actor="paper_risk",
            action="paper_account.exit_only",
            payload={
                "accountKey": account.key,
                "rule": rule,
                "observed": str(observed),
                "limit": str(limit),
            },
        )
    )


async def paper_account_loss_state(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> AccountLossState | None:
    """Rolling losses and drawdown from marked-to-market paper equity snapshots.

    Paper accounts have no deposits or withdrawals, so equity itself is the
    performance index. A balance reset clears the snapshots.
    """

    snapshot = PaperAccountEquitySnapshot
    latest = await session.scalar(
        select(snapshot.equity_usd)
        .where(snapshot.account_key == account_key)
        .order_by(snapshot.recorded_at.desc())
        .limit(1)
    )
    if latest is None:
        return None
    baseline = await session.scalar(
        select(snapshot.equity_usd)
        .where(snapshot.account_key == account_key)
        .order_by(snapshot.recorded_at.asc())
        .limit(1)
    )
    peak = await session.scalar(
        select(func.max(snapshot.equity_usd)).where(snapshot.account_key == account_key)
    )

    async def equity_at(moment: datetime) -> Decimal:
        value = await session.scalar(
            select(snapshot.equity_usd)
            .where(snapshot.account_key == account_key, snapshot.recorded_at <= moment)
            .order_by(snapshot.recorded_at.desc())
            .limit(1)
        )
        return Decimal(value if value is not None else baseline or ZERO)

    current = Decimal(latest)
    return AccountLossState(
        daily_loss_pct=performance_loss_pct(current, await equity_at(now - timedelta(days=1))),
        weekly_loss_pct=performance_loss_pct(current, await equity_at(now - timedelta(days=7))),
        drawdown_pct=performance_loss_pct(current, Decimal(peak or ZERO)),
    )


async def paper_source_net_loss_usd(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    now: datetime,
    mark_prices: dict[str, Decimal],
) -> Decimal:
    """Realized net loss on a source over the rolling window plus its unrealized loss."""

    realized = await session.scalar(
        select(
            func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd - PaperCopyFill.fee_usd), ZERO)
        ).where(
            PaperCopyFill.account_key == account_key,
            PaperCopyFill.source_wallet == source_wallet,
            PaperCopyFill.action != "skip",
            PaperCopyFill.filled_at >= now - PAPER_SOURCE_LOSS_WINDOW,
        )
    )
    positions = await session.scalars(
        select(PaperPosition).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
        )
    )
    unrealized = sum(
        (
            position_pnl(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=mark_prices[position.coin],
                size=position.size,
            )
            for position in positions.all()
            if mark_prices.get(position.coin)
        ),
        ZERO,
    )
    return max(-(Decimal(realized or ZERO) + unrealized), ZERO)


async def paper_entry_risk_reason(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    source_wallet: str,
    mark_prices: dict[str, Decimal],
    settings: Settings,
    now: datetime | None = None,
) -> str | None:
    """Skip reason when a live loss limit would block this entry, tripping the account."""

    if not settings.paper_copy_loss_limits_enabled:
        return None
    if account.exit_only_at is not None:
        return "paper_account_exit_only"
    resolved_now = now or datetime.now(UTC)
    breach = account_loss_breach(
        await paper_account_loss_state(session, account_key=account.key, now=resolved_now),
        settings=settings,
    )
    if breach is not None:
        trip_paper_account_exit_only(
            session,
            account,
            rule=breach.rule,
            observed=breach.observed,
            limit=breach.limit,
            now=resolved_now,
        )
        return f"paper_{breach.rule}"
    limit = source_loss_limit_usd(equity_usd=max(account.equity_usd, ZERO), settings=settings)
    if limit is not None:
        loss = await paper_source_net_loss_usd(
            session,
            account_key=account.key,
            source_wallet=source_wallet,
            now=resolved_now,
            mark_prices=mark_prices,
        )
        if loss >= limit:
            return "source_loss_limit"
    return None


def paper_marked_equity(
    account: PaperTradingAccount,
    positions: Iterable[PaperPosition],
    mark_prices: dict[str, Decimal],
) -> Decimal | None:
    """Cash plus unrealized PnL; None when a position has no price."""

    unrealized = ZERO
    for position in positions:
        price = mark_prices.get(position.coin)
        if not price:
            return None
        unrealized += position_pnl(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=price,
            size=position.size,
        )
    return account.cash_balance_usd + unrealized


async def record_paper_equity_snapshots(
    session: AsyncSession,
    *,
    mark_prices: dict[str, Decimal],
    now: datetime,
) -> int:
    accounts = list((await session.scalars(select(PaperTradingAccount))).all())
    positions = list((await session.scalars(select(PaperPosition))).all())
    recorded = 0
    for account in accounts:
        equity = paper_marked_equity(
            account,
            (position for position in positions if position.account_key == account.key),
            mark_prices,
        )
        if equity is None:
            continue
        session.add(
            PaperAccountEquitySnapshot(
                account_key=account.key,
                equity_usd=max(equity, ZERO),
                recorded_at=now,
            )
        )
        recorded += 1
    await session.execute(
        delete(PaperAccountEquitySnapshot).where(
            PaperAccountEquitySnapshot.recorded_at < now - PAPER_EQUITY_SNAPSHOT_RETENTION
        )
    )
    return recorded


def simulated_position(
    position: PaperPosition,
    *,
    max_leverages: dict[str, int],
) -> SimulatedPosition:
    return SimulatedPosition(
        key=position.coin,
        side=position.side,
        size=position.size,
        entry_price=position.entry_price,
        margin_usd=position.margin_usd,
        margin_mode=position.margin_mode,
        funding_usd=position.funding_usd,
        max_leverage=max_leverages.get(position.coin),
    )


async def lock_paper_accounts(session: AsyncSession) -> list[PaperTradingAccount]:
    result = await session.scalars(
        select(PaperTradingAccount).order_by(PaperTradingAccount.key.asc()).with_for_update()
    )
    return list(result.all())


async def run_paper_liquidations(
    session: AsyncSession,
    *,
    mark_prices: dict[str, Decimal],
    max_leverages: dict[str, int],
    settings: Settings,
    now: datetime,
) -> int:
    """Liquidate isolated positions and cross accounts that ran out of margin."""

    accounts = await lock_paper_accounts(session)
    positions = list(
        (
            await session.scalars(
                select(PaperPosition)
                .order_by(PaperPosition.account_key, PaperPosition.coin)
                .with_for_update()
            )
        ).all()
    )
    liquidated = 0
    for account in accounts:
        open_positions = [position for position in positions if position.account_key == account.key]
        if not open_positions:
            continue
        for position in [item for item in open_positions if item.margin_mode == "isolated"]:
            price = mark_prices.get(position.coin)
            if not price:
                continue
            simulated = simulated_position(position, max_leverages=max_leverages)
            if isolated_liquidation_due(simulated, mark_price=price):
                await liquidate_paper_position(
                    session,
                    account=account,
                    position=position,
                    simulated=simulated,
                    mark_price=price,
                    settings=settings,
                    now=now,
                )
                open_positions.remove(position)
                liquidated += 1
        cross_positions = [item for item in open_positions if item.margin_mode != "isolated"]
        if cross_positions and cross_liquidation_due(
            [simulated_position(item, max_leverages=max_leverages) for item in open_positions],
            cash_balance_usd=account.cash_balance_usd,
            mark_prices=mark_prices,
        ):
            for position in cross_positions:
                await liquidate_paper_position(
                    session,
                    account=account,
                    position=position,
                    simulated=simulated_position(position, max_leverages=max_leverages),
                    mark_price=mark_prices[position.coin],
                    settings=settings,
                    now=now,
                )
                liquidated += 1
    return liquidated


async def liquidate_paper_position(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    position: PaperPosition,
    simulated: SimulatedPosition,
    mark_price: Decimal,
    settings: Settings,
    now: datetime,
) -> None:
    notional_usd = position.size * mark_price
    realized_pnl = liquidation_realized_pnl(simulated, mark_price=mark_price)
    fee = notional_usd * settings.paper_copy_fee_rate
    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    session.add(
        PaperCopyFill(
            account_key=account.key,
            source_wallet=position.source_wallet,
            source_fill_id=f"liquidation:{position.id}",
            sequence_index=0,
            coin=position.coin,
            action="liquidation",
            side=position.side,
            price=mark_price,
            size=position.size,
            notional_usd=notional_usd,
            leverage=position.leverage,
            margin_usd=position.margin_usd,
            fee_usd=fee,
            realized_pnl_usd=realized_pnl,
            filled_at=now,
            raw_payload={
                "liquidation": {
                    "marginMode": position.margin_mode,
                    "markPrice": str(mark_price),
                    "entryPrice": str(position.entry_price),
                    "maintenanceMarginUsd": str(
                        maintenance_margin_usd(simulated, mark_price=mark_price)
                    ),
                    "fundingUsd": str(position.funding_usd),
                },
                "paper": {
                    "openedAt": position.opened_at.isoformat(),
                    "durationMs": int((now - position.opened_at).total_seconds() * 1000),
                    "leverage": str(position.leverage),
                    "marginUsd": str(position.margin_usd),
                },
            },
        )
    )
    logger.warning(
        "paper position liquidated account=%s source=%s coin=%s mode=%s pnl=%s",
        account.key,
        position.source_wallet,
        position.coin,
        position.margin_mode,
        realized_pnl,
    )
    await session.delete(position)


async def apply_paper_funding(
    session: AsyncSession,
    *,
    client: HyperliquidClient,
    mark_prices: dict[str, Decimal],
    settings: Settings,
    now: datetime,
) -> int:
    """Charge or credit hourly funding to open paper positions, once per position and hour."""

    if not settings.paper_copy_funding_enabled:
        return 0
    accounts = {account.key: account for account in await lock_paper_accounts(session)}
    positions = list(
        (
            await session.scalars(
                select(PaperPosition)
                .order_by(PaperPosition.account_key, PaperPosition.coin)
                .with_for_update()
            )
        ).all()
    )
    if not positions:
        return 0
    start = max(min(position.opened_at for position in positions), now - PAPER_FUNDING_LOOKBACK)
    start_ms = int(start.timestamp() * 1000)
    coins = sorted({position.coin for position in positions})
    histories = await asyncio.gather(
        *(funding_history(client, coin=coin, start_time_ms=start_ms) for coin in coins)
    )
    rates_by_coin = dict(zip(coins, histories, strict=True))
    applied = 0
    for position in positions:
        account = accounts.get(position.account_key)
        price = mark_prices.get(position.coin)
        if account is None or not price:
            continue
        for funding_time, rate in rates_by_coin.get(position.coin, []):
            if not position.opened_at < funding_time <= now:
                continue
            amount = funding_payment(side=position.side, size=position.size, price=price, rate=rate)
            inserted = await session.scalar(
                insert(PaperFundingPayment)
                .values(
                    position_id=position.id,
                    account_key=position.account_key,
                    source_wallet=position.source_wallet,
                    coin=position.coin,
                    side=position.side,
                    funding_time=funding_time,
                    funding_rate=rate,
                    size=position.size,
                    price=price,
                    amount_usd=amount,
                )
                .on_conflict_do_nothing(constraint="ux_paper_funding_payments_position_time")
                .returning(PaperFundingPayment.id)
            )
            if inserted is None:
                continue
            position.funding_usd += amount
            apply_account_funding(account, amount)
            applied += 1
    return applied


async def funding_history(
    client: HyperliquidClient,
    *,
    coin: str,
    start_time_ms: int,
) -> list[tuple[datetime, Decimal]]:
    try:
        rows = await client.funding_history(coin=coin, start_time_ms=start_time_ms)
    except Exception as exc:
        logger.warning("paper funding history fetch failed coin=%s error=%s", coin, exc)
        return []
    rates: list[tuple[datetime, Decimal]] = []
    for row in rows:
        try:
            funding_time = datetime.fromtimestamp(int(row["time"]) / 1000, tz=UTC).replace(
                second=0,
                microsecond=0,
            )
            rate = Decimal(str(row["fundingRate"]))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
        rates.append((funding_time, rate))
    return rates


async def load_max_leverages(client: HyperliquidClient, *, dexes: Iterable[str]) -> dict[str, int]:
    """Maximum leverage per coin from exchange metadata, for maintenance margin."""

    leverages: dict[str, int] = {}
    for dex in sorted(set(dexes)):
        try:
            meta = await client.meta(dex=dex or None)
        except Exception as exc:
            logger.warning("paper max leverage fetch failed dex=%s error=%s", dex or "default", exc)
            continue
        universe = meta.get("universe")
        if not isinstance(universe, list):
            continue
        for asset in universe:
            if not isinstance(asset, dict):
                continue
            name = asset.get("name")
            max_leverage = asset.get("maxLeverage")
            if isinstance(name, str) and isinstance(max_leverage, int) and max_leverage > 0:
                leverages[name] = max_leverage
    return leverages


@dataclass
class PaperRiskLoopState:
    last_snapshot_at: datetime | None = None
    last_funding_hour: datetime | None = None
    max_leverages: dict[str, int] = field(default_factory=dict)
    max_leverages_loaded_at: datetime | None = None


@dataclass(frozen=True)
class PaperRiskRunResult:
    liquidated: int = 0
    funding_payments: int = 0
    snapshots: int = 0


async def run_paper_risk_once(
    sessionmaker: Any,
    *,
    client: HyperliquidClient,
    settings: Settings,
    state: PaperRiskLoopState,
    price_cache: MarketPriceCache | None = None,
    now: datetime | None = None,
) -> PaperRiskRunResult:
    """One pass of liquidation checks, hourly funding, and equity snapshots."""

    resolved_now = now or datetime.now(UTC)
    async with sessionmaker() as session:
        coins = set((await session.scalars(select(PaperPosition.coin).distinct())).all())
        await session.commit()
    mark_prices = (
        await load_mark_prices(client=client, coins=coins, price_cache=price_cache) if coins else {}
    )
    if coins and (
        state.max_leverages_loaded_at is None
        or resolved_now - state.max_leverages_loaded_at >= PAPER_MAX_LEVERAGE_REFRESH
    ):
        state.max_leverages = await load_max_leverages(
            client,
            dexes={"", *(dex_from_coin(coin) for coin in coins)},
        )
        state.max_leverages_loaded_at = resolved_now

    liquidated = 0
    if settings.paper_copy_liquidation_enabled and coins:
        async with sessionmaker() as session:
            liquidated = await run_paper_liquidations(
                session,
                mark_prices=mark_prices,
                max_leverages=state.max_leverages,
                settings=settings,
                now=resolved_now,
            )
            await session.commit()

    funding_payments = 0
    current_hour = resolved_now.replace(minute=0, second=0, microsecond=0)
    # Funding settles on the hour; read it once the hour's rate is published.
    if (
        settings.paper_copy_funding_enabled
        and coins
        and state.last_funding_hour != current_hour
        and resolved_now - current_hour >= timedelta(minutes=1)
    ):
        async with sessionmaker() as session:
            funding_payments = await apply_paper_funding(
                session,
                client=client,
                mark_prices=mark_prices,
                settings=settings,
                now=resolved_now,
            )
            await session.commit()
        state.last_funding_hour = current_hour

    snapshots = 0
    if state.last_snapshot_at is None or resolved_now - state.last_snapshot_at >= timedelta(
        seconds=settings.paper_copy_equity_snapshot_seconds
    ):
        async with sessionmaker() as session:
            snapshots = await record_paper_equity_snapshots(
                session,
                mark_prices=mark_prices,
                now=resolved_now,
            )
            await session.commit()
        state.last_snapshot_at = resolved_now
    return PaperRiskRunResult(
        liquidated=liquidated,
        funding_payments=funding_payments,
        snapshots=snapshots,
    )
