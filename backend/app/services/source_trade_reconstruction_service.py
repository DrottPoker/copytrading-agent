from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.source_trade import SourceTradeListResponse, SourceTradeSummary
from app.schemas.wallet import normalize_wallet_address

ZERO = Decimal("0")
POSITION_EPSILON = Decimal("0.00000001")


@dataclass(frozen=True)
class ReconstructedSourceTrade:
    id: str
    wallet_address: str
    coin: str
    side: str
    status: str
    opened_at_ms: int
    closed_at_ms: int | None
    duration_ms: int | None
    entry_size: Decimal
    closed_size: Decimal
    remaining_size: Decimal
    entry_notional_usd: Decimal
    close_notional_usd: Decimal
    average_entry_price: Decimal | None
    average_exit_price: Decimal | None
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    net_pnl_usd: Decimal
    entry_fill_count: int
    close_fill_count: int


@dataclass
class OpenSourceTrade:
    wallet_address: str
    coin: str
    side: str
    opened_at_ms: int
    entry_size: Decimal = ZERO
    closed_size: Decimal = ZERO
    remaining_size: Decimal = ZERO
    entry_notional_usd: Decimal = ZERO
    close_notional_usd: Decimal = ZERO
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    entry_fill_count: int = 0
    close_fill_count: int = 0

    def add_entry(
        self,
        *,
        size: Decimal,
        notional_usd: Decimal,
        fee_usd: Decimal,
        timestamp_ms: int,
    ) -> None:
        if self.entry_fill_count == 0:
            self.opened_at_ms = timestamp_ms
        self.entry_size += size
        self.remaining_size += size
        self.entry_notional_usd += notional_usd
        self.fee_usd += fee_usd
        self.entry_fill_count += 1

    def add_close(
        self,
        *,
        size: Decimal,
        notional_usd: Decimal,
        pnl_usd: Decimal,
        fee_usd: Decimal,
    ) -> Decimal:
        if size <= ZERO or self.remaining_size <= ZERO:
            return ZERO

        closed_size = min(size, self.remaining_size)
        ratio = closed_size / size
        self.remaining_size -= closed_size
        self.closed_size += closed_size
        self.close_notional_usd += notional_usd * ratio
        self.realized_pnl_usd += pnl_usd * ratio
        self.fee_usd += fee_usd * ratio
        self.close_fill_count += 1
        return closed_size

    @property
    def is_closed(self) -> bool:
        return self.remaining_size.copy_abs() <= POSITION_EPSILON

    @property
    def net_pnl_usd(self) -> Decimal:
        return self.realized_pnl_usd - self.fee_usd

    @property
    def average_entry_price(self) -> Decimal | None:
        if self.entry_size <= ZERO:
            return None
        return self.entry_notional_usd / self.entry_size

    @property
    def average_exit_price(self) -> Decimal | None:
        if self.closed_size <= ZERO:
            return None
        return self.close_notional_usd / self.closed_size


