import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import func, literal, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import (
    PaperCopyAllocation,
    PaperCopyFill,
    PaperPosition,
    PaperTradingAccount,
    WalletFill,
    WalletScore,
    WatchedWallet,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.paper_trading import (
    PaperTradingPolicyRead,
    PaperTradingSummaryResponse,
)
from app.services.wallet_current_state_service import object_or_empty

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
ONE = Decimal("1")
POSITION_EPSILON = Decimal("0.00000001")
BPS_DENOMINATOR = Decimal("10000")
PAPER_COPY_RECOVERY_OVERLAP_MS = 5 * 60 * 1000
SOURCE_EQUITY_ACTIONS = frozenset({"open", "flip_open"})
SOURCE_CLOSE_DIRECTIONS = frozenset(
    {"Close Long", "Close Short", "Long > Short", "Short > Long"}
)
RETRIABLE_EXIT_SKIP_REASONS = frozenset(
    {
        "source_account_state_missing",
        "source_account_state_fetch_failed",
        "source_account_margin_summary_missing",
        "source_perp_equity_missing",
        "source_perp_equity_zero",
        "execution_price_unavailable",
    }
)


class PaperPositionCloseError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperPositionNotFoundError(PaperPositionCloseError):
    status_code = 404


class PaperPositionCloseUnavailableError(PaperPositionCloseError):
    status_code = 409


@dataclass(frozen=True)
class PaperSourceAllocation:
    source_wallet: str
    rank: int
    pool_rank: int | None
    score: Decimal | None
    allocation_pct: Decimal
    active: bool
    has_realtime_slot: bool
    status_reason: str


@dataclass(frozen=True)
class PaperSourceAccountState:
    dex: str
    perp_equity: Decimal
    leverage_by_coin: dict[str, Decimal]
    positions_by_coin: dict[str, "PaperSourceCurrentPosition"]
    skip_reason: str | None = None


@dataclass(frozen=True)
class PaperSourceCurrentPosition:
    coin: str
    side: str
    size: Decimal


@dataclass(frozen=True)
class SourceFillPart:
    action: str
    side: str
    source_size: Decimal
    source_notional_usd: Decimal
    sequence_index: int
    close_ratio: Decimal | None = None
    start_position: Decimal | None = None


@dataclass(frozen=True)
class PaperExecutionContext:
    source_price: Decimal
    observed_price: Decimal
    execution_price: Decimal
    price_drift_bps: Decimal
    slippage_bps: Decimal
    latency_ms: int
    price_source: str


@dataclass(frozen=True)
class PaperCopyBatchResult:
    processed_fills: int = 0
    skipped_fills: int = 0
    accounts_updated: int = 0
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO


async def get_paper_trading_summary(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    recent_fill_limit: int = 100,
    closed_trade_limit: int = 100,
    client: HyperliquidClient | None = None,
) -> PaperTradingSummaryResponse:
    resolved_settings = settings or get_settings()
    source_allocations = await refresh_paper_copy_allocations(
        session,
        settings=resolved_settings,
    )
    await session.commit()

    accounts_result = await session.execute(
        select(PaperTradingAccount).order_by(PaperTradingAccount.key.asc())
    )
    open_position_sources = (
        select(PaperPosition.source_wallet)
        .where(PaperPosition.source_wallet != "")
        .distinct()
    )
    allocations_result = await session.execute(
        select(PaperCopyAllocation)
        .where(
            PaperCopyAllocation.active.is_(True)
            | PaperCopyAllocation.source_wallet.in_(open_position_sources)
            | PaperCopyAllocation.source_wallet.in_(list(source_allocations.keys()))
        )
        .order_by(
            PaperCopyAllocation.active.desc(),
            PaperCopyAllocation.account_key.asc(),
            PaperCopyAllocation.rank.asc(),
        )
    )
    positions_result = await session.execute(
        select(PaperPosition).order_by(
            PaperPosition.account_key.asc(),
            PaperPosition.source_wallet.asc(),
            PaperPosition.coin.asc(),
        )
    )
    fills_result = await session.execute(
        select(PaperCopyFill)
        .order_by(PaperCopyFill.filled_at.desc(), PaperCopyFill.created_at.desc())
        .limit(recent_fill_limit)
    )
    closed_trades_result = await session.execute(
        select(PaperCopyFill)
        .where(PaperCopyFill.action.in_(("close", "flip_close")))
        .order_by(PaperCopyFill.filled_at.desc(), PaperCopyFill.created_at.desc())
        .limit(closed_trade_limit)
    )
    fill_performance_result = await session.execute(
        select(
            PaperCopyFill.source_wallet,
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action != "skip")
            .label("copied_fill_count"),
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action == "skip")
            .label("skipped_fill_count"),
            func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd), ZERO).label(
                "realized_pnl_usd"
            ),
            func.coalesce(func.sum(PaperCopyFill.fee_usd), ZERO).label("fee_usd"),
            func.max(PaperCopyFill.filled_at).label("last_fill_at"),
        )
        .group_by(PaperCopyFill.source_wallet)
    )
    accounts = list(accounts_result.scalars().all())
    allocations = list(allocations_result.scalars().all())
    positions = list(positions_result.scalars().all())
    recent_fills = list(fills_result.scalars().all())
    closed_trades = list(closed_trades_result.scalars().all())
    fill_performance_rows = list(fill_performance_result.mappings().all())

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await get_paper_trading_summary(
                session,
                settings=resolved_settings,
                recent_fill_limit=recent_fill_limit,
                closed_trade_limit=closed_trade_limit,
                client=hyperliquid_client,
            )

    updated_at = datetime.now(UTC)
    market_prices = await load_open_position_market_prices(
        client=client,
        positions=positions,
    )
    position_rows = [
        paper_position_read(
            position,
            mark_price=resolve_coin_decimal(market_prices, position.coin),
            price_updated_at=updated_at if position.coin in market_prices else None,
        )
        for position in positions
    ]
    return PaperTradingSummaryResponse(
        policy=paper_trading_policy(resolved_settings),
        accounts=paper_account_reads(accounts=accounts, positions=position_rows),
        allocations=paper_allocation_reads(
            allocations=allocations,
            positions=position_rows,
            source_allocations=source_allocations,
        ),
        positions=position_rows,
        wallet_performance=paper_wallet_performance_reads(
            allocations=allocations,
            positions=position_rows,
            fill_performance_rows=fill_performance_rows,
            source_allocations=source_allocations,
        ),
        closed_trades=paper_closed_trade_reads(closed_trades),
        recent_fills=recent_fills,
        updated_at=updated_at,
        market_data_status=market_data_status(
            open_position_count=len(positions),
            priced_position_count=sum(
                1 for position in position_rows if position["mark_price"] is not None
            ),
        ),
    )


async def load_open_position_market_prices(
    *,
    client: HyperliquidClient,
    positions: list[PaperPosition],
) -> dict[str, Decimal]:
    coins = {position.coin for position in positions if position.coin}
    if not coins:
        return {}

    try:
        mids = await client.all_mids()
    except Exception as exc:
        logger.warning("paper summary allMids fetch failed error=%s", exc)
        mids = {}

    market_prices: dict[str, Decimal] = {}
    for coin in coins:
        price = resolve_coin_decimal(mids, coin)
        if price is not None and price > ZERO:
            market_prices[coin] = price

    missing_dexes = {
        dex_from_coin(coin)
        for coin in coins
        if coin not in market_prices and dex_from_coin(coin)
    }
    for dex in sorted(missing_dexes):
        dex_prices = await load_dex_market_prices(client=client, dex=dex)
        for coin in coins:
            if coin in market_prices or dex_from_coin(coin) != dex:
                continue
            price = resolve_coin_decimal(dex_prices, coin)
            if price is not None and price > ZERO:
                market_prices[coin] = price
    return market_prices


async def close_paper_position_manually(
    session: AsyncSession,
    *,
    position_id: UUID,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await close_paper_position_manually(
                session,
                position_id=position_id,
                settings=resolved_settings,
                client=hyperliquid_client,
            )

    position_snapshot = await session.scalar(
        select(PaperPosition).where(PaperPosition.id == position_id)
    )
    if position_snapshot is None:
        raise PaperPositionNotFoundError("Paper position was not found or is already closed.")

    market_prices = await load_open_position_market_prices(
        client=client,
        positions=[position_snapshot],
    )
    mark_price = resolve_coin_decimal(market_prices, position_snapshot.coin)
    if mark_price is None or mark_price <= ZERO:
        raise PaperPositionCloseUnavailableError("Execution price is unavailable.")

    position = await session.scalar(
        select(PaperPosition)
        .where(PaperPosition.id == position_id)
        .with_for_update()
    )
    if position is None:
        raise PaperPositionNotFoundError("Paper position was not found or is already closed.")

    account = await session.scalar(
        select(PaperTradingAccount)
        .where(PaperTradingAccount.key == position.account_key)
        .with_for_update()
    )
    if account is None:
        raise PaperPositionCloseUnavailableError("Paper account is unavailable.")

    allocation = await load_paper_allocation_for_position(session, position=position)
    source_fill_id = f"manual-close:{position.id}"
    if await paper_fill_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=0,
    ):
        raise PaperPositionCloseUnavailableError("Manual close has already been recorded.")

    leverage = safe_leverage(position.leverage)
    execution_price = apply_adverse_slippage(
        price=mark_price,
        side=position.side,
        action="close",
        slippage_bps=resolved_settings.paper_copy_slippage_bps,
    )
    if execution_price <= ZERO or position.size <= POSITION_EPSILON:
        raise PaperPositionCloseUnavailableError("Paper position cannot be closed safely.")

    close_size = position.size
    notional_usd = close_size * execution_price
    margin_usd = margin_from_notional(notional_usd, leverage)
    realized_pnl = realized_pnl_for_close(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=execution_price,
        size=close_size,
    )
    fee = notional_usd * resolved_settings.paper_copy_fee_rate
    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    filled_at = datetime.now(UTC)
    fill = manual_close_fill_payload(
        source_fill_id=source_fill_id,
        position=position,
        price=mark_price,
        timestamp_ms=timestamp_ms(filled_at),
    )
    part = SourceFillPart(
        action="close",
        side=position.side,
        source_size=close_size,
        source_notional_usd=notional_usd,
        sequence_index=0,
        close_ratio=ONE,
    )
    execution_context = PaperExecutionContext(
        source_price=mark_price,
        observed_price=mark_price,
        execution_price=execution_price,
        price_drift_bps=ZERO,
        slippage_bps=resolved_settings.paper_copy_slippage_bps,
        latency_ms=resolved_settings.paper_copy_latency_ms,
        price_source="manual_live_mid",
    )

    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    await session.delete(position)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action="close",
            price=execution_price,
            size=close_size,
            notional_usd=notional_usd,
            leverage=leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=realized_pnl,
            source_perp_equity=ZERO,
            allocation_usd=allocation_usd,
            settings=resolved_settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


