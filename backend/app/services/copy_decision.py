"""One definition of when and how much to copy, shared by paper and live.

The paper simulator and live execution gather their own inputs (their copied
position, exposure, and account equity) and apply a decision through their own
executor. The rules themselves live here, so paper results describe what live
would have done. Reasons are engine-neutral; each engine maps them to its own
stored reason names.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.config import Settings
from app.services.copy_signal_service import (
    ExecutionContext,
    ExecutionMarketPrices,
    SourceAccountState,
    SourceFillPart,
    build_execution_context,
    decimal_or_zero,
    is_preexisting_source_add,
    resolve_source_current_position,
    source_state_available_for_reconciliation,
)
from app.services.trading_core import (
    EntrySizingDecision,
    entry_sizing_limits,
    margin_from_notional,
    plan_entry_sizing,
    source_position_after_fill,
)

ZERO = Decimal("0")
ONE = Decimal("1")
POSITION_EPSILON = Decimal("0.00000001")
ENTRY_ACTIONS = frozenset({"open", "add", "flip_open"})

# Engine-neutral reasons.
INVALID_SOURCE_PRICE = "invalid_source_price"
PREEXISTING_SOURCE_POSITION = "preexisting_source_position"
OPPOSITE_POSITION = "opposite_position"
ALLOCATION_INACTIVE = "allocation_inactive"
SOURCE_EQUITY_MISSING = "source_equity_missing"
EXECUTION_PRICE_UNAVAILABLE = "execution_price_unavailable"
PRICE_DRIFT_TOO_HIGH = "price_drift_too_high"
SOURCE_POSITION_UNKNOWN = "source_position_unknown"
MATCHING_POSITION_MISSING = "matching_position_missing"
CLOSE_ALREADY_PENDING = "close_already_pending"
CLOSE_RATIO_MISSING = "close_ratio_missing"
CLOSE_SIZE_ZERO = "close_size_zero"
CLOSE_BELOW_MIN_ORDER = "close_below_min_order_notional"


@dataclass(frozen=True)
class CopyPosition:
    """The engine's copied position for one source and coin."""

    side: str
    size: Decimal


@dataclass(frozen=True)
class EntryDecision:
    reason: str | None
    execution_context: ExecutionContext | None = None
    sizing: EntrySizingDecision | None = None
    action: str = ""
    size: Decimal = ZERO
    notional_usd: Decimal = ZERO
    margin_usd: Decimal = ZERO
    allocation_usd: Decimal = ZERO
    source_exposure_pct: Decimal = ZERO

    @property
    def approved(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class CloseDecision:
    reason: str | None
    execution_context: ExecutionContext | None = None
    close_size: Decimal = ZERO
    notional_usd: Decimal = ZERO
    final_close: bool = False
    # Set when a partial close is below the minimum order and may be combined
    # with earlier skipped partial closes before it can be sent.
    needs_aggregation: bool = False
    aggregated: bool = False

    @property
    def approved(self) -> bool:
        return self.reason is None and not self.needs_aggregation


def entry_precheck(
    *,
    part: SourceFillPart,
    position: CopyPosition | None,
    allocation_active: bool,
    source_price: Decimal,
) -> str | None:
    """Entry rules that need no prices or exposure."""

    if source_price <= ZERO:
        return INVALID_SOURCE_PRICE
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return PREEXISTING_SOURCE_POSITION
    if position is not None and position.side != part.side:
        return OPPOSITE_POSITION
    if position is None and not allocation_active:
        return ALLOCATION_INACTIVE
    return None


def entry_price_drift_blocked(execution_context: ExecutionContext, *, settings: Settings) -> bool:
    """Entries are blocked when the market moved adversely beyond the limit."""

    return execution_context.price_drift_bps > settings.trading_copy_max_price_drift_bps


def decide_entry(
    *,
    fill: dict[str, Any],
    part: SourceFillPart,
    position: CopyPosition | None,
    allocation_active: bool,
    allocation_pct: Decimal,
    account_equity_usd: Decimal,
    source_equity_usd: Decimal,
    source_leverage: Decimal,
    market_prices: ExecutionMarketPrices,
    source_exposure_usd: Decimal,
    account_exposure_usd: Decimal,
    settings: Settings,
    min_order_notional_usd: Decimal,
    slippage_bps: Decimal | None = None,
    latency_ms: int | None = None,
) -> EntryDecision:
    """Decide whether to copy an entry part and size it against a target position."""

    reason = entry_precheck(
        part=part,
        position=position,
        allocation_active=allocation_active,
        source_price=decimal_or_zero(fill.get("price")),
    )
    if reason is not None:
        return EntryDecision(reason=reason)
    if source_equity_usd <= ZERO:
        return EntryDecision(reason=SOURCE_EQUITY_MISSING)
    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=slippage_bps,
        latency_ms=latency_ms,
    )
    if execution_context is None:
        return EntryDecision(reason=EXECUTION_PRICE_UNAVAILABLE)
    if entry_price_drift_blocked(execution_context, settings=settings):
        return EntryDecision(reason=PRICE_DRIFT_TOO_HIGH, execution_context=execution_context)
    if position is not None and part.start_position is None:
        return EntryDecision(reason=SOURCE_POSITION_UNKNOWN, execution_context=execution_context)

    price = execution_context.execution_price
    equity_usd = max(account_equity_usd, ZERO)
    allocation_usd = equity_usd * allocation_pct
    sizing = plan_entry_sizing(
        opening=position is None,
        allocation_usd=allocation_usd,
        account_equity_usd=equity_usd,
        source_equity_usd=source_equity_usd,
        source_fill_size=part.source_size,
        source_position_after=source_position_after_fill(
            start_position=part.start_position,
            fill_size=part.source_size,
        ),
        current_position_size=position.size if position is not None else ZERO,
        price=price,
        source_exposure_usd=source_exposure_usd,
        account_exposure_usd=account_exposure_usd,
        limits=entry_sizing_limits(settings, min_order_notional_usd=min_order_notional_usd),
    )
    source_exposure_pct = part.source_notional_usd / source_equity_usd
    if sizing.skip_reason is not None:
        return EntryDecision(
            reason=sizing.skip_reason,
            execution_context=execution_context,
            sizing=sizing,
            allocation_usd=allocation_usd,
            source_exposure_pct=source_exposure_pct,
        )
    notional_usd = sizing.notional_usd
    return EntryDecision(
        reason=None,
        execution_context=execution_context,
        sizing=sizing,
        action="add" if position is not None and part.action == "open" else part.action,
        size=notional_usd / price,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, source_leverage),
        allocation_usd=allocation_usd,
        source_exposure_pct=source_exposure_pct,
    )


