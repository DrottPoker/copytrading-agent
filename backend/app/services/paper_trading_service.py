import asyncio
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, literal, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import (
    AuditLog,
    LiveCopyFillState,
    PaperAccountEquitySnapshot,
    PaperCopyAllocation,
    PaperCopyFill,
    PaperPosition,
    PaperTradingAccount,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletMonitoringStat,
    WalletScore,
    WatchedWallet,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.paper_trading import (
    PaperTradingPolicyRead,
    PaperTradingSummaryResponse,
)
from app.schemas.wallet import normalize_wallet_address
from app.services.copy_decision import (
    ALLOCATION_INACTIVE,
    CLOSE_BELOW_MIN_ORDER,
    CLOSE_RATIO_MISSING,
    CLOSE_SIZE_ZERO,
    INVALID_SOURCE_PRICE,
    MATCHING_POSITION_MISSING,
    OPPOSITE_POSITION,
    SOURCE_EQUITY_MISSING,
    CopyPosition,
    aggregate_below_min_close,
    decide_close,
    decide_entry,
    entry_precheck,
)
from app.services.copy_execution_cache import AllocationSnapshotCache
from app.services.copy_signal_service import (
    ExecutionContext,
    ExecutionMarketPrices,
    SourceAccountState,
    SourceFillPart,
    SourceStateCache,
    UnifiedEquityCache,
    apply_adverse_slippage,
    decimal_or_none,
    decimal_or_zero,
    dex_from_coin,
    fill_datetime,
    leverage_for_fill,
    load_execution_market_prices,
    load_mark_prices,
    load_source_account_state,
    load_source_account_states,
    part_requires_source_equity,
    plan_source_fill,
    raw_json_from_fill,
    resolve_coin_decimal,
    resolve_coin_margin_mode,
    resolve_source_current_position,
    side_from_fill_direction,
    sorted_source_fills,
    source_fill_age_exceeds_entry_limit,
    source_fill_from_wallet_fill,
    source_state_available_for_reconciliation,
    timestamp_ms,
    unique_strings,
)
from app.services.live_copy_state_service import (
    POSITION_EPSILON as LIVE_POSITION_EPSILON,
)
from app.services.live_copy_state_service import (
    live_copy_unresolved_exit_predicate,
    live_copy_unresolved_order_predicate,
)
from app.services.live_trading_service import (
    LIVE_EXCHANGE_SOURCE,
    LIVE_MANUAL_TEST_SOURCE,
)
from app.services.market_price_cache import MarketPriceCache
from app.services.paper_execution_simulator import (
    IocFill,
    OrderBook,
    ioc_limit_price,
    parse_order_book,
    position_pnl,
    simulate_ioc_fill,
)
from app.services.paper_risk_service import (
    apply_account_fee,
    apply_account_realized_result,
    clear_paper_account_exit_only,
    paper_entry_risk_reason,
)
from app.services.realtime_subscription_state_service import (
    RealtimeSubscriptionSnapshot,
    load_realtime_subscription_state,
    realtime_subscription_stale_after_seconds,
)
from app.services.trading_account_service import sync_paper_trading_account_mirrors
from app.services.trading_core import (
    MinOrderAdjustment as PaperMinOrderAdjustment,
)
from app.services.trading_core import (
    TradeIntent,
    build_copy_trade_intent,
    copy_allocation_pct,
    margin_from_notional,
    parse_trade_action,
    parse_trade_side,
    safe_leverage,
    trade_intent_payload,
    trade_is_buy,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
ONE = Decimal("1")
POSITION_EPSILON = Decimal("0.00000001")
BPS_DENOMINATOR = Decimal("10000")
PAPER_COPY_RECOVERY_OVERLAP_MS = 5 * 60 * 1000
PAPER_COPY_SOURCE_LOCK_NAMESPACE = "paper_copy_source"
MONITORING_MIN_SNAPSHOT_GAP_SECONDS = 60
POSITION_ADD_FILL_ACTIONS = frozenset({"add"})
POSITION_CLOSE_FILL_ACTIONS = frozenset({"reduce", "close", "flip_close"})
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
SUMMARY_MARKET_PRICE_CACHE = MarketPriceCache()
# Stored paper reason names for engine-neutral decision reasons.
PAPER_DECISION_REASONS = {
    INVALID_SOURCE_PRICE: "invalid_price",
    OPPOSITE_POSITION: "opposite_paper_position",
    ALLOCATION_INACTIVE: "retained_source_new_position_blocked",
    SOURCE_EQUITY_MISSING: "source_perp_equity_zero",
    MATCHING_POSITION_MISSING: "no_matching_paper_position",
    CLOSE_RATIO_MISSING: "missing_source_start_position",
    CLOSE_SIZE_ZERO: "invalid_close_size",
}
PAPER_CLOSE_AGGREGATED_REASON = "close_aggregated"


class PaperPositionCloseError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperPositionNotFoundError(PaperPositionCloseError):
    status_code = 404


class PaperPositionCloseUnavailableError(PaperPositionCloseError):
    status_code = 409


class PaperAccountResetError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperAccountResetNotFoundError(PaperAccountResetError):
    status_code = 404


class PaperAccountResetUnavailableError(PaperAccountResetError):
    status_code = 409


class PaperAccountControlError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperAccountControlNotFoundError(PaperAccountControlError):
    status_code = 404


class PaperAccountCreateError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperAccountCreateUnavailableError(PaperAccountCreateError):
    status_code = 501


class PaperAccountDeleteError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class PaperAccountDeleteNotFoundError(PaperAccountDeleteError):
    status_code = 404


@dataclass(frozen=True)
class PaperSourceAllocation:
    source_wallet: str
    source_label: str | None
    rank: int
    pool_rank: int | None
    score: Decimal | None
    allocation_pct: Decimal
    active: bool
    has_realtime_slot: bool
    status_reason: str


@dataclass(frozen=True)
class WalletMonitoringSummary:
    first_monitored_at: datetime | None
    current_monitoring_started_at: datetime | None
    last_monitored_at: datetime | None
    monitored_seconds: int


CopyAllocationCache = AllocationSnapshotCache[dict[str, PaperSourceAllocation]]


@dataclass(frozen=True)
class PaperCopyBatchResult:
    processed_fills: int = 0
    skipped_fills: int = 0
    accounts_updated: int = 0
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    skip_reasons: dict[str, int] = field(default_factory=dict)


async def get_paper_trading_summary(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    recent_fill_limit: int = 100,
    closed_trade_limit: int = 100,
    client: HyperliquidClient | None = None,
    include_market_prices: bool = True,
    refresh_allocations: bool = False,
) -> PaperTradingSummaryResponse:
    resolved_settings = settings or get_settings()
    source_allocations: dict[str, PaperSourceAllocation] = {}
    if refresh_allocations:
        source_allocations = await refresh_paper_copy_allocations(
            session,
            settings=resolved_settings,
        )
        await session.commit()
    else:
        source_allocations = {
            allocation.source_wallet.lower(): allocation
            for allocation in await load_paper_source_allocations(
                session,
                settings=resolved_settings,
            )
        }
    realtime_monitoring = await load_realtime_subscription_state(
        session,
        stale_after_seconds=realtime_subscription_stale_after_seconds(
            resolved_settings.realtime_subscription_refresh_seconds
        ),
    )

    accounts_result = await session.execute(
        select(PaperTradingAccount).order_by(PaperTradingAccount.key.asc())
    )
    open_sources = open_copy_source_select().subquery("summary_open_copy_sources")
    open_position_sources = select(open_sources.c.source_wallet).distinct()
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
        .where(PaperCopyFill.action.in_(("close", "flip_close", "liquidation")))
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
            func.coalesce(func.sum(PaperCopyFill.realized_pnl_usd), ZERO).label("realized_pnl_usd"),
            func.coalesce(func.sum(PaperCopyFill.fee_usd), ZERO).label("fee_usd"),
            func.max(PaperCopyFill.filled_at).label("last_fill_at"),
        ).group_by(PaperCopyFill.source_wallet)
    )
    accounts = list(accounts_result.scalars().all())
    allocations = list(allocations_result.scalars().all())
    positions = list(positions_result.scalars().all())
    recent_fills = list(fills_result.scalars().all())
    closed_trades = list(closed_trades_result.scalars().all())
    fill_performance_rows = list(fill_performance_result.mappings().all())
    summary_source_wallets = [
        *(allocation.source_wallet for allocation in allocations),
        *(position.source_wallet for position in positions),
        *(fill.source_wallet for fill in recent_fills),
        *(fill.source_wallet for fill in closed_trades),
        *(row["source_wallet"] for row in fill_performance_rows if row["source_wallet"]),
        *(allocation.source_wallet for allocation in source_allocations.values()),
    ]
    source_labels = await load_paper_source_labels(session, source_wallets=summary_source_wallets)
    monitoring_stats = await load_wallet_monitoring_stats(
        session,
        source_wallets=summary_source_wallets,
        settings=resolved_settings,
        now=datetime.now(UTC),
    )
    if not source_allocations:
        source_allocations = paper_source_allocations_from_copy_rows(
            allocations=allocations,
            source_labels=source_labels,
        )
    liquidation_source_fill_ids = await load_liquidation_source_fill_ids(
        session,
        fills=closed_trades,
    )
    opened_at_by_closed_fill_id = await load_closed_trade_open_times(
        session,
        fills=closed_trades,
    )

    market_prices: dict[str, Decimal] = {}
    if include_market_prices and client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            market_prices = await load_open_position_market_prices(
                client=hyperliquid_client,
                positions=positions,
                price_cache=SUMMARY_MARKET_PRICE_CACHE,
            )
    elif include_market_prices and client is not None:
        market_prices = await load_open_position_market_prices(
            client=client,
            positions=positions,
            price_cache=SUMMARY_MARKET_PRICE_CACHE,
        )

    updated_at = datetime.now(UTC)
    position_fill_counts = await load_paper_position_fill_counts(
        session,
        positions=positions,
    )
    position_rows = [
        paper_position_read(
            position,
            mark_price=resolve_coin_decimal(market_prices, position.coin),
            price_updated_at=updated_at if position.coin in market_prices else None,
            source_label=source_labels.get(position.source_wallet.lower()),
            fill_counts=position_fill_counts.get(position.id, (0, 0)),
        )
        for position in positions
    ]
    return PaperTradingSummaryResponse(
        policy=paper_trading_policy(resolved_settings),
        accounts=paper_account_reads(accounts=accounts, positions=position_rows),
        allocations=paper_allocation_reads(
            accounts=accounts,
            allocations=allocations,
            positions=position_rows,
            source_allocations=source_allocations,
            source_labels=source_labels,
            realtime_monitoring=realtime_monitoring,
        ),
        positions=position_rows,
        wallet_performance=paper_wallet_performance_reads(
            allocations=allocations,
            positions=position_rows,
            fill_performance_rows=fill_performance_rows,
            source_allocations=source_allocations,
            source_labels=source_labels,
            monitoring_stats=monitoring_stats,
            realtime_monitoring=realtime_monitoring,
        ),
        closed_trades=paper_closed_trade_reads(
            closed_trades,
            source_labels=source_labels,
            liquidation_source_fill_ids=liquidation_source_fill_ids,
            opened_at_by_closed_fill_id=opened_at_by_closed_fill_id,
        ),
        recent_fills=paper_copy_fill_reads(recent_fills, source_labels=source_labels),
        realtime_monitoring=realtime_monitoring_read(realtime_monitoring),
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
    price_cache: MarketPriceCache | None = None,
    max_age_seconds: float = 15.0,
) -> dict[str, Decimal]:
    return await load_mark_prices(
        client=client,
        coins=(position.coin for position in positions),
        price_cache=price_cache,
        max_age_seconds=max_age_seconds,
    )


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

    await lock_paper_source_mutation(
        session,
        source_wallet=position_snapshot.source_wallet,
    )
    position = await session.scalar(
        select(PaperPosition).where(PaperPosition.id == position_id).with_for_update()
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
        side=parse_trade_side(position.side),
        source_size=close_size,
        source_notional_usd=notional_usd,
        sequence_index=0,
        close_ratio=ONE,
    )
    execution_context = ExecutionContext(
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
            opened_at=position.opened_at,
        )
    )
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