async def load_paper_allocation_for_position(
    session: AsyncSession,
    *,
    position: PaperPosition,
) -> PaperSourceAllocation:
    allocation = await session.scalar(
        select(PaperCopyAllocation).where(
            PaperCopyAllocation.account_key == position.account_key,
            PaperCopyAllocation.source_wallet == position.source_wallet,
        )
    )
    if allocation is None:
        return PaperSourceAllocation(
            source_wallet=position.source_wallet,
            rank=0,
            pool_rank=None,
            score=None,
            allocation_pct=ZERO,
            active=False,
            has_realtime_slot=False,
            status_reason="allocation_missing",
        )
    return PaperSourceAllocation(
        source_wallet=allocation.source_wallet,
        rank=allocation.rank,
        pool_rank=allocation.rank,
        score=allocation.score,
        allocation_pct=allocation.allocation_pct,
        active=allocation.active,
        has_realtime_slot=allocation.active,
        status_reason="copy_candidate" if allocation.active else "existing_exposure_only",
    )


def manual_close_fill_payload(
    *,
    source_fill_id: str,
    position: PaperPosition,
    price: Decimal,
    timestamp_ms: int,
) -> dict[str, Any]:
    direction = "Close Long" if position.side == "long" else "Close Short"
    return {
        "externalFillId": source_fill_id,
        "coin": position.coin,
        "side": position.side,
        "price": str(price),
        "size": str(position.size),
        "notionalUsd": str(position.size * price),
        "feeUsd": "0",
        "pnlUsd": None,
        "timestampMs": timestamp_ms,
        "sourceTimestampMs": None,
        "ingestLatencyMs": None,
        "rawJson": {
            "dir": direction,
            "manual": True,
            "reason": "dashboard_manual_close",
        },
    }


def paper_position_read(
    position: PaperPosition,
    *,
    mark_price: Decimal | None,
    price_updated_at: datetime | None,
) -> dict[str, Any]:
    current_notional = (
        abs(position.size) * mark_price
        if mark_price is not None and mark_price > ZERO
        else None
    )
    unrealized_pnl = paper_unrealized_pnl(position=position, mark_price=mark_price)
    return {
        "id": position.id,
        "account_key": position.account_key,
        "source_wallet": position.source_wallet,
        "coin": position.coin,
        "side": position.side,
        "size": position.size,
        "entry_price": position.entry_price,
        "notional_usd": position.notional_usd,
        "leverage": position.leverage,
        "margin_usd": position.margin_usd,
        "realized_pnl_usd": position.realized_pnl_usd,
        "mark_price": mark_price,
        "current_notional_usd": current_notional,
        "unrealized_pnl_usd": unrealized_pnl,
        "unrealized_pnl_pct": (
            unrealized_pnl / position.margin_usd
            if unrealized_pnl is not None and position.margin_usd > ZERO
            else None
        ),
        "price_updated_at": price_updated_at,
        "fee_usd": position.fee_usd,
        "opened_at": position.opened_at,
        "created_at": position.created_at,
        "updated_at": position.updated_at,
    }


def paper_unrealized_pnl(
    *,
    position: PaperPosition,
    mark_price: Decimal | None,
) -> Decimal | None:
    if mark_price is None or mark_price <= ZERO or position.size <= ZERO:
        return None
    if position.side == "long":
        return (mark_price - position.entry_price) * position.size
    return (position.entry_price - mark_price) * position.size