def source_position_is_final_close(
    source_account_state: SourceAccountState | None,
    *,
    coin: str,
    side: str,
) -> bool:
    """The source no longer holds the copied side, so the copy should close fully."""

    if not source_state_available_for_reconciliation(source_account_state):
        return False
    source_position = resolve_source_current_position(
        source_account_state.positions_by_coin,
        coin,
    )
    return source_position is None or source_position.side != side


def copy_close_size(
    *,
    position_size: Decimal,
    part: SourceFillPart,
    final_close: bool,
    available_size: Decimal | None = None,
) -> Decimal | None:
    """Close everything on a final source close, otherwise the source's close ratio."""

    size = min(position_size, available_size) if available_size is not None else position_size
    if final_close:
        return size
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return None
    return min(size, size * part.close_ratio)


def decide_close(
    *,
    fill: dict[str, Any],
    part: SourceFillPart,
    position: CopyPosition | None,
    available_size: Decimal,
    source_account_state: SourceAccountState | None,
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    min_order_notional_usd: Decimal,
    slippage_bps: Decimal | None = None,
    latency_ms: int | None = None,
) -> CloseDecision:
    """Decide how much of the copied position a source close part closes.

    Price drift never blocks an exit. A partial close below the minimum order is
    returned with ``needs_aggregation`` so the engine can add earlier skipped
    partial closes through :func:`aggregate_below_min_close`.
    """

    coin = str(fill.get("coin") or "")
    if position is None or position.side != part.side:
        return CloseDecision(reason=MATCHING_POSITION_MISSING)
    if available_size <= POSITION_EPSILON:
        return CloseDecision(reason=CLOSE_ALREADY_PENDING)
    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=slippage_bps,
        latency_ms=latency_ms,
    )
    if execution_context is None:
        return CloseDecision(reason=EXECUTION_PRICE_UNAVAILABLE)
    final_close = source_position_is_final_close(
        source_account_state,
        coin=coin,
        side=part.side,
    )
    close_size = copy_close_size(
        position_size=position.size,
        part=part,
        final_close=final_close,
        available_size=available_size,
    )
    if close_size is None:
        return CloseDecision(reason=CLOSE_RATIO_MISSING, execution_context=execution_context)
    if close_size <= POSITION_EPSILON:
        return CloseDecision(reason=CLOSE_SIZE_ZERO, execution_context=execution_context)
    notional_usd = close_size * execution_context.execution_price
    return CloseDecision(
        reason=None,
        execution_context=execution_context,
        close_size=close_size,
        notional_usd=notional_usd,
        final_close=final_close,
        needs_aggregation=not final_close and notional_usd < min_order_notional_usd,
    )


def aggregate_below_min_close(
    decision: CloseDecision,
    *,
    previous_skipped_size: Decimal,
    available_size: Decimal,
    min_order_notional_usd: Decimal,
) -> CloseDecision:
    """Combine a small partial close with earlier skipped ones, or skip it again."""

    if not decision.needs_aggregation or decision.execution_context is None:
        return decision
    price = decision.execution_context.execution_price
    aggregate_size = min(max(decision.close_size + previous_skipped_size, ZERO), available_size)
    aggregate_notional = aggregate_size * price
    if aggregate_notional < min_order_notional_usd:
        return CloseDecision(
            reason=CLOSE_BELOW_MIN_ORDER,
            execution_context=decision.execution_context,
            close_size=decision.close_size,
            notional_usd=decision.notional_usd,
        )
    return CloseDecision(
        reason=None,
        execution_context=decision.execution_context,
        close_size=aggregate_size,
        notional_usd=aggregate_notional,
        aggregated=previous_skipped_size > ZERO,
    )
