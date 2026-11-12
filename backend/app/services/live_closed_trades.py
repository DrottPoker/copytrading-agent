"""Closed live trades rebuilt from exchange fills, with fees and funding per round trip."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    TradingFill,
    TradingFundingPayment,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
)
from app.services.live_trading_constants import (
    LIVE_EXCHANGE_SOURCE,
    POSITION_EPSILON,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class LiveClosedTrade:
    id: str
    account_key: str
    source_wallet: str
    source_label: str | None
    coin: str
    side: str
    entry_price: Decimal | None
    exit_price: Decimal | None
    size: Decimal
    entry_notional_usd: Decimal
    exit_notional_usd: Decimal
    fee_usd: Decimal
    funding_usd: Decimal
    realized_pnl_usd: Decimal
    net_pnl_usd: Decimal
    opened_at: datetime
    closed_at: datetime
    duration_ms: int | None
    open_fill_count: int
    close_fill_count: int
    is_liquidation: bool


@dataclass
class LiveTradeAccumulator:
    account_key: str
    source_wallet: str
    source_label: str | None
    coin: str
    side: str
    opened_at: datetime
    closed_at: datetime
    last_close_fill_id: str
    opened_size: Decimal = ZERO
    remaining_size: Decimal = ZERO
    closed_size: Decimal = ZERO
    entry_notional_usd: Decimal = ZERO
    exit_notional_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    funding_usd: Decimal = ZERO
    realized_pnl_usd: Decimal = ZERO
    open_fill_count: int = 0
    close_fill_count: int = 0
    is_liquidation: bool = False


async def load_live_closed_trades(
    session: AsyncSession,
    *,
    limit: int = 100,
    fill_scan_limit: int = 5000,
) -> list[LiveClosedTrade]:
    fill_result = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_type == "live",
            TradingFill.source_wallet != "",
        )
        .order_by(TradingFill.filled_at.desc(), TradingFill.created_at.desc())
        .limit(fill_scan_limit)
    )
    fills = list(fill_result.all())
    trades = live_closed_trades_from_fills(fills, limit=limit)
    if not trades:
        return trades
    funding_result = await session.scalars(
        select(TradingFundingPayment).where(
            TradingFundingPayment.account_key.in_({trade.account_key for trade in trades}),
            TradingFundingPayment.occurred_at >= min(trade.opened_at for trade in trades),
            TradingFundingPayment.occurred_at <= max(trade.closed_at for trade in trades),
        )
    )
    return attach_live_funding_to_closed_trades(trades, list(funding_result.all()))


def live_closed_trades_from_fills(
    fills: list[TradingFill],
    *,
    limit: int = 100,
) -> list[LiveClosedTrade]:
    open_trades: dict[tuple[str, str, str, str], LiveTradeAccumulator] = {}
    closed_trades: list[LiveClosedTrade] = []
    for fill in sorted(fills, key=live_fill_chronology_key):
        key = live_fill_trade_key(fill)
        if live_fill_is_open(fill):
            trade = open_trades.get(key) or live_trade_accumulator(fill)
            fill_size = decimal_or_none(fill.size) or ZERO
            trade.opened_size += fill_size
            trade.remaining_size += fill_size
            trade.entry_notional_usd += decimal_or_none(fill.notional_usd) or ZERO
            trade.fee_usd += decimal_or_none(fill.fee_usd) or ZERO
            trade.open_fill_count += 1
            trade.opened_at = min(trade.opened_at, fill.filled_at)
            open_trades[key] = trade
            continue
        if not live_fill_is_close(fill):
            continue
        open_trade = open_trades.get(key)
        if open_trade is None:
            close_only_trade = live_exchange_close_only_trade(fill)
            if close_only_trade is not None:
                closed_trades.append(close_only_trade)
            continue
        trade = open_trade
        fill_size = decimal_or_none(fill.size) or ZERO
        close_size = min(fill_size, trade.remaining_size)
        if close_size <= ZERO:
            continue
        fill_ratio = close_size / fill_size if fill_size > ZERO else ZERO
        trade.remaining_size = max(trade.remaining_size - close_size, ZERO)
        trade.closed_size += close_size
        trade.exit_notional_usd += (decimal_or_none(fill.notional_usd) or ZERO) * fill_ratio
        trade.realized_pnl_usd += (decimal_or_none(fill.realized_pnl_usd) or ZERO) * fill_ratio
        trade.fee_usd += (decimal_or_none(fill.fee_usd) or ZERO) * fill_ratio
        trade.close_fill_count += 1
        trade.is_liquidation = trade.is_liquidation or live_fill_is_liquidation(fill)
        trade.closed_at = fill.filled_at
        trade.last_close_fill_id = str(fill.id)
        if trade.remaining_size <= POSITION_EPSILON:
            closed_trades.append(finish_live_closed_trade(trade))
            del open_trades[key]
    return sorted(closed_trades, key=lambda trade: trade.closed_at, reverse=True)[:limit]


def live_exchange_close_only_trade(fill: TradingFill) -> LiveClosedTrade | None:
    if not live_fill_is_close(fill):
        return None
    is_unattributed = fill.source_wallet == LIVE_EXCHANGE_SOURCE
    if not is_unattributed and not live_fill_has_reconciled_exchange_attribution(fill):
        return None
    fill_size = decimal_or_none(fill.size) or ZERO
    if fill_size <= ZERO:
        return None
    exit_notional_usd = decimal_or_none(fill.notional_usd) or ZERO
    realized_pnl_usd = decimal_or_none(fill.realized_pnl_usd) or ZERO
    fee_usd = decimal_or_none(fill.fee_usd) or ZERO
    exit_price = (
        exit_notional_usd / fill_size if exit_notional_usd > ZERO else decimal_or_none(fill.price)
    )
    entry_price = live_close_only_entry_price(
        side=fill.side,
        exit_price=exit_price,
        realized_pnl_usd=realized_pnl_usd,
        size=fill_size,
    )
    entry_notional_usd = entry_price * fill_size if entry_price is not None else exit_notional_usd
    is_liquidation = live_fill_is_liquidation(fill)
    return LiveClosedTrade(
        id=f"live-closed:{fill.account_key}:{fill.source_wallet}:{fill.coin}:{fill.side}:{fill.id}",
        account_key=fill.account_key,
        source_wallet=fill.source_wallet,
        source_label=(
            ("Unattributed liquidation" if is_liquidation else "Unattributed exchange close")
            if is_unattributed
            else None
        ),
        coin=fill.coin,
        side=fill.side,
        entry_price=entry_price,
        exit_price=exit_price,
        size=fill_size,
        entry_notional_usd=entry_notional_usd,
        exit_notional_usd=exit_notional_usd,
        fee_usd=fee_usd,
        funding_usd=ZERO,
        realized_pnl_usd=realized_pnl_usd,
        net_pnl_usd=realized_pnl_usd - fee_usd,
        opened_at=fill.filled_at,
        closed_at=fill.filled_at,
        duration_ms=0,
        open_fill_count=0,
        close_fill_count=1,
        is_liquidation=is_liquidation,
    )


def live_close_only_entry_price(
    *,
    side: str,
    exit_price: Decimal | None,
    realized_pnl_usd: Decimal,
    size: Decimal,
) -> Decimal | None:
    if exit_price is None or exit_price <= ZERO or size <= ZERO:
        return None
    pnl_per_unit = realized_pnl_usd / size
    if side == "short":
        entry_price = exit_price + pnl_per_unit
    elif side == "long":
        entry_price = exit_price - pnl_per_unit
    else:
        return None
    return entry_price if entry_price > ZERO else None


def live_trade_accumulator(fill: TradingFill) -> LiveTradeAccumulator:
    return LiveTradeAccumulator(
        account_key=fill.account_key,
        source_wallet=fill.source_wallet,
        source_label=None,
        coin=fill.coin,
        side=fill.side,
        opened_at=fill.filled_at,
        closed_at=fill.filled_at,
        last_close_fill_id=str(fill.id),
    )


def finish_live_closed_trade(trade: LiveTradeAccumulator) -> LiveClosedTrade:
    duration_ms = int((trade.closed_at - trade.opened_at).total_seconds() * 1000)
    return LiveClosedTrade(
        id=(
            f"live-closed:{trade.account_key}:{trade.source_wallet}:"
            f"{trade.coin}:{trade.side}:{trade.last_close_fill_id}"
        ),
        account_key=trade.account_key,
        source_wallet=trade.source_wallet,
        source_label=trade.source_label,
        coin=trade.coin,
        side=trade.side,
        entry_price=(
            trade.entry_notional_usd / trade.opened_size if trade.opened_size > ZERO else None
        ),
        exit_price=(
            trade.exit_notional_usd / trade.closed_size if trade.closed_size > ZERO else None
        ),
        size=trade.closed_size,
        entry_notional_usd=trade.entry_notional_usd,
        exit_notional_usd=trade.exit_notional_usd,
        fee_usd=trade.fee_usd,
        funding_usd=trade.funding_usd,
        realized_pnl_usd=trade.realized_pnl_usd,
        net_pnl_usd=trade.realized_pnl_usd - trade.fee_usd + trade.funding_usd,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        duration_ms=max(duration_ms, 0),
        open_fill_count=trade.open_fill_count,
        close_fill_count=trade.close_fill_count,
        is_liquidation=trade.is_liquidation,
    )


def attach_live_funding_to_closed_trades(
    trades: list[LiveClosedTrade],
    payments: list[TradingFundingPayment],
) -> list[LiveClosedTrade]:
    totals = [ZERO for _ in trades]
    for payment in payments:
        candidates = [
            index
            for index, trade in enumerate(trades)
            if trade.account_key == payment.account_key
            and trade.coin == payment.coin
            and trade.opened_at <= payment.occurred_at <= trade.closed_at
        ]
        if not candidates:
            continue
        index = max(candidates, key=lambda item: trades[item].opened_at)
        totals[index] += decimal_or_none(payment.amount_usd) or ZERO
    return [
        replace(
            trade,
            funding_usd=totals[index],
            net_pnl_usd=trade.realized_pnl_usd - trade.fee_usd + totals[index],
        )
        for index, trade in enumerate(trades)
    ]


def live_fill_chronology_key(fill: TradingFill) -> tuple[datetime, datetime, str, int]:
    return (
        fill.filled_at,
        fill.created_at or fill.filled_at,
        fill.source_fill_id or "",
        fill.sequence_index or 0,
    )


def live_fill_trade_key(fill: TradingFill) -> tuple[str, str, str, str]:
    return (fill.account_key, fill.source_wallet.lower(), fill.coin, fill.side)


def live_fill_is_open(fill: TradingFill) -> bool:
    return fill.action in {"open", "add", "flip_open"}


def live_fill_is_close(fill: TradingFill) -> bool:
    return "close" in fill.action or "reduce" in fill.action


def live_fill_is_liquidation(fill: TradingFill) -> bool:
    return isinstance(fill.raw_payload, dict) and "liquidation" in fill.raw_payload


def live_fill_has_reconciled_exchange_attribution(fill: TradingFill) -> bool:
    if fill.order_id is not None or not isinstance(fill.raw_payload, dict):
        return False
    direct = fill.raw_payload.get("sourceAttribution")
    if isinstance(direct, dict) and direct.get("method") == "unique_open_source_position":
        return True
    repair = fill.raw_payload.get("sourceAttributionRepair")
    return isinstance(repair, dict) and repair.get("method") == "source_fill_inventory"