async def close_paper_source_positions_manually(
    session: AsyncSession,
    *,
    source_wallet: str,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
) -> PaperCopyBatchResult:
    try:
        normalized_source = normalize_wallet_address(source_wallet)
    except ValueError as exc:
        raise PaperPositionCloseError("Invalid source wallet address.") from exc

    resolved_settings = settings or get_settings()
    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await close_paper_source_positions_manually(
                session,
                source_wallet=normalized_source,
                settings=resolved_settings,
                client=hyperliquid_client,
            )

    await lock_paper_source_mutation(session, source_wallet=normalized_source)
    result = await session.execute(
        select(PaperPosition.id)
        .where(PaperPosition.source_wallet == normalized_source)
        .order_by(PaperPosition.account_key.asc(), PaperPosition.coin.asc())
    )
    position_ids = list(result.scalars().all())
    if not position_ids:
        raise PaperPositionNotFoundError("No open paper positions found for this source wallet.")

    total = PaperCopyBatchResult()
    for position_id in position_ids:
        close_result = await close_paper_position_manually(
            session,
            position_id=position_id,
            settings=resolved_settings,
            client=client,
        )
        total = combine_batch_results(total, close_result)
    return total


async def close_paper_account_positions_manually(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await close_paper_account_positions_manually(
                session,
                account_key=account_key,
                settings=resolved_settings,
                client=hyperliquid_client,
            )

    result = await session.execute(
        select(PaperPosition.id)
        .where(PaperPosition.account_key == account_key)
        .order_by(PaperPosition.source_wallet.asc(), PaperPosition.coin.asc())
    )
    position_ids = list(result.scalars().all())
    total = PaperCopyBatchResult()
    for position_id in position_ids:
        close_result = await close_paper_position_manually(
            session,
            position_id=position_id,
            settings=resolved_settings,
            client=client,
        )
        total = combine_batch_results(total, close_result)
    return total


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
        source_label = await load_paper_source_label(session, position.source_wallet)
        return PaperSourceAllocation(
            source_wallet=position.source_wallet,
            source_label=source_label,
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
        source_label=await load_paper_source_label(session, allocation.source_wallet),
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
    source_label: str | None,
    fill_counts: tuple[int, int] = (0, 0),
) -> dict[str, Any]:
    current_notional = (
        abs(position.size) * mark_price if mark_price is not None and mark_price > ZERO else None
    )
    unrealized_pnl = paper_unrealized_pnl(position=position, mark_price=mark_price)
    return {
        "id": position.id,
        "account_key": position.account_key,
        "source_wallet": position.source_wallet,
        "source_label": source_label,
        "coin": position.coin,
        "side": position.side,
        "size": position.size,
        "entry_price": position.entry_price,
        "notional_usd": position.notional_usd,
        "leverage": position.leverage,
        "margin_usd": position.margin_usd,
        "margin_mode": position.margin_mode,
        "funding_usd": position.funding_usd,
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
        "add_fill_count": fill_counts[0],
        "close_fill_count": fill_counts[1],
        "opened_at": position.opened_at,
        "entry_execution_delay_ms": duration_between_ms(position.opened_at, position.created_at),
        "created_at": position.created_at,
        "updated_at": position.updated_at,
    }


async def load_paper_position_fill_counts(
    session: AsyncSession,
    *,
    positions: list[PaperPosition],
) -> dict[UUID, tuple[int, int]]:
    if not positions:
        return {}
    position_ids = [position.id for position in positions]
    result = await session.execute(
        select(
            PaperPosition.id,
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action.in_(POSITION_ADD_FILL_ACTIONS))
            .label("add_fill_count"),
            func.count(PaperCopyFill.id)
            .filter(PaperCopyFill.action.in_(POSITION_CLOSE_FILL_ACTIONS))
            .label("close_fill_count"),
        )
        .outerjoin(
            PaperCopyFill,
            and_(
                PaperCopyFill.account_key == PaperPosition.account_key,
                PaperCopyFill.source_wallet == PaperPosition.source_wallet,
                PaperCopyFill.coin == PaperPosition.coin,
                PaperCopyFill.side == PaperPosition.side,
                PaperCopyFill.filled_at >= PaperPosition.opened_at,
            ),
        )
        .where(PaperPosition.id.in_(position_ids))
        .group_by(PaperPosition.id)
    )
    return {
        row.id: (int(row.add_fill_count or 0), int(row.close_fill_count or 0))
        for row in result.all()
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
        total_pnl = account.realized_pnl_usd + account.funding_usd + unrealized_pnl
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
                "funding_usd": account.funding_usd,
                "enabled": account.enabled,
                "exit_only_at": account.exit_only_at,
                "exit_only_reason": account.exit_only_reason,
                "created_at": account.created_at,
                "updated_at": account.updated_at,
            }
        )
    return rows


def paper_allocation_reads(
    *,
    accounts: list[PaperTradingAccount],
    allocations: list[PaperCopyAllocation],
    positions: list[dict[str, Any]],
    source_allocations: dict[str, PaperSourceAllocation],
    source_labels: dict[str, str],
    realtime_monitoring: RealtimeSubscriptionSnapshot,
) -> list[dict[str, Any]]:
    account_enabled_by_key = {account.key: account.enabled for account in accounts}
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
            source_allocation.pool_rank if source_allocation is not None else allocation.rank
        )
        source_status_reason = (
            source_allocation.status_reason if source_allocation is not None else "copy_candidate"
        )
        open_position_count = open_position_count_by_source.get(source_wallet, 0)
        account_enabled = account_enabled_by_key.get(allocation.account_key)
        source_can_open_new_positions = (
            source_allocation.active if source_allocation is not None else allocation.active
        )
        can_open_new_positions = account_enabled is not False and source_can_open_new_positions
        monitor_status = paper_monitor_status(
            source_wallet=source_wallet,
            has_realtime_slot=has_realtime_slot,
            realtime_monitoring=realtime_monitoring,
        )
        source_status = paper_source_status(
            has_realtime_slot=has_realtime_slot or monitor_status == "monitored",
            can_open_new_positions=can_open_new_positions,
            open_position_count=open_position_count,
        )
        is_realtime_monitored = source_wallet in realtime_monitoring.monitored_wallets
        remaining = max(allocation.allocation_usd - open_margin, ZERO)
        rows.append(
            {
                "id": allocation.id,
                "account_key": allocation.account_key,
                "source_wallet": allocation.source_wallet,
                "source_label": source_labels.get(source_wallet),
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
                "is_realtime_monitored": is_realtime_monitored,
                "can_open_new_positions": can_open_new_positions,
                "monitor_status": monitor_status,
                "source_status": source_status,
                "source_status_reason": paper_allocation_status_reason(
                    source_status=source_status,
                    source_status_reason=source_status_reason,
                    source_allocation=source_allocation,
                    can_open_new_positions=can_open_new_positions,
                    account_enabled=account_enabled,
                ),
                "updated_at": allocation.updated_at,
            }
        )
    return rows


def paper_source_allocations_from_copy_rows(
    *,
    allocations: list[PaperCopyAllocation],
    source_labels: dict[str, str],
) -> dict[str, PaperSourceAllocation]:
    rows: dict[str, PaperSourceAllocation] = {}
    for allocation in sorted(allocations, key=lambda row: (row.rank, row.account_key)):
        source_wallet = allocation.source_wallet.lower()
        existing = rows.get(source_wallet)
        if existing is not None and (existing.active or not allocation.active):
            continue
        rows[source_wallet] = PaperSourceAllocation(
            source_wallet=source_wallet,
            source_label=source_labels.get(source_wallet),
            rank=allocation.rank,
            pool_rank=allocation.rank,
            score=allocation.score,
            allocation_pct=allocation.allocation_pct,
            active=allocation.active,
            has_realtime_slot=allocation.active,
            status_reason="copy_candidate" if allocation.active else "existing_exposure_only",
        )
    return rows


def paper_source_status(
    *,
    has_realtime_slot: bool,
    can_open_new_positions: bool,
    open_position_count: int,
) -> str:
    if not has_realtime_slot:
        return "retained" if open_position_count > 0 else "waiting_for_slot"
    if open_position_count > 0 and can_open_new_positions:
        return "trading"
    if open_position_count > 0:
        return "retained"
    return "waiting_for_trades" if can_open_new_positions else "retained"


def paper_monitor_status(
    *,
    source_wallet: str,
    has_realtime_slot: bool,
    realtime_monitoring: RealtimeSubscriptionSnapshot,
) -> str:
    normalized_source = source_wallet.lower()
    if normalized_source in realtime_monitoring.monitored_wallets:
        return "monitored"
    if not has_realtime_slot:
        return "waiting"
    if normalized_source in realtime_monitoring.desired_wallets:
        return "connecting"
    return "offline"


def realtime_monitoring_read(
    realtime_monitoring: RealtimeSubscriptionSnapshot,
) -> dict[str, Any]:
    return {
        "status": realtime_monitoring.status,
        "desired_wallets": list(realtime_monitoring.desired_wallets),
        "monitored_wallets": sorted(realtime_monitoring.monitored_wallets),
        "worker_role": realtime_monitoring.worker_role,
        "worker_instance_id": realtime_monitoring.worker_instance_id,
        "updated_at": realtime_monitoring.updated_at,
    }


