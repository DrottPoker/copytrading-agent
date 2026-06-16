from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CopyTrade, WalletFill
from app.schemas.trade import CopyTradeListResponse
from app.schemas.wallet import normalize_wallet_address
from app.schemas.wallet_stats import WalletCoinStats, WalletStatsResponse, WalletWindowStats
from app.services.wallet_current_state_service import get_wallet_current_state

ZERO = Decimal("0")


async def get_wallet_stats(session: AsyncSession, *, address: str) -> WalletStatsResponse:
    normalized_address = normalize_wallet_address(address)
    base_filter = WalletFill.wallet_address == normalized_address
    now = datetime.now(UTC)

    aggregate_row = (
        await session.execute(
            select(
                func.count(WalletFill.id).label("fill_count"),
                func.count(func.distinct(WalletFill.coin)).label("unique_coin_count"),
                func.coalesce(func.sum(WalletFill.notional_usd), 0).label("total_notional_usd"),
                func.coalesce(func.sum(WalletFill.pnl_usd), 0).label("total_pnl_usd"),
                func.coalesce(func.sum(WalletFill.fee_usd), 0).label("total_fee_usd"),
                func.coalesce(
                    func.sum(case((WalletFill.is_snapshot.is_(True), 1), else_=0)), 0
                ).label("snapshot_fill_count"),
                func.coalesce(
                    func.sum(case((WalletFill.is_snapshot.is_(False), 1), else_=0)), 0
                ).label("realtime_fill_count"),
                func.coalesce(func.sum(case((WalletFill.side == "buy", 1), else_=0)), 0).label(
                    "buy_count"
                ),
                func.coalesce(func.sum(case((WalletFill.side == "sell", 1), else_=0)), 0).label(
                    "sell_count"
                ),
                func.coalesce(
                    func.sum(case((WalletFill.pnl_usd > 0, 1), else_=0)), 0
                ).label("profitable_fill_count"),
                func.coalesce(
                    func.sum(case((WalletFill.pnl_usd < 0, 1), else_=0)), 0
                ).label("losing_fill_count"),
                func.avg(WalletFill.ingest_latency_ms).label("average_ingest_latency_ms"),
                func.max(WalletFill.ingest_latency_ms).label("max_ingest_latency_ms"),
                func.min(WalletFill.timestamp_ms).label("first_fill_time_ms"),
                func.max(WalletFill.timestamp_ms).label("last_fill_time_ms"),
            ).where(base_filter)
        )
    ).one()

    fill_count = int(aggregate_row.fill_count or 0)
    profitable_fill_count = int(aggregate_row.profitable_fill_count or 0)
    losing_fill_count = int(aggregate_row.losing_fill_count or 0)
    resolved_fill_count = profitable_fill_count + losing_fill_count
    win_rate = (
        Decimal(profitable_fill_count) / Decimal(resolved_fill_count)
        if resolved_fill_count > 0
        else None
    )
    total_notional_usd = decimal_value(aggregate_row.total_notional_usd)

    windows = [
        await get_window_stats(
            session,
            address=normalized_address,
            label="24h",
            start_time_ms=timestamp_ms(now - timedelta(days=1)),
        ),
        await get_window_stats(
            session,
            address=normalized_address,
            label="7d",
            start_time_ms=timestamp_ms(now - timedelta(days=7)),
        ),
        await get_window_stats(
            session,
            address=normalized_address,
            label="30d",
            start_time_ms=timestamp_ms(now - timedelta(days=30)),
        ),
    ]

    return WalletStatsResponse(
        wallet_address=normalized_address,
        fill_count=fill_count,
        snapshot_fill_count=int(aggregate_row.snapshot_fill_count or 0),
        realtime_fill_count=int(aggregate_row.realtime_fill_count or 0),
        unique_coin_count=int(aggregate_row.unique_coin_count or 0),
        buy_count=int(aggregate_row.buy_count or 0),
        sell_count=int(aggregate_row.sell_count or 0),
        profitable_fill_count=profitable_fill_count,
        losing_fill_count=losing_fill_count,
        win_rate=win_rate,
        total_notional_usd=total_notional_usd,
        average_fill_notional_usd=(
            total_notional_usd / Decimal(fill_count) if fill_count > 0 else ZERO
        ),
        total_pnl_usd=decimal_value(aggregate_row.total_pnl_usd),
        total_fee_usd=decimal_value(aggregate_row.total_fee_usd),
        average_ingest_latency_ms=decimal_or_none(aggregate_row.average_ingest_latency_ms),
        max_ingest_latency_ms=(
            int(aggregate_row.max_ingest_latency_ms)
            if aggregate_row.max_ingest_latency_ms is not None
            else None
        ),
        first_fill_time_ms=aggregate_row.first_fill_time_ms,
        last_fill_time_ms=aggregate_row.last_fill_time_ms,
        windows=windows,
        top_coins=await get_top_coin_stats(session, address=normalized_address),
        current_state=await get_wallet_current_state(session, address=normalized_address),
    )