def paper_account_reads(
    *,
    accounts: list[PaperTradingAccount],
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    positions_by_account: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        positions_by_account.setdefault(str(position["account_key"]), []).append(position)

    rows: list[dict[str, Any]] = []
    for account in accounts:
        account_positions = positions_by_account.get(account.key, [])
        unrealized_pnl = sum_decimal(
            position["unrealized_pnl_usd"] for position in account_positions
        )
        total_pnl = account.realized_pnl_usd + unrealized_pnl
        rows.append(
            {
                "key": account.key,
                "label": account.label,
                "starting_balance_usd": account.starting_balance_usd,
                "cash_balance_usd": account.cash_balance_usd,
                "equity_usd": account.equity_usd,
                "realized_pnl_usd": account.realized_pnl_usd,
                "unrealized_pnl_usd": unrealized_pnl,
                "total_pnl_usd": total_pnl,
                "total_pnl_pct": (
                    total_pnl / account.starting_balance_usd
                    if account.starting_balance_usd > ZERO
                    else None
                ),
                "open_position_count": len(account_positions),
                "open_notional_usd": sum_decimal(
                    position["current_notional_usd"] or position["notional_usd"]
                    for position in account_positions
                ),
                "open_margin_usd": sum_decimal(
                    position["margin_usd"] for position in account_positions
                ),
                "fee_usd": account.fee_usd,
                "enabled": account.enabled,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
            }
        )
    return rows


def paper_allocation_reads(
    *,
    allocations: list[PaperCopyAllocation],
    positions: list[dict[str, Any]],
    source_allocations: dict[str, PaperSourceAllocation],
) -> list[dict[str, Any]]:
    open_margin_by_allocation: dict[tuple[str, str], Decimal] = {}
    open_position_count_by_source: dict[str, int] = {}
    for position in positions:
        key = (
            str(position["account_key"]),
            str(position["source_wallet"]).lower(),
        )
        open_margin_by_allocation[key] = open_margin_by_allocation.get(key, ZERO) + decimal_or_zero(
            position["margin_usd"]
        )
        source = str(position["source_wallet"]).lower()
        open_position_count_by_source[source] = open_position_count_by_source.get(source, 0) + 1

    rows: list[dict[str, Any]] = []
    for allocation in allocations:
        source_wallet = allocation.source_wallet.lower()
        key = (allocation.account_key, source_wallet)
        open_margin = open_margin_by_allocation.get(key, ZERO)
        source_allocation = source_allocations.get(source_wallet)
        has_realtime_slot = (
            source_allocation.has_realtime_slot
            if source_allocation is not None
            else allocation.active
        )
        pool_rank = (
            source_allocation.pool_rank
            if source_allocation is not None
            else allocation.rank
        )
        source_status_reason = (
            source_allocation.status_reason
            if source_allocation is not None
            else "copy_candidate"
        )
        open_position_count = open_position_count_by_source.get(source_wallet, 0)
        remaining = max(allocation.allocation_usd - open_margin, ZERO)
        rows.append(
            {
                "id": allocation.id,
                "account_key": allocation.account_key,
                "source_wallet": allocation.source_wallet,
                "rank": allocation.rank,
                "pool_rank": pool_rank,
                "score": allocation.score,
                "allocation_pct": allocation.allocation_pct,
                "allocation_usd": allocation.allocation_usd,
                "open_margin_usd": open_margin,
                "remaining_allocation_usd": remaining,
                "pocket_used_pct": (
                    open_margin / allocation.allocation_usd
                    if allocation.allocation_usd > ZERO
                    else None
                ),
                "max_total_allocation_pct": allocation.max_total_allocation_pct,
                "active": allocation.active,
                "has_realtime_slot": has_realtime_slot,
                "can_open_new_positions": allocation.active,
                "monitor_status": "monitored" if has_realtime_slot else "waiting",
                "source_status": paper_source_status(
                    has_realtime_slot=has_realtime_slot,
                    can_open_new_positions=allocation.active,
                    open_position_count=open_position_count,
                ),
                "source_status_reason": source_status_reason,
                "updated_at": allocation.updated_at,
            }
        )
    return rows


def paper_source_status(
    *,
    has_realtime_slot: bool,
    can_open_new_positions: bool,
    open_position_count: int,
) -> str:
    if not has_realtime_slot:
        return "waiting_for_slot"
    if open_position_count > 0 and can_open_new_positions:
        return "trading"
    if open_position_count > 0:
        return "retained"
    return "waiting_for_trades"


def paper_wallet_performance_reads(
    *,
    allocations: list[PaperCopyAllocation],
    positions: list[dict[str, Any]],
    fill_performance_rows: list[dict[str, Any]],
    source_allocations: dict[str, PaperSourceAllocation],
) -> list[dict[str, Any]]:
    sources = {
        allocation.source_wallet.lower()
        for allocation in allocations
        if allocation.source_wallet
    }
    sources.update(
        str(position["source_wallet"]).lower()
        for position in positions
        if position["source_wallet"]
    )
    sources.update(
        str(row["source_wallet"]).lower()
        for row in fill_performance_rows
        if row["source_wallet"]
    )

    allocations_by_source: dict[str, list[PaperCopyAllocation]] = {}
    for allocation in allocations:
        allocations_by_source.setdefault(allocation.source_wallet.lower(), []).append(allocation)
    positions_by_source: dict[str, list[dict[str, Any]]] = {}
    for position in positions:
        positions_by_source.setdefault(str(position["source_wallet"]).lower(), []).append(position)
    fills_by_source = {
        str(row["source_wallet"]).lower(): row
        for row in fill_performance_rows
        if row["source_wallet"]
    }

    rows: list[dict[str, Any]] = []
    for source in sources:
        allocation_rows = allocations_by_source.get(source, [])
        source_positions = positions_by_source.get(source, [])
        fill_row = fills_by_source.get(source, {})
        source_allocation = source_allocations.get(source)
        unrealized_pnl = sum_decimal(
            position["unrealized_pnl_usd"] for position in source_positions
        )
        realized_pnl = decimal_or_zero(fill_row.get("realized_pnl_usd"))
        allocation_pct = first_decimal(
            allocation.allocation_pct for allocation in allocation_rows
        )
        rows.append(
            {
                "source_wallet": source,
                "rank": min((allocation.rank for allocation in allocation_rows), default=None),
                "pool_rank": (
                    source_allocation.pool_rank
                    if source_allocation is not None
                    else min((allocation.rank for allocation in allocation_rows), default=None)
                ),
                "score": first_decimal(allocation.score for allocation in allocation_rows),
                "allocation_pct": allocation_pct,
                "active": any(allocation.active for allocation in allocation_rows),
                "account_count": len({allocation.account_key for allocation in allocation_rows}),
                "open_position_count": len(source_positions),
                "copied_fill_count": int(fill_row.get("copied_fill_count") or 0),
                "skipped_fill_count": int(fill_row.get("skipped_fill_count") or 0),
                "realized_pnl_usd": realized_pnl,
                "unrealized_pnl_usd": unrealized_pnl,
                "total_pnl_usd": realized_pnl + unrealized_pnl,
                "fee_usd": decimal_or_zero(fill_row.get("fee_usd")),
                "open_notional_usd": sum_decimal(
                    position["current_notional_usd"] or position["notional_usd"]
                    for position in source_positions
                ),
                "open_margin_usd": sum_decimal(
                    position["margin_usd"] for position in source_positions
                ),
                "last_fill_at": fill_row.get("last_fill_at"),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -row["open_position_count"],
            row["pool_rank"] or 9999,
            -row["total_pnl_usd"],
        ),
    )


def paper_closed_trade_reads(fills: list[PaperCopyFill]) -> list[dict[str, Any]]:
    return [
        {
            "id": fill.id,
            "account_key": fill.account_key,
            "source_wallet": fill.source_wallet,
            "source_fill_id": fill.source_fill_id,
            "coin": fill.coin,
            "close_type": fill.action,
            "side": fill.side,
            "exit_price": fill.price,
            "size": fill.size,
            "notional_usd": fill.notional_usd,
            "leverage": fill.leverage,
            "margin_usd": fill.margin_usd,
            "fee_usd": fill.fee_usd,
            "realized_pnl_usd": fill.realized_pnl_usd,
            "net_pnl_usd": fill.realized_pnl_usd - fill.fee_usd,
            "closed_at": fill.filled_at,
            "created_at": fill.created_at,
        }
        for fill in fills
    ]


def first_decimal(values: Any) -> Decimal | None:
    for value in values:
        parsed = decimal_or_none(value)
        if parsed is not None:
            return parsed
    return None


def market_data_status(
    *,
    open_position_count: int,
    priced_position_count: int,
) -> str:
    if open_position_count == 0:
        return "no_open_positions"
    if priced_position_count == open_position_count:
        return "live"
    if priced_position_count > 0:
        return "partial"
    return "unavailable"


async def process_paper_copy_fills(
    session: AsyncSession,
    *,
    source_wallet: str,
    fills: list[dict[str, Any]],
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.paper_trading_enabled
        or not resolved_settings.paper_copy_enabled
        or not fills
    ):
        return PaperCopyBatchResult()

    allocations = await refresh_paper_copy_allocations(session, settings=resolved_settings)
    allocation = allocations.get(source_wallet.lower())
    if allocation is None:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    accounts = await load_enabled_paper_accounts(session)
    if not accounts:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_paper_copy_fills(
                session,
                source_wallet=source_wallet,
                fills=fills,
                settings=resolved_settings,
                client=hyperliquid_client,
            )

    source_account_states = await load_source_account_states(
        client=client,
        source_wallet=source_wallet,
        fills=fills,
    )

    market_prices = await load_execution_market_prices(
        client=client,
        fills=fills,
        settings=resolved_settings,
    )

    processed = 0
    skipped = 0
    realized_pnl = ZERO
    fee_usd = ZERO
    accounts_updated: set[str] = set()

    for fill in sorted_paper_source_fills(fills):
        parts = plan_source_fill(fill)
        if not parts:
            skip_result = await record_skip_for_accounts(
                session,
                accounts=accounts,
                allocation=allocation,
                fill=fill,
                reason="unsupported_source_fill_direction",
                settings=resolved_settings,
            )
            skipped += skip_result.skipped_fills
            continue

        source_account_state = source_account_states.get(dex_from_coin(fill.get("coin")))
        if source_account_state is None:
            source_perp_equity = ZERO
            source_leverages: dict[str, Decimal] = {}
            source_state_skip_reason = "source_account_state_missing"
        else:
            source_perp_equity = source_account_state.perp_equity
            source_leverages = source_account_state.leverage_by_coin
            source_state_skip_reason = source_account_state.skip_reason

        for account in accounts:
            for part in parts:
                if source_state_skip_reason is not None and part_requires_source_equity(part):
                    fill_result = await record_skip(
                        session,
                        account=account,
                        allocation=allocation,
                        fill=fill,
                        part=part,
                        source_perp_equity=source_perp_equity,
                        reason=source_state_skip_reason,
                        settings=resolved_settings,
                    )
                    skipped += fill_result.skipped_fills
                    continue

                fill_result = await apply_paper_fill_part(
                    session,
                    account=account,
                    allocation=allocation,
                    fill=fill,
                    part=part,
                    source_perp_equity=source_perp_equity,
                    source_leverages=source_leverages,
                    market_prices=market_prices,
                    settings=resolved_settings,
                )
                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
                realized_pnl += fill_result.realized_pnl_usd
                fee_usd += fill_result.fee_usd
                if fill_result.accounts_updated > 0:
                    accounts_updated.add(account.key)

    await session.commit()
    return PaperCopyBatchResult(
        processed_fills=processed,
        skipped_fills=skipped,
        accounts_updated=len(accounts_updated),
        realized_pnl_usd=realized_pnl,
        fee_usd=fee_usd,
    )


async def process_paper_copy_recovery(
    session: AsyncSession,
    *,
    source_wallet: str | None = None,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
    max_sources: int = 100,
    fill_limit_per_source: int = 1000,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.paper_trading_enabled
        or not resolved_settings.paper_copy_enabled
    ):
        return PaperCopyBatchResult()

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_paper_copy_recovery(
                session,
                source_wallet=source_wallet,
                settings=resolved_settings,
                client=hyperliquid_client,
                max_sources=max_sources,
                fill_limit_per_source=fill_limit_per_source,
            )

    if source_wallet:
        source_wallets = [source_wallet.lower()]
    else:
        source_wallets = await load_paper_copy_recovery_sources(
            session,
            max_sources=max_sources,
        )

    total = PaperCopyBatchResult()
    for wallet in source_wallets:
        result = await process_paper_copy_recovery_for_source(
            session,
            source_wallet=wallet,
            settings=resolved_settings,
            client=client,
            fill_limit=fill_limit_per_source,
        )
        total = combine_batch_results(total, result)
    return total


async def load_paper_copy_recovery_sources(
    session: AsyncSession,
    *,
    max_sources: int,
) -> list[str]:
    position_result = await session.execute(
        select(PaperPosition.source_wallet)
        .where(PaperPosition.source_wallet != "")
        .distinct()
        .limit(max_sources)
    )
    sources = [
        str(source).lower()
        for source in position_result.scalars().all()
        if source
    ]
    remaining = max(max_sources - len(sources), 0)
    if remaining <= 0:
        return unique_strings(sources)

    fill_result = await session.execute(
        select(PaperCopyFill.source_wallet)
        .where(PaperCopyFill.source_wallet != "")
        .distinct()
        .limit(remaining)
    )
    sources.extend(
        str(source).lower()
        for source in fill_result.scalars().all()
        if source
    )
    return unique_strings(sources)


async def process_paper_copy_recovery_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
    settings: Settings,
    client: HyperliquidClient,
    fill_limit: int,
) -> PaperCopyBatchResult:
    start_time_ms = await paper_copy_recovery_start_time_ms(
        session,
        source_wallet=source_wallet,
    )
    if start_time_ms is None:
        return PaperCopyBatchResult()

    fills = await load_wallet_fills_for_paper_copy_recovery(
        session,
        source_wallet=source_wallet,
        start_time_ms=start_time_ms,
        limit=fill_limit,
    )
    replay_result = PaperCopyBatchResult()
    if fills:
        replay_result = await process_paper_copy_fills(
            session,
            source_wallet=source_wallet,
            fills=fills,
            settings=settings,
            client=client,
        )
    reconcile_result = await reconcile_open_paper_positions_for_source(
        session,
        source_wallet=source_wallet,
        settings=settings,
        client=client,
    )
    return combine_batch_results(replay_result, reconcile_result)


