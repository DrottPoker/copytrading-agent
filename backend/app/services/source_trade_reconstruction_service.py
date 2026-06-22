from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SourceTrade, SourceTradeIgnoredFill, SourceTradeSyncState
from app.schemas.source_trade import SourceTradeListResponse, SourceTradeSummary
from app.schemas.wallet import normalize_wallet_address

ZERO = Decimal("0")
ONE = Decimal("1")
POSITION_EPSILON = Decimal("0.00000001")
SOURCE_TRADE_INSERT_BATCH_SIZE = 1000


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
    has_liquidation: bool = False
    liquidation_fill_count: int = 0
    liquidation_notional_usd: Decimal = ZERO


@dataclass(frozen=True)
class IgnoredSourceFill:
    wallet_address: str
    external_fill_id: str
    timestamp_ms: int
    reason: str


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
    has_liquidation: bool = False
    liquidation_fill_count: int = 0
    liquidation_notional_usd: Decimal = ZERO

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
        is_liquidation: bool,
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
        if is_liquidation:
            self.has_liquidation = True
            self.liquidation_fill_count += 1
            self.liquidation_notional_usd += notional_usd * ratio
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
    liquidation_trade_count: int = 0
    liquidation_close_fill_count: int = 0
    liquidation_notional_usd: Decimal = ZERO
    items: list[ReconstructedSourceTrade] = field(default_factory=list)
    ignored_fills: list[IgnoredSourceFill] = field(default_factory=list)
    winning_trade_pnls: list[Decimal] = field(default_factory=list)
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

    @property
    def effective_winning_trade_count(self) -> Decimal | None:
        if self.gross_profit_usd <= ZERO or not self.winning_trade_pnls:
            return None
        hhi = sum(
            (
                (profit_usd / self.gross_profit_usd)
                * (profit_usd / self.gross_profit_usd)
                for profit_usd in self.winning_trade_pnls
                if profit_usd > ZERO
            ),
            ZERO,
        )
        if hhi <= ZERO:
            return None
        return ONE / hhi

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
            self.winning_trade_pnls.append(net_pnl_usd)
        elif net_pnl_usd < ZERO:
            self.gross_loss_usd += net_pnl_usd.copy_abs()
            self.losing_trade_count += 1
        if trade.has_liquidation:
            self.liquidation_trade_count += 1
            self.liquidation_close_fill_count += trade.liquidation_fill_count
            self.liquidation_notional_usd += trade.liquidation_notional_usd

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

    def record_ignored_fill(self, fill: "FillParts", *, reason: str) -> None:
        if reason == "unmatched_close":
            self.unmatched_close_fill_count += 1
        elif reason == "preexisting_open":
            self.preexisting_open_fill_count += 1
        self.ignored_fills.append(
            IgnoredSourceFill(
                wallet_address=fill.wallet_address,
                external_fill_id=fill.external_fill_id,
                timestamp_ms=fill.timestamp_ms,
                reason=reason,
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
              wf.raw_json->>'startPosition' as start_position,
              wf.raw_json ? 'liquidation' as is_liquidation
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
    await sync_materialized_source_trades(
        session,
        include_disabled=True,
        wallet_address=normalized_address,
    )
    trades_by_wallet = await load_materialized_wallet_trades(
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
            liquidation_trade_count=wallet_trades.liquidation_trade_count,
            liquidation_notional_usd=wallet_trades.liquidation_notional_usd,
        ),
    )


async def sync_materialized_source_trades(
    session: AsyncSession,
    *,
    include_disabled: bool,
    wallet_address: str | None = None,
) -> int:
    candidates = await load_source_trade_refresh_candidates(
        session,
        include_disabled=include_disabled,
        wallet_address=wallet_address,
    )
    for candidate in candidates:
        await refresh_materialized_source_trades_for_wallet(
            session,
            wallet_address=str(candidate["wallet_address"]),
            fill_count=int(candidate["fill_count"] or 0),
            last_fill_timestamp_ms=(
                int(candidate["last_fill_timestamp_ms"])
                if candidate["last_fill_timestamp_ms"] is not None
                else None
            ),
        )
    return len(candidates)


async def load_source_trade_refresh_candidates(
    session: AsyncSession,
    *,
    include_disabled: bool,
    wallet_address: str | None,
) -> list[Mapping[str, Any]]:
    result = await session.execute(
        text(
            """
            with target_wallets as (
              select address
              from watched_wallets
              where (:include_disabled or enabled is true)
                and (
                  cast(:wallet_address as text) is null
                  or address = cast(:wallet_address as text)
                )
            ),
            fill_state as (
              select
                tw.address as wallet_address,
                count(wf.id) as fill_count,
                max(wf.timestamp_ms) as last_fill_timestamp_ms
              from target_wallets tw
              left join wallet_fills wf on wf.wallet_address = tw.address
              group by tw.address
            )
            select
              fs.wallet_address,
              fs.fill_count,
              fs.last_fill_timestamp_ms
            from fill_state fs
            left join source_trade_sync_states sts on sts.wallet_address = fs.wallet_address
            where sts.wallet_address is null
               or sts.fill_count <> fs.fill_count
               or coalesce(sts.last_fill_timestamp_ms, -1)
                  <> coalesce(fs.last_fill_timestamp_ms, -1)
            order by fs.wallet_address
            """
        ),
        {
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
        },
    )
    return list(result.mappings().all())


async def refresh_materialized_source_trades_for_wallet(
    session: AsyncSession,
    *,
    wallet_address: str,
    fill_count: int,
    last_fill_timestamp_ms: int | None,
) -> None:
    trades_by_wallet = await reconstruct_wallet_trades(
        session,
        window_start_ms=0,
        start_24h_ms=2**63 - 1,
        start_7d_ms=2**63 - 1,
        include_disabled=True,
        wallet_address=wallet_address,
    )
    wallet_trades = trades_by_wallet.get(
        wallet_address,
        ReconstructedWalletTrades(wallet_address=wallet_address),
    )

    await session.execute(delete(SourceTrade).where(SourceTrade.wallet_address == wallet_address))
    await session.execute(
        delete(SourceTradeIgnoredFill).where(
            SourceTradeIgnoredFill.wallet_address == wallet_address
        )
    )
    records = [source_trade_record(item) for item in wallet_trades.items]
    await insert_source_trade_records(session, records=records)
    ignored_records = [ignored_fill_record(item) for item in wallet_trades.ignored_fills]
    await insert_source_trade_ignored_fill_records(session, records=ignored_records)

    stmt = insert(SourceTradeSyncState).values(
        wallet_address=wallet_address,
        fill_count=fill_count,
        last_fill_timestamp_ms=last_fill_timestamp_ms,
        unmatched_close_fill_count=wallet_trades.unmatched_close_fill_count,
        preexisting_open_fill_count=wallet_trades.preexisting_open_fill_count,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["wallet_address"],
            set_={
                "fill_count": stmt.excluded.fill_count,
                "last_fill_timestamp_ms": stmt.excluded.last_fill_timestamp_ms,
                "unmatched_close_fill_count": stmt.excluded.unmatched_close_fill_count,
                "preexisting_open_fill_count": stmt.excluded.preexisting_open_fill_count,
                "synced_at": text("now()"),
            },
        )
    )