def paper_allocation_status_reason(
    *,
    source_status: str,
    source_status_reason: str,
    source_allocation: PaperSourceAllocation | None,
    can_open_new_positions: bool,
    account_enabled: bool | None,
) -> str:
    if source_status in {"trading", "waiting_for_trades"}:
        return "active_copy_source"
    if source_status == "retained":
        if (
            source_allocation is not None
            and source_allocation.active
            and not can_open_new_positions
        ):
            return "paper_account_disabled" if account_enabled is False else "allocation_inactive"
        if source_status_reason == "copy_candidate":
            return "existing_exposure_only"
    return source_status_reason


def paper_wallet_performance_reads(
    *,
    allocations: list[PaperCopyAllocation],
    positions: list[dict[str, Any]],
    fill_performance_rows: Sequence[Mapping[Any, Any]],
    source_allocations: dict[str, PaperSourceAllocation],
    source_labels: dict[str, str],
    monitoring_stats: dict[str, WalletMonitoringSummary],
    realtime_monitoring: RealtimeSubscriptionSnapshot,
) -> list[dict[str, Any]]:
    sources = {
        allocation.source_wallet.lower() for allocation in allocations if allocation.source_wallet
    }
    sources.update(
        str(position["source_wallet"]).lower()
        for position in positions
        if position["source_wallet"]
    )
    sources.update(
        str(row["source_wallet"]).lower() for row in fill_performance_rows if row["source_wallet"]
    )
    sources.update(monitoring_stats.keys())

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
        total_pnl = realized_pnl + unrealized_pnl
        monitoring = monitoring_stats.get(source)
        monitored_seconds = monitoring.monitored_seconds if monitoring is not None else 0
        allocation_pct = first_decimal(allocation.allocation_pct for allocation in allocation_rows)
        rows.append(
            {
                "source_wallet": source,
                "source_label": source_labels.get(source),
                "rank": min((allocation.rank for allocation in allocation_rows), default=None),
                "pool_rank": (
                    source_allocation.pool_rank
                    if source_allocation is not None
                    else min((allocation.rank for allocation in allocation_rows), default=None)
                ),
                "score": first_decimal(allocation.score for allocation in allocation_rows),
                "allocation_pct": allocation_pct,
                "active": any(allocation.active for allocation in allocation_rows),
                "monitor_status": (
                    "monitored" if source in realtime_monitoring.monitored_wallets else "history"
                ),
                "account_count": len({allocation.account_key for allocation in allocation_rows}),
                "open_position_count": len(source_positions),
                "copied_fill_count": int(fill_row.get("copied_fill_count") or 0),
                "skipped_fill_count": int(fill_row.get("skipped_fill_count") or 0),
                "realized_pnl_usd": realized_pnl,
                "unrealized_pnl_usd": unrealized_pnl,
                "total_pnl_usd": total_pnl,
                "monitored_seconds": monitored_seconds,
                "monitored_hours": monitored_hours(monitored_seconds),
                "realized_pnl_per_monitored_hour_usd": pnl_per_monitored_hour(
                    realized_pnl,
                    monitored_seconds,
                ),
                "total_pnl_per_monitored_hour_usd": pnl_per_monitored_hour(
                    total_pnl,
                    monitored_seconds,
                ),
                "first_monitored_at": (
                    monitoring.first_monitored_at if monitoring is not None else None
                ),
                "current_monitoring_started_at": (
                    monitoring.current_monitoring_started_at if monitoring is not None else None
                ),
                "last_monitored_at": (
                    monitoring.last_monitored_at if monitoring is not None else None
                ),
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
            -row["total_pnl_usd"],
            -row["realized_pnl_usd"],
            row["pool_rank"] or 9999,
            row["source_wallet"],
        ),
    )


def monitored_hours(monitored_seconds: int) -> Decimal:
    if monitored_seconds <= 0:
        return ZERO
    return (Decimal(monitored_seconds) / Decimal("3600")).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )


def pnl_per_monitored_hour(
    pnl_usd: Decimal,
    monitored_seconds: int,
) -> Decimal | None:
    if monitored_seconds <= 0:
        return None
    return (pnl_usd * Decimal("3600") / Decimal(monitored_seconds)).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )


def paper_closed_trade_reads(
    fills: list[PaperCopyFill],
    *,
    source_labels: dict[str, str],
    liquidation_source_fill_ids: set[tuple[str, str]],
    opened_at_by_closed_fill_id: dict[UUID, datetime],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fill in fills:
        opened_at = paper_fill_opened_at(fill) or opened_at_by_closed_fill_id.get(fill.id)
        stored_duration_ms = paper_fill_duration_ms(fill)
        duration_ms = (
            stored_duration_ms
            if stored_duration_ms is not None
            else duration_between_ms(opened_at, fill.filled_at)
        )
        rows.append(
            {
                "id": fill.id,
                "account_key": fill.account_key,
                "source_wallet": fill.source_wallet,
                "source_label": source_labels.get(fill.source_wallet.lower()),
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
                "is_source_liquidation": (
                    fill.source_wallet.lower(),
                    fill.source_fill_id,
                )
                in liquidation_source_fill_ids,
                "is_paper_liquidation": fill.action == "liquidation",
                "opened_at": opened_at,
                "closed_at": fill.filled_at,
                "duration_ms": duration_ms,
                "created_at": fill.created_at,
            }
        )
    return rows


async def load_closed_trade_open_times(
    session: AsyncSession,
    *,
    fills: list[PaperCopyFill],
) -> dict[UUID, datetime]:
    close_fills = [fill for fill in fills if fill.side]
    if not close_fills:
        return {}

    position_keys = {
        (fill.account_key, fill.source_wallet, fill.coin, fill.side)
        for fill in close_fills
        if fill.side
    }
    latest_close_at = max(fill.filled_at for fill in close_fills)
    result = await session.execute(
        select(
            PaperCopyFill.account_key,
            PaperCopyFill.source_wallet,
            PaperCopyFill.coin,
            PaperCopyFill.side,
            PaperCopyFill.filled_at,
        )
        .where(
            PaperCopyFill.action.in_(("open", "flip_open")),
            tuple_(
                PaperCopyFill.account_key,
                PaperCopyFill.source_wallet,
                PaperCopyFill.coin,
                PaperCopyFill.side,
            ).in_(position_keys),
            PaperCopyFill.filled_at <= latest_close_at,
        )
        .order_by(PaperCopyFill.filled_at.asc(), PaperCopyFill.created_at.asc())
    )
    open_times_by_key: dict[tuple[str, str, str, str], list[datetime]] = {}
    for row in result.mappings().all():
        key = (
            str(row["account_key"]),
            str(row["source_wallet"]),
            str(row["coin"]),
            str(row["side"]),
        )
        open_times_by_key.setdefault(key, []).append(row["filled_at"])

    opened_at_by_closed_fill_id: dict[UUID, datetime] = {}
    for fill in close_fills:
        if not fill.side:
            continue
        key = (fill.account_key, fill.source_wallet, fill.coin, fill.side)
        candidates = [
            opened_at for opened_at in open_times_by_key.get(key, []) if opened_at <= fill.filled_at
        ]
        if candidates:
            opened_at_by_closed_fill_id[fill.id] = candidates[-1]
    return opened_at_by_closed_fill_id


async def load_liquidation_source_fill_ids(
    session: AsyncSession,
    *,
    fills: list[PaperCopyFill],
) -> set[tuple[str, str]]:
    pairs = {
        (fill.source_wallet.lower(), fill.source_fill_id)
        for fill in fills
        if fill.source_wallet and fill.source_fill_id
    }
    if not pairs:
        return set()

    result = await session.execute(
        select(WalletFill.wallet_address, WalletFill.external_fill_id).where(
            tuple_(WalletFill.wallet_address, WalletFill.external_fill_id).in_(pairs),
            WalletFill.raw_json.has_key("liquidation"),
        )
    )
    return {(str(row.wallet_address).lower(), str(row.external_fill_id)) for row in result.all()}


def paper_copy_fill_reads(
    fills: list[PaperCopyFill],
    *,
    source_labels: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        {
            "id": fill.id,
            "account_key": fill.account_key,
            "source_wallet": fill.source_wallet,
            "source_label": source_labels.get(fill.source_wallet.lower()),
            "source_fill_id": fill.source_fill_id,
            "sequence_index": fill.sequence_index,
            "coin": fill.coin,
            "action": fill.action,
            "side": fill.side,
            "price": fill.price,
            "size": fill.size,
            "notional_usd": fill.notional_usd,
            "leverage": fill.leverage,
            "margin_usd": fill.margin_usd,
            "fee_usd": fill.fee_usd,
            "realized_pnl_usd": fill.realized_pnl_usd,
            "source_price": fill.source_price,
            "observed_price": paper_execution_decimal(fill, "observedPrice"),
            "execution_price": paper_execution_decimal(fill, "executionPrice"),
            "price_drift_bps": paper_execution_decimal(fill, "priceDriftBps"),
            "price_source": paper_execution_string(fill, "priceSource"),
            "max_price_drift_bps": paper_policy_decimal(fill, "maxPriceDriftBps"),
            "source_size": fill.source_size,
            "source_notional_usd": fill.source_notional_usd,
            "source_perp_equity_usd": fill.source_perp_equity_usd,
            "source_account_value_usd": fill.source_perp_equity_usd,
            "source_exposure_pct": fill.source_exposure_pct,
            "allocation_pct": fill.allocation_pct,
            "allocation_usd": fill.allocation_usd,
            "skipped_reason": fill.skipped_reason,
            "min_order_adjusted": paper_min_order_adjusted(fill),
            "original_notional_usd": paper_min_order_adjustment_decimal(
                fill,
                "originalNotionalUsd",
            ),
            "adjusted_notional_usd": paper_min_order_adjustment_decimal(
                fill,
                "adjustedNotionalUsd",
            ),
            "min_order_notional_usd": paper_min_order_adjustment_decimal(
                fill,
                "minOrderNotionalUsd",
            ),
            "filled_at": fill.filled_at,
            "created_at": fill.created_at,
        }
        for fill in fills
    ]


def paper_execution_payload(fill: PaperCopyFill) -> dict[str, Any]:
    payload = fill.raw_payload
    if not isinstance(payload, dict):
        return {}
    execution = payload.get("execution")
    return execution if isinstance(execution, dict) else {}


def paper_execution_decimal(fill: PaperCopyFill, key: str) -> Decimal | None:
    return decimal_or_none(paper_execution_payload(fill).get(key))


def paper_execution_string(fill: PaperCopyFill, key: str) -> str | None:
    value = paper_execution_payload(fill).get(key)
    return str(value) if value is not None else None


def paper_policy_payload(fill: PaperCopyFill) -> dict[str, Any]:
    payload = fill.raw_payload
    if not isinstance(payload, dict):
        return {}
    policy = payload.get("policy")
    return policy if isinstance(policy, dict) else {}


def paper_policy_decimal(fill: PaperCopyFill, key: str) -> Decimal | None:
    return decimal_or_none(paper_policy_payload(fill).get(key))


def paper_detail_payload(fill: PaperCopyFill) -> dict[str, Any]:
    payload = fill.raw_payload
    if not isinstance(payload, dict):
        return {}
    paper = payload.get("paper")
    return paper if isinstance(paper, dict) else {}


def paper_min_order_adjustment_payload(fill: PaperCopyFill) -> dict[str, Any]:
    adjustment = paper_detail_payload(fill).get("minOrderAdjustment")
    return adjustment if isinstance(adjustment, dict) else {}


def paper_min_order_adjusted(fill: PaperCopyFill) -> bool:
    return bool(paper_min_order_adjustment_payload(fill))


def paper_min_order_adjustment_decimal(fill: PaperCopyFill, key: str) -> Decimal | None:
    return decimal_or_none(paper_min_order_adjustment_payload(fill).get(key))


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
    price_cache: MarketPriceCache | None = None,
    allocation_cache: CopyAllocationCache | None = None,
    source_state_cache: SourceStateCache | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.paper_trading_enabled
        or not resolved_settings.paper_copy_enabled
        or not fills
    ):
        return PaperCopyBatchResult()

    normalized_source_wallet = source_wallet.lower()
    allocations = await copy_allocations(
        session,
        settings=resolved_settings,
        cache=allocation_cache,
    )
    allocation = allocations.get(normalized_source_wallet)
    if allocation is None:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    accounts = await load_paper_accounts_for_source_copy(
        session,
        source_wallet=normalized_source_wallet,
    )
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
                price_cache=price_cache,
                allocation_cache=allocation_cache,
                source_state_cache=source_state_cache,
            )

    source_account_states_task = asyncio.create_task(
        load_source_account_states(
            client=client,
            source_wallet=normalized_source_wallet,
            fills=fills,
            cache=source_state_cache,
        )
    )
    market_prices_task = asyncio.create_task(
        load_execution_market_prices(
            client=client,
            fills=fills,
            settings=resolved_settings,
            price_cache=price_cache,
        )
    )
    source_account_states, market_prices = await asyncio.gather(
        source_account_states_task,
        market_prices_task,
    )
    order_books = await load_paper_order_books(
        client,
        coins={str(fill.get("coin") or "") for fill in fills},
        settings=resolved_settings,
    )

    await lock_paper_source_mutation(session, source_wallet=normalized_source_wallet)
    accounts = await load_paper_accounts_for_source_copy(
        session,
        source_wallet=normalized_source_wallet,
        for_update=True,
    )
    if not accounts:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    risk_mark_prices = dict(market_prices.prices)
    if resolved_settings.paper_copy_loss_limits_enabled:
        open_coins = set(
            (
                await session.scalars(
                    select(PaperPosition.coin)
                    .where(PaperPosition.source_wallet == normalized_source_wallet)
                    .distinct()
                )
            ).all()
        )
        missing_coins = open_coins - set(risk_mark_prices)
        if missing_coins:
            risk_mark_prices.update(
                await load_mark_prices(client=client, coins=missing_coins, price_cache=price_cache)
            )

    processed = 0
    skipped = 0
    realized_pnl = ZERO
    fee_usd = ZERO
    accounts_updated: set[str] = set()

    for fill in sorted_source_fills(fills):
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
            source_state_skip_reason: str | None = "source_account_state_missing"
        else:
            source_perp_equity = source_account_state.perp_equity
            source_leverages = source_account_state.leverage_by_coin
            source_state_skip_reason = source_account_state.skip_reason

        for account in accounts:
            for part in parts:
                if not account.enabled and part_requires_source_equity(part):
                    fill_result = await record_skip(
                        session,
                        account=account,
                        allocation=allocation,
                        fill=fill,
                        part=part,
                        source_perp_equity=source_perp_equity,
                        reason="paper_account_disabled",
                        settings=resolved_settings,
                    )
                    skipped += fill_result.skipped_fills
                    continue

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
                    source_account_state=source_account_state,
                    source_perp_equity=source_perp_equity,
                    source_leverages=source_leverages,
                    market_prices=market_prices,
                    settings=resolved_settings,
                    order_book=order_books.get(str(fill.get("coin") or "")),
                    mark_prices=risk_mark_prices,
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
    price_cache: MarketPriceCache | None = None,
    max_sources: int = 100,
    fill_limit_per_source: int = 1000,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if not resolved_settings.paper_trading_enabled or not resolved_settings.paper_copy_enabled:
        return PaperCopyBatchResult()

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_paper_copy_recovery(
                session,
                source_wallet=source_wallet,
                settings=resolved_settings,
                client=hyperliquid_client,
                price_cache=price_cache,
                max_sources=max_sources,
                fill_limit_per_source=fill_limit_per_source,
            )

    if source_wallet:
        source_wallets = [source_wallet.lower()]
    else:
        await refresh_paper_copy_allocations(session, settings=resolved_settings)
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
            price_cache=price_cache,
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
        select(
            PaperPosition.source_wallet,
            func.max(WalletScore.score).label("score"),
        )
        .outerjoin(WalletScore, WalletScore.wallet_address == PaperPosition.source_wallet)
        .where(PaperPosition.source_wallet != "")
        .group_by(PaperPosition.source_wallet)
        .order_by(
            func.max(WalletScore.score).desc().nulls_last(),
            PaperPosition.source_wallet.asc(),
        )
        .limit(max_sources)
    )
    sources = [str(row.source_wallet).lower() for row in position_result.all() if row.source_wallet]
    remaining = max(max_sources - len(sources), 0)
    if remaining <= 0:
        return unique_strings(sources)

    allocation_result = await session.execute(
        select(
            PaperCopyAllocation.source_wallet,
            func.min(PaperCopyAllocation.rank).label("first_rank"),
        )
        .where(
            PaperCopyAllocation.active.is_(True),
            PaperCopyAllocation.source_wallet != "",
        )
        .group_by(PaperCopyAllocation.source_wallet)
        .order_by(
            func.min(PaperCopyAllocation.rank).asc(),
            PaperCopyAllocation.source_wallet.asc(),
        )
        .limit(remaining)
    )
    sources.extend(
        str(row.source_wallet).lower() for row in allocation_result.all() if row.source_wallet
    )
    return unique_strings(sources)