async def paper_copy_recovery_start_time_ms(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> int | None:
    latest_processed_ms = await session.scalar(
        select(func.max(PaperCopyFill.filled_at)).where(
            PaperCopyFill.source_wallet == source_wallet
        )
    )
    earliest_opened_at = await session.scalar(
        select(func.min(PaperPosition.opened_at)).where(
            PaperPosition.source_wallet == source_wallet
        )
    )
    if latest_processed_ms is None and earliest_opened_at is None:
        return None

    if earliest_opened_at is not None:
        anchor = earliest_opened_at
    else:
        anchor = latest_processed_ms
    if anchor is None:
        return None
    return max(0, int(anchor.timestamp() * 1000) - PAPER_COPY_RECOVERY_OVERLAP_MS)


async def load_wallet_fills_for_paper_copy_recovery(
    session: AsyncSession,
    *,
    source_wallet: str,
    start_time_ms: int,
    limit: int,
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(WalletFill)
        .where(
            WalletFill.wallet_address == source_wallet,
            WalletFill.timestamp_ms >= start_time_ms,
        )
        .order_by(WalletFill.timestamp_ms.asc(), WalletFill.external_fill_id.asc())
        .limit(limit)
    )
    return [
        paper_source_fill_from_wallet_fill(fill)
        for fill in result.scalars().all()
    ]


def paper_source_fill_from_wallet_fill(fill: WalletFill) -> dict[str, Any]:
    return {
        "id": str(fill.id),
        "externalFillId": fill.external_fill_id,
        "coin": fill.coin,
        "side": fill.side,
        "price": str(fill.price),
        "size": str(fill.size),
        "notionalUsd": str(fill.notional_usd) if fill.notional_usd is not None else None,
        "feeUsd": str(fill.fee_usd) if fill.fee_usd is not None else None,
        "pnlUsd": str(fill.pnl_usd) if fill.pnl_usd is not None else None,
        "timestampMs": fill.timestamp_ms,
        "sourceTimestampMs": fill.source_timestamp_ms,
        "ingestLatencyMs": fill.ingest_latency_ms,
        "rawJson": fill.raw_json,
    }


async def reconcile_open_paper_positions_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
    settings: Settings,
    client: HyperliquidClient,
) -> PaperCopyBatchResult:
    positions = await load_open_paper_positions_for_source(
        session,
        source_wallet=source_wallet,
    )
    if not positions:
        return PaperCopyBatchResult()

    await refresh_paper_copy_allocations(session, settings=settings)
    accounts = {
        account.key: account
        for account in await load_enabled_paper_accounts(session)
    }
    allocations = await load_paper_copy_allocations_for_source(
        session,
        source_wallet=source_wallet,
    )
    market_prices = await load_open_position_market_prices(client=client, positions=positions)
    source_states = await load_source_account_states_for_positions(
        client=client,
        source_wallet=source_wallet,
        positions=positions,
    )

    result = PaperCopyBatchResult()
    updated_accounts: set[str] = set()
    for position in positions:
        source_state = source_states.get(dex_from_coin(position.coin))
        if not source_state_available_for_reconciliation(source_state):
            continue
        source_position = resolve_source_current_position(
            source_state.positions_by_coin,
            position.coin,
        )
        if source_position is not None and source_position.side == position.side:
            continue

        account = accounts.get(position.account_key)
        allocation = allocations.get(position.account_key)
        mark_price = market_prices.get(position.coin)
        if account is None or allocation is None or mark_price is None or mark_price <= ZERO:
            continue

        close_result = await reconcile_closed_source_position(
            session,
            account=account,
            allocation=allocation,
            position=position,
            mark_price=mark_price,
            source_perp_equity=source_state.perp_equity,
            settings=settings,
        )
        result = combine_batch_results(result, close_result)
        if close_result.accounts_updated > 0:
            updated_accounts.add(account.key)

    if result.processed_fills > 0:
        await session.commit()
    return PaperCopyBatchResult(
        processed_fills=result.processed_fills,
        skipped_fills=result.skipped_fills,
        accounts_updated=len(updated_accounts),
        realized_pnl_usd=result.realized_pnl_usd,
        fee_usd=result.fee_usd,
    )


def source_state_available_for_reconciliation(
    source_state: PaperSourceAccountState | None,
) -> bool:
    if source_state is None:
        return False
    return source_state.skip_reason not in {
        "source_account_state_fetch_failed",
        "source_account_margin_summary_missing",
    }


def resolve_source_current_position(
    positions_by_coin: dict[str, PaperSourceCurrentPosition],
    coin: str,
) -> PaperSourceCurrentPosition | None:
    candidates = coin_symbol_candidates(coin)
    for candidate in candidates:
        position = positions_by_coin.get(candidate)
        if position is not None:
            return position

    casefold_index = {
        key.casefold(): position
        for key, position in positions_by_coin.items()
        if key.strip()
    }
    for candidate in candidates:
        position = casefold_index.get(candidate.casefold())
        if position is not None:
            return position

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, position in positions_by_coin.items():
        if normalize_coin_symbol(key) in normalized_candidates:
            return position
    return None


async def load_open_paper_positions_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> list[PaperPosition]:
    result = await session.execute(
        select(PaperPosition)
        .where(PaperPosition.source_wallet == source_wallet)
        .order_by(PaperPosition.opened_at.asc(), PaperPosition.account_key.asc())
    )
    return list(result.scalars().all())


async def load_paper_copy_allocations_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> dict[str, PaperSourceAllocation]:
    result = await session.execute(
        select(PaperCopyAllocation).where(PaperCopyAllocation.source_wallet == source_wallet)
    )
    rows: dict[str, PaperSourceAllocation] = {}
    for allocation in result.scalars().all():
        rows[allocation.account_key] = PaperSourceAllocation(
            source_wallet=allocation.source_wallet,
            rank=allocation.rank,
            pool_rank=allocation.rank,
            score=allocation.score,
            allocation_pct=allocation.allocation_pct,
            active=allocation.active,
            has_realtime_slot=allocation.active,
            status_reason="copy_candidate" if allocation.active else "existing_exposure_only",
        )
    return rows


async def load_source_account_states_for_positions(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    positions: list[PaperPosition],
) -> dict[str, PaperSourceAccountState]:
    states: dict[str, PaperSourceAccountState] = {}
    for dex in sorted({dex_from_coin(position.coin) for position in positions}):
        states[dex] = await load_source_account_state(
            client=client,
            source_wallet=source_wallet,
            dex=dex,
        )
    return states


async def reconcile_closed_source_position(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    position: PaperPosition,
    mark_price: Decimal,
    source_perp_equity: Decimal,
    settings: Settings,
) -> PaperCopyBatchResult:
    leverage = safe_leverage(position.leverage)
    execution_price = apply_adverse_slippage(
        price=mark_price,
        side=position.side,
        action="close",
        slippage_bps=settings.paper_copy_slippage_bps,
    )
    if execution_price <= ZERO or position.size <= POSITION_EPSILON:
        return PaperCopyBatchResult()

    source_fill_id = f"reconcile-close:{position.id}"
    if await paper_fill_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=0,
    ):
        return PaperCopyBatchResult()

    close_size = position.size
    notional_usd = close_size * execution_price
    margin_usd = margin_from_notional(notional_usd, leverage)
    realized_pnl = realized_pnl_for_close(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=execution_price,
        size=close_size,
    )
    fee = notional_usd * settings.paper_copy_fee_rate
    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    filled_at = datetime.now(UTC)
    fill = reconciled_close_fill_payload(
        source_fill_id=source_fill_id,
        position=position,
        price=mark_price,
        timestamp_ms=timestamp_ms(filled_at),
    )
    part = SourceFillPart(
        action="close",
        side=position.side,
        source_size=ZERO,
        source_notional_usd=ZERO,
        sequence_index=0,
        close_ratio=ONE,
    )
    execution_context = PaperExecutionContext(
        source_price=mark_price,
        observed_price=mark_price,
        execution_price=execution_price,
        price_drift_bps=ZERO,
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        price_source="reconciled_live_mid",
    )

    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    await session.delete(position)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action="close",
            price=execution_price,
            size=close_size,
            notional_usd=notional_usd,
            leverage=leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=realized_pnl,
            source_perp_equity=source_perp_equity,
            allocation_usd=allocation_usd,
            settings=settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


def reconciled_close_fill_payload(
    *,
    source_fill_id: str,
    position: PaperPosition,
    price: Decimal,
    timestamp_ms: int,
) -> dict[str, Any]:
    direction = "Close Long" if position.side == "long" else "Close Short"
    return {
        "externalFillId": source_fill_id,
        "coin": position.coin,
        "side": position.side,
        "price": str(price),
        "size": str(position.size),
        "notionalUsd": str(position.size * price),
        "feeUsd": "0",
        "pnlUsd": None,
        "timestampMs": timestamp_ms,
        "sourceTimestampMs": None,
        "ingestLatencyMs": None,
        "rawJson": {
            "dir": direction,
            "reconciled": True,
            "reason": "source_position_absent",
        },
    }


def combine_batch_results(
    left: PaperCopyBatchResult,
    right: PaperCopyBatchResult,
) -> PaperCopyBatchResult:
    return PaperCopyBatchResult(
        processed_fills=left.processed_fills + right.processed_fills,
        skipped_fills=left.skipped_fills + right.skipped_fills,
        accounts_updated=left.accounts_updated + right.accounts_updated,
        realized_pnl_usd=left.realized_pnl_usd + right.realized_pnl_usd,
        fee_usd=left.fee_usd + right.fee_usd,
    )


