"""Send a live order: the entry gates, the per-account execution lock, and dispatch recovery."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    LiveCopyFillState,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
    WalletFill,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveOrderRejectedError,
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
    LiveOrderResult,
)
from app.services.copy_risk_rules import (
    account_loss_breach,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_account_access import (
    live_account_user_address,
    load_live_account,
    load_live_account_for_update,
    load_live_order_by_client_order_id,
    validate_live_account_identity,
)
from app.services.live_account_state import (
    LIVE_CAPITAL_MODE_UNIFIED,
    live_capital_mode,
    live_perp_equity_usd,
    live_reconciliation_is_fresh,
    live_reconciliation_status,
    live_unified_equity_usd,
)
from app.services.live_copy_state_service import (
    LIVE_COPY_FILL_PLAN_VERSION,
    LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    LIVE_COPY_OUTCOME_ORDER,
    LIVE_COPY_OUTCOME_PENDING,
    LIVE_COPY_OUTCOME_RETRYABLE,
    LIVE_COPY_OUTCOME_TERMINAL_SKIP,
    acquire_live_copy_lifecycle_lock,
    get_live_copy_source_state,
    get_prior_live_copy_fill_blocker,
    is_live_copy_fill_post_baseline,
    live_copy_entry_follows_owned_position_lifecycle,
    live_copy_unresolved_order_predicate,
)
from app.services.live_exchange_fetch import (
    live_dex_from_coin,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    merge_raw_payload,
)
from app.services.live_execution_state import (
    MAX_LIVE_ORDER_SUBMIT_ATTEMPTS,
    RECOVERABLE_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    create_next_live_order_dispatch,
    load_live_order_dispatch,
    mark_live_order_dispatch_completed,
    mark_live_order_dispatching,
    mark_live_order_failed,
    mark_live_order_uncertain,
    prepare_live_order_dispatch,
    record_live_order_exchange_result,
    record_live_order_status_lookup,
    trade_intent_from_order,
)
from app.services.live_fill_reconciliation import (
    apply_order_status_response,
    mapped_exchange_order_status,
)
from app.services.live_order_reject_policy import (
    OPEN_INTEREST_CAP,
    OPEN_INTEREST_CAP_COOLDOWN_REASON,
    classify_live_order_reject,
)
from app.services.live_performance_service import (
    live_account_loss_state,
)
from app.services.live_position_reconciliation import (
    live_position_current_notional,
    live_position_unrealized_pnl,
)
from app.services.live_signer_service import (
    live_signer_entry_block,
)
from app.services.live_trading_constants import (
    LIVE_EXCHANGE_SOURCE,
    LIVE_MANUAL_TEST_SOURCE,
    POSITION_EPSILON,
)
from app.services.live_trading_errors import (
    LiveCopyEntryLifecycleDeferred,
    LiveOrderSubmitError,
)
from app.services.source_fill_ordering import source_fill_order_components
from app.services.trading_core import (
    MarginMode,
    TradeIntent,
    margin_from_notional,
)
from app.services.trading_safety_service import (
    record_audit_log,
    trip_live_account_risk,
)

ZERO = Decimal("0")


LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR = "Live entry intent expired before exchange submission."


LIVE_ENTRY_RETRY_EXPIRED_AFTER_REJECTION_ERROR = (
    "Live entry retry window expired after Hyperliquid rejected an earlier exchange attempt."
)


LIVE_ENTRY_RETRY_EXPIRED_AFTER_ATTEMPT_ERROR = (
    "Live entry retry window expired after an earlier exchange submission attempt."
)


@dataclass(frozen=True)
class LiveOrderLifecycleResult:
    order: TradingOrder
    exchange_result: LiveOrderResult | None
    submitted: bool


@dataclass(frozen=True)
class LiveDispatchRecoveryResult:
    inspected: int = 0
    recovered: int = 0
    dispatched: int = 0
    uncertain: int = 0
    failed: int = 0


async def submit_live_trade_intent(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
    client: HyperliquidLiveTradingClient | None = None,
) -> LiveOrderLifecycleResult:
    if account.account_type != "live":
        raise LiveOrderSubmitError("Only live accounts can submit live trade intents.")
    if intent.account_type != "live":
        raise LiveOrderSubmitError("Trade intent must target a live account.")

    live_client = client or HyperliquidLiveTradingClient(settings=settings)
    return await submit_live_trade_intent_under_account_lock(
        session,
        account_key=account.key,
        intent=intent,
        settings=settings,
        live_client=live_client,
    )


async def validate_live_copy_entry_lifecycle_gate(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
) -> None:
    """Fence a copied entry against a concurrent source-lane deactivation.

    Callers hold the live execution lock and the locked account row before this
    function acquires the lifecycle advisory lock.  Copy workers commit their
    lifecycle lease before submission, so this order has no inverse holder.
    """

    if intent.reduce_only or intent.source_wallet in {
        LIVE_EXCHANGE_SOURCE,
        LIVE_MANUAL_TEST_SOURCE,
    }:
        return
    await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
    source_state = await get_live_copy_source_state(
        session,
        account_key=account.key,
        source_wallet=intent.source_wallet,
        for_update=True,
    )
    if source_state is None:
        raise LiveCopyEntryLifecycleDeferred("live_source_lifecycle_inactive")
    wallet_fill = await session.scalar(
        select(WalletFill)
        .where(
            WalletFill.wallet_address == intent.source_wallet,
            WalletFill.external_fill_id == intent.source_fill_id,
            WalletFill.coin == intent.coin,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if wallet_fill is None:
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_missing")
    plan_result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == account.key,
            LiveCopyFillState.account_type == "live",
            LiveCopyFillState.source_wallet == intent.source_wallet,
            LiveCopyFillState.source_fill_id == intent.source_fill_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    plan_states = list(plan_result.all())
    target_state = next(
        (state for state in plan_states if state.sequence_index == intent.sequence_index),
        None,
    )
    expected_part_count = len(plan_states)
    source_fill = {
        "externalFillId": wallet_fill.external_fill_id,
        "coin": wallet_fill.coin,
        "timestampMs": wallet_fill.timestamp_ms,
        "rawJson": wallet_fill.raw_json,
    }
    expected_direction_rank, expected_position, expected_numeric_fill_id = (
        source_fill_order_components(source_fill)
    )
    if target_state is not None and (
        target_state.fill_complete
        or target_state.trading_order_id is not None
        or target_state.outcome not in {LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE}
    ):
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    if (
        target_state is None
        or expected_part_count == 0
        or {state.sequence_index for state in plan_states} != set(range(expected_part_count))
        or any(
            state.expected_part_count != expected_part_count
            or state.plan_version != LIVE_COPY_FILL_PLAN_VERSION
            for state in plan_states
        )
        or target_state.coin != intent.coin
        or target_state.side != intent.side
        or int(target_state.source_timestamp_ms) != int(wallet_fill.timestamp_ms)
        or int(target_state.source_order_direction_rank) != expected_direction_rank
        or target_state.source_order_position != expected_position
        or target_state.source_order_fill_id_numeric != expected_numeric_fill_id
        or target_state.outcome not in {LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE}
        or (
            target_state.outcome == LIVE_COPY_OUTCOME_RETRYABLE
            and target_state.reason != "processing"
        )
    ):
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_reclassified")
    if account.status != "enabled":
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_TERMINAL_SKIP,
            reason="live_account_not_enabled",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_account_not_enabled",
            state_reclassified=True,
        )
    if source_state.status != "active":
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_source_deactivated",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    unresolved_lower_sequence = await session.scalar(
        select(LiveCopyFillState.sequence_index)
        .where(
            LiveCopyFillState.account_key == account.key,
            LiveCopyFillState.source_wallet == intent.source_wallet,
            LiveCopyFillState.source_fill_id == intent.source_fill_id,
            LiveCopyFillState.sequence_index < intent.sequence_index,
            exists(
                select(TradingOrder.id).where(
                    TradingOrder.id == LiveCopyFillState.trading_order_id,
                    live_copy_unresolved_order_predicate(),
                )
            ),
        )
        .order_by(LiveCopyFillState.sequence_index.asc())
        .limit(1)
        .with_for_update()
    )
    if any(
        state.sequence_index < intent.sequence_index
        and (
            state.outcome
            not in {
                LIVE_COPY_OUTCOME_TERMINAL_SKIP,
                LIVE_COPY_OUTCOME_BASELINE_IGNORED,
                LIVE_COPY_OUTCOME_ORDER,
            }
            or state.sequence_index == unresolved_lower_sequence
        )
        for state in plan_states
    ):
        raise LiveCopyEntryLifecycleDeferred("live_prior_source_fill_pending")
    predecessor = await get_prior_live_copy_fill_blocker(
        session,
        account_key=account.key,
        source_wallet=intent.source_wallet,
        fill=source_fill,
    )
    if predecessor is not None:
        raise LiveCopyEntryLifecycleDeferred("live_prior_source_fill_pending")

    competing_position = await session.scalar(
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == intent.coin,
            TradingPosition.source_wallet.not_in((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
            TradingPosition.size > POSITION_EPSILON,
        )
        .with_for_update()
        .limit(1)
    )
    competing_order = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.source_wallet.not_in((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
            live_copy_unresolved_order_predicate(),
        )
        .with_for_update()
        .limit(1)
    )
    same_source_unresolved_order = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.source_wallet == intent.source_wallet,
            or_(
                TradingOrder.source_fill_id != intent.source_fill_id,
                TradingOrder.sequence_index != intent.sequence_index,
            ),
            live_copy_unresolved_order_predicate(),
        )
        .with_for_update()
        .limit(1)
    )
    position_result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == intent.coin,
            TradingPosition.source_wallet.in_((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    positions = list(position_result.all())
    source_position = next(
        (position for position in positions if position.source_wallet == intent.source_wallet),
        None,
    )
    exchange_position = next(
        (position for position in positions if position.source_wallet == LIVE_EXCHANGE_SOURCE),
        None,
    )
    source_has_matching_position = (
        source_position is not None
        and source_position.size > POSITION_EPSILON
        and source_position.side == intent.side
    )
    normal_entry_is_post_activation = is_live_copy_fill_post_baseline(
        source_state,
        fill=source_fill,
        origin=target_state.origin,
        observed_at=target_state.observed_at,
        first_observed_at=target_state.first_observed_at,
    )
    raw_json = wallet_fill.raw_json if isinstance(wallet_fill.raw_json, dict) else {}
    owned_continuation = await live_copy_entry_follows_owned_position_lifecycle(
        session,
        source_state=source_state,
        fill=source_fill,
        action=target_state.action,
        side=target_state.side,
        start_position=raw_json.get("startPosition"),
    )
    if not source_state.entry_eligible and not owned_continuation:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_retained_source_new_market",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    if not normal_entry_is_post_activation and not owned_continuation:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_copy_baseline_entry",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    planned_open_to_add = (
        target_state.action == "open" and intent.action == "add" and source_has_matching_position
    )
    if (target_state.action != intent.action and not planned_open_to_add) or (
        intent.action == "add" and not source_has_matching_position
    ):
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_reclassified")
    conflict_reason: str | None = None
    if competing_position is not None or competing_order is not None:
        conflict_reason = "live_market_reserved_by_other_source"
    elif (
        source_position is not None
        and source_position.size > POSITION_EPSILON
        and source_position.side != intent.side
    ):
        conflict_reason = "live_market_reserved_by_other_source"
    elif exchange_position is not None and exchange_position.size > POSITION_EPSILON:
        if source_position is None or exchange_position.side != intent.side:
            conflict_reason = "live_exchange_position_conflict"
        elif (
            exchange_position.size > source_position.size + POSITION_EPSILON
            and same_source_unresolved_order is None
        ):
            conflict_reason = "live_exchange_position_conflict"
    if conflict_reason is not None:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_TERMINAL_SKIP,
            reason=conflict_reason,
        )
        raise LiveCopyEntryLifecycleDeferred(
            conflict_reason,
            state_reclassified=True,
        )


async def mark_live_copy_entry_state_terminal(
    session: AsyncSession,
    *,
    state: LiveCopyFillState,
    outcome: str,
    reason: str,
) -> None:
    """Finish a stale copied-entry decision without manufacturing an order row."""

    state.outcome = outcome
    state.reason = reason
    state.next_attempt_at = None
    state.fill_complete = False
    state.trading_order_id = None
    state.decision_at = datetime.now(UTC)
    await session.flush()
    await session.commit()


@dataclass(frozen=True)
class LiveCopyExposure:
    """Notional exposure that copy sizing and the entry gate count against caps."""

    source_usd: Decimal
    account_usd: Decimal


async def load_live_copy_exposure(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_wallet: str,
    coin: str,
    settings: Settings,
    exclude_client_order_id: str | None = None,
    lock: bool = False,
) -> LiveCopyExposure:
    """Sum current position notional and unfilled entry orders.

    Account exposure takes the larger of the attributed source positions and the
    authoritative exchange positions: source positions move immediately on fills,
    while exchange positions also include unattributed exposure.
    """

    capacity_dex = live_dex_from_coin(coin)
    unified_mode = live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED
    position_query = select(TradingPosition).where(
        TradingPosition.account_key == account.key,
        TradingPosition.account_type == "live",
        TradingPosition.size > POSITION_EPSILON,
    )
    order_query = select(TradingOrder).where(
        TradingOrder.account_key == account.key,
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet != LIVE_EXCHANGE_SOURCE,
        TradingOrder.reduce_only.is_(False),
        live_copy_unresolved_order_predicate(),
    )
    if exclude_client_order_id is not None:
        order_query = order_query.where(
            or_(
                TradingOrder.client_order_id.is_(None),
                TradingOrder.client_order_id != exclude_client_order_id,
            )
        )
    if lock:
        position_query = position_query.execution_options(populate_existing=True).with_for_update()
        order_query = order_query.execution_options(populate_existing=True).with_for_update()
    positions = [
        position
        for position in (await session.scalars(position_query)).all()
        if unified_mode or live_dex_from_coin(position.coin) == capacity_dex
    ]
    orders = [
        order
        for order in (await session.scalars(order_query)).all()
        if unified_mode or live_dex_from_coin(order.coin) == capacity_dex
    ]
    order_ids = [order.id for order in orders if order.id is not None]
    materialized_size_by_order: dict[Any, Decimal] = {}
    if order_ids:
        fill_query = select(TradingFill).where(TradingFill.order_id.in_(order_ids))
        if lock:
            fill_query = fill_query.execution_options(populate_existing=True).with_for_update()
        for fill in (await session.scalars(fill_query)).all():
            if fill.order_id is None:
                continue
            materialized_size_by_order[fill.order_id] = materialized_size_by_order.get(
                fill.order_id, ZERO
            ) + abs(fill.size or ZERO)

    def position_notional(position: TradingPosition) -> Decimal:
        return max(live_position_current_notional(position), ZERO)

    def order_notional(order: TradingOrder) -> Decimal:
        return live_copy_capacity_order_reservation(
            order,
            materialized_size=materialized_size_by_order.get(order.id, ZERO),
        )

    source_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet == source_wallet),
        ZERO,
    )
    attributed_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet != LIVE_EXCHANGE_SOURCE),
        ZERO,
    )
    exchange_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet == LIVE_EXCHANGE_SOURCE),
        ZERO,
    )
    source_orders = sum(
        (order_notional(order) for order in orders if order.source_wallet == source_wallet),
        ZERO,
    )
    all_orders = sum((order_notional(order) for order in orders), ZERO)
    return LiveCopyExposure(
        source_usd=source_positions + source_orders,
        account_usd=max(attributed_positions, exchange_positions) + all_orders,
    )


LIVE_SOURCE_LOSS_WINDOW = timedelta(days=7)


async def live_source_net_loss_usd(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    now: datetime,
) -> Decimal:
    """Realized net loss over the rolling window plus current unrealized loss."""

    realized = await session.scalar(
        select(
            func.coalesce(func.sum(TradingFill.realized_pnl_usd - TradingFill.fee_usd), ZERO)
        ).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.source_wallet == source_wallet,
            TradingFill.filled_at >= now - LIVE_SOURCE_LOSS_WINDOW,
        )
    )
    position_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.size > POSITION_EPSILON,
        )
    )
    unrealized = sum(
        (live_position_unrealized_pnl(position) or ZERO for position in position_result.all()),
        ZERO,
    )
    return max(-((decimal_or_none(realized) or ZERO) + unrealized), ZERO)


def live_copy_allocation_equity(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        equity_usd = live_unified_equity_usd(account)
        return equity_usd if equity_usd > ZERO else account.equity_usd or ZERO
    return live_perp_equity_usd(account, dex=dex)


async def validate_live_copy_entry_capacity_gate(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
) -> None:
    """Reject a stale copied entry when current exposure leaves no room for it."""

    if intent.reduce_only or intent.source_wallet in {
        LIVE_EXCHANGE_SOURCE,
        LIVE_MANUAL_TEST_SOURCE,
    }:
        return
    allocation_pct = decimal_or_none(intent.allocation_pct)
    if allocation_pct is None:
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")
    await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
    allocation_equity_usd = live_copy_allocation_equity(
        account,
        settings=settings,
        dex=live_dex_from_coin(intent.coin),
    )
    if allocation_equity_usd <= ZERO:
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")
    exposure = await load_live_copy_exposure(
        session,
        account=account,
        source_wallet=intent.source_wallet,
        coin=intent.coin,
        settings=settings,
        exclude_client_order_id=intent.client_order_id,
        lock=True,
    )
    allocation_usd = allocation_equity_usd * allocation_pct
    source_room = (
        settings.trading_copy_max_source_exposure_multiple * allocation_usd - exposure.source_usd
    )
    total_room = (
        settings.trading_copy_max_total_exposure_multiple * allocation_equity_usd
        - exposure.account_usd
    )
    incoming_notional = max(decimal_or_none(intent.notional_usd) or ZERO, ZERO)
    if (
        incoming_notional > source_room + POSITION_EPSILON
        or incoming_notional > total_room + POSITION_EPSILON
    ):
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")


def live_copy_capacity_order_reservation(
    order: TradingOrder,
    *,
    materialized_size: Decimal,
) -> Decimal:
    """Notional still reserved by an entry order that has not fully materialized."""

    requested_size = decimal_or_none(order.requested_size) or ZERO
    filled_size = decimal_or_none(order.filled_size) or ZERO
    expected_size = (
        (filled_size if filled_size > POSITION_EPSILON else requested_size)
        if order.status == "filled"
        else requested_size
    )
    outstanding_size = max(expected_size - materialized_size, ZERO)
    if outstanding_size <= POSITION_EPSILON:
        return ZERO
    requested_notional = max(decimal_or_none(order.requested_notional_usd) or ZERO, ZERO)
    if requested_size <= POSITION_EPSILON:
        return requested_notional
    return requested_notional * min(outstanding_size / requested_size, Decimal("1"))


async def submit_live_trade_intent_under_account_lock(
    session: AsyncSession,
    *,
    account_key: str,
    intent: TradeIntent,
    settings: Settings,
    live_client: HyperliquidLiveTradingClient,
) -> LiveOrderLifecycleResult:
    try:
        async with job_lock(
            session,
            key=f"live_execution:{account_key}",
            ttl_seconds=120,
        ):
            account = await load_live_account_for_update(session, account_key=account_key)
            validate_live_account_identity(account, settings=settings)
            is_live_copy_entry = not intent.reduce_only and intent.source_wallet not in {
                LIVE_EXCHANGE_SOURCE,
                LIVE_MANUAL_TEST_SOURCE,
            }
            existing_order = await load_live_order_by_client_order_id(
                session,
                client_order_id=intent.client_order_id,
            )
            will_dispatch = (
                existing_order is None
                or existing_order.status == "ready"
                or is_retryable_live_order_submit_failure(existing_order)
            )
            if not intent.reduce_only and account.status != "enabled":
                raise LiveOrderSubmitError(
                    "Live account is not enabled for entry execution.",
                    status_code=409,
                )

            if not intent.reduce_only and will_dispatch:
                await ensure_live_entry_intent_is_fresh(
                    session,
                    intent=intent,
                    order=existing_order,
                    settings=settings,
                )
                await validate_live_entry_risk_guardrails(
                    session,
                    account=account,
                    settings=settings,
                )

            if will_dispatch:
                try:
                    live_client.validate_account_order(account=account, intent=intent)
                except Exception as exc:
                    raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_entry_lifecycle_gate(
                    session,
                    account=account,
                    intent=intent,
                )
            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_exchange_market_cooldown(
                    session,
                    intent=intent,
                )
            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_entry_capacity_gate(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                )

            order, dispatch, _ = await prepare_live_order_dispatch(
                session,
                intent=intent,
            )
            if order.status in TERMINAL_ORDER_STATUSES:
                if is_retryable_live_order_submit_failure(order):
                    if dispatch.attempt_count > 0 or dispatch.status != "pending":
                        dispatch = await create_next_live_order_dispatch(
                            session,
                            order=order,
                        )
                    reset_live_order_for_retry(order, intent=intent)
                    order.status = "ready"
                    await session.commit()
                else:
                    dispatch.status = "completed"
                    dispatch.completed_at = dispatch.completed_at or datetime.now(UTC)
                    await session.commit()
                    return LiveOrderLifecycleResult(
                        order=order,
                        exchange_result=None,
                        submitted=False,
                    )

            if order.status in {"submitting", "uncertain", "submitted"}:
                return LiveOrderLifecycleResult(
                    order=order,
                    exchange_result=None,
                    submitted=False,
                )
            if order.status not in RECOVERABLE_ORDER_STATUSES:
                return LiveOrderLifecycleResult(
                    order=order,
                    exchange_result=None,
                    submitted=False,
                )

            await mark_live_order_dispatching(
                session,
                order=order,
                dispatch=dispatch,
            )
            exchange_intent = replace(intent, client_order_id=dispatch.client_order_id)
            try:
                result = await live_client.submit_order(account=account, intent=exchange_intent)
            except (
                HyperliquidLiveOrderRejectedError,
                HyperliquidLiveTradingConfigurationError,
            ) as exc:
                await mark_live_order_failed(
                    session,
                    order=order,
                    dispatch=dispatch,
                    error=exc,
                )
                raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc
            except Exception as exc:
                await mark_live_order_uncertain(
                    session,
                    order=order,
                    dispatch=dispatch,
                    error=exc,
                )
                raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

            apply_live_order_result(
                order,
                result,
                dispatch=dispatch,
                updated_at=datetime.now(UTC),
            )
            await mark_live_order_dispatch_completed(session, dispatch=dispatch)
            return LiveOrderLifecycleResult(
                order=order,
                exchange_result=result,
                submitted=True,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveOrderSubmitError(
            "Another live order is already being dispatched for this account.",
            status_code=409,
        ) from exc


async def validate_live_copy_exchange_market_cooldown(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    now: datetime | None = None,
) -> None:
    """Defer copied entries while a recent market-wide OI-cap reject cools down."""

    checked_at = now or datetime.now(UTC)
    recent_reject = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.status == "rejected",
            TradingOrder.error.startswith(f"{OPEN_INTEREST_CAP.code}:"),
            TradingOrder.updated_at
            >= checked_at - timedelta(seconds=OPEN_INTEREST_CAP.retry_delay_seconds),
            TradingOrder.client_order_id != intent.client_order_id,
        )
        .order_by(TradingOrder.updated_at.desc())
        .limit(1)
    )
    if recent_reject is not None:
        raise LiveCopyEntryLifecycleDeferred(OPEN_INTEREST_CAP_COOLDOWN_REASON)


async def sync_live_position_margin_setting(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    leverage: Decimal,
    margin_mode: MarginMode,
    settings: Settings,
    client: HyperliquidLiveTradingClient | None = None,
) -> bool:
    live_client = client or HyperliquidLiveTradingClient(settings=settings)
    position_query = (
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == coin,
            TradingPosition.source_wallet.in_([source_wallet, LIVE_EXCHANGE_SOURCE]),
        )
        .execution_options(populate_existing=True)
    )

    def position_pair(
        positions: list[TradingPosition],
    ) -> tuple[TradingPosition | None, TradingPosition | None]:
        exchange_position = next(
            (position for position in positions if position.source_wallet == LIVE_EXCHANGE_SOURCE),
            None,
        )
        source_position = next(
            (position for position in positions if position.source_wallet == source_wallet),
            None,
        )
        return exchange_position, source_position

    def synchronize_position_metadata(
        positions: list[TradingPosition],
        *,
        response: dict[str, Any] | None,
    ) -> None:
        synced_at = datetime.now(UTC)
        for position in positions:
            position.leverage = leverage
            position.margin_mode = margin_mode
            position.margin_usd = margin_from_notional(position.notional_usd, leverage)
            position.raw_payload = merge_raw_payload(
                position.raw_payload,
                {
                    "marginSettingSync": {
                        "leverage": str(leverage),
                        "marginMode": margin_mode,
                        "sourceWallet": source_wallet,
                        "syncedAt": synced_at.isoformat(),
                        "exchangeResponse": response,
                    }
                },
            )

    preflight_account = await load_live_account(session, account_key=account_key)
    validate_live_account_identity(preflight_account, settings=settings)
    preflight_result = await session.scalars(position_query)
    preflight_positions = list(preflight_result.all())
    exchange_position, source_position = position_pair(preflight_positions)
    if exchange_position is None or source_position is None:
        await session.commit()
        return False

    exchange_change_needed = (
        exchange_position.leverage != leverage or exchange_position.margin_mode != margin_mode
    )
    if not exchange_change_needed:
        locked_result = await session.scalars(position_query.with_for_update())
        locked_positions = list(locked_result.all())
        exchange_position, source_position = position_pair(locked_positions)
        if exchange_position is None or source_position is None:
            await session.commit()
            return False
        exchange_change_needed = (
            exchange_position.leverage != leverage or exchange_position.margin_mode != margin_mode
        )
        if not exchange_change_needed:
            synchronize_position_metadata(locked_positions, response=None)
            await session.commit()
            return False

        # Do not retain position row locks while waiting for the account execution lock.
        await session.commit()

    try:
        async with job_lock(
            session,
            key=f"live_execution:{account_key}",
            ttl_seconds=120,
        ):
            account = await load_live_account_for_update(session, account_key=account_key)
            validate_live_account_identity(account, settings=settings)
            result = await session.scalars(position_query.with_for_update())
            positions = list(result.all())
            exchange_position, source_position = position_pair(positions)
            if exchange_position is None or source_position is None:
                await session.commit()
                return False

            changed = (
                exchange_position.leverage != leverage
                or exchange_position.margin_mode != margin_mode
            )
            await session.commit()
            response: dict[str, Any] | None = None
            if changed:
                try:
                    response = await live_client.update_margin_setting(
                        account=account,
                        coin=coin,
                        leverage=leverage,
                        margin_mode=margin_mode,
                    )
                except (
                    HyperliquidLiveOrderRejectedError,
                    HyperliquidLiveTradingConfigurationError,
                ) as exc:
                    raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc
                except Exception as exc:
                    raise LiveOrderSubmitError(
                        "Live margin setting update failed before synchronization."
                    ) from exc

            locked_result = await session.scalars(position_query.with_for_update())
            positions = list(locked_result.all())
            if not any(
                position.source_wallet == LIVE_EXCHANGE_SOURCE for position in positions
            ) or not any(position.source_wallet == source_wallet for position in positions):
                raise LiveOrderSubmitError(
                    "Live margin setting changed externally but local position state disappeared."
                )
            synchronize_position_metadata(positions, response=response)
            if changed:
                record_audit_log(
                    session,
                    actor="copy_recovery",
                    action="live_position.margin_setting_synced",
                    payload={
                        "accountKey": account_key,
                        "coin": coin,
                        "leverage": str(leverage),
                        "marginMode": margin_mode,
                        "sourceWallet": source_wallet,
                    },
                )
            await session.commit()
            return changed
    except JobLockAlreadyHeldError as exc:
        raise LiveOrderSubmitError(
            "Live margin setting sync deferred because account execution is busy.",
            status_code=409,
        ) from exc


async def ensure_live_entry_intent_is_fresh(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    order: TradingOrder | None,
    settings: Settings,
) -> None:
    ttl_reference = order.created_at if order is not None else intent.created_at
    if ttl_reference.tzinfo is None:
        ttl_reference = ttl_reference.replace(tzinfo=UTC)
    if datetime.now(UTC) - ttl_reference.astimezone(UTC) <= timedelta(
        seconds=settings.live_trading_entry_intent_ttl_seconds
    ):
        return

    expiry_error = live_entry_expiry_error(order)
    if order is not None and (
        order.status in {"planned", "ready", "failed", "canceled"}
        or (order.status == "rejected" and is_retryable_live_order_submit_failure(order))
    ):
        dispatch = await load_live_order_dispatch(session, order_id=order.id)
        order.status = "canceled"
        order.error = expiry_error
        if dispatch is not None and dispatch.status in {"pending", "dispatching", "uncertain"}:
            dispatch.status = "canceled"
            dispatch.completed_at = datetime.now(UTC)
            dispatch.last_error = order.error
        record_audit_log(
            session,
            actor="execution_engine",
            action="live_entry.expired",
            payload={"accountKey": order.account_key, "orderId": str(order.id)},
        )
        await session.commit()
    raise LiveOrderSubmitError(
        expiry_error,
        status_code=409,
    )


def live_entry_expiry_error(order: TradingOrder | None) -> str:
    if order is None:
        return LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR
    if order.status == "rejected":
        return LIVE_ENTRY_RETRY_EXPIRED_AFTER_REJECTION_ERROR
    if order.submitted_at is not None:
        return LIVE_ENTRY_RETRY_EXPIRED_AFTER_ATTEMPT_ERROR
    return LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR


async def recover_live_order_dispatches(
    session: AsyncSession,
    *,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    limit: int = 100,
) -> LiveDispatchRecoveryResult:
    dispatch_ids_result = await session.scalars(
        select(TradingOrderDispatch.id)
        .where(
            TradingOrderDispatch.status.in_(["pending", "dispatching", "uncertain"]),
            TradingOrderDispatch.available_at <= datetime.now(UTC),
        )
        .order_by(TradingOrderDispatch.created_at.asc())
        .limit(limit)
    )
    dispatch_ids = list(dispatch_ids_result.all())
    await session.commit()
    if not dispatch_ids:
        return LiveDispatchRecoveryResult()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
    inspected = recovered = dispatched_count = uncertain = failed = 0
    try:
        for dispatch_id in dispatch_ids:
            dispatch = await session.get(TradingOrderDispatch, dispatch_id)
            if dispatch is None or dispatch.status not in {
                "pending",
                "dispatching",
                "uncertain",
            }:
                await session.rollback()
                continue
            order = await session.get(TradingOrder, dispatch.order_id)
            account = (
                await session.get(TradingAccount, dispatch.account_key)
                if order is not None
                else None
            )
            await session.commit()
            if order is None or account is None or account.account_type != "live":
                dispatch = await session.get(TradingOrderDispatch, dispatch_id)
                if dispatch is not None:
                    dispatch.status = "canceled"
                    dispatch.last_error = "Live order or account is missing."
                    dispatch.completed_at = datetime.now(UTC)
                    await session.commit()
                failed += 1
                inspected += 1
                continue

            inspected += 1
            if dispatch.status == "pending" and order.status == "ready":
                if not order.reduce_only and not settings.live_trading_enabled:
                    refreshed_order = await session.get(TradingOrder, order.id)
                    refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
                    if refreshed_order is not None and refreshed_dispatch is not None:
                        refreshed_order.status = "canceled"
                        refreshed_order.error = (
                            "Entry canceled while live entry execution is stopped."
                        )
                        refreshed_dispatch.status = "canceled"
                        refreshed_dispatch.completed_at = datetime.now(UTC)
                        refreshed_dispatch.last_error = refreshed_order.error
                        record_audit_log(
                            session,
                            actor="recovery_engine",
                            action="live_entry.canceled_while_stopped",
                            payload={
                                "accountKey": refreshed_order.account_key,
                                "orderId": str(refreshed_order.id),
                                "liveTradingEnabled": False,
                            },
                        )
                        await session.commit()
                    failed += 1
                    continue
                try:
                    result = await submit_live_trade_intent(
                        session,
                        account=account,
                        intent=trade_intent_from_order(order),
                        settings=settings,
                        client=live_client,
                    )
                except LiveOrderSubmitError:
                    refreshed_order = await session.get(TradingOrder, order.id)
                    if refreshed_order is not None and refreshed_order.status == "uncertain":
                        uncertain += 1
                    else:
                        failed += 1
                else:
                    dispatched_count += int(result.submitted)
                continue

            try:
                status_response = await client.order_status(
                    user=live_account_user_address(account, settings=settings),
                    oid=dispatch.client_order_id,
                )
            except Exception as exc:
                refreshed_order = await session.get(TradingOrder, order.id)
                refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
                if refreshed_order is not None and refreshed_dispatch is not None:
                    record_live_order_status_lookup(refreshed_dispatch, error=exc)
                    await mark_live_order_uncertain(
                        session,
                        order=refreshed_order,
                        dispatch=refreshed_dispatch,
                        error=exc,
                    )
                uncertain += 1
                continue

            refreshed_order = await session.get(TradingOrder, order.id)
            refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
            if refreshed_order is None or refreshed_dispatch is None:
                await session.rollback()
                failed += 1
                continue
            if mapped_exchange_order_status(status_response) is not None:
                apply_order_status_response(refreshed_order, status_response)
                refreshed_dispatch.status = "completed"
                refreshed_dispatch.completed_at = datetime.now(UTC)
                record_live_order_status_lookup(
                    refreshed_dispatch,
                    response=status_response,
                )
                record_live_order_exchange_result(
                    refreshed_dispatch,
                    exchange_status=mapped_exchange_order_status(status_response) or "unknown",
                    error_code=None,
                    error_message=None,
                    raw_response=status_response,
                )
                await session.commit()
                recovered += 1
                continue

            record_live_order_status_lookup(
                refreshed_dispatch,
                response=status_response,
            )
            await mark_live_order_uncertain(
                session,
                order=refreshed_order,
                dispatch=refreshed_dispatch,
                error=RuntimeError("Exchange order status is still unknown for this cloid."),
            )
            uncertain += 1
    finally:
        if client_created:
            await client.__aexit__(None, None, None)

    return LiveDispatchRecoveryResult(
        inspected=inspected,
        recovered=recovered,
        dispatched=dispatched_count,
        uncertain=uncertain,
        failed=failed,
    )


async def validate_live_entry_risk_guardrails(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)

    async def reject(
        *,
        rule: str,
        message: str,
        observed: str | int | float | None = None,
        limit: str | int | float | None = None,
        trip_account: bool = True,
    ) -> None:
        if trip_account:
            await trip_live_account_risk(
                session,
                account=account,
                rule=rule,
                message=message,
                observed=observed,
                limit=limit,
            )
        await session.commit()
        raise LiveOrderSubmitError(message, status_code=409)

    if live_reconciliation_status(account) != "complete":
        await reject(
            rule="reconciliation_incomplete",
            message="Live entries require a complete exchange reconciliation snapshot.",
            trip_account=False,
        )
    if not live_reconciliation_is_fresh(account, settings=settings, now=now):
        await reject(
            rule="reconciliation_stale",
            message="Live entries require a fresh exchange reconciliation snapshot.",
            observed=(
                account.last_reconciled_at.isoformat() if account.last_reconciled_at else None
            ),
            limit=settings.live_trading_reconciliation_max_snapshot_age_seconds,
            trip_account=False,
        )
    signer_block = live_signer_entry_block(account, settings=settings)
    if signer_block is not None:
        await reject(
            rule=signer_block.rule,
            message=signer_block.message,
            observed=signer_block.status,
            limit="api_wallet",
            trip_account=signer_block.trip_account,
        )
    breach = account_loss_breach(
        await live_account_loss_state(
            session,
            account_key=account.key,
            now=now,
            since=account.loss_limits_reset_at,
        ),
        settings=settings,
    )
    if breach is not None:
        await reject(
            rule=breach.rule,
            message=f"Live account {breach.label} guard is active.",
            observed=str(breach.observed),
            limit=str(breach.limit),
        )

    if settings.live_trading_max_orders_per_minute > 0:
        recent_orders = await live_account_recent_order_count(
            session,
            account_key=account.key,
            now=now,
        )
        if recent_orders >= settings.live_trading_max_orders_per_minute:
            await reject(
                rule="max_orders_per_minute",
                message="Live account order rate guard is active.",
                observed=recent_orders,
                limit=settings.live_trading_max_orders_per_minute,
            )


async def live_account_recent_order_count(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> int:
    value = await session.scalar(
        select(func.count(TradingOrder.id)).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.submitted_at.is_not(None),
            TradingOrder.submitted_at >= now - timedelta(minutes=1),
        )
    )
    return int(value or 0)


def is_retryable_live_order_submit_failure(order: TradingOrder) -> bool:
    if order.exchange_order_id or order.filled_size > ZERO or order.filled_notional_usd > ZERO:
        return False
    if order.status == "rejected":
        payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
        exchange_reject = payload.get("exchangeReject")
        return bool(
            isinstance(exchange_reject, dict)
            and exchange_reject.get("transient") is True
            and exchange_reject.get("retryExhausted") is not True
        )
    if order.status != "failed":
        return False
    if is_retryable_live_copy_skip(order):
        return True
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    submit_error = payload.get("submitError")
    if not isinstance(submit_error, dict):
        return False
    if submit_error.get("type") != "HyperliquidLiveOrderRejectedError":
        return False
    message = str(submit_error.get("message") or order.error or "")
    return message.startswith("Live order market is not available for exchange submission:")


def is_retryable_live_copy_skip(order: TradingOrder) -> bool:
    if (
        order.error == "skip:live_close_below_min_order_notional"
        and order.reduce_only
        and order.action in {"reduce", "close", "flip_close"}
    ):
        return True
    return order.error in {
        "skip:live_account_not_enabled",
        "skip:live_account_exit_only",
        "skip:live_execution_busy",
        "skip:live_execution_price_unavailable",
        "skip:live_matching_position_missing",
        "skip:live_preexisting_source_add",
        "skip:live_reconciliation_failed",
        "skip:live_reconciliation_incomplete",
        "skip:live_reconciliation_stale",
        "skip:live_source_leverage_missing",
        "skip:live_source_margin_mode_missing",
        "skip:source_account_margin_summary_missing",
        "skip:source_account_mode_unavailable",
        "skip:source_account_state_fetch_failed",
        "skip:source_account_state_missing",
        "skip:source_perp_equity_missing",
        "skip:source_perp_equity_zero",
        "skip:source_unified_equity_unavailable",
    }


def reset_live_order_for_retry(order: TradingOrder, *, intent: TradeIntent) -> None:
    preserve_original_submit_at = order.status == "rejected"
    retry_reason = live_order_retry_reason(order)
    order.status = "planned"
    order.error = None
    if not preserve_original_submit_at:
        order.submitted_at = None
    order.coin = intent.coin
    order.action = intent.action
    order.side = intent.side
    order.is_buy = intent.is_buy
    order.reduce_only = intent.reduce_only
    order.order_type = "ioc"
    order.requested_size = intent.size
    order.requested_notional_usd = intent.notional_usd
    order.margin_usd = intent.margin_usd
    order.leverage = intent.leverage
    order.margin_mode = intent.margin_mode
    order.limit_price = intent.limit_price
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {
            "retry": {
                "reason": retry_reason,
                "requestedAt": datetime.now(UTC).isoformat(),
            }
        },
    )


def live_order_retry_reason(order: TradingOrder) -> str:
    if is_retryable_live_copy_skip(order):
        return str(order.error or "retryable_live_copy_skip").removeprefix("skip:")
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    exchange_reject = payload.get("exchangeReject")
    if isinstance(exchange_reject, dict) and exchange_reject.get("code"):
        return str(exchange_reject["code"])
    return "market_metadata_available_after_previous_submit_failure"


def apply_live_order_result(
    order: TradingOrder,
    result: LiveOrderResult,
    *,
    dispatch: TradingOrderDispatch | None = None,
    updated_at: datetime,
) -> None:
    order.status = result.status
    order.exchange_order_id = result.exchange_order_id or order.exchange_order_id
    order.error = result.error
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {"exchangeResponse": result.raw_response},
    )
    if result.status == "rejected":
        classification = classify_live_order_reject(result.error)
        retry_exhausted = (
            dispatch is not None and dispatch.attempt_number >= MAX_LIVE_ORDER_SUBMIT_ATTEMPTS
        )
        order.raw_payload = merge_raw_payload(
            order.raw_payload,
            {
                "exchangeReject": {
                    "code": classification.code,
                    "message": result.error,
                    "transient": classification.transient,
                    "retryExhausted": retry_exhausted,
                    "retryDelaySeconds": classification.retry_delay_seconds,
                }
            },
        )
        order.error = f"{classification.code}: {result.error or 'Order rejected.'}"
        if dispatch is not None:
            record_live_order_exchange_result(
                dispatch,
                exchange_status=result.status,
                error_code=classification.code,
                error_message=result.error,
                raw_response=result.raw_response,
            )
    elif dispatch is not None:
        record_live_order_exchange_result(
            dispatch,
            exchange_status=result.status,
            error_code=None,
            error_message=result.error,
            raw_response=result.raw_response,
        )
    if result.submitted_size is not None:
        order.requested_size = result.submitted_size
    if result.submitted_limit_price is not None:
        order.limit_price = result.submitted_limit_price
    if result.submitted_notional_usd is not None:
        order.requested_notional_usd = result.submitted_notional_usd
        order.margin_usd = margin_from_notional(result.submitted_notional_usd, order.leverage)
    if result.status in {"accepted", "filled"}:
        order.accepted_at = order.accepted_at or updated_at
    if result.status == "filled":
        order.filled_at = order.filled_at or updated_at
        if result.filled_size is not None:
            order.filled_size = result.filled_size
        if result.average_fill_price is not None:
            order.average_fill_price = result.average_fill_price
        if order.average_fill_price is not None and order.filled_size > ZERO:
            order.filled_notional_usd = order.average_fill_price * order.filled_size