async def get_window_stats(
    session: AsyncSession,
    *,
    address: str,
    label: str,
    start_time_ms: int,
) -> WalletWindowStats:
    row = (
        await session.execute(
            select(
                func.count(WalletFill.id).label("fill_count"),
                func.coalesce(func.sum(WalletFill.notional_usd), 0).label("notional_usd"),
                func.coalesce(func.sum(WalletFill.pnl_usd), 0).label("pnl_usd"),
                func.coalesce(func.sum(WalletFill.fee_usd), 0).label("fee_usd"),
            ).where(
                WalletFill.wallet_address == address,
                WalletFill.timestamp_ms >= start_time_ms,
            )
        )
    ).one()
    return WalletWindowStats(
        label=label,
        fill_count=int(row.fill_count or 0),
        notional_usd=decimal_value(row.notional_usd),
        pnl_usd=decimal_value(row.pnl_usd),
        fee_usd=decimal_value(row.fee_usd),
    )


async def get_top_coin_stats(
    session: AsyncSession,
    *,
    address: str,
    limit: int = 8,
) -> list[WalletCoinStats]:
    rows = (
        await session.execute(
            select(
                WalletFill.coin.label("coin"),
                func.count(WalletFill.id).label("fill_count"),
                func.coalesce(func.sum(case((WalletFill.side == "buy", 1), else_=0)), 0).label(
                    "buy_count"
                ),
                func.coalesce(func.sum(case((WalletFill.side == "sell", 1), else_=0)), 0).label(
                    "sell_count"
                ),
                func.coalesce(func.sum(WalletFill.notional_usd), 0).label("notional_usd"),
                func.coalesce(func.sum(WalletFill.pnl_usd), 0).label("pnl_usd"),
                func.coalesce(func.sum(WalletFill.fee_usd), 0).label("fee_usd"),
                func.max(WalletFill.timestamp_ms).label("last_fill_time_ms"),
            )
            .where(WalletFill.wallet_address == address)
            .group_by(WalletFill.coin)
            .order_by(desc("notional_usd"))
            .limit(limit)
        )
    ).all()
    return [
        WalletCoinStats(
            coin=row.coin,
            fill_count=int(row.fill_count or 0),
            buy_count=int(row.buy_count or 0),
            sell_count=int(row.sell_count or 0),
            notional_usd=decimal_value(row.notional_usd),
            pnl_usd=decimal_value(row.pnl_usd),
            fee_usd=decimal_value(row.fee_usd),
            last_fill_time_ms=row.last_fill_time_ms,
        )
        for row in rows
    ]


async def list_wallet_copy_trades(
    session: AsyncSession,
    *,
    address: str,
    limit: int = 100,
    offset: int = 0,
) -> CopyTradeListResponse:
    normalized_address = normalize_wallet_address(address)
    filters = [CopyTrade.source_wallet == normalized_address]
    base_query: Select[tuple[CopyTrade]] = select(CopyTrade)
    count_query = select(func.count()).select_from(CopyTrade)
    for condition in filters:
        base_query = base_query.where(condition)
        count_query = count_query.where(condition)

    result = await session.execute(
        base_query.order_by(CopyTrade.created_at.desc()).limit(limit).offset(offset)
    )
    total = await session.scalar(count_query)
    return CopyTradeListResponse(
        items=list(result.scalars().all()),
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )


def timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def decimal_value(value: object) -> Decimal:
    if value is None:
        return ZERO
    return Decimal(str(value))


def decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