async def insert_source_trade_records(
    session: AsyncSession,
    *,
    records: list[dict[str, Any]],
) -> None:
    for batch in record_batches(records, batch_size=SOURCE_TRADE_INSERT_BATCH_SIZE):
        await session.execute(insert(SourceTrade).values(batch))


async def insert_source_trade_ignored_fill_records(
    session: AsyncSession,
    *,
    records: list[dict[str, Any]],
) -> None:
    for batch in record_batches(records, batch_size=SOURCE_TRADE_INSERT_BATCH_SIZE):
        await session.execute(insert(SourceTradeIgnoredFill).values(batch))


def record_batches(
    records: list[dict[str, Any]],
    *,
    batch_size: int,
) -> list[list[dict[str, Any]]]:
    if batch_size <= 0:
        batch_size = SOURCE_TRADE_INSERT_BATCH_SIZE
    return [records[start : start + batch_size] for start in range(0, len(records), batch_size)]


def source_trade_record(item: ReconstructedSourceTrade) -> dict[str, Any]:
    return {
        "trade_key": item.id,
        "wallet_address": item.wallet_address,
        "coin": item.coin,
        "side": item.side,
        "status": item.status,
        "opened_at_ms": item.opened_at_ms,
        "closed_at_ms": item.closed_at_ms,
        "duration_ms": item.duration_ms,
        "entry_size": item.entry_size,
        "closed_size": item.closed_size,
        "remaining_size": item.remaining_size,
        "entry_notional_usd": item.entry_notional_usd,
        "close_notional_usd": item.close_notional_usd,
        "average_entry_price": item.average_entry_price,
        "average_exit_price": item.average_exit_price,
        "realized_pnl_usd": item.realized_pnl_usd,
        "fee_usd": item.fee_usd,
        "net_pnl_usd": item.net_pnl_usd,
        "entry_fill_count": item.entry_fill_count,
        "close_fill_count": item.close_fill_count,
        "has_liquidation": item.has_liquidation,
        "liquidation_fill_count": item.liquidation_fill_count,
        "liquidation_notional_usd": item.liquidation_notional_usd,
    }