@dataclass
class ReconstructedWalletTrades:
    wallet_address: str
    closed_trade_count: int = 0
    open_trade_count: int = 0
    unmatched_close_fill_count: int = 0
    preexisting_open_fill_count: int = 0
    entry_fill_count: int = 0
    close_fill_count: int = 0
    unique_coins: set[str] = field(default_factory=set)
    active_days: set[date] = field(default_factory=set)
    coin_notional_usd: dict[str, Decimal] = field(default_factory=dict)
    total_entry_notional_usd: Decimal = ZERO
    total_close_notional_usd: Decimal = ZERO
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    net_pnl_usd: Decimal = ZERO
    gross_profit_usd: Decimal = ZERO
    gross_loss_usd: Decimal = ZERO
    winning_trade_count: int = 0
    losing_trade_count: int = 0
    max_drawdown_usd: Decimal = ZERO
    first_trade_time_ms: int | None = None
    last_trade_time_ms: int | None = None
    trades_24h: int = 0
    notional_24h: Decimal = ZERO
    net_pnl_24h: Decimal = ZERO
    trades_7d: int = 0
    notional_7d: Decimal = ZERO
    net_pnl_7d: Decimal = ZERO
    items: list[ReconstructedSourceTrade] = field(default_factory=list)
    _cumulative_pnl_usd: Decimal = ZERO
    _peak_pnl_usd: Decimal = ZERO

    @property
    def active_day_count(self) -> int:
        return len(self.active_days)

    @property
    def unique_coin_count(self) -> int:
        return len(self.unique_coins)

    @property
    def average_trade_notional_usd(self) -> Decimal:
        if self.closed_trade_count <= 0:
            return ZERO
        return self.total_entry_notional_usd / Decimal(self.closed_trade_count)

    @property
    def max_coin_notional_usd(self) -> Decimal:
        if not self.coin_notional_usd:
            return ZERO
        return max(self.coin_notional_usd.values())

    def record_closed_trade(
        self,
        trade: OpenSourceTrade,
        *,
        closed_at_ms: int,
        start_24h_ms: int,
        start_7d_ms: int,
    ) -> None:
        net_pnl_usd = trade.net_pnl_usd
        self.closed_trade_count += 1
        self.entry_fill_count += trade.entry_fill_count
        self.close_fill_count += trade.close_fill_count
        self.unique_coins.add(trade.coin)
        self.active_days.add(datetime.fromtimestamp(closed_at_ms / 1000, tz=UTC).date())
        self.coin_notional_usd[trade.coin] = (
            self.coin_notional_usd.get(trade.coin, ZERO) + trade.entry_notional_usd
        )
        self.total_entry_notional_usd += trade.entry_notional_usd
        self.total_close_notional_usd += trade.close_notional_usd
        self.realized_pnl_usd += trade.realized_pnl_usd
        self.fee_usd += trade.fee_usd
        self.net_pnl_usd += net_pnl_usd
        if net_pnl_usd > ZERO:
            self.gross_profit_usd += net_pnl_usd
            self.winning_trade_count += 1
        elif net_pnl_usd < ZERO:
            self.gross_loss_usd += net_pnl_usd.copy_abs()
            self.losing_trade_count += 1

        self._cumulative_pnl_usd += net_pnl_usd
        self._peak_pnl_usd = max(self._peak_pnl_usd, self._cumulative_pnl_usd)
        self.max_drawdown_usd = max(
            self.max_drawdown_usd,
            self._peak_pnl_usd - self._cumulative_pnl_usd,
        )

        if self.first_trade_time_ms is None:
            self.first_trade_time_ms = closed_at_ms
        self.last_trade_time_ms = closed_at_ms

        if closed_at_ms >= start_24h_ms:
            self.trades_24h += 1
            self.notional_24h += trade.entry_notional_usd
            self.net_pnl_24h += net_pnl_usd
        if closed_at_ms >= start_7d_ms:
            self.trades_7d += 1
            self.notional_7d += trade.entry_notional_usd
            self.net_pnl_7d += net_pnl_usd

        self.items.append(
            source_trade_from_open_trade(
                trade,
                sequence=self.closed_trade_count,
                status="closed",
                closed_at_ms=closed_at_ms,
            )
        )

    def record_open_trade(self, trade: OpenSourceTrade) -> None:
        self.open_trade_count += 1
        self.items.append(
            source_trade_from_open_trade(
                trade,
                sequence=self.closed_trade_count + self.open_trade_count,
                status="open",
                closed_at_ms=None,
            )
        )


async def reconstruct_wallet_trades(
    session: AsyncSession,
    *,
    window_start_ms: int,
    start_24h_ms: int,
    start_7d_ms: int,
    include_disabled: bool,
    wallet_address: str | None = None,
) -> dict[str, ReconstructedWalletTrades]:
    result = await session.execute(
        text(
            """
            select
              wf.wallet_address,
              wf.coin,
              wf.external_fill_id,
              wf.timestamp_ms,
              coalesce(wf.notional_usd, 0) as notional_usd,
              coalesce(wf.fee_usd, 0) as fee_usd,
              coalesce(wf.pnl_usd, 0) as pnl_usd,
              wf.size,
              wf.raw_json->>'dir' as direction,
              wf.raw_json->>'startPosition' as start_position
            from wallet_fills wf
            left join watched_wallets ww on ww.address = wf.wallet_address
            where wf.timestamp_ms >= :window_start_ms
              and (
                cast(:wallet_address as text) is not null
                or ww.address is not null
              )
              and (
                cast(:wallet_address as text) is not null
                or :include_disabled
                or ww.enabled is true
              )
              and (
                cast(:wallet_address as text) is null
                or wf.wallet_address = cast(:wallet_address as text)
              )
              and wf.raw_json->>'dir' in (
                'Open Long',
                'Close Long',
                'Open Short',
                'Close Short',
                'Long > Short',
                'Short > Long'
              )
            order by wf.wallet_address, wf.timestamp_ms, wf.external_fill_id
            """
        ),
        {
            "window_start_ms": window_start_ms,
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
        },
    )

    trades_by_wallet: dict[str, ReconstructedWalletTrades] = {}
    open_trades: dict[tuple[str, str, str], OpenSourceTrade] = {}

    for row in result.mappings():
        process_trade_fill(
            row,
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )

    for trade in open_trades.values():
        get_wallet_trades(trades_by_wallet, trade.wallet_address).record_open_trade(trade)

    return trades_by_wallet