async def process_paper_copy_recovery_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
    settings: Settings,
    client: HyperliquidClient,
    fill_limit: int,
    price_cache: MarketPriceCache | None = None,
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
            price_cache=price_cache,
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

    anchor = earliest_opened_at if earliest_opened_at is not None else latest_processed_ms
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
    return [source_fill_from_wallet_fill(fill) for fill in result.scalars().all()]


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

    await lock_paper_source_mutation(session, source_wallet=source_wallet)
    accounts = {
        account.key: account
        for account in await load_paper_accounts_for_source_copy(
            session,
            source_wallet=source_wallet,
            for_update=True,
        )
    }
    positions = await load_open_paper_positions_for_source(
        session,
        source_wallet=source_wallet,
        for_update=True,
    )
    if not positions:
        return PaperCopyBatchResult()

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


async def load_open_paper_positions_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
    for_update: bool = False,
) -> list[PaperPosition]:
    stmt = (
        select(PaperPosition)
        .where(PaperPosition.source_wallet == source_wallet)
        .order_by(PaperPosition.opened_at.asc(), PaperPosition.account_key.asc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def load_paper_copy_allocations_for_source(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> dict[str, PaperSourceAllocation]:
    result = await session.execute(
        select(PaperCopyAllocation, WatchedWallet.label)
        .outerjoin(WatchedWallet, WatchedWallet.address == PaperCopyAllocation.source_wallet)
        .where(PaperCopyAllocation.source_wallet == source_wallet)
    )
    rows: dict[str, PaperSourceAllocation] = {}
    for allocation, source_label in result.all():
        rows[allocation.account_key] = PaperSourceAllocation(
            source_wallet=allocation.source_wallet,
            source_label=str(source_label) if source_label else None,
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
) -> dict[str, SourceAccountState]:
    states: dict[str, SourceAccountState] = {}
    unified_equity_cache: UnifiedEquityCache = {}
    for dex in sorted({dex_from_coin(position.coin) for position in positions}):
        states[dex] = await load_source_account_state(
            client=client,
            source_wallet=source_wallet,
            dex=dex,
            unified_equity_cache=unified_equity_cache,
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
        side=parse_trade_side(position.side),
        source_size=ZERO,
        source_notional_usd=ZERO,
        sequence_index=0,
        close_ratio=ONE,
    )
    execution_context = ExecutionContext(
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
            opened_at=position.opened_at,
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
        skip_reasons=combine_skip_reasons(left.skip_reasons, right.skip_reasons),
    )


def combine_skip_reasons(
    left: dict[str, int],
    right: dict[str, int],
) -> dict[str, int]:
    reasons = dict(left)
    for reason, count in right.items():
        reasons[reason] = reasons.get(reason, 0) + count
    return reasons


def normalized_wallet_addresses(source_wallets: Iterable[str | None]) -> set[str]:
    wallets: set[str] = set()
    for source_wallet in source_wallets:
        if not source_wallet:
            continue
        try:
            wallets.add(normalize_wallet_address(str(source_wallet)))
        except ValueError:
            continue
    return wallets


def wallet_monitoring_snapshot_max_gap_seconds(settings: Settings) -> int:
    refresh_seconds = int(getattr(settings, "realtime_subscription_refresh_seconds", 15) or 15)
    return max(refresh_seconds * 3, MONITORING_MIN_SNAPSHOT_GAP_SECONDS)


def monitored_delta_seconds(
    previous_at: datetime | None,
    observed_at: datetime,
    *,
    max_gap_seconds: int,
) -> int:
    if previous_at is None:
        return 0
    elapsed_seconds = int((observed_at - previous_at).total_seconds())
    if elapsed_seconds <= 0:
        return 0
    return min(elapsed_seconds, max_gap_seconds)


def monitored_until(
    previous_at: datetime | None,
    observed_at: datetime,
    *,
    max_gap_seconds: int,
) -> datetime:
    if previous_at is None:
        return observed_at
    delta_seconds = monitored_delta_seconds(
        previous_at,
        observed_at,
        max_gap_seconds=max_gap_seconds,
    )
    return previous_at + timedelta(seconds=delta_seconds)


def apply_wallet_monitoring_snapshot(
    stats_by_wallet: dict[str, WalletMonitoringStat],
    *,
    monitored_wallets: set[str],
    observed_at: datetime,
    max_gap_seconds: int,
) -> None:
    for wallet in monitored_wallets:
        stat = stats_by_wallet.get(wallet)
        if stat is None:
            continue
        if stat.current_monitoring_started_at is None:
            stat.current_monitoring_started_at = observed_at
            stat.last_monitored_at = observed_at
            stat.updated_at = observed_at
            if stat.first_monitored_at is None:
                stat.first_monitored_at = observed_at
            continue
        if stat.first_monitored_at is None:
            stat.first_monitored_at = observed_at
        delta_seconds = monitored_delta_seconds(
            stat.last_monitored_at or stat.current_monitoring_started_at,
            observed_at,
            max_gap_seconds=max_gap_seconds,
        )
        stat.total_monitored_seconds = int(stat.total_monitored_seconds or 0) + delta_seconds
        stat.last_monitored_at = observed_at
        stat.updated_at = observed_at

    for wallet, stat in stats_by_wallet.items():
        if wallet in monitored_wallets or stat.current_monitoring_started_at is None:
            continue
        previous_at = stat.last_monitored_at or stat.current_monitoring_started_at
        delta_seconds = monitored_delta_seconds(
            previous_at,
            observed_at,
            max_gap_seconds=max_gap_seconds,
        )
        stat.total_monitored_seconds = int(stat.total_monitored_seconds or 0) + delta_seconds
        stat.last_monitored_at = monitored_until(
            previous_at,
            observed_at,
            max_gap_seconds=max_gap_seconds,
        )
        stat.current_monitoring_started_at = None
        stat.updated_at = observed_at


async def record_wallet_monitoring_snapshot(
    session: AsyncSession,
    *,
    monitored_wallets: Iterable[str],
    settings: Settings,
    observed_at: datetime | None = None,
) -> None:
    observed = observed_at or datetime.now(UTC)
    monitored = normalized_wallet_addresses(monitored_wallets)
    conditions = [WalletMonitoringStat.current_monitoring_started_at.is_not(None)]
    if monitored:
        conditions.append(WalletMonitoringStat.wallet_address.in_(monitored))
    result = await session.execute(
        select(WalletMonitoringStat).where(or_(*conditions)).with_for_update()
    )
    existing_stats = list(result.scalars().all())
    stats_by_wallet = {
        stat.wallet_address.lower(): stat for stat in existing_stats if stat.wallet_address
    }
    missing_wallets = sorted(monitored - set(stats_by_wallet))
    if missing_wallets:
        await session.execute(
            insert(WalletMonitoringStat)
            .values(
                [
                    {
                        "wallet_address": wallet,
                        "first_monitored_at": observed,
                        "current_monitoring_started_at": observed,
                        "last_monitored_at": observed,
                        "total_monitored_seconds": 0,
                        "created_at": observed,
                        "updated_at": observed,
                    }
                    for wallet in missing_wallets
                ]
            )
            .on_conflict_do_nothing(index_elements=["wallet_address"])
        )
    apply_wallet_monitoring_snapshot(
        stats_by_wallet,
        monitored_wallets=monitored,
        observed_at=observed,
        max_gap_seconds=wallet_monitoring_snapshot_max_gap_seconds(settings),
    )


def wallet_monitoring_summary(
    stat: WalletMonitoringStat,
    *,
    now: datetime,
    max_gap_seconds: int,
) -> WalletMonitoringSummary:
    monitored_seconds = int(stat.total_monitored_seconds or 0)
    if stat.current_monitoring_started_at is not None:
        monitored_seconds += monitored_delta_seconds(
            stat.last_monitored_at or stat.current_monitoring_started_at,
            now,
            max_gap_seconds=max_gap_seconds,
        )
    return WalletMonitoringSummary(
        first_monitored_at=stat.first_monitored_at,
        current_monitoring_started_at=stat.current_monitoring_started_at,
        last_monitored_at=stat.last_monitored_at,
        monitored_seconds=monitored_seconds,
    )


async def load_wallet_monitoring_stats(
    session: AsyncSession,
    *,
    source_wallets: Iterable[str | None],
    settings: Settings,
    now: datetime,
) -> dict[str, WalletMonitoringSummary]:
    wallets = sorted(normalized_wallet_addresses(source_wallets))
    if not wallets:
        return {}
    result = await session.execute(
        select(WalletMonitoringStat).where(WalletMonitoringStat.wallet_address.in_(wallets))
    )
    max_gap_seconds = wallet_monitoring_snapshot_max_gap_seconds(settings)
    return {
        stat.wallet_address.lower(): wallet_monitoring_summary(
            stat,
            now=now,
            max_gap_seconds=max_gap_seconds,
        )
        for stat in result.scalars().all()
        if stat.wallet_address
    }


async def refresh_paper_copy_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
) -> dict[str, PaperSourceAllocation]:
    await ensure_open_paper_sources_watched(session)
    accounts = await sync_paper_trading_accounts(session, settings=settings)
    account_keys = [account.key for account in accounts]
    source_allocations = await load_paper_source_allocations(session, settings=settings)
    allocation_refresh_at = datetime.now(UTC)
    active_source_wallets = {
        allocation.source_wallet.lower()
        for allocation in source_allocations
        if allocation.active and allocation.source_wallet
    }
    for source_wallet in sorted(active_source_wallets):
        await session.execute(
            insert(WatchedWallet)
            .values(
                address=source_wallet,
                enabled=True,
                eligible=False,
                copy_enabled=False,
                polling_tier="pool",
                notes="Created for the live-copy source eligibility epoch.",
            )
            .on_conflict_do_nothing(index_elements=["address"])
        )
    if active_source_wallets:
        await session.execute(
            update(WatchedWallet)
            .where(
                WatchedWallet.address.in_(active_source_wallets),
                WatchedWallet.copy_eligibility_started_at.is_(None),
            )
            .values(copy_eligibility_started_at=allocation_refresh_at)
        )
    inactive_source_epoch_update = update(WatchedWallet).where(
        WatchedWallet.copy_eligibility_started_at.is_not(None)
    )
    if active_source_wallets:
        inactive_source_epoch_update = inactive_source_epoch_update.where(
            WatchedWallet.address.not_in(active_source_wallets)
        )
    await session.execute(inactive_source_epoch_update.values(copy_eligibility_started_at=None))
    realtime_monitoring = await load_realtime_subscription_state(
        session,
        stale_after_seconds=realtime_subscription_stale_after_seconds(
            settings.realtime_subscription_refresh_seconds
        ),
    )
    await record_wallet_monitoring_snapshot(
        session,
        monitored_wallets=realtime_monitoring.monitored_wallets,
        settings=settings,
    )
    allocated_sources = {
        allocation.source_wallet.lower()
        for allocation in source_allocations
        if allocation.source_wallet
    }
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
                max_total_allocation_pct=settings.trading_copy_max_total_allocation_pct,
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

    if account_keys:
        no_longer_allocated = update(PaperCopyAllocation).where(
            PaperCopyAllocation.account_key.in_(account_keys)
        )
        if allocated_sources:
            no_longer_allocated = no_longer_allocated.where(
                PaperCopyAllocation.source_wallet.not_in(allocated_sources)
            )
        await session.execute(no_longer_allocated.values(active=False))

    return {allocation.source_wallet.lower(): allocation for allocation in source_allocations}


def store_copy_allocations(
    cache: CopyAllocationCache | None,
    allocations: dict[str, PaperSourceAllocation],
) -> None:
    if cache is None:
        return
    active_sources = frozenset(
        source for source, allocation in allocations.items() if allocation.active
    )
    cache.store(allocations, signature=active_sources)


async def copy_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
    cache: CopyAllocationCache | None = None,
) -> dict[str, PaperSourceAllocation]:
    """Use the periodically refreshed allocation snapshot when it is still fresh."""

    if cache is not None:
        cached = cache.fresh()
        if cached is not None:
            return cached
    allocations = await refresh_paper_copy_allocations(session, settings=settings)
    store_copy_allocations(cache, allocations)
    return allocations


async def ensure_open_paper_sources_watched(session: AsyncSession) -> None:
    open_sources = open_copy_source_select().subquery("open_copy_sources")
    open_source_query = select(open_sources.c.source_wallet).distinct()
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
            open_sources.c.source_wallet,
            literal(True),
            literal(False),
            literal(False),
            literal("pool"),
            literal("Restored automatically because copy positions are still open."),
        )
        .where(
            ~select(WatchedWallet.id)
            .where(WatchedWallet.address == open_sources.c.source_wallet)
            .exists()
        )
        .distinct(),
    )
    await session.execute(insert_stmt.on_conflict_do_nothing(index_elements=["address"]))
    await session.execute(
        update(WatchedWallet)
        .where(
            WatchedWallet.address.in_(open_source_query),
            WatchedWallet.polling_tier == "exit_only",
        )
        .values(enabled=True, polling_tier="pool")
    )