def ignored_fill_record(item: IgnoredSourceFill) -> dict[str, Any]:
    return {
        "wallet_address": item.wallet_address,
        "external_fill_id": item.external_fill_id,
        "timestamp_ms": item.timestamp_ms,
        "reason": item.reason,
    }


async def load_materialized_wallet_trades(
    session: AsyncSession,
    *,
    window_start_ms: int,
    start_24h_ms: int,
    start_7d_ms: int,
    include_disabled: bool,
    wallet_address: str | None = None,
) -> dict[str, ReconstructedWalletTrades]:
    trades_by_wallet: dict[str, ReconstructedWalletTrades] = {}
    await apply_materialized_ignored_fill_counts(
        session,
        trades_by_wallet=trades_by_wallet,
        window_start_ms=window_start_ms,
        include_disabled=include_disabled,
        wallet_address=wallet_address,
    )

    result = await session.execute(
        text(
            """
            select
              st.trade_key,
              st.wallet_address,
              st.coin,
              st.side,
              st.status,
              st.opened_at_ms,
              st.closed_at_ms,
              st.duration_ms,
              st.entry_size,
              st.closed_size,
              st.remaining_size,
              st.entry_notional_usd,
              st.close_notional_usd,
              st.average_entry_price,
              st.average_exit_price,
              st.realized_pnl_usd,
              st.fee_usd,
              st.net_pnl_usd,
              st.entry_fill_count,
              st.close_fill_count,
              st.has_liquidation,
              st.liquidation_fill_count,
              st.liquidation_notional_usd
            from source_trades st
            left join watched_wallets ww on ww.address = st.wallet_address
            where (
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
                or st.wallet_address = cast(:wallet_address as text)
              )
              and (
                st.status = 'open'
                or st.closed_at_ms >= :window_start_ms
              )
            order by st.wallet_address, coalesce(st.closed_at_ms, st.opened_at_ms), st.trade_key
            """
        ),
        {
            "window_start_ms": window_start_ms,
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
        },
    )
    for row in result.mappings().all():
        record_materialized_trade(
            row,
            trades_by_wallet=trades_by_wallet,
            start_24h_ms=start_24h_ms,
            start_7d_ms=start_7d_ms,
        )
    return trades_by_wallet