async def refresh_paper_copy_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
) -> dict[str, PaperSourceAllocation]:
    await ensure_open_paper_sources_watched(session)
    accounts = await sync_paper_trading_accounts(session, settings=settings)
    account_keys = [account.key for account in accounts]
    if account_keys:
        await session.execute(
            update(PaperCopyAllocation)
            .where(PaperCopyAllocation.account_key.in_(account_keys))
            .values(active=False)
        )

    source_allocations = await load_paper_source_allocations(session, settings=settings)
    for account in accounts:
        for allocation in source_allocations:
            source_wallet = allocation.source_wallet.lower()
            allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
            stmt = insert(PaperCopyAllocation).values(
                account_key=account.key,
                source_wallet=source_wallet,
                rank=allocation.rank,
                score=allocation.score,
                allocation_pct=allocation.allocation_pct,
                allocation_usd=allocation_usd,
                max_total_allocation_pct=settings.paper_copy_max_total_allocation_pct,
                active=account.enabled and allocation.active,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    constraint="ux_paper_copy_allocations_account_source",
                    set_={
                        "rank": stmt.excluded.rank,
                        "score": stmt.excluded.score,
                        "allocation_pct": stmt.excluded.allocation_pct,
                        "allocation_usd": stmt.excluded.allocation_usd,
                        "max_total_allocation_pct": stmt.excluded.max_total_allocation_pct,
                        "active": stmt.excluded.active,
                    },
                )
            )

    return {allocation.source_wallet.lower(): allocation for allocation in source_allocations}


async def ensure_open_paper_sources_watched(session: AsyncSession) -> None:
    open_source_query = (
        select(PaperPosition.source_wallet)
        .where(PaperPosition.source_wallet != "")
        .distinct()
    )
    insert_stmt = insert(WatchedWallet).from_select(
        [
            "address",
            "enabled",
            "eligible",
            "copy_enabled",
            "polling_tier",
            "notes",
        ],
        select(
            PaperPosition.source_wallet,
            literal(True),
            literal(False),
            literal(False),
            literal("exit_only"),
            literal("Restored automatically because paper positions are still open."),
        )
        .where(PaperPosition.source_wallet != "")
        .where(
            ~select(WatchedWallet.id)
            .where(WatchedWallet.address == PaperPosition.source_wallet)
            .exists()
        )
        .distinct(),
    )
    await session.execute(insert_stmt.on_conflict_do_nothing(index_elements=["address"]))
    await session.execute(
        update(WatchedWallet)
        .where(
            WatchedWallet.address.in_(open_source_query),
            WatchedWallet.polling_tier.not_in(("active", "exit_only")),
        )
        .values(enabled=True, polling_tier="exit_only")
    )


async def sync_paper_trading_accounts(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[PaperTradingAccount]:
    for account_config in settings.paper_copy_accounts:
        config_payload = account_config.model_dump(mode="json")
        stmt = insert(PaperTradingAccount).values(
            key=account_config.key,
            label=account_config.label,
            starting_balance_usd=account_config.starting_balance_usd,
            cash_balance_usd=account_config.starting_balance_usd,
            equity_usd=account_config.starting_balance_usd,
            enabled=account_config.enabled,
            config_payload=config_payload,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "label": stmt.excluded.label,
                    "enabled": stmt.excluded.enabled,
                    "config_payload": stmt.excluded.config_payload,
                },
            )
        )

    result = await session.execute(
        select(PaperTradingAccount).where(
            PaperTradingAccount.key.in_([account.key for account in settings.paper_copy_accounts])
        )
    )
    return list(result.scalars().all())


async def load_paper_source_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[PaperSourceAllocation]:
    ranked_pool = (
        select(
            WatchedWallet.address.label("address"),
            WalletScore.score.label("score"),
            WalletScore.current_drawdown_status.label("current_drawdown_status"),
            func.row_number()
            .over(
                order_by=(
                    WalletScore.score.desc(),
                    WalletScore.updated_at.desc(),
                    WatchedWallet.address.asc(),
                )
            )
            .label("pool_rank"),
        )
        .join(WalletScore, WalletScore.wallet_address == WatchedWallet.address)
        .where(
            WatchedWallet.enabled.is_(True),
            WatchedWallet.polling_tier != "cooldown",
            WalletScore.score > ZERO,
        )
        .cte("paper_ranked_pool")
    )
    candidate_filters = []
    if settings.scoring_current_drawdown_enabled:
        candidate_filters.append(ranked_pool.c.current_drawdown_status == "ok")
    candidate_result = await session.execute(
        select(
            ranked_pool.c.address,
            ranked_pool.c.score,
            ranked_pool.c.pool_rank,
        )
        .where(*candidate_filters)
        .order_by(ranked_pool.c.pool_rank.asc())
        .limit(settings.paper_copy_top_wallet_count)
    )
    candidate_rows = list(candidate_result.mappings().all())
    open_source_result = await session.execute(
        select(
            PaperPosition.source_wallet,
            WalletScore.score.label("score"),
            WalletScore.current_drawdown_status.label("current_drawdown_status"),
            WatchedWallet.enabled.label("wallet_enabled"),
            WatchedWallet.polling_tier.label("polling_tier"),
            ranked_pool.c.pool_rank.label("pool_rank"),
            func.max(PaperCopyAllocation.allocation_pct).label("allocation_pct"),
        )
        .outerjoin(WatchedWallet, WatchedWallet.address == PaperPosition.source_wallet)
        .outerjoin(WalletScore, WalletScore.wallet_address == PaperPosition.source_wallet)
        .outerjoin(ranked_pool, ranked_pool.c.address == PaperPosition.source_wallet)
        .outerjoin(
            PaperCopyAllocation,
            PaperCopyAllocation.source_wallet == PaperPosition.source_wallet,
        )
        .where(PaperPosition.source_wallet != "")
        .group_by(
            PaperPosition.source_wallet,
            WalletScore.score,
            WalletScore.current_drawdown_status,
            WatchedWallet.enabled,
            WatchedWallet.polling_tier,
            ranked_pool.c.pool_rank,
        )
        .order_by(
            ranked_pool.c.pool_rank.asc().nulls_last(),
            WalletScore.score.desc().nulls_last(),
            PaperPosition.source_wallet.asc(),
        )
    )
    open_source_rows = list(open_source_result.mappings().all())
    max_realtime_slots = max(settings.max_realtime_wallets, 0)
    slot_sources: list[str] = []
    for row in open_source_rows:
        if len(slot_sources) >= max_realtime_slots:
            break
        source_wallet = str(row["source_wallet"]).lower()
        if source_wallet and source_wallet not in slot_sources:
            slot_sources.append(source_wallet)

    for row in candidate_rows:
        if len(slot_sources) >= max_realtime_slots:
            break
        source_wallet = str(row["address"]).lower()
        if source_wallet and source_wallet not in slot_sources:
            slot_sources.append(source_wallet)

    slot_source_set = set(slot_sources)
    allocations: list[PaperSourceAllocation] = []
    for copy_rank, row in enumerate(candidate_rows, start=1):
        source_wallet = str(row["address"]).lower()
        allocation_pct = (
            settings.paper_copy_top_tier_allocation_pct
            if copy_rank <= settings.paper_copy_top_tier_wallet_count
            else settings.paper_copy_standard_allocation_pct
        )
        pool_rank = int(row["pool_rank"])
        has_realtime_slot = source_wallet in slot_source_set
        allocations.append(
            PaperSourceAllocation(
                source_wallet=source_wallet,
                rank=pool_rank,
                pool_rank=pool_rank,
                score=row["score"],
                allocation_pct=allocation_pct,
                active=has_realtime_slot,
                has_realtime_slot=has_realtime_slot,
                status_reason=(
                    "copy_candidate"
                    if has_realtime_slot
                    else "waiting_for_realtime_slot"
                ),
            )
        )
    allocation_sources = {allocation.source_wallet for allocation in allocations}
    retained_rank = settings.paper_copy_top_wallet_count + 1
    for row in open_source_rows:
        source_wallet = str(row["source_wallet"]).lower()
        if not source_wallet or source_wallet in allocation_sources:
            continue
        allocation_pct = decimal_or_none(row["allocation_pct"])
        pool_rank = int_or_none(row["pool_rank"])
        has_realtime_slot = source_wallet in slot_source_set
        allocations.append(
            PaperSourceAllocation(
                source_wallet=source_wallet,
                rank=pool_rank or retained_rank,
                pool_rank=pool_rank,
                score=row["score"],
                allocation_pct=(
                    allocation_pct
                    if allocation_pct is not None and allocation_pct > ZERO
                    else settings.paper_copy_standard_allocation_pct
                ),
                active=False,
                has_realtime_slot=has_realtime_slot,
                status_reason=paper_retained_status_reason(
                    row,
                    settings=settings,
                    has_realtime_slot=has_realtime_slot,
                ),
            )
        )
        retained_rank += 1
        allocation_sources.add(source_wallet)
    return allocations


def paper_retained_status_reason(
    row: Any,
    *,
    settings: Settings,
    has_realtime_slot: bool,
) -> str:
    score = decimal_or_none(row["score"])
    pool_rank = int_or_none(row["pool_rank"])
    if row["wallet_enabled"] is not True:
        return "wallet_disabled_or_missing"
    if row["polling_tier"] == "cooldown":
        return "wallet_cooldown"
    if score is None:
        return "score_unavailable"
    if score <= ZERO:
        return "score_not_positive"
    if (
        settings.scoring_current_drawdown_enabled
        and row["current_drawdown_status"] != "ok"
    ):
        return "current_drawdown_blocked"
    if pool_rank is not None and pool_rank > settings.paper_copy_top_wallet_count:
        return "outside_copy_top_wallet_count"
    if not has_realtime_slot:
        return "waiting_for_realtime_slot"
    return "existing_exposure_only"