def open_copy_source_select() -> Any:
    paper_sources = select(func.lower(PaperPosition.source_wallet).label("source_wallet")).where(
        PaperPosition.source_wallet != ""
    )
    return paper_sources.union_all(live_open_copy_source_select())


def live_open_copy_source_select() -> Any:
    position_sources = select(
        func.lower(TradingPosition.source_wallet).label("source_wallet")
    ).where(
        TradingPosition.account_type == "live",
        TradingPosition.source_wallet != "",
        TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        TradingPosition.source_wallet != LIVE_MANUAL_TEST_SOURCE,
        TradingPosition.size > LIVE_POSITION_EPSILON,
    )
    unresolved_order_sources = select(
        func.lower(TradingOrder.source_wallet).label("source_wallet")
    ).where(
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet != "",
        TradingOrder.source_wallet != LIVE_EXCHANGE_SOURCE,
        TradingOrder.source_wallet != LIVE_MANUAL_TEST_SOURCE,
        live_copy_unresolved_order_predicate(),
    )
    unresolved_exit_sources = select(
        func.lower(LiveCopyFillState.source_wallet).label("source_wallet")
    ).where(live_copy_unresolved_exit_predicate())
    return position_sources.union_all(unresolved_order_sources, unresolved_exit_sources)


def select_realtime_slot_sources(
    *,
    open_source_wallets: Iterable[str],
    live_open_source_wallets: set[str],
    candidate_source_wallets: Iterable[str],
    live_trading_enabled: bool,
    max_realtime_slots: int,
) -> list[str]:
    if max_realtime_slots <= 0:
        return []

    normalized_live_sources = {source.lower() for source in live_open_source_wallets if source}
    priority_open_sources = (
        (
            source
            for source in open_source_wallets
            if source and source.lower() in normalized_live_sources
        )
        if live_trading_enabled
        else open_source_wallets
    )
    selected: list[str] = []
    for source in (*priority_open_sources, *candidate_source_wallets):
        normalized_source = source.lower()
        if normalized_source and normalized_source not in selected:
            selected.append(normalized_source)
        if len(selected) >= max_realtime_slots:
            break
    return selected