async def apply_materialized_ignored_fill_counts(
    session: AsyncSession,
    *,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    window_start_ms: int,
    include_disabled: bool,
    wallet_address: str | None,
) -> None:
    result = await session.execute(
        text(
            """
            select
              sif.wallet_address,
              count(*) filter (where sif.reason = 'unmatched_close')
                as unmatched_close_fill_count,
              count(*) filter (where sif.reason = 'preexisting_open')
                as preexisting_open_fill_count
            from source_trade_ignored_fills sif
            left join watched_wallets ww on ww.address = sif.wallet_address
            where (
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
                or sif.wallet_address = cast(:wallet_address as text)
              )
              and sif.timestamp_ms >= :window_start_ms
            group by sif.wallet_address
            """
        ),
        {
            "window_start_ms": window_start_ms,
            "include_disabled": include_disabled,
            "wallet_address": wallet_address,
        },
    )
    for row in result.mappings().all():
        wallet_trades = get_wallet_trades(trades_by_wallet, str(row["wallet_address"]))
        wallet_trades.unmatched_close_fill_count = int(row["unmatched_close_fill_count"] or 0)
        wallet_trades.preexisting_open_fill_count = int(row["preexisting_open_fill_count"] or 0)


def record_materialized_trade(
    row: Mapping[str, Any],
    *,
    trades_by_wallet: dict[str, ReconstructedWalletTrades],
    start_24h_ms: int,
    start_7d_ms: int,
) -> None:
    wallet_trades = get_wallet_trades(trades_by_wallet, str(row["wallet_address"]))
    item = reconstructed_source_trade_from_row(row)
    if item.status == "open":
        wallet_trades.open_trade_count += 1
        wallet_trades.items.append(item)
        return

    closed_at_ms = int(item.closed_at_ms or item.opened_at_ms)
    net_pnl_usd = item.net_pnl_usd
    wallet_trades.closed_trade_count += 1
    wallet_trades.entry_fill_count += item.entry_fill_count
    wallet_trades.close_fill_count += item.close_fill_count
    wallet_trades.unique_coins.add(item.coin)
    wallet_trades.active_days.add(datetime.fromtimestamp(closed_at_ms / 1000, tz=UTC).date())
    wallet_trades.coin_notional_usd[item.coin] = (
        wallet_trades.coin_notional_usd.get(item.coin, ZERO) + item.entry_notional_usd
    )
    wallet_trades.total_entry_notional_usd += item.entry_notional_usd
    wallet_trades.total_close_notional_usd += item.close_notional_usd
    wallet_trades.realized_pnl_usd += item.realized_pnl_usd
    wallet_trades.fee_usd += item.fee_usd
    wallet_trades.net_pnl_usd += net_pnl_usd
    if net_pnl_usd > ZERO:
        wallet_trades.gross_profit_usd += net_pnl_usd
        wallet_trades.winning_trade_count += 1
        wallet_trades.winning_trade_pnls.append(net_pnl_usd)
    elif net_pnl_usd < ZERO:
        wallet_trades.gross_loss_usd += net_pnl_usd.copy_abs()
        wallet_trades.losing_trade_count += 1
    if item.has_liquidation:
        wallet_trades.liquidation_trade_count += 1
        wallet_trades.liquidation_close_fill_count += item.liquidation_fill_count
        wallet_trades.liquidation_notional_usd += item.liquidation_notional_usd

    wallet_trades._cumulative_pnl_usd += net_pnl_usd
    wallet_trades._peak_pnl_usd = max(
        wallet_trades._peak_pnl_usd,
        wallet_trades._cumulative_pnl_usd,
    )
    wallet_trades.max_drawdown_usd = max(
        wallet_trades.max_drawdown_usd,
        wallet_trades._peak_pnl_usd - wallet_trades._cumulative_pnl_usd,
    )

    if wallet_trades.first_trade_time_ms is None:
        wallet_trades.first_trade_time_ms = closed_at_ms
    wallet_trades.last_trade_time_ms = closed_at_ms

    if closed_at_ms >= start_24h_ms:
        wallet_trades.trades_24h += 1
        wallet_trades.notional_24h += item.entry_notional_usd
        wallet_trades.net_pnl_24h += net_pnl_usd
    if closed_at_ms >= start_7d_ms:
        wallet_trades.trades_7d += 1
        wallet_trades.notional_7d += item.entry_notional_usd
        wallet_trades.net_pnl_7d += net_pnl_usd

    wallet_trades.items.append(item)