async def list_reconstructed_source_trades(
    session: AsyncSession,
    *,
    address: str,
    days: int,
    limit: int = 100,
    offset: int = 0,
) -> SourceTradeListResponse:
    normalized_address = normalize_wallet_address(address)
    now = datetime.now(UTC)
    window_start_ms = int((now.timestamp() - days * 86_400) * 1000)
    start_24h_ms = int((now.timestamp() - 86_400) * 1000)
    start_7d_ms = int((now.timestamp() - 7 * 86_400) * 1000)
    trades_by_wallet = await reconstruct_wallet_trades(
        session,
        window_start_ms=window_start_ms,
        start_24h_ms=start_24h_ms,
        start_7d_ms=start_7d_ms,
        include_disabled=True,
        wallet_address=normalized_address,
    )
    wallet_trades = trades_by_wallet.get(
        normalized_address,
        ReconstructedWalletTrades(wallet_address=normalized_address),
    )
    items = sorted(
        wallet_trades.items,
        key=lambda item: item.closed_at_ms or item.opened_at_ms,
        reverse=True,
    )
    return SourceTradeListResponse(
        items=items[offset : offset + limit],
        total=len(items),
        limit=limit,
        offset=offset,
        days=days,
        summary=SourceTradeSummary(
            closed_trade_count=wallet_trades.closed_trade_count,
            open_trade_count=wallet_trades.open_trade_count,
            unmatched_close_fill_count=wallet_trades.unmatched_close_fill_count,
            preexisting_open_fill_count=wallet_trades.preexisting_open_fill_count,
            total_entry_notional_usd=wallet_trades.total_entry_notional_usd,
            realized_pnl_usd=wallet_trades.realized_pnl_usd,
            fee_usd=wallet_trades.fee_usd,
            net_pnl_usd=wallet_trades.net_pnl_usd,
        ),
    )


def source_trade_from_open_trade(
    trade: OpenSourceTrade,
    *,
    sequence: int,
    status: str,
    closed_at_ms: int | None,
) -> ReconstructedSourceTrade:
    duration_ms = closed_at_ms - trade.opened_at_ms if closed_at_ms is not None else None
    return ReconstructedSourceTrade(
        id=(
            f"{trade.wallet_address}:{trade.coin}:{trade.side}:"
            f"{trade.opened_at_ms}:{closed_at_ms or 0}:{sequence}"
        ),
        wallet_address=trade.wallet_address,
        coin=trade.coin,
        side=trade.side,
        status=status,
        opened_at_ms=trade.opened_at_ms,
        closed_at_ms=closed_at_ms,
        duration_ms=duration_ms,
        entry_size=trade.entry_size,
        closed_size=trade.closed_size,
        remaining_size=trade.remaining_size,
        entry_notional_usd=trade.entry_notional_usd,
        close_notional_usd=trade.close_notional_usd,
        average_entry_price=trade.average_entry_price,
        average_exit_price=trade.average_exit_price,
        realized_pnl_usd=trade.realized_pnl_usd,
        fee_usd=trade.fee_usd,
        net_pnl_usd=trade.net_pnl_usd,
        entry_fill_count=trade.entry_fill_count,
        close_fill_count=trade.close_fill_count,
    )


def process_trade_fill(
    row: Mapping[str, Any],
    *,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    open_trades: dict[tuple[str, str, str], OpenSourceTrade],
    start_24h_ms: int,
    start_7d_ms: int,
) -> None:
    wallet_address = str(row["wallet_address"])
    coin = str(row["coin"])
    direction = str(row["direction"])
    size = decimal_value(row["size"])
    if size <= ZERO:
        return

    start_position = decimal_or_none(row["start_position"])
    fill = FillParts(
        wallet_address=wallet_address,
        coin=coin,
        timestamp_ms=int(row["timestamp_ms"]),
        size=size,
        notional_usd=decimal_value(row["notional_usd"]),
        fee_usd=decimal_value(row["fee_usd"]),
        pnl_usd=decimal_value(row["pnl_usd"]),
        start_position=start_position,
    )

    if direction == "Open Long":
        apply_open(fill, side="long", trades_by_wallet=trades_by_wallet, open_trades=open_trades)
    elif direction == "Open Short":
        apply_open(fill, side="short", trades_by_wallet=trades_by_wallet, open_trades=open_trades)
    elif direction == "Close Long":
        apply_close(
            fill,
            side="long",
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )
    elif direction == "Close Short":
        apply_close(
            fill,
            side="short",
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )
    elif direction == "Long > Short":
        apply_flip(
            fill,
            close_side="long",
            open_side="short",
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )
    elif direction == "Short > Long":
        apply_flip(
            fill,
            close_side="short",
            open_side="long",
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )


@dataclass(frozen=True)
class FillParts:
    wallet_address: str
    coin: str
    timestamp_ms: int
    size: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    pnl_usd: Decimal
    start_position: Decimal | None


def apply_open(
    fill: FillParts,
    *,
    side: str,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    open_trades: dict[tuple[str, str, str], OpenSourceTrade],
) -> None:
    key = (fill.wallet_address, fill.coin, side)
    existing_trade = open_trades.get(key)
    if existing_trade is None and is_preexisting_position_add(fill.start_position, side=side):
        get_wallet_trades(
            trades_by_wallet,
            fill.wallet_address,
        ).preexisting_open_fill_count += 1
        return

    trade = existing_trade or OpenSourceTrade(
        wallet_address=fill.wallet_address,
        coin=fill.coin,
        side=side,
        opened_at_ms=fill.timestamp_ms,
    )
    trade.add_entry(
        size=fill.size,
        notional_usd=fill.notional_usd,
        fee_usd=fill.fee_usd,
        timestamp_ms=fill.timestamp_ms,
    )
    open_trades[key] = trade


def apply_close(
    fill: FillParts,
    *,
    side: str,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    open_trades: dict[tuple[str, str, str], OpenSourceTrade],
    start_24h_ms: int,
    start_7d_ms: int,
) -> Decimal:
    key = (fill.wallet_address, fill.coin, side)
    trade = open_trades.get(key)
    wallet_trades = get_wallet_trades(trades_by_wallet, fill.wallet_address)
    if trade is None:
        wallet_trades.unmatched_close_fill_count += 1
        return ZERO

    closed_size = trade.add_close(
        size=fill.size,
        notional_usd=fill.notional_usd,
        pnl_usd=fill.pnl_usd,
        fee_usd=fill.fee_usd,
    )
    if closed_size < fill.size:
        wallet_trades.unmatched_close_fill_count += 1

    if trade.is_closed:
        wallet_trades.record_closed_trade(
            trade,
            closed_at_ms=fill.timestamp_ms,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )
        open_trades.pop(key, None)

    return closed_size


def apply_flip(
    fill: FillParts,
    *,
    close_side: str,
    open_side: str,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    open_trades: dict[tuple[str, str, str], OpenSourceTrade],
    start_24h_ms: int,
    start_7d_ms: int,
) -> None:
    close_size, open_size = split_flip_size(fill, close_side=close_side)

    if close_size > ZERO:
        close_fill = proportional_fill(fill, size=close_size)
        apply_close(
            close_fill,
            side=close_side,
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )

    if open_size > ZERO:
        open_fill = proportional_fill(
            FillParts(
                wallet_address=fill.wallet_address,
                coin=fill.coin,
                timestamp_ms=fill.timestamp_ms,
                size=fill.size,
                notional_usd=fill.notional_usd,
                fee_usd=fill.fee_usd,
                pnl_usd=ZERO,
                start_position=ZERO,
            ),
            size=open_size,
        )
        apply_open(
            open_fill,
            side=open_side,
            trades_by_wallet=trades_by_wallet,
            open_trades=open_trades,
        )


def split_flip_size(fill: FillParts, *, close_side: str) -> tuple[Decimal, Decimal]:
    if fill.start_position is None:
        return fill.size, ZERO

    if close_side == "long":
        close_size = min(fill.size, max(fill.start_position, ZERO))
    else:
        close_size = min(fill.size, max(fill.start_position.copy_abs(), ZERO))

    open_size = max(fill.size - close_size, ZERO)
    return close_size, open_size


def proportional_fill(fill: FillParts, *, size: Decimal) -> FillParts:
    if fill.size <= ZERO:
        ratio = ZERO
    else:
        ratio = size / fill.size
    return FillParts(
        wallet_address=fill.wallet_address,
        coin=fill.coin,
        timestamp_ms=fill.timestamp_ms,
        size=size,
        notional_usd=fill.notional_usd * ratio,
        fee_usd=fill.fee_usd * ratio,
        pnl_usd=fill.pnl_usd * ratio,
        start_position=fill.start_position,
    )


def is_preexisting_position_add(start_position: Decimal | None, *, side: str) -> bool:
    if start_position is None:
        return False
    if side == "long":
        return start_position > POSITION_EPSILON
    return start_position < -POSITION_EPSILON


def get_wallet_trades(
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    wallet_address: str,
) -> ReconstructedWalletTrades:
    if wallet_address not in trades_by_wallet:
        trades_by_wallet[wallet_address] = ReconstructedWalletTrades(
            wallet_address=wallet_address
        )
    return trades_by_wallet[wallet_address]


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_value(value: Any) -> Decimal:
    parsed = decimal_or_none(value)
    return parsed if parsed is not None else ZERO