async def sync_paper_trading_accounts(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[PaperTradingAccount]:
    account_configs = list(settings.paper_copy_accounts)
    account_keys = [account.key for account in account_configs]
    existing_result = await session.execute(
        select(PaperTradingAccount).where(PaperTradingAccount.key.in_(account_keys))
    )
    existing_by_key = {account.key: account for account in existing_result.scalars().all()}

    for account_config in settings.paper_copy_accounts:
        config_payload = account_config.model_dump(mode="json")
        existing_account = existing_by_key.get(account_config.key)
        account_enabled = account_config.enabled
        if existing_account is not None and existing_account.config_payload == config_payload:
            account_enabled = existing_account.enabled

        stmt = insert(PaperTradingAccount).values(
            key=account_config.key,
            label=account_config.label,
            starting_balance_usd=account_config.starting_balance_usd,
            cash_balance_usd=account_config.starting_balance_usd,
            equity_usd=account_config.starting_balance_usd,
            enabled=account_enabled,
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
        select(PaperTradingAccount).order_by(
            PaperTradingAccount.created_at.asc(),
            PaperTradingAccount.key.asc(),
        )
    )
    accounts = list(result.scalars().all())
    await sync_paper_trading_account_mirrors(
        session,
        accounts=accounts,
        network=settings.hyperliquid_network,
    )
    return accounts


async def create_paper_trading_account(
    session: AsyncSession,
    *,
    account_type: str,
    starting_balance_usd: Decimal,
    settings: Settings,
) -> PaperTradingAccount:
    if account_type == "live":
        raise PaperAccountCreateUnavailableError("Live accounts are not available yet.")
    if account_type != "paper":
        raise PaperAccountCreateError("Unsupported account type.")

    starting_balance = normalize_paper_account_starting_balance(starting_balance_usd)
    await sync_paper_trading_accounts(session, settings=settings)
    key, label = await next_paper_account_identity(
        session,
        starting_balance_usd=starting_balance,
    )
    account = PaperTradingAccount(
        key=key,
        label=label,
        starting_balance_usd=starting_balance,
        cash_balance_usd=starting_balance,
        equity_usd=starting_balance,
        realized_pnl_usd=ZERO,
        fee_usd=ZERO,
        enabled=False,
        config_payload={
            "account_type": "paper",
            "source": "dashboard",
        },
    )
    session.add(account)
    await session.flush()
    await sync_paper_trading_account_mirrors(
        session,
        accounts=[account],
        network=settings.hyperliquid_network,
    )
    return account


async def delete_paper_trading_account(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
) -> None:
    await sync_paper_trading_accounts(session, settings=settings)
    account = await session.scalar(
        select(PaperTradingAccount).where(PaperTradingAccount.key == account_key).with_for_update()
    )
    if account is None:
        raise PaperAccountDeleteNotFoundError("Paper account was not found.")

    await session.execute(
        delete(PaperCopyAllocation).where(PaperCopyAllocation.account_key == account_key)
    )
    await session.execute(delete(PaperPosition).where(PaperPosition.account_key == account_key))
    await session.execute(delete(PaperCopyFill).where(PaperCopyFill.account_key == account_key))
    await session.execute(
        delete(TradingFill).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "paper",
        )
    )
    await session.execute(
        delete(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "paper",
        )
    )
    await session.execute(
        delete(TradingPosition).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "paper",
        )
    )
    await session.execute(
        delete(TradingAccount).where(
            TradingAccount.key == account_key,
            TradingAccount.account_type == "paper",
        )
    )
    await session.delete(account)
    await session.flush()


async def next_paper_account_identity(
    session: AsyncSession,
    *,
    starting_balance_usd: Decimal,
) -> tuple[str, str]:
    amount_label = format_paper_account_amount(starting_balance_usd)
    base_key = f"paper_{paper_account_amount_slug(starting_balance_usd)}"
    existing_result = await session.execute(
        select(PaperTradingAccount.key).where(PaperTradingAccount.key.like(f"{base_key}%"))
    )
    existing_keys = {str(key) for key in existing_result.scalars().all()}
    if base_key not in existing_keys:
        return base_key, f"Paper {amount_label} USD"

    suffix = 2
    while f"{base_key}_{suffix}" in existing_keys:
        suffix += 1
    return f"{base_key}_{suffix}", f"Paper {amount_label} USD #{suffix}"


def normalize_paper_account_starting_balance(value: Decimal) -> Decimal:
    try:
        normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise PaperAccountCreateError("Starting balance is invalid.") from exc
    if normalized <= ZERO:
        raise PaperAccountCreateError("Starting balance must be at least 0.01 USD.")
    return normalized