def reconstructed_source_trade_from_row(row: Mapping[str, Any]) -> ReconstructedSourceTrade:
    return ReconstructedSourceTrade(
        id=str(row["trade_key"]),
        wallet_address=str(row["wallet_address"]),
        coin=str(row["coin"]),
        side=str(row["side"]),
        status=str(row["status"]),
        opened_at_ms=int(row["opened_at_ms"]),
        closed_at_ms=int(row["closed_at_ms"]) if row["closed_at_ms"] is not None else None,
        duration_ms=int(row["duration_ms"]) if row["duration_ms"] is not None else None,
        entry_size=decimal_value(row["entry_size"]),
        closed_size=decimal_value(row["closed_size"]),
        remaining_size=decimal_value(row["remaining_size"]),
        entry_notional_usd=decimal_value(row["entry_notional_usd"]),
        close_notional_usd=decimal_value(row["close_notional_usd"]),
        average_entry_price=decimal_or_none(row["average_entry_price"]),
        average_exit_price=decimal_or_none(row["average_exit_price"]),
        realized_pnl_usd=decimal_value(row["realized_pnl_usd"]),
        fee_usd=decimal_value(row["fee_usd"]),
        net_pnl_usd=decimal_value(row["net_pnl_usd"]),
        entry_fill_count=int(row["entry_fill_count"] or 0),
        close_fill_count=int(row["close_fill_count"] or 0),
        has_liquidation=bool(row["has_liquidation"]),
        liquidation_fill_count=int(row["liquidation_fill_count"] or 0),
        liquidation_notional_usd=decimal_value(row["liquidation_notional_usd"]),
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
        has_liquidation=trade.has_liquidation,
        liquidation_fill_count=trade.liquidation_fill_count,
        liquidation_notional_usd=trade.liquidation_notional_usd,
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
        external_fill_id=str(row["external_fill_id"]),
        coin=coin,
        timestamp_ms=int(row["timestamp_ms"]),
        size=size,
        notional_usd=decimal_value(row["notional_usd"]),
        fee_usd=decimal_value(row["fee_usd"]),
        pnl_usd=decimal_value(row["pnl_usd"]),
        start_position=start_position,
        is_liquidation=bool(row["is_liquidation"]),
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
    external_fill_id: str
    coin: str
    timestamp_ms: int
    size: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    pnl_usd: Decimal
    start_position: Decimal | None
    is_liquidation: bool


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
        ).record_ignored_fill(fill, reason="preexisting_open")
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
        wallet_trades.record_ignored_fill(fill, reason="unmatched_close")
        return ZERO

    closed_size = trade.add_close(
        size=fill.size,
        notional_usd=fill.notional_usd,
        pnl_usd=fill.pnl_usd,
        fee_usd=fill.fee_usd,
        is_liquidation=fill.is_liquidation,
    )
    if closed_size < fill.size:
        wallet_trades.record_ignored_fill(fill, reason="unmatched_close")

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
                external_fill_id=fill.external_fill_id,
                coin=fill.coin,
                timestamp_ms=fill.timestamp_ms,
                size=fill.size,
                notional_usd=fill.notional_usd,
                fee_usd=fill.fee_usd,
                pnl_usd=ZERO,
                start_position=ZERO,
                is_liquidation=False,
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
        external_fill_id=fill.external_fill_id,
        coin=fill.coin,
        timestamp_ms=fill.timestamp_ms,
        size=size,
        notional_usd=fill.notional_usd * ratio,
        fee_usd=fill.fee_usd * ratio,
        pnl_usd=fill.pnl_usd * ratio,
        start_position=fill.start_position,
        is_liquidation=fill.is_liquidation,
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