async def load_enabled_paper_accounts(session: AsyncSession) -> list[PaperTradingAccount]:
    result = await session.execute(
        select(PaperTradingAccount)
        .where(PaperTradingAccount.enabled.is_(True))
        .order_by(PaperTradingAccount.key.asc())
    )
    return list(result.scalars().all())


async def load_source_account_states(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    fills: list[dict[str, Any]],
) -> dict[str, PaperSourceAccountState]:
    dexes = {dex_from_coin(fill.get("coin")) for fill in fills}
    if not dexes:
        dexes.add("")

    states: dict[str, PaperSourceAccountState] = {}
    for dex in sorted(dexes):
        states[dex] = await load_source_account_state(
            client=client,
            source_wallet=source_wallet,
            dex=dex,
        )
    return states


async def load_source_account_state(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    dex: str,
) -> PaperSourceAccountState:
    try:
        clearinghouse_state = await client.clearinghouse_state(
            user=source_wallet,
            dex=dex or None,
        )
    except Exception as exc:
        logger.warning(
            "paper copy source account fetch failed wallet=%s dex=%s error=%s",
            source_wallet,
            dex or "default",
            exc,
        )
        return PaperSourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin={},
            positions_by_coin={},
            skip_reason="source_account_state_fetch_failed",
        )

    leverage_by_coin = parse_source_leverages(clearinghouse_state)
    positions_by_coin = parse_source_current_positions(clearinghouse_state)
    margin_summary_raw = clearinghouse_state.get("marginSummary")
    if not isinstance(margin_summary_raw, dict):
        return PaperSourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            skip_reason="source_account_margin_summary_missing",
        )

    perp_equity = decimal_or_none(margin_summary_raw.get("accountValue"))
    if perp_equity is None:
        return PaperSourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            skip_reason="source_perp_equity_missing",
        )
    if perp_equity <= ZERO:
        return PaperSourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            skip_reason="source_perp_equity_zero",
        )

    return PaperSourceAccountState(
        dex=dex,
        perp_equity=perp_equity,
        leverage_by_coin=leverage_by_coin,
        positions_by_coin=positions_by_coin,
    )


def parse_source_leverages(payload: dict[str, Any]) -> dict[str, Decimal]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    leverages: dict[str, Decimal] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        leverage = object_or_empty(position.get("leverage"))
        leverage_value = decimal_or_none(leverage.get("value"))
        if coin and leverage_value is not None and leverage_value > ZERO:
            leverages[coin] = leverage_value
    return leverages


def parse_source_current_positions(
    payload: dict[str, Any],
) -> dict[str, PaperSourceCurrentPosition]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    positions: dict[str, PaperSourceCurrentPosition] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        signed_size = decimal_or_none(position.get("szi"))
        if not coin or signed_size is None or signed_size.copy_abs() <= POSITION_EPSILON:
            continue
        positions[coin] = PaperSourceCurrentPosition(
            coin=coin,
            side="long" if signed_size > ZERO else "short",
            size=signed_size.copy_abs(),
        )
    return positions


async def load_execution_market_prices(
    *,
    client: HyperliquidClient,
    fills: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Decimal]:
    if settings.paper_copy_latency_ms > 0:
        await asyncio.sleep(settings.paper_copy_latency_ms / 1000)
    if not settings.paper_copy_use_live_mid_price:
        return {}

    coins = {str(fill.get("coin") or "") for fill in fills}
    coins.discard("")
    if not coins:
        return {}

    try:
        mids = await client.all_mids()
    except Exception as exc:
        logger.warning("paper copy allMids fetch failed error=%s", exc)
        mids = {}

    market_prices: dict[str, Decimal] = {}
    for coin in coins:
        price = resolve_coin_decimal(mids, coin)
        if price is not None and price > ZERO:
            market_prices[coin] = price

    missing_dexes = {
        dex_from_coin(coin)
        for coin in coins
        if coin not in market_prices and dex_from_coin(coin)
    }
    for dex in sorted(missing_dexes):
        dex_prices = await load_dex_market_prices(client=client, dex=dex)
        for coin in coins:
            if coin in market_prices or dex_from_coin(coin) != dex:
                continue
            price = resolve_coin_decimal(dex_prices, coin)
            if price is not None and price > ZERO:
                market_prices[coin] = price
    return market_prices


async def load_dex_market_prices(
    *,
    client: HyperliquidClient,
    dex: str,
) -> dict[str, Decimal]:
    try:
        mids = await client.all_mids(dex=dex)
    except Exception as exc:
        logger.warning("paper copy dex allMids fetch failed dex=%s error=%s", dex, exc)
    else:
        prices = market_prices_from_mids(mids)
        if prices:
            return prices

    try:
        payload = await client.meta_and_asset_ctxs(dex=dex)
    except Exception as exc:
        logger.warning("paper copy dex market price fetch failed dex=%s error=%s", dex, exc)
        return {}
    return market_prices_from_meta_and_asset_ctxs(payload)


def market_prices_from_mids(mids: dict[str, Any]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for coin, raw_price in mids.items():
        price = decimal_or_none(raw_price)
        if coin and price is not None and price > ZERO:
            prices[str(coin)] = price
    return prices


def market_prices_from_meta_and_asset_ctxs(payload: list[Any]) -> dict[str, Decimal]:
    if len(payload) < 2:
        return {}
    meta = payload[0]
    contexts = payload[1]
    if not isinstance(meta, dict) or not isinstance(contexts, list):
        return {}
    universe = meta.get("universe")
    if not isinstance(universe, list):
        return {}

    prices: dict[str, Decimal] = {}
    for asset, context in zip(universe, contexts, strict=False):
        if not isinstance(asset, dict) or not isinstance(context, dict):
            continue
        coin = str(asset.get("name") or "")
        price = (
            decimal_or_none(context.get("midPx"))
            or decimal_or_none(context.get("markPx"))
            or decimal_or_none(context.get("oraclePx"))
        )
        if coin and price is not None and price > ZERO:
            prices[coin] = price
    return prices


def plan_source_fill(fill: dict[str, Any]) -> list[SourceFillPart]:
    raw_json = raw_json_from_fill(fill)
    direction = str(raw_json.get("dir") or "")
    source_size = decimal_or_zero(fill.get("size"))
    source_price = decimal_or_zero(fill.get("price"))
    source_notional = decimal_or_zero(fill.get("notionalUsd")) or source_price * source_size
    start_position = decimal_or_none(raw_json.get("startPosition"))

    if source_size <= ZERO or source_price <= ZERO or source_notional <= ZERO:
        return []
    if direction == "Open Long":
        return [
            SourceFillPart(
                action="open",
                side="long",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Open Short":
        return [
            SourceFillPart(
                action="open",
                side="short",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Close Long":
        return close_parts(
            side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Close Short":
        return close_parts(
            side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Long > Short":
        return flip_parts(
            close_side="long",
            open_side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    if direction == "Short > Long":
        return flip_parts(
            close_side="short",
            open_side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    return []


def close_parts(
    *,
    side: str,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
    action: str,
) -> list[SourceFillPart]:
    close_ratio = close_ratio_from_start_position(source_size, start_position)
    return [
        SourceFillPart(
            action=action,
            side=side,
            source_size=source_size,
            source_notional_usd=source_notional,
            sequence_index=0,
            close_ratio=close_ratio,
            start_position=start_position,
        )
    ]


def flip_parts(
    *,
    close_side: str,
    open_side: str,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
) -> list[SourceFillPart]:
    if start_position is None:
        return close_parts(
            side=close_side,
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="flip_close",
        )

    close_size = min(source_size, start_position.copy_abs())
    open_size = max(source_size - close_size, ZERO)
    parts: list[SourceFillPart] = []
    if close_size > ZERO:
        close_notional = proportional_value(source_notional, close_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_close",
                side=close_side,
                source_size=close_size,
                source_notional_usd=close_notional,
                sequence_index=0,
                close_ratio=close_ratio_from_start_position(close_size, start_position),
                start_position=start_position,
            )
        )
    if open_size > ZERO:
        open_notional = proportional_value(source_notional, open_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_open",
                side=open_side,
                source_size=open_size,
                source_notional_usd=open_notional,
                sequence_index=1,
                start_position=ZERO,
            )
        )
    return parts


async def apply_paper_fill_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return PaperCopyBatchResult(skipped_fills=1)
    existing_fill = await load_paper_copy_fill(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    )
    if existing_fill is not None:
        if not can_retry_existing_paper_fill(existing_fill, part):
            return PaperCopyBatchResult()
        await session.delete(existing_fill)
        await session.flush()

    if part.action in {"open", "flip_open"}:
        return await apply_open_part(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            source_leverages=source_leverages,
            market_prices=market_prices,
            settings=settings,
        )
    return await apply_close_part(
        session,
        account=account,
        allocation=allocation,
        fill=fill,
        part=part,
        source_perp_equity=source_perp_equity,
        source_leverages=source_leverages,
        market_prices=market_prices,
        settings=settings,
    )


async def apply_open_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="invalid_price",
            settings=settings,
            leverage=source_leverage,
        )

    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=str(fill.get("coin") or ""),
    )
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="preexisting_source_position",
            settings=settings,
            leverage=source_leverage,
        )
    if position is not None and position.side != part.side:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="opposite_paper_position",
            settings=settings,
            leverage=source_leverage,
        )
    if position is None and not allocation.active:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="retained_source_new_position_blocked",
            settings=settings,
            leverage=source_leverage,
        )
    if source_perp_equity <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="source_perp_equity_zero",
            settings=settings,
            leverage=source_leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="execution_price_unavailable",
            settings=settings,
            leverage=source_leverage,
        )
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="price_drift_too_high",
            settings=settings,
            execution_context=execution_context,
            leverage=source_leverage,
        )
    price = execution_context.execution_price

    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    source_exposure_pct = part.source_notional_usd / source_perp_equity
    target_notional = allocation_usd * source_exposure_pct
    target_margin = margin_from_notional(target_notional, source_leverage)
    source_remaining = max(
        allocation_usd
        - await open_margin_for_source(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
        ),
        ZERO,
    )
    global_remaining = max(
        max(account.equity_usd, ZERO) * settings.paper_copy_max_total_allocation_pct
        - await open_margin_for_account(session, account_key=account.key),
        ZERO,
    )
    margin_usd = min(target_margin, source_remaining, global_remaining)
    notional_usd = margin_usd * source_leverage
    if notional_usd < settings.paper_copy_min_order_notional_usd:
        reason = open_notional_skip_reason(
            target_notional=target_notional,
            source_remaining=source_remaining * source_leverage,
            global_remaining=global_remaining * source_leverage,
            min_order_notional=settings.paper_copy_min_order_notional_usd,
        )
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason=reason,
            settings=settings,
            leverage=source_leverage,
        )

    paper_size = notional_usd / price
    fee = notional_usd * settings.paper_copy_fee_rate
    filled_at = fill_datetime(fill)
    action = "add" if position is not None and part.action == "open" else part.action

    if position is None:
        position = PaperPosition(
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            coin=str(fill.get("coin") or ""),
            side=part.side,
            size=paper_size,
            entry_price=price,
            notional_usd=notional_usd,
            leverage=source_leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            opened_at=filled_at,
        )
        session.add(position)
    else:
        next_size = position.size + paper_size
        position.entry_price = weighted_average_price(
            position.entry_price,
            position.size,
            price,
            paper_size,
        )
        position.size = next_size
        position.notional_usd = next_size * price
        position.leverage = source_leverage
        position.margin_usd = margin_from_notional(position.notional_usd, source_leverage)
        position.fee_usd += fee

    apply_account_fee(account, fee)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action=action,
            price=price,
            size=paper_size,
            notional_usd=notional_usd,
            leverage=source_leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=ZERO,
            source_perp_equity=source_perp_equity,
            allocation_usd=allocation_usd,
            settings=settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(processed_fills=1, accounts_updated=1, fee_usd=fee)


