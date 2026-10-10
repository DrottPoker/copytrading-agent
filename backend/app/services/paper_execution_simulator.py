"""Market mechanics the paper simulator applies so paper copies behave like live orders.

Live copies are IOC limit orders on Hyperliquid. They fill only against resting
book liquidity within the limit price, so they can fill partially or not at all.
Open positions pay or receive hourly funding, and a position is liquidated when
its margin no longer covers the maintenance requirement. These pure functions
model those mechanics from public Hyperliquid data.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")
ONE = Decimal("1")
TWO = Decimal("2")
BPS_DENOMINATOR = Decimal("10000")
# Maintenance margin is half the initial margin at the asset's maximum leverage.
# Without metadata, assume 20x maximum leverage, which liquidates conservatively.
DEFAULT_MAX_LEVERAGE = 20


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class OrderBook:
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]


@dataclass(frozen=True)
class IocFill:
    requested_size: Decimal
    filled_size: Decimal
    average_price: Decimal | None
    limit_price: Decimal

    @property
    def partial(self) -> bool:
        return ZERO < self.filled_size < self.requested_size

    @property
    def unfilled(self) -> bool:
        return self.filled_size <= ZERO

    def payload(self) -> dict[str, Any]:
        return {
            "requestedSize": str(self.requested_size),
            "filledSize": str(self.filled_size),
            "averagePrice": str(self.average_price) if self.average_price is not None else None,
            "limitPrice": str(self.limit_price),
            "partial": self.partial,
        }


def parse_order_book(payload: Any) -> OrderBook | None:
    """Parse an l2Book response into price-sorted levels; None when unusable."""

    if not isinstance(payload, dict):
        return None
    levels = payload.get("levels")
    if not isinstance(levels, list) or len(levels) < 2:
        return None
    bids = tuple(sorted(parse_levels(levels[0]), key=lambda level: level.price, reverse=True))
    asks = tuple(sorted(parse_levels(levels[1]), key=lambda level: level.price))
    if not bids and not asks:
        return None
    return OrderBook(bids=bids, asks=asks)


def parse_levels(raw_levels: Any) -> list[BookLevel]:
    levels: list[BookLevel] = []
    if not isinstance(raw_levels, list):
        return levels
    for raw in raw_levels:
        if not isinstance(raw, dict):
            continue
        try:
            price = Decimal(str(raw.get("px")))
            size = Decimal(str(raw.get("sz")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if price > ZERO and size > ZERO:
            levels.append(BookLevel(price=price, size=size))
    return levels


def ioc_limit_price(observed_price: Decimal, *, is_buy: bool, slippage_bps: Decimal) -> Decimal:
    """The limit price a live IOC order uses: the mid plus the allowed slippage."""

    offset = observed_price * slippage_bps / BPS_DENOMINATOR
    return observed_price + offset if is_buy else observed_price - offset


def simulate_ioc_fill(
    book: OrderBook,
    *,
    is_buy: bool,
    size: Decimal,
    limit_price: Decimal,
) -> IocFill:
    """Fill against the opposite side of the book up to the limit price."""

    remaining = max(size, ZERO)
    filled = ZERO
    cost = ZERO
    for level in book.asks if is_buy else book.bids:
        if remaining <= ZERO:
            break
        if (is_buy and level.price > limit_price) or (not is_buy and level.price < limit_price):
            break
        take = min(remaining, level.size)
        filled += take
        cost += take * level.price
        remaining -= take
    return IocFill(
        requested_size=size,
        filled_size=filled,
        average_price=cost / filled if filled > ZERO else None,
        limit_price=limit_price,
    )


def position_pnl(*, side: str, entry_price: Decimal, exit_price: Decimal, size: Decimal) -> Decimal:
    if side == "long":
        return (exit_price - entry_price) * size
    return (entry_price - exit_price) * size


def maintenance_margin_rate(max_leverage: int | None) -> Decimal:
    leverage = max_leverage if max_leverage is not None and max_leverage > 0 else None
    return ONE / (TWO * Decimal(leverage or DEFAULT_MAX_LEVERAGE))


@dataclass(frozen=True)
class SimulatedPosition:
    """The fields of a paper position that liquidation and funding need."""

    key: str
    side: str
    size: Decimal
    entry_price: Decimal
    margin_usd: Decimal
    margin_mode: str
    funding_usd: Decimal
    max_leverage: int | None


def maintenance_margin_usd(position: SimulatedPosition, *, mark_price: Decimal) -> Decimal:
    return position.size * mark_price * maintenance_margin_rate(position.max_leverage)


def isolated_liquidation_due(position: SimulatedPosition, *, mark_price: Decimal) -> bool:
    """Isolated margin plus PnL and funding no longer covers maintenance margin."""

    equity = (
        position.margin_usd
        + position_pnl(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=mark_price,
            size=position.size,
        )
        + position.funding_usd
    )
    return equity <= maintenance_margin_usd(position, mark_price=mark_price)


def cross_liquidation_due(
    positions: Iterable[SimulatedPosition],
    *,
    cash_balance_usd: Decimal,
    mark_prices: dict[str, Decimal],
) -> bool:
    """Cross account value no longer covers the cross positions' maintenance margin.

    Cross account value is the cash balance minus margin locked in isolated
    positions plus the unrealized PnL of cross positions. Funding is already
    part of the cash balance.
    """

    cross = [position for position in positions if position.margin_mode != "isolated"]
    priced = [position for position in cross if mark_prices.get(position.key)]
    if not priced or len(priced) < len(cross):
        return False
    isolated_margin = sum(
        (position.margin_usd for position in positions if position.margin_mode == "isolated"),
        ZERO,
    )
    unrealized = sum(
        (
            position_pnl(
                side=position.side,
                entry_price=position.entry_price,
                exit_price=mark_prices[position.key],
                size=position.size,
            )
            for position in priced
        ),
        ZERO,
    )
    maintenance = sum(
        (
            maintenance_margin_usd(position, mark_price=mark_prices[position.key])
            for position in priced
        ),
        ZERO,
    )
    return cash_balance_usd - isolated_margin + unrealized <= maintenance


def liquidation_realized_pnl(position: SimulatedPosition, *, mark_price: Decimal) -> Decimal:
    """Realized PnL of a liquidation; isolated losses never exceed the position margin."""

    pnl = position_pnl(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=mark_price,
        size=position.size,
    )
    if position.margin_mode == "isolated":
        return max(pnl, -(position.margin_usd + position.funding_usd))
    return pnl


def funding_payment(*, side: str, size: Decimal, price: Decimal, rate: Decimal) -> Decimal:
    """Signed funding for one interval: longs pay a positive rate, shorts receive it."""

    amount = size * price * rate
    return -amount if side == "long" else amount