def format_paper_account_amount(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    formatted = f"{normalized:,.2f}"
    return formatted[:-3] if formatted.endswith(".00") else formatted


def paper_account_amount_slug(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    slug = format(normalized, "f").rstrip("0").rstrip(".").replace(".", "_")
    return slug or "0"


async def load_paper_source_label(session: AsyncSession, source_wallet: str) -> str | None:
    labels = await load_paper_source_labels(session, source_wallets=[source_wallet])
    return labels.get(source_wallet.lower())


async def load_paper_source_labels(
    session: AsyncSession,
    *,
    source_wallets: list[str],
) -> dict[str, str]:
    normalized = sorted({wallet.lower() for wallet in source_wallets if wallet})
    if not normalized:
        return {}

    result = await session.execute(
        select(WatchedWallet.address, WatchedWallet.label).where(
            WatchedWallet.address.in_(normalized)
        )
    )
    return {
        str(address).lower(): str(label) for address, label in result.all() if address and label
    }


async def reset_paper_trading_account_balance(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
) -> PaperTradingAccount:
    await sync_paper_trading_accounts(session, settings=settings)
    account = await session.scalar(
        select(PaperTradingAccount).where(PaperTradingAccount.key == account_key).with_for_update()
    )
    if account is None:
        raise PaperAccountResetNotFoundError("Paper account was not found.")
    if account.enabled:
        raise PaperAccountResetUnavailableError(
            "Stop the paper account before resetting its balance."
        )
    open_positions = await session.scalar(
        select(func.count(PaperPosition.id)).where(PaperPosition.account_key == account.key)
    )
    if int(open_positions or 0) > 0:
        raise PaperAccountResetUnavailableError(
            "Close all paper positions before resetting the account balance."
        )

    previous_balance = account.cash_balance_usd
    previous_equity = account.equity_usd

    account_config = next(
        (config for config in settings.paper_copy_accounts if config.key == account_key),
        None,
    )
    if account_config is not None:
        config_payload = account_config.model_dump(mode="json")
        account.label = account_config.label
        account.starting_balance_usd = account_config.starting_balance_usd
        account.config_payload = config_payload

    account.cash_balance_usd = account.starting_balance_usd
    account.equity_usd = account.starting_balance_usd
    account.realized_pnl_usd = ZERO
    account.fee_usd = ZERO
    account.funding_usd = ZERO
    clear_paper_account_exit_only(account)
    # Loss limits restart from the new balance.
    await session.execute(
        delete(PaperAccountEquitySnapshot).where(
            PaperAccountEquitySnapshot.account_key == account.key
        )
    )
    session.add(
        AuditLog(
            actor="dashboard",
            action="paper_account.balance_reset",
            payload={
                "accountKey": account.key,
                "previousCashBalanceUsd": str(previous_balance),
                "previousEquityUsd": str(previous_equity),
                "startingBalanceUsd": str(account.starting_balance_usd),
            },
        )
    )
    await sync_paper_trading_account_mirrors(
        session,
        accounts=[account],
        network=settings.hyperliquid_network,
    )
    return account


async def set_paper_trading_account_enabled(
    session: AsyncSession,
    *,
    account_key: str,
    enabled: bool,
    settings: Settings,
) -> PaperTradingAccount:
    await sync_paper_trading_accounts(session, settings=settings)
    account = await session.scalar(
        select(PaperTradingAccount).where(PaperTradingAccount.key == account_key).with_for_update()
    )
    if account is None:
        raise PaperAccountControlNotFoundError("Paper account was not found.")

    account.enabled = enabled
    if enabled:
        # Starting the account again lifts a tripped loss limit, as for live accounts.
        clear_paper_account_exit_only(account)
    await sync_paper_trading_account_mirrors(
        session,
        accounts=[account],
        network=settings.hyperliquid_network,
    )
    return account


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
            WatchedWallet.label.label("source_label"),
        )
        .join(WatchedWallet, WatchedWallet.address == ranked_pool.c.address)
        .where(*candidate_filters)
        .order_by(ranked_pool.c.pool_rank.asc())
        .limit(settings.trading_copy_top_wallet_count)
    )
    candidate_rows = list(candidate_result.mappings().all())
    open_sources = open_copy_source_select().subquery("open_copy_sources")
    open_source_result = await session.execute(
        select(
            open_sources.c.source_wallet,
            WatchedWallet.label.label("source_label"),
            WalletScore.score.label("score"),
            WalletScore.current_drawdown_status.label("current_drawdown_status"),
            WatchedWallet.enabled.label("wallet_enabled"),
            WatchedWallet.polling_tier.label("polling_tier"),
            ranked_pool.c.pool_rank.label("pool_rank"),
            func.max(PaperCopyAllocation.allocation_pct).label("allocation_pct"),
        )
        .outerjoin(WatchedWallet, WatchedWallet.address == open_sources.c.source_wallet)
        .outerjoin(WalletScore, WalletScore.wallet_address == open_sources.c.source_wallet)
        .outerjoin(ranked_pool, ranked_pool.c.address == open_sources.c.source_wallet)
        .outerjoin(
            PaperCopyAllocation,
            PaperCopyAllocation.source_wallet == open_sources.c.source_wallet,
        )
        .group_by(
            open_sources.c.source_wallet,
            WatchedWallet.label,
            WalletScore.score,
            WalletScore.current_drawdown_status,
            WatchedWallet.enabled,
            WatchedWallet.polling_tier,
            ranked_pool.c.pool_rank,
        )
        .order_by(
            ranked_pool.c.pool_rank.asc().nulls_last(),
            WalletScore.score.desc().nulls_last(),
            open_sources.c.source_wallet.asc(),
        )
    )
    open_source_rows = list(open_source_result.mappings().all())
    live_open_source_result = await session.execute(live_open_copy_source_select())
    live_open_source_wallets = {
        str(source_wallet).lower()
        for source_wallet in live_open_source_result.scalars().all()
        if source_wallet
    }
    slot_sources = select_realtime_slot_sources(
        open_source_wallets=(str(row["source_wallet"]) for row in open_source_rows),
        live_open_source_wallets=live_open_source_wallets,
        candidate_source_wallets=(str(row["address"]) for row in candidate_rows),
        live_trading_enabled=settings.live_trading_enabled,
        max_realtime_slots=settings.max_realtime_wallets,
    )

    slot_source_set = set(slot_sources)
    allocations: list[PaperSourceAllocation] = []
    allocation_pct = copy_allocation_pct(settings)
    for row in candidate_rows:
        source_wallet = str(row["address"]).lower()
        pool_rank = int(row["pool_rank"])
        has_realtime_slot = source_wallet in slot_source_set
        allocations.append(
            PaperSourceAllocation(
                source_wallet=source_wallet,
                source_label=str(row["source_label"]) if row["source_label"] else None,
                rank=pool_rank,
                pool_rank=pool_rank,
                score=row["score"],
                allocation_pct=allocation_pct,
                active=has_realtime_slot,
                has_realtime_slot=has_realtime_slot,
                status_reason=(
                    "copy_candidate" if has_realtime_slot else "waiting_for_realtime_slot"
                ),
            )
        )
    allocation_sources = {allocation.source_wallet for allocation in allocations}
    retained_rank = settings.trading_copy_top_wallet_count + 1
    for row in open_source_rows:
        source_wallet = str(row["source_wallet"]).lower()
        if not source_wallet or source_wallet in allocation_sources:
            continue
        retained_allocation_pct = decimal_or_none(row["allocation_pct"])
        retained_pool_rank = int_or_none(row["pool_rank"])
        has_realtime_slot = source_wallet in slot_source_set
        allocations.append(
            PaperSourceAllocation(
                source_wallet=source_wallet,
                source_label=str(row["source_label"]) if row["source_label"] else None,
                rank=retained_pool_rank or retained_rank,
                pool_rank=retained_pool_rank,
                score=row["score"],
                allocation_pct=(
                    retained_allocation_pct
                    if retained_allocation_pct is not None and retained_allocation_pct > ZERO
                    else copy_allocation_pct(settings)
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
    if settings.scoring_current_drawdown_enabled and row["current_drawdown_status"] != "ok":
        return "current_drawdown_blocked"
    if pool_rank is not None and pool_rank > settings.trading_copy_top_wallet_count:
        return "outside_copy_top_wallet_count"
    if not has_realtime_slot:
        return "waiting_for_realtime_slot"
    return "existing_exposure_only"


async def load_enabled_paper_accounts(
    session: AsyncSession,
    *,
    for_update: bool = False,
) -> list[PaperTradingAccount]:
    stmt = (
        select(PaperTradingAccount)
        .where(PaperTradingAccount.enabled.is_(True))
        .order_by(PaperTradingAccount.key.asc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def load_paper_accounts_for_source_copy(
    session: AsyncSession,
    *,
    source_wallet: str,
    for_update: bool = False,
) -> list[PaperTradingAccount]:
    normalized_source_wallet = source_wallet.lower()
    open_exposure_exists = (
        select(PaperPosition.id)
        .where(
            PaperPosition.account_key == PaperTradingAccount.key,
            PaperPosition.source_wallet == normalized_source_wallet,
        )
        .exists()
    )
    stmt = (
        select(PaperTradingAccount)
        .where(or_(PaperTradingAccount.enabled.is_(True), open_exposure_exists))
        .order_by(PaperTradingAccount.key.asc())
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def apply_paper_fill_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    source_account_state: SourceAccountState | None = None,
    order_book: OrderBook | None = None,
    mark_prices: dict[str, Decimal] | None = None,
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
            source_account_state=source_account_state,
            order_book=order_book,
            mark_prices=mark_prices,
        )
    return await apply_close_part(
        session,
        account=account,
        allocation=allocation,
        fill=fill,
        part=part,
        source_account_state=source_account_state,
        source_perp_equity=source_perp_equity,
        source_leverages=source_leverages,
        market_prices=market_prices,
        settings=settings,
        order_book=order_book,
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
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    source_account_state: SourceAccountState | None = None,
    order_book: OrderBook | None = None,
    mark_prices: dict[str, Decimal] | None = None,
) -> PaperCopyBatchResult:
    source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)

    async def skip(
        reason: str,
        execution_context: ExecutionContext | None = None,
    ) -> PaperCopyBatchResult:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason=PAPER_DECISION_REASONS.get(reason, reason),
            settings=settings,
            execution_context=execution_context,
            leverage=source_leverage,
        )

    if not account.enabled:
        return await skip("paper_account_disabled")
    if source_fill_age_exceeds_entry_limit(fill, settings=settings):
        return await skip("source_fill_too_old")

    coin = str(fill.get("coin") or "")
    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    copy_position = (
        CopyPosition(side=position.side, size=position.size) if position is not None else None
    )
    precheck_reason = entry_precheck(
        part=part,
        position=copy_position,
        allocation_active=allocation.active,
        source_price=decimal_or_zero(fill.get("price")),
    )
    if precheck_reason is not None:
        return await skip(precheck_reason)
    risk_reason = await paper_entry_risk_reason(
        session,
        account=account,
        source_wallet=allocation.source_wallet,
        mark_prices=mark_prices if mark_prices is not None else market_prices.prices,
        settings=settings,
    )
    if risk_reason is not None:
        return await skip(risk_reason)

    decision = decide_entry(
        fill=fill,
        part=part,
        position=copy_position,
        allocation_active=allocation.active,
        allocation_pct=allocation.allocation_pct,
        account_equity_usd=account.equity_usd,
        source_equity_usd=source_perp_equity,
        source_leverage=source_leverage,
        market_prices=market_prices,
        source_exposure_usd=await open_notional_for_source(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
        ),
        account_exposure_usd=await open_notional_for_account(session, account_key=account.key),
        settings=settings,
        min_order_notional_usd=settings.trading_copy_min_order_notional_usd,
    )
    execution_context = decision.execution_context
    if decision.reason is not None or execution_context is None or decision.sizing is None:
        return await skip(decision.reason or "execution_price_unavailable", execution_context)

    price = execution_context.execution_price
    paper_size = decision.size
    book_fill = simulate_paper_book_fill(
        order_book,
        is_buy=trade_is_buy(side=part.side, reduce_only=False),
        size=paper_size,
        observed_price=execution_context.observed_price,
        settings=settings,
    )
    if book_fill is not None:
        if book_fill.average_price is None:
            return await skip("paper_ioc_unfilled", execution_context)
        paper_size = book_fill.filled_size
        price = book_fill.average_price
    notional_usd = paper_size * price
    margin_usd = margin_from_notional(notional_usd, source_leverage)
    min_order_adjustment = (
        PaperMinOrderAdjustment(
            original_notional_usd=decision.sizing.desired_notional_usd,
            adjusted_notional_usd=notional_usd,
            min_order_notional_usd=settings.trading_copy_min_order_notional_usd,
        )
        if decision.sizing.rounded_up
        else None
    )
    fee = notional_usd * settings.paper_copy_fee_rate
    filled_at = fill_datetime(fill)
    action = decision.action
    trade_intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="paper",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=parse_trade_action(action),
        side=part.side,
        size=paper_size,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        leverage=source_leverage,
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=decision.allocation_usd,
        source_perp_equity_usd=source_perp_equity,
        source_exposure_pct=decision.source_exposure_pct,
        created_at=filled_at,
    )

    if position is None:
        position = PaperPosition(
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            coin=coin,
            side=part.side,
            size=paper_size,
            entry_price=price,
            notional_usd=notional_usd,
            leverage=source_leverage,
            margin_usd=margin_usd,
            margin_mode=(
                resolve_coin_margin_mode(source_account_state.margin_mode_by_coin, coin)
                if source_account_state is not None
                else None
            )
            or "cross",
            fee_usd=fee,
            opened_at=filled_at,
        )
        session.add(position)
    else:
        previous_notional = position.notional_usd
        previous_margin = position.margin_usd
        next_size = position.size + paper_size
        position.entry_price = weighted_average_price(
            position.entry_price,
            position.size,
            price,
            paper_size,
        )
        position.size = next_size
        position.notional_usd = previous_notional + notional_usd
        position.margin_usd = previous_margin + margin_usd
        position.leverage = effective_leverage(
            notional_usd=position.notional_usd,
            margin_usd=position.margin_usd,
            fallback=source_leverage,
        )
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
            allocation_usd=decision.allocation_usd,
            settings=settings,
            execution_context=execution_context,
            trade_intent=trade_intent,
            min_order_adjustment=min_order_adjustment,
            book_fill=book_fill,
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
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    source_account_state: SourceAccountState | None = None,
    order_book: OrderBook | None = None,
) -> PaperCopyBatchResult:
    coin = str(fill.get("coin") or "")
    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    leverage = (
        safe_leverage(position.leverage)
        if position is not None
        else leverage_for_fill(fill=fill, source_leverages=source_leverages)
    )

    async def skip(
        reason: str,
        execution_context: ExecutionContext | None = None,
        requested_close_size: Decimal | None = None,
    ) -> PaperCopyBatchResult:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            reason=PAPER_DECISION_REASONS.get(reason, reason),
            settings=settings,
            execution_context=execution_context,
            leverage=leverage,
            requested_close_size=requested_close_size,
        )

    if decimal_or_zero(fill.get("price")) <= ZERO:
        return await skip("invalid_price")
    min_order_notional = settings.trading_copy_min_order_notional_usd
    decision = decide_close(
        fill=fill,
        part=part,
        position=(
            CopyPosition(side=position.side, size=position.size) if position is not None else None
        ),
        available_size=position.size if position is not None else ZERO,
        source_account_state=source_account_state,
        market_prices=market_prices,
        settings=settings,
        min_order_notional_usd=min_order_notional,
    )
    aggregated_skips: list[PaperCopyFill] = []
    if decision.needs_aggregation and position is not None:
        aggregated_skips = await load_paper_below_min_close_skips(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            coin=coin,
            side=part.side,
            since=position.opened_at,
        )
        decision = aggregate_below_min_close(
            decision,
            previous_skipped_size=sum(
                (paper_requested_close_size(item) for item in aggregated_skips),
                ZERO,
            ),
            available_size=position.size,
            min_order_notional_usd=min_order_notional,
        )
    execution_context = decision.execution_context
    if decision.reason is not None or execution_context is None or position is None:
        return await skip(
            decision.reason or "execution_price_unavailable",
            execution_context,
            requested_close_size=decision.close_size if decision.close_size > ZERO else None,
        )

    price = execution_context.execution_price
    close_size = decision.close_size
    book_fill = simulate_paper_book_fill(
        order_book,
        is_buy=trade_is_buy(side=part.side, reduce_only=True),
        size=close_size,
        observed_price=execution_context.observed_price,
        settings=settings,
    )
    if book_fill is not None:
        if book_fill.average_price is None:
            return await skip("paper_ioc_unfilled", execution_context)
        close_size = book_fill.filled_size
        price = book_fill.average_price
    notional_usd = close_size * price
    margin_usd = margin_from_notional(notional_usd, leverage)
    source_exposure_pct = (
        part.source_notional_usd / source_perp_equity if source_perp_equity > ZERO else None
    )
    realized_pnl = realized_pnl_for_close(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=price,
        size=close_size,
    )
    fee = notional_usd * settings.paper_copy_fee_rate
    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    filled_at = fill_datetime(fill)
    opened_at = position.opened_at
    position.realized_pnl_usd += realized_pnl
    position.fee_usd += fee
    previous_size = position.size
    previous_notional = position.notional_usd
    previous_margin = position.margin_usd
    remaining_size = previous_size - close_size
    if remaining_size <= POSITION_EPSILON:
        await session.delete(position)
        action = "close" if part.action in {"close", "reduce"} else part.action
    else:
        remaining_ratio = remaining_size / previous_size if previous_size > ZERO else ZERO
        position.size = remaining_size
        position.notional_usd = previous_notional * remaining_ratio
        position.margin_usd = previous_margin * remaining_ratio
        position.leverage = effective_leverage(
            notional_usd=position.notional_usd,
            margin_usd=position.margin_usd,
            fallback=leverage,
        )
        action = "reduce" if part.action == "close" else part.action

    trade_intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="paper",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=parse_trade_action(action),
        side=part.side,
        size=close_size,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        leverage=leverage,
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=allocation_usd,
        source_perp_equity_usd=source_perp_equity if source_perp_equity > ZERO else None,
        source_exposure_pct=source_exposure_pct,
        created_at=filled_at,
    )
    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    close_fill = paper_copy_fill(
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
        trade_intent=trade_intent,
        opened_at=opened_at,
        book_fill=book_fill,
    )
    session.add(close_fill)
    if decision.aggregated:
        mark_paper_close_skips_aggregated(aggregated_skips, into=close_fill)
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