async def apply_close_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="invalid_price",
            settings=settings,
        )
    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=str(fill.get("coin") or ""),
    )
    if position is None or position.side != part.side:
        source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="no_matching_paper_position",
            settings=settings,
            leverage=source_leverage,
        )
    leverage = safe_leverage(position.leverage)
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="missing_source_start_position",
            settings=settings,
            leverage=leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="execution_price_unavailable",
            settings=settings,
            leverage=leverage,
        )
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="price_drift_too_high",
            settings=settings,
            execution_context=execution_context,
            leverage=leverage,
        )
    price = execution_context.execution_price

    close_ratio = min(part.close_ratio, Decimal("1"))
    close_size = min(position.size, position.size * close_ratio)
    if close_size <= POSITION_EPSILON or price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason="invalid_close_size",
            settings=settings,
            leverage=leverage,
        )

    notional_usd = close_size * price
    margin_usd = margin_from_notional(notional_usd, leverage)
    realized_pnl = realized_pnl_for_close(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=price,
        size=close_size,
    )
    fee = notional_usd * settings.paper_copy_fee_rate
    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    position.realized_pnl_usd += realized_pnl
    position.fee_usd += fee
    remaining_size = position.size - close_size
    if remaining_size <= POSITION_EPSILON:
        await session.delete(position)
        action = "close" if part.action == "close" else part.action
    else:
        position.size = remaining_size
        position.notional_usd = remaining_size * price
        position.margin_usd = margin_from_notional(position.notional_usd, leverage)
        action = "reduce" if part.action == "close" else part.action

    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action=action,
            price=price,
            size=close_size,
            notional_usd=notional_usd,
            leverage=leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=realized_pnl,
            source_perp_equity=source_perp_equity,
            allocation_usd=allocation_usd,
            settings=settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


async def record_batch_skip(
    session: AsyncSession,
    *,
    accounts: list[PaperTradingAccount],
    allocation: PaperSourceAllocation,
    fills: list[dict[str, Any]],
    reason: str,
    settings: Settings,
) -> PaperCopyBatchResult:
    skipped = 0
    for fill in fills:
        result = await record_skip_for_accounts(
            session,
            accounts=accounts,
            allocation=allocation,
            fill=fill,
            reason=reason,
            settings=settings,
        )
        skipped += result.skipped_fills
    return PaperCopyBatchResult(skipped_fills=skipped)


async def record_skip_for_accounts(
    session: AsyncSession,
    *,
    accounts: list[PaperTradingAccount],
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    reason: str,
    settings: Settings,
) -> PaperCopyBatchResult:
    skipped = 0
    part = SourceFillPart(
        action="skip",
        side=side_from_fill_direction(raw_json_from_fill(fill).get("dir")),
        source_size=decimal_or_zero(fill.get("size")),
        source_notional_usd=decimal_or_zero(fill.get("notionalUsd")),
        sequence_index=0,
    )
    for account in accounts:
        result = await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=ZERO,
            reason=reason,
            settings=settings,
        )
        skipped += result.skipped_fills
    return PaperCopyBatchResult(skipped_fills=skipped)


async def record_skip(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    reason: str,
    settings: Settings,
    execution_context: PaperExecutionContext | None = None,
    leverage: Decimal | None = None,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return PaperCopyBatchResult(skipped_fills=1)
    if await paper_fill_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    ):
        return PaperCopyBatchResult()

    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action="skip",
            price=decimal_or_none(fill.get("price")),
            size=None,
            notional_usd=None,
            leverage=leverage,
            margin_usd=None,
            fee_usd=ZERO,
            realized_pnl_usd=ZERO,
            source_perp_equity=source_perp_equity,
            allocation_usd=allocation_usd,
            settings=settings,
            skipped_reason=reason,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(skipped_fills=1)


def paper_copy_fill(
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    action: str,
    price: Decimal | None,
    size: Decimal | None,
    notional_usd: Decimal | None,
    leverage: Decimal | None,
    margin_usd: Decimal | None,
    fee_usd: Decimal,
    realized_pnl_usd: Decimal,
    source_perp_equity: Decimal,
    allocation_usd: Decimal,
    settings: Settings,
    skipped_reason: str | None = None,
    execution_context: PaperExecutionContext | None = None,
) -> PaperCopyFill:
    source_exposure_pct = (
        part.source_notional_usd / source_perp_equity if source_perp_equity > ZERO else None
    )
    return PaperCopyFill(
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=str(fill.get("coin") or ""),
        action=action,
        side=part.side,
        price=price,
        size=size,
        notional_usd=notional_usd,
        leverage=leverage,
        margin_usd=margin_usd,
        fee_usd=fee_usd,
        realized_pnl_usd=realized_pnl_usd,
        source_price=decimal_or_none(fill.get("price")),
        source_size=part.source_size,
        source_notional_usd=part.source_notional_usd,
        source_perp_equity_usd=source_perp_equity if source_perp_equity > ZERO else None,
        source_exposure_pct=source_exposure_pct,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=allocation_usd,
        skipped_reason=skipped_reason,
        filled_at=fill_datetime(fill),
        raw_payload=paper_fill_payload(
            fill=fill,
            settings=settings,
            execution_context=execution_context,
            leverage=leverage,
            margin_usd=margin_usd,
        ),
    )


async def load_paper_position(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> PaperPosition | None:
    return await session.scalar(
        select(PaperPosition).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
            PaperPosition.coin == coin,
        )
    )


async def paper_fill_exists(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
) -> bool:
    existing = await session.scalar(
        select(PaperCopyFill.id).where(
            PaperCopyFill.account_key == account_key,
            PaperCopyFill.source_wallet == source_wallet,
            PaperCopyFill.source_fill_id == source_fill_id,
            PaperCopyFill.sequence_index == sequence_index,
        )
    )
    return existing is not None


async def load_paper_copy_fill(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
) -> PaperCopyFill | None:
    return await session.scalar(
        select(PaperCopyFill).where(
            PaperCopyFill.account_key == account_key,
            PaperCopyFill.source_wallet == source_wallet,
            PaperCopyFill.source_fill_id == source_fill_id,
            PaperCopyFill.sequence_index == sequence_index,
        )
    )


def can_retry_existing_paper_fill(fill: PaperCopyFill, part: SourceFillPart) -> bool:
    return (
        fill.action == "skip"
        and fill.skipped_reason in RETRIABLE_EXIT_SKIP_REASONS
        and not part_requires_source_equity(part)
    )


async def open_margin_for_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.margin_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
        )
    )
    return decimal_or_zero(value)


async def open_margin_for_account(session: AsyncSession, *, account_key: str) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.margin_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
        )
    )
    return decimal_or_zero(value)


def leverage_for_fill(
    *,
    fill: dict[str, Any],
    source_leverages: dict[str, Decimal],
) -> Decimal:
    coin = str(fill.get("coin") or "")
    return safe_leverage(resolve_coin_decimal(source_leverages, coin))