def simulate_paper_book_fill(
    order_book: OrderBook | None,
    *,
    is_buy: bool,
    size: Decimal,
    observed_price: Decimal,
    settings: Settings,
) -> IocFill | None:
    """Fill like a live IOC order at the live limit slippage; None without a book."""

    if not settings.paper_copy_order_book_fills or order_book is None:
        return None
    return simulate_ioc_fill(
        order_book,
        is_buy=is_buy,
        size=size,
        limit_price=ioc_limit_price(
            observed_price,
            is_buy=is_buy,
            slippage_bps=settings.live_trading_limit_slippage_bps,
        ),
    )


async def load_paper_order_books(
    client: HyperliquidClient,
    *,
    coins: set[str],
    settings: Settings,
) -> dict[str, OrderBook]:
    """Current order books for the batch; coins without a book fall back to mid pricing."""

    if not settings.paper_copy_order_book_fills:
        return {}

    async def load(coin: str) -> tuple[str, OrderBook | None]:
        try:
            return coin, parse_order_book(await client.l2_book(coin=coin))
        except Exception as exc:
            logger.warning("paper order book fetch failed coin=%s error=%s", coin, exc)
            return coin, None

    results = await asyncio.gather(*(load(coin) for coin in sorted(coin for coin in coins if coin)))
    return {coin: book for coin, book in results if book is not None}


async def load_paper_below_min_close_skips(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    side: str,
    since: datetime,
) -> list[PaperCopyFill]:
    """Partial closes skipped below the minimum order since the position opened."""

    result = await session.scalars(
        select(PaperCopyFill)
        .where(
            PaperCopyFill.account_key == account_key,
            PaperCopyFill.source_wallet == source_wallet,
            PaperCopyFill.coin == coin,
            PaperCopyFill.side == side,
            PaperCopyFill.action == "skip",
            PaperCopyFill.skipped_reason == CLOSE_BELOW_MIN_ORDER,
            PaperCopyFill.filled_at >= since,
        )
        .order_by(PaperCopyFill.filled_at.asc())
    )
    return list(result.all())


def paper_requested_close_size(fill: PaperCopyFill) -> Decimal:
    payload = fill.raw_payload if isinstance(fill.raw_payload, dict) else {}
    return max(decimal_or_zero(payload.get("requestedCloseSize")), ZERO)


def mark_paper_close_skips_aggregated(
    skips: list[PaperCopyFill],
    *,
    into: PaperCopyFill,
) -> None:
    for skipped in skips:
        payload = dict(skipped.raw_payload) if isinstance(skipped.raw_payload, dict) else {}
        skipped.skipped_reason = PAPER_CLOSE_AGGREGATED_REASON
        skipped.raw_payload = {
            **payload,
            "aggregatedInto": {
                "sourceFillId": into.source_fill_id,
                "sequenceIndex": into.sequence_index,
            },
        }


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
    batch_result = PaperCopyBatchResult()
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
        batch_result = combine_batch_results(batch_result, result)
    return batch_result


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
    execution_context: ExecutionContext | None = None,
    leverage: Decimal | None = None,
    requested_close_size: Decimal | None = None,
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
    skipped_fill = paper_copy_fill(
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
    if requested_close_size is not None:
        skipped_fill.raw_payload = {
            **(skipped_fill.raw_payload or {}),
            "requestedCloseSize": str(requested_close_size),
        }
    session.add(skipped_fill)
    return PaperCopyBatchResult(skipped_fills=1, skip_reasons={reason: 1})


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
    execution_context: ExecutionContext | None = None,
    trade_intent: TradeIntent | None = None,
    min_order_adjustment: PaperMinOrderAdjustment | None = None,
    opened_at: datetime | None = None,
    book_fill: IocFill | None = None,
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
            trade_intent=trade_intent,
            leverage=leverage,
            margin_usd=margin_usd,
            min_order_adjustment=min_order_adjustment,
            opened_at=opened_at,
            book_fill=book_fill,
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
        select(PaperPosition)
        .where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
            PaperPosition.coin == coin,
        )
        .with_for_update()
    )


async def lock_paper_source_mutation(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> None:
    if not source_wallet:
        return
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name != "postgresql":
        return
    await session.execute(
        text(
            """
            select pg_advisory_xact_lock(
              hashtext(:namespace),
              hashtext(:source_wallet)
            )
            """
        ),
        {
            "namespace": PAPER_COPY_SOURCE_LOCK_NAMESPACE,
            "source_wallet": source_wallet.lower(),
        },
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


async def open_notional_for_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.notional_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
        )
    )
    return decimal_or_zero(value)


async def open_notional_for_account(session: AsyncSession, *, account_key: str) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.notional_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
        )
    )
    return decimal_or_zero(value)


def effective_leverage(
    *,
    notional_usd: Decimal,
    margin_usd: Decimal,
    fallback: Decimal,
) -> Decimal:
    if notional_usd <= ZERO or margin_usd <= ZERO:
        return safe_leverage(fallback)
    return safe_leverage(notional_usd / margin_usd)


def paper_trading_policy(settings: Settings) -> PaperTradingPolicyRead:
    return PaperTradingPolicyRead(
        enabled=settings.paper_trading_enabled and settings.paper_copy_enabled,
        top_wallet_count=settings.trading_copy_top_wallet_count,
        source_allocation_pct=copy_allocation_pct(settings),
        max_total_allocation_pct=settings.trading_copy_max_total_allocation_pct,
        max_source_exposure_multiple=settings.trading_copy_max_source_exposure_multiple,
        max_total_exposure_multiple=settings.trading_copy_max_total_exposure_multiple,
        min_order_notional_usd=settings.trading_copy_min_order_notional_usd,
        min_order_round_up_ratio=settings.trading_copy_min_order_round_up_ratio,
        fee_rate=settings.paper_copy_fee_rate,
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        max_price_drift_bps=settings.trading_copy_max_price_drift_bps,
        use_live_mid_price=settings.trading_copy_use_live_mid_price,
        market_price_cache_enabled=settings.trading_copy_market_price_cache_enabled,
        market_price_cache_stale_seconds=settings.trading_copy_market_price_cache_stale_seconds,
    )


def realized_pnl_for_close(
    *,
    side: str,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
) -> Decimal:
    return position_pnl(side=side, entry_price=entry_price, exit_price=exit_price, size=size)


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


def paper_fill_payload(
    *,
    fill: dict[str, Any],
    settings: Settings,
    execution_context: ExecutionContext | None = None,
    trade_intent: TradeIntent | None = None,
    leverage: Decimal | None = None,
    margin_usd: Decimal | None = None,
    min_order_adjustment: PaperMinOrderAdjustment | None = None,
    opened_at: datetime | None = None,
    book_fill: IocFill | None = None,
) -> dict[str, Any]:
    filled_at = fill_datetime(fill)
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
            "maxTotalAllocationPct": str(settings.trading_copy_max_total_allocation_pct),
            "minOrderNotionalUsd": str(settings.trading_copy_min_order_notional_usd),
            "minOrderRoundUpRatio": str(settings.trading_copy_min_order_round_up_ratio),
            "maxSourceExposureMultiple": str(settings.trading_copy_max_source_exposure_multiple),
            "maxTotalExposureMultiple": str(settings.trading_copy_max_total_exposure_multiple),
            "slippageBps": str(settings.paper_copy_slippage_bps),
            "latencyMs": settings.paper_copy_latency_ms,
            "maxPriceDriftBps": str(settings.trading_copy_max_price_drift_bps),
            "useLiveMidPrice": settings.trading_copy_use_live_mid_price,
            "marketPriceCacheEnabled": settings.trading_copy_market_price_cache_enabled,
            "marketPriceCacheStaleSeconds": settings.trading_copy_market_price_cache_stale_seconds,
        },
        "execution": execution_payload(execution_context),
        "tradeIntent": trade_intent_payload(trade_intent),
        "paper": {
            "leverage": str(leverage) if leverage is not None else None,
            "marginUsd": str(margin_usd) if margin_usd is not None else None,
            "minOrderAdjustment": min_order_adjustment_payload(min_order_adjustment),
            "openedAt": opened_at.isoformat() if opened_at is not None else None,
            "durationMs": duration_between_ms(opened_at, filled_at),
            "orderBookFill": book_fill.payload() if book_fill is not None else None,
        },
    }


def min_order_adjustment_payload(
    adjustment: PaperMinOrderAdjustment | None,
) -> dict[str, str] | None:
    if adjustment is None:
        return None
    return {
        "originalNotionalUsd": str(adjustment.original_notional_usd),
        "adjustedNotionalUsd": str(adjustment.adjusted_notional_usd),
        "minOrderNotionalUsd": str(adjustment.min_order_notional_usd),
    }


def paper_fill_opened_at(fill: PaperCopyFill) -> datetime | None:
    value = paper_payload_value(fill, "openedAt")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def paper_fill_duration_ms(fill: PaperCopyFill) -> int | None:
    value = paper_payload_value(fill, "durationMs")
    if value is None:
        return None
    parsed = int_or_none(value)
    if parsed is None:
        return None
    return max(0, parsed)


def paper_payload_value(fill: PaperCopyFill, key: str) -> Any:
    payload = fill.raw_payload
    if not isinstance(payload, dict):
        return None
    paper_payload = payload.get("paper")
    if not isinstance(paper_payload, dict):
        return None
    return paper_payload.get(key)


def duration_between_ms(opened_at: datetime | None, closed_at: datetime | None) -> int | None:
    if opened_at is None or closed_at is None:
        return None
    return max(0, timestamp_ms(closed_at) - timestamp_ms(opened_at))


def execution_payload(context: ExecutionContext | None) -> dict[str, Any] | None:
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


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def sum_decimal(values: Any) -> Decimal:
    total = ZERO
    for value in values:
        if value is not None:
            total += decimal_or_zero(value)
    return total