def resolve_coin_decimal(values_by_coin: dict[str, Any], coin: str) -> Decimal | None:
    candidates = coin_symbol_candidates(coin)
    if not candidates:
        return None

    for candidate in candidates:
        value = decimal_or_none(values_by_coin.get(candidate))
        if value is not None:
            return value

    casefold_index = {
        str(key).casefold(): value
        for key, value in values_by_coin.items()
        if str(key).strip()
    }
    for candidate in candidates:
        value = decimal_or_none(casefold_index.get(candidate.casefold()))
        if value is not None:
            return value

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, raw_value in values_by_coin.items():
        if normalize_coin_symbol(str(key)) in normalized_candidates:
            value = decimal_or_none(raw_value)
            if value is not None:
                return value
    return None


def coin_symbol_candidates(coin: str) -> list[str]:
    value = str(coin or "").strip()
    if not value:
        return []

    candidates = [value]
    if ":" in value:
        candidates.append(value.rsplit(":", maxsplit=1)[-1])
    if "/" in value:
        candidates.append(value.split("/", maxsplit=1)[0])

    expanded: list[str] = []
    for candidate in candidates:
        if candidate:
            expanded.append(candidate)
            expanded.append(candidate.upper())
            expanded.append(candidate.lower())
    return unique_strings(expanded)


def normalize_coin_symbol(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def safe_leverage(value: Decimal | None) -> Decimal:
    if value is None or value <= ZERO:
        return Decimal("1")
    return value


def margin_from_notional(notional_usd: Decimal, leverage: Decimal) -> Decimal:
    resolved_leverage = safe_leverage(leverage)
    if notional_usd <= ZERO:
        return ZERO
    return notional_usd / resolved_leverage


def open_notional_skip_reason(
    *,
    target_notional: Decimal,
    source_remaining: Decimal,
    global_remaining: Decimal,
    min_order_notional: Decimal,
) -> str:
    if target_notional < min_order_notional:
        return "below_min_order_notional"

    source_cap_blocked = source_remaining < min_order_notional
    total_cap_blocked = global_remaining < min_order_notional
    if source_cap_blocked and total_cap_blocked:
        return "source_and_total_allocation_caps_reached"
    if source_cap_blocked:
        return "source_allocation_cap_reached"
    if total_cap_blocked:
        return "total_allocation_cap_reached"
    return "below_min_order_notional"


def paper_trading_policy(settings: Settings) -> PaperTradingPolicyRead:
    return PaperTradingPolicyRead(
        enabled=settings.paper_trading_enabled and settings.paper_copy_enabled,
        top_wallet_count=settings.paper_copy_top_wallet_count,
        top_tier_wallet_count=settings.paper_copy_top_tier_wallet_count,
        top_tier_allocation_pct=settings.paper_copy_top_tier_allocation_pct,
        standard_allocation_pct=settings.paper_copy_standard_allocation_pct,
        max_total_allocation_pct=settings.paper_copy_max_total_allocation_pct,
        min_order_notional_usd=settings.paper_copy_min_order_notional_usd,
        fee_rate=settings.paper_copy_fee_rate,
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        max_price_drift_bps=settings.paper_copy_max_price_drift_bps,
        use_live_mid_price=settings.paper_copy_use_live_mid_price,
    )


def close_ratio_from_start_position(
    source_size: Decimal,
    start_position: Decimal | None,
) -> Decimal | None:
    if start_position is None or start_position.copy_abs() <= ZERO:
        return None
    return min(source_size / start_position.copy_abs(), Decimal("1"))


def sorted_paper_source_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(fills, key=paper_source_fill_sort_key)


def paper_source_fill_sort_key(fill: dict[str, Any]) -> tuple[int, str, int, Decimal, str]:
    raw_json = raw_json_from_fill(fill)
    direction = str(raw_json.get("dir") or "")
    start_position = decimal_or_none(raw_json.get("startPosition"))
    source_position = start_position.copy_abs() if start_position is not None else ZERO
    direction_order = 0 if direction in SOURCE_CLOSE_DIRECTIONS else 1
    source_position_order = (
        -source_position if direction in SOURCE_CLOSE_DIRECTIONS else source_position
    )
    return (
        int(fill.get("timestampMs") or 0),
        str(fill.get("coin") or ""),
        direction_order,
        source_position_order,
        str(fill.get("externalFillId") or ""),
    )


def part_requires_source_equity(part: SourceFillPart) -> bool:
    return part.action in SOURCE_EQUITY_ACTIONS


def is_preexisting_source_add(start_position: Decimal | None, *, side: str) -> bool:
    if start_position is None:
        return False
    if side == "long":
        return start_position > POSITION_EPSILON
    return start_position < -POSITION_EPSILON


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


def realized_pnl_for_close(
    *,
    side: str,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
) -> Decimal:
    if side == "long":
        return (exit_price - entry_price) * size
    return (entry_price - exit_price) * size


def weighted_average_price(
    current_price: Decimal,
    current_size: Decimal,
    added_price: Decimal,
    added_size: Decimal,
) -> Decimal:
    total_size = current_size + added_size
    if total_size <= ZERO:
        return added_price
    return ((current_price * current_size) + (added_price * added_size)) / total_size


def proportional_value(value: Decimal, part: Decimal, total: Decimal) -> Decimal:
    if total <= ZERO:
        return ZERO
    return value * part / total


def build_execution_context(
    *,
    fill: dict[str, Any],
    part: SourceFillPart,
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperExecutionContext | None:
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return None

    coin = str(fill.get("coin") or "")
    observed_price = market_prices.get(coin) if settings.paper_copy_use_live_mid_price else None
    if observed_price is None:
        if settings.paper_copy_use_live_mid_price:
            return None
        observed_price = source_price
    if observed_price <= ZERO:
        return None

    execution_price = apply_adverse_slippage(
        price=observed_price,
        side=part.side,
        action=part.action,
        slippage_bps=settings.paper_copy_slippage_bps,
    )
    if execution_price <= ZERO:
        return None

    return PaperExecutionContext(
        source_price=source_price,
        observed_price=observed_price,
        execution_price=execution_price,
        price_drift_bps=price_drift_bps(source_price=source_price, observed_price=observed_price),
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        price_source="live_mid" if settings.paper_copy_use_live_mid_price else "source_fill",
    )


def apply_adverse_slippage(
    *,
    price: Decimal,
    side: str,
    action: str,
    slippage_bps: Decimal,
) -> Decimal:
    adjustment = slippage_bps / BPS_DENOMINATOR
    if is_buy_execution(side=side, action=action):
        return price * (Decimal("1") + adjustment)
    return price * max(Decimal("1") - adjustment, ZERO)


def is_buy_execution(*, side: str, action: str) -> bool:
    is_open = action in {"open", "add", "flip_open"}
    if side == "long":
        return is_open
    return not is_open


def price_drift_bps(*, source_price: Decimal, observed_price: Decimal) -> Decimal:
    if source_price <= ZERO:
        return ZERO
    return (observed_price - source_price).copy_abs() / source_price * BPS_DENOMINATOR


def raw_json_from_fill(fill: dict[str, Any]) -> dict[str, Any]:
    raw_json = fill.get("rawJson")
    return raw_json if isinstance(raw_json, dict) else {}


def dex_from_coin(value: Any) -> str:
    coin = str(value or "").strip()
    if ":" not in coin:
        return ""
    dex = coin.split(":", maxsplit=1)[0].strip()
    return dex


def side_from_fill_direction(value: Any) -> str:
    direction = str(value or "")
    if "Short" in direction:
        return "short"
    return "long"


def fill_datetime(fill: dict[str, Any]) -> datetime:
    timestamp_ms = int(fill.get("timestampMs") or 0)
    if timestamp_ms <= 0:
        return datetime.now(UTC)
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def paper_fill_payload(
    *,
    fill: dict[str, Any],
    settings: Settings,
    execution_context: PaperExecutionContext | None = None,
    leverage: Decimal | None = None,
    margin_usd: Decimal | None = None,
) -> dict[str, Any]:
    return {
        "sourceFill": {
            "externalFillId": fill.get("externalFillId"),
            "coin": fill.get("coin"),
            "side": fill.get("side"),
            "price": fill.get("price"),
            "size": fill.get("size"),
            "timestampMs": fill.get("timestampMs"),
            "rawJson": fill.get("rawJson"),
        },
        "policy": {
            "feeRate": str(settings.paper_copy_fee_rate),
            "maxTotalAllocationPct": str(settings.paper_copy_max_total_allocation_pct),
            "slippageBps": str(settings.paper_copy_slippage_bps),
            "latencyMs": settings.paper_copy_latency_ms,
            "maxPriceDriftBps": str(settings.paper_copy_max_price_drift_bps),
            "useLiveMidPrice": settings.paper_copy_use_live_mid_price,
        },
        "execution": execution_payload(execution_context),
        "paper": {
            "leverage": str(leverage) if leverage is not None else None,
            "marginUsd": str(margin_usd) if margin_usd is not None else None,
        },
    }


def execution_payload(context: PaperExecutionContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "sourcePrice": str(context.source_price),
        "observedPrice": str(context.observed_price),
        "executionPrice": str(context.execution_price),
        "priceDriftBps": str(context.price_drift_bps),
        "slippageBps": str(context.slippage_bps),
        "latencyMs": context.latency_ms,
        "priceSource": context.price_source,
    }


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_or_zero(value: Any) -> Decimal:
    return decimal_or_none(value) or ZERO


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sum_decimal(values: Any) -> Decimal:
    total = ZERO
    for value in values:
        if value is not None:
            total += decimal_or_zero(value)
    return total
