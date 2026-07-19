import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, inspect, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import (
    LiveCopyFillState,
    LiveCopySourceState,
    PaperCopyAllocation,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletScore,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import HyperliquidLiveTradingClient
from app.services.live_copy_state_service import (
    LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
    LIVE_COPY_ORIGIN_REALTIME,
    LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    LIVE_COPY_OUTCOME_TERMINAL_SKIP,
    LiveCopyProcessingDeferred,
    LiveCopyProcessingOrigin,
    acquire_live_copy_source_lock,
    claim_live_copy_fill_part,
    ensure_live_copy_fill_plan_states,
    ensure_live_copy_source_state,
    link_live_copy_fill_state_to_order,
    live_copy_entry_follows_owned_position_lifecycle,
    live_copy_unresolved_order_predicate,
    load_live_copy_recovery_candidate_fills,
    load_live_copy_source_eligibility_epochs,
    load_owned_live_copy_account_source_pairs,
    mark_live_copy_fill_baseline_ignored,
    mark_live_copy_fill_complete_if_durable,
    mark_live_copy_fill_retryable,
    mark_live_copy_fill_terminal_skip,
    preexisting_market_matches_part,
    synchronize_live_copy_source_activity,
)
from app.services.live_trading_service import (
    LIVE_CAPITAL_MODE_UNIFIED,
    LIVE_EXCHANGE_SOURCE,
    LIVE_MANUAL_TEST_SOURCE,
    POSITION_EPSILON,
    LiveCopyEntryLifecycleDeferred,
    LiveOrderSubmitError,
    LiveReconciliationError,
    is_retryable_live_order_submit_failure,
    live_capital_mode,
    live_perp_equity_usd,
    live_tradable_equity_usd,
    live_unified_equity_usd,
    load_live_source_position,
    reconcile_live_trading_account,
    submit_live_trade_intent,
    sync_live_position_margin_setting,
)
from app.services.market_price_cache import MarketPriceCache, dex_from_coin
from app.services.paper_trading_service import (
    ExecutionMarketPrices,
    PaperCopyBatchResult,
    PaperSourceAccountState,
    PaperSourceAllocation,
    SourceFillPart,
    build_execution_context,
    combine_skip_reasons,
    decimal_or_zero,
    fill_datetime,
    is_preexisting_source_add,
    leverage_for_fill,
    load_execution_market_prices,
    load_source_account_state,
    load_source_account_states,
    paper_source_fill_from_wallet_fill,
    part_requires_source_equity,
    plan_source_fill,
    refresh_paper_copy_allocations,
    resolve_coin_decimal,
    resolve_coin_margin_mode,
    resolve_source_current_position,
    sorted_paper_source_fills,
    source_fill_age_exceeds_entry_limit,
    source_state_available_for_reconciliation,
)
from app.services.source_fill_ordering import (
    decimal_or_none as source_order_decimal_or_none,
)
from app.services.source_fill_ordering import (
    source_fill_order_components,
    source_fill_order_key,
)
from app.services.trading_core import (
    MarginMode,
    TradeIntent,
    adjust_open_sizing_to_min_order,
    build_client_order_id,
    build_copy_trade_intent,
    margin_from_notional,
    trade_is_buy,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
PENDING_CLOSE_ORDER_STATUSES = {
    "ready",
    "submitting",
    "uncertain",
    "submitted",
    "accepted",
    "partially_filled",
    "filled",
}
LIVE_CLOSE_AGGREGATED_SKIP_REASON = "live_close_aggregated_into_later_order"
LIVE_COPY_TERMINAL_SKIP_ERRORS = frozenset(
    {
        "skip:live_account_exit_only",
        "skip:live_account_not_enabled",
        "skip:live_close_below_min_order_notional",
    }
)
LIVE_COPY_RETRYABLE_SKIP_REASONS = frozenset(
    {
        "live_execution_busy",
        "live_execution_price_unavailable",
        "live_reconciliation_failed",
        "live_reconciliation_incomplete",
        "live_reconciliation_stale",
        "live_source_leverage_missing",
        "live_source_attribution_ambiguous",
        "live_source_margin_mode_missing",
        "source_account_margin_summary_missing",
        "source_account_state_fetch_failed",
        "source_account_state_missing",
        "source_perp_equity_missing",
        "source_perp_equity_zero",
    }
)


@dataclass(frozen=True, slots=True)
class LiveSourceLifecycleProof:
    """Reconstructed ownership evidence for the currently open exchange lifecycle."""

    aggregate_signed_size: Decimal
    contributions: tuple[tuple[str, Decimal], ...]
    lifecycle_opened_at: datetime | None
    source_first_fill_at: datetime | None
    last_fill_at: datetime | None
    history_incomplete: bool
    source_opening_fill_id: str | None = None
    source_opening_sequence_index: int | None = None


class LiveCopyPartDeferred(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LiveCopyPartTerminal(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def live_skip(reason: str, count: int = 1) -> PaperCopyBatchResult:
    return PaperCopyBatchResult(
        skipped_fills=count,
        skip_reasons={reason: count} if count > 0 else {},
    )


def live_copy_allocation_equity_usd(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None = None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        equity_usd = live_unified_equity_usd(account)
        return equity_usd if equity_usd > ZERO else account.equity_usd or ZERO
    return live_perp_equity_usd(account, dex=dex)


async def synchronize_live_copy_lanes(
    session: AsyncSession,
    *,
    accounts: list[TradingAccount],
    active_source_wallets: set[str],
    protected_account_keys: set[str] | None = None,
) -> set[tuple[str, str]]:
    """Synchronize live lanes before a live worker performs source I/O.

    Paper allocation refresh intentionally does not own live lifecycle locks.
    This coordinator is called only by live control and worker paths, then its
    transaction is committed before Hyperliquid requests begin.
    """

    account_by_key = {account.key: account for account in accounts}
    owned_pairs = await load_owned_live_copy_account_source_pairs(
        session,
        account_keys=set(account_by_key),
    )
    source_epochs = await load_live_copy_source_eligibility_epochs(session)
    selected_pairs = {
        (account.key, source_wallet)
        for account in accounts
        if account.status == "enabled"
        for source_wallet in active_source_wallets
    }
    eligible_pairs = selected_pairs | owned_pairs
    eligible_epochs = {
        (account_key, source_wallet): max(
            account_by_key[account_key].status_changed_at,
            source_epochs.get(source_wallet, account_by_key[account_key].status_changed_at),
        )
        for account_key, source_wallet in selected_pairs
        if account_key in account_by_key
    }
    await synchronize_live_copy_source_activity(
        session,
        eligible_account_source_pairs=eligible_pairs,
        entry_eligible_account_source_pairs=selected_pairs,
        eligible_account_source_epochs=eligible_epochs,
        protected_account_keys=protected_account_keys,
        target_account_keys=set(account_by_key),
    )
    return owned_pairs


async def bootstrap_missing_live_source_attribution(
    session: AsyncSession,
    *,
    accounts: list[TradingAccount],
) -> int:
    """Recover only proven source attribution for legacy exchange positions.

    This runs before source lanes are synchronized.  It never creates a source
    state or baseline for ambiguous, manual, incomplete, or historical data.
    """

    account_keys = {account.key for account in accounts}
    if not account_keys or not callable(getattr(session, "execute", None)):
        return 0
    accounts_by_key = {account.key: account for account in accounts}
    result = await session.execute(
        select(
            TradingPosition.account_key,
            TradingPosition.coin,
            TradingPosition.side,
            TradingFill.source_wallet,
        )
        .join(
            TradingFill,
            and_(
                TradingFill.account_key == TradingPosition.account_key,
                TradingFill.account_type == TradingPosition.account_type,
                TradingFill.coin == TradingPosition.coin,
            ),
        )
        .where(
            TradingPosition.account_key.in_(account_keys),
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
            TradingPosition.size > POSITION_EPSILON,
            TradingFill.source_wallet != "",
            TradingFill.source_wallet != LIVE_EXCHANGE_SOURCE,
            TradingFill.source_wallet != LIVE_MANUAL_TEST_SOURCE,
        )
        .distinct()
    )
    recovered = 0
    for account_key, coin, side, source_wallet in result.all():
        account = accounts_by_key.get(str(account_key))
        if account is None or not coin or not side or not source_wallet:
            continue
        try:
            position = await recover_live_source_position_attribution(
                session,
                account=account,
                source_wallet=str(source_wallet).lower(),
                coin=str(coin),
                side=str(side),
            )
        except LiveCopyPartDeferred:
            continue
        recovered += int(position is not None)
    return recovered


async def process_live_copy_fills(
    session: AsyncSession,
    *,
    source_wallet: str,
    fills: list[dict[str, Any]],
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    price_cache: MarketPriceCache | None = None,
    origin: LiveCopyProcessingOrigin = LIVE_COPY_ORIGIN_REALTIME,
    realtime_observed_at: datetime | None = None,
    accounts_override: list[TradingAccount] | None = None,
    prechecked_reconciliation_failures: set[str] | None = None,
    lanes_synchronized: bool = False,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if not fills or not live_copy_processing_enabled(resolved_settings):
        return PaperCopyBatchResult()

    normalized_source_wallet = source_wallet.lower()
    allocations = await refresh_paper_copy_allocations(session, settings=resolved_settings)
    allocation = allocations.get(normalized_source_wallet)
    if allocation is None:
        return live_skip("live_allocation_missing", len(fills))

    candidate_accounts = (
        accounts_override
        if accounts_override is not None
        else await load_live_accounts_for_source_copy(
            session,
            source_wallet=normalized_source_wallet,
        )
    )
    if not lanes_synchronized:
        if callable(getattr(session, "execute", None)):
            await bootstrap_missing_live_source_attribution(
                session,
                accounts=candidate_accounts,
            )
            await synchronize_live_copy_lanes(
                session,
                accounts=candidate_accounts,
                active_source_wallets={
                    source for source, candidate in allocations.items() if candidate.active
                },
            )
        await session.commit()
        lanes_synchronized = True
    accounts = candidate_accounts
    accounts = await filter_live_accounts_for_source_allocation(
        session,
        accounts=accounts,
        source_wallet=normalized_source_wallet,
        allocation=allocation,
    )
    if not accounts:
        return live_skip("live_no_enabled_accounts", len(fills))

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_live_copy_fills(
                session,
                source_wallet=source_wallet,
                fills=fills,
                settings=resolved_settings,
                client=hyperliquid_client,
                trading_client=trading_client,
                price_cache=price_cache,
                origin=origin,
                realtime_observed_at=realtime_observed_at,
                accounts_override=candidate_accounts,
                prechecked_reconciliation_failures=prechecked_reconciliation_failures,
                lanes_synchronized=lanes_synchronized,
            )

    source_account_states_task = load_source_account_states(
        client=client,
        source_wallet=normalized_source_wallet,
        fills=fills,
    )
    market_prices_task = load_execution_market_prices(
        client=client,
        fills=fills,
        latency_ms=0,
        settings=resolved_settings,
        price_cache=price_cache,
    )
    source_account_states, market_prices = await gather_two(
        source_account_states_task,
        market_prices_task,
    )

    accounts = (
        accounts_override
        if accounts_override is not None
        else await load_live_accounts_for_source_copy(
            session,
            source_wallet=normalized_source_wallet,
        )
    )
    accounts = await filter_live_accounts_for_source_allocation(
        session,
        accounts=accounts,
        source_wallet=normalized_source_wallet,
        allocation=allocation,
    )
    if not accounts:
        return live_skip("live_no_enabled_accounts", len(fills))
    failed_reconciliation_accounts = prechecked_reconciliation_failures
    if failed_reconciliation_accounts is None:
        failed_reconciliation_accounts = await refresh_stale_live_copy_accounts(
            session,
            accounts=accounts,
            settings=resolved_settings,
            client=client,
        )

    processed = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    touched_accounts: dict[str, TradingAccount] = {}
    deferred_reasons: set[str] = set()
    blocked_market_lanes: set[tuple[str, str]] = set()
    live_client = trading_client or HyperliquidLiveTradingClient(settings=resolved_settings)
    source_lifecycle_states = {
        account.key: await ensure_live_copy_source_state(
            session,
            account_key=account.key,
            source_wallet=normalized_source_wallet,
            reactivate=False,
        )
        for account in accounts
    }
    execution_fill_ids = {
        str(fill.get("externalFillId") or "") for fill in fills if fill.get("externalFillId")
    }
    first_observed_at_by_fill_id: dict[str, datetime] = {}
    if execution_fill_ids and callable(getattr(session, "execute", None)):
        first_observed_result = await session.execute(
            select(WalletFill.external_fill_id, WalletFill.received_at).where(
                WalletFill.wallet_address == normalized_source_wallet,
                WalletFill.external_fill_id.in_(execution_fill_ids),
            )
        )
        first_observed_at_by_fill_id = {
            str(source_fill_id): received_at
            for source_fill_id, received_at in first_observed_result.all()
            if source_fill_id and received_at is not None
        }

    def add_skip(reason: str, count: int = 1) -> None:
        nonlocal skipped
        skipped += count
        skip_reasons[reason] = skip_reasons.get(reason, 0) + count

    sorted_fills = sorted_paper_source_fills(fills)
    planned_fills: list[tuple[dict[str, Any], list[SourceFillPart]]] = []
    for fill in sorted_fills:
        if not str(fill.get("externalFillId") or ""):
            add_skip("live_source_fill_id_missing", len(accounts))
            continue
        parts = plan_source_fill(fill)
        if not parts:
            add_skip("live_no_planned_source_parts", len(accounts))
            continue
        planned_fills.append((fill, parts))
        for account in accounts:
            lifecycle_state = source_lifecycle_states[account.key]
            await ensure_live_copy_fill_plan_states(
                session,
                source_state=lifecycle_state,
                fill=fill,
                planned_parts=parts,
                origin=origin,
                observed_at=realtime_observed_at,
                first_observed_at=first_observed_at_by_fill_id.get(
                    str(fill.get("externalFillId") or "")
                ),
            )

    # Terminal entry decisions are independent of execution ordering.  Make
    # them before an unresolved earlier order can hide stale or baseline work.
    for fill, parts in planned_fills:
        for account in accounts:
            lifecycle_state = source_lifecycle_states[account.key]
            pre_barrier_terminalized = False
            for part in parts:
                if part.action not in {"open", "add", "flip_open"}:
                    continue
                stale_entry = source_fill_age_exceeds_entry_limit(
                    fill,
                    settings=resolved_settings,
                )
                claim = await claim_live_copy_fill_part(
                    session,
                    source_state=lifecycle_state,
                    fill=fill,
                    part=part,
                    origin=origin,
                    entry_is_stale=stale_entry,
                )
                if claim.state is None or claim.reason in {"complete", "blocked", "missing_plan"}:
                    continue
                if claim.reason == "baseline":
                    await mark_live_copy_fill_baseline_ignored(
                        session,
                        source_state=lifecycle_state,
                        fill_state=claim.state,
                        part=part,
                        reason="live_copy_baseline_entry",
                        record_preexisting_market=False,
                    )
                    pre_barrier_terminalized = True
                    continue
                if not stale_entry or not claim.claimed:
                    continue
                await mark_live_copy_fill_terminal_skip(
                    session,
                    fill_state=claim.state,
                    reason="live_source_fill_too_old",
                )
                fill_result = live_skip("live_source_fill_too_old")
                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
                skip_reasons = combine_skip_reasons(skip_reasons, fill_result.skip_reasons)
                pre_barrier_terminalized = True
            if pre_barrier_terminalized:
                await mark_live_copy_fill_complete_if_durable(
                    session,
                    source_state=lifecycle_state,
                    source_fill_id=str(fill.get("externalFillId") or ""),
                    planned_parts=parts,
                )
    await session.commit()

    for fill, parts in planned_fills:
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
            market_lane = (account.key, str(fill.get("coin") or ""))
            if market_lane in blocked_market_lanes:
                continue
            lifecycle_state = source_lifecycle_states[account.key]
            account_fill_deferred = False
            for part in parts:
                if account_fill_deferred:
                    break
                stale_entry = part_requires_source_equity(
                    part
                ) and source_fill_age_exceeds_entry_limit(fill, settings=resolved_settings)
                claim = await claim_live_copy_fill_part(
                    session,
                    source_state=lifecycle_state,
                    fill=fill,
                    part=part,
                    origin=origin,
                    entry_is_stale=stale_entry,
                )
                fill_state = claim.state
                if claim.reason == "blocked":
                    deferred_reasons.add("live_prior_source_fill_pending")
                    account_fill_deferred = True
                    continue
                if claim.reason == "missing_plan":
                    deferred_reasons.add("live_copy_plan_missing")
                    account_fill_deferred = True
                    continue
                if fill_state is None:
                    continue
                if claim.reason == "complete":
                    continue
                if claim.reason == "not_due":
                    deferred_reasons.add(fill_state.reason or "live_copy_retry_not_due")
                    account_fill_deferred = True
                    continue

                baseline_part = claim.reason == "baseline"
                if baseline_part and part_requires_source_equity(part):
                    await mark_live_copy_fill_baseline_ignored(
                        session,
                        source_state=lifecycle_state,
                        fill_state=fill_state,
                        part=part,
                        reason="live_copy_baseline_entry",
                    )
                    continue

                if stale_entry:
                    await mark_live_copy_fill_terminal_skip(
                        session,
                        fill_state=fill_state,
                        reason="live_source_fill_too_old",
                    )
                    fill_result = live_skip("live_source_fill_too_old")
                else:
                    if not lifecycle_state.entry_eligible and part.action in {
                        "open",
                        "add",
                        "flip_open",
                    }:
                        raw_json = fill.get("rawJson")
                        retained_continuation = (
                            await live_copy_entry_follows_owned_position_lifecycle(
                                session,
                                source_state=lifecycle_state,
                                fill=fill,
                                action=part.action,
                                side=part.side,
                                start_position=(
                                    raw_json.get("startPosition")
                                    if isinstance(raw_json, dict)
                                    else None
                                ),
                            )
                        )
                        if not retained_continuation:
                            await mark_live_copy_fill_baseline_ignored(
                                session,
                                source_state=lifecycle_state,
                                fill_state=fill_state,
                                part=part,
                                reason="live_retained_source_new_market",
                                record_preexisting_market=False,
                            )
                            continue
                    try:
                        unowned_source_lifecycle = await live_copy_part_is_unowned_source_lifecycle(
                            session,
                            account=account,
                            source_state=lifecycle_state,
                            fill=fill,
                            part=part,
                            baseline_part=baseline_part,
                        )
                    except LiveCopyPartDeferred as exc:
                        await mark_live_copy_fill_retryable(
                            session,
                            fill_state=fill_state,
                            reason=exc.reason,
                        )
                        deferred_reasons.add(exc.reason)
                        account_fill_deferred = True
                        continue
                    if unowned_source_lifecycle:
                        await mark_live_copy_fill_baseline_ignored(
                            session,
                            source_state=lifecycle_state,
                            fill_state=fill_state,
                            part=part,
                            reason="unowned_preexisting_lifecycle",
                        )
                        continue

                if (
                    account.key in failed_reconciliation_accounts
                    and part_requires_source_equity(part)
                    and not stale_entry
                ):
                    reason = "live_reconciliation_deferred"
                    await mark_live_copy_fill_retryable(
                        session,
                        fill_state=fill_state,
                        reason=reason,
                    )
                    deferred_reasons.add(reason)
                    account_fill_deferred = True
                    continue
                elif (
                    source_state_skip_reason is not None
                    and part_requires_source_equity(part)
                    and not stale_entry
                ):
                    await mark_live_copy_fill_retryable(
                        session,
                        fill_state=fill_state,
                        reason=source_state_skip_reason,
                    )
                    deferred_reasons.add(source_state_skip_reason)
                    account_fill_deferred = True
                    continue
                elif not stale_entry:
                    # Claims and any source-lifecycle repair hold transaction
                    # advisory locks.  Persist that work before submission so
                    # the execution gate can lock account then lifecycle.
                    await session.commit()
                    try:
                        fill_result = await apply_live_copy_part(
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
                            trading_client=live_client,
                        )
                    except LiveCopyPartTerminal as exc:
                        await mark_live_copy_fill_terminal_skip(
                            session,
                            fill_state=fill_state,
                            reason=exc.reason,
                        )
                        fill_result = live_skip(exc.reason)
                    except LiveCopyPartDeferred as exc:
                        await mark_live_copy_fill_retryable(
                            session,
                            fill_state=fill_state,
                            reason=exc.reason,
                        )
                        deferred_reasons.add(exc.reason)
                        account_fill_deferred = True
                        continue

                disposition_is_durable = await finalize_live_copy_fill_disposition(
                    session,
                    fill_state=fill_state,
                )
                if not disposition_is_durable:
                    deferred_reasons.add(fill_state.reason or "live_copy_decision_deferred")
                    account_fill_deferred = True
                    continue

                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
                skip_reasons = combine_skip_reasons(skip_reasons, fill_result.skip_reasons)
                if fill_result.processed_fills > 0:
                    touched_accounts[account.key] = account
                    await session.commit()

            if not account_fill_deferred:
                await mark_live_copy_fill_complete_if_durable(
                    session,
                    source_state=lifecycle_state,
                    source_fill_id=str(fill.get("externalFillId") or ""),
                    planned_parts=parts,
                )
            else:
                blocked_market_lanes.add(market_lane)

    for account in touched_accounts.values():
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=resolved_settings,
            info_client=client,
        )

    await session.commit()
    if deferred_reasons:
        reasons = ", ".join(sorted(deferred_reasons))
        raise LiveCopyProcessingDeferred(
            "live_copy_parts_deferred",
            f"Live copy processing deferred: {reasons}",
        )
    return PaperCopyBatchResult(
        processed_fills=processed,
        skipped_fills=skipped,
        accounts_updated=len(touched_accounts),
        skip_reasons=skip_reasons,
    )


async def live_copy_part_is_unowned_source_lifecycle(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_state: LiveCopySourceState,
    fill: dict[str, Any],
    part: SourceFillPart,
    baseline_part: bool,
) -> bool:
    if baseline_part and part.action in {"open", "add", "flip_open"}:
        return True
    coin = str(fill.get("coin") or "")
    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=source_state.source_wallet,
        coin=coin,
    )
    if position is not None and position.side == part.side:
        if part.action in {"open", "add", "reduce", "close", "flip_close"}:
            position = await repair_live_source_position_lifecycle_key(
                session,
                account=account,
                source_wallet=source_state.source_wallet,
                position=position,
            )
            if live_copy_fill_predates_position_lifecycle(fill, position=position):
                return True
        return False
    if preexisting_market_matches_part(source_state, coin=coin, part=part):
        return True
    continuation_part = part.action in {"reduce", "close", "flip_close"} or (
        part.action in {"open", "add"}
        and is_preexisting_source_add(part.start_position, side=part.side)
    )
    if position is None and not baseline_part and continuation_part:
        position = await recover_live_source_position_attribution(
            session,
            account=account,
            source_wallet=source_state.source_wallet,
            coin=coin,
            side=part.side,
        )
        if position is not None:
            return False
    if part.action == "flip_open":
        return baseline_part and position is None
    if part.action in {"open", "add"}:
        if position is None:
            if part.start_position is None:
                return True
            if is_preexisting_source_add(part.start_position, side=part.side):
                return True
        return baseline_part and position is None
    if part.action not in {"reduce", "close", "flip_close"}:
        return baseline_part and position is None
    if position is not None:
        return True
    if baseline_part:
        return True
    return True


def live_copy_fill_predates_position_lifecycle(
    fill: dict[str, Any],
    *,
    position: TradingPosition,
) -> bool:
    position_lifecycle_key = live_copy_position_lifecycle_order_key(position)
    if position_lifecycle_key is None:
        return True
    return source_fill_order_key(fill) <= position_lifecycle_key


async def repair_live_source_position_lifecycle_key(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_wallet: str,
    position: TradingPosition,
) -> TradingPosition:
    """Repair a legacy position key only from its proven copied source lifecycle."""

    if live_copy_position_lifecycle_order_key(position) is not None:
        return position
    await acquire_live_copy_source_lock(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
    )
    position_result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == position.coin,
            TradingPosition.source_wallet.in_((source_wallet, LIVE_EXCHANGE_SOURCE)),
        )
        .with_for_update()
    )
    locked_positions = list(position_result.all())
    locked_position = next((item for item in locked_positions if item.id == position.id), None)
    exchange_position = next(
        (item for item in locked_positions if item.source_wallet == LIVE_EXCHANGE_SOURCE),
        None,
    )
    if locked_position is None or exchange_position is None:
        raise LiveCopyPartDeferred("live_source_lifecycle_key_unavailable")
    if live_copy_position_lifecycle_order_key(locked_position) is not None:
        return locked_position

    proof = await load_live_source_lifecycle_proof(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
        coin=locked_position.coin,
    )
    normalized_source = source_wallet.lower()
    source_signed_size = dict(proof.contributions).get(normalized_source, ZERO)
    expected_sign = Decimal("1") if locked_position.side == "long" else Decimal("-1")
    locked_signed_size = locked_position.size * expected_sign
    exchange_sign = Decimal("1") if exchange_position.side == "long" else Decimal("-1")
    exchange_signed_size = exchange_position.size * exchange_sign
    manual_signed_size = dict(proof.contributions).get(LIVE_EXCHANGE_SOURCE, ZERO)
    competing_source_exposure = any(
        wallet not in {normalized_source, LIVE_EXCHANGE_SOURCE}
        and abs(signed_size) > POSITION_EPSILON
        for wallet, signed_size in proof.contributions
    )
    expected_source_size = min(abs(source_signed_size), exchange_position.size)
    other_position = await session.scalar(
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == locked_position.coin,
            TradingPosition.source_wallet.not_in((source_wallet, LIVE_EXCHANGE_SOURCE)),
            TradingPosition.size > POSITION_EPSILON,
        )
        .with_for_update()
        .limit(1)
    )
    unresolved_order = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == locked_position.coin,
            live_copy_unresolved_order_predicate(),
        )
        .with_for_update()
        .limit(1)
    )
    if (
        proof.history_incomplete
        or proof.source_opening_fill_id is None
        or proof.source_opening_sequence_index is None
        or exchange_position.size <= POSITION_EPSILON
        or exchange_position.side != locked_position.side
        or abs(locked_position.size - expected_source_size) > POSITION_EPSILON
        or abs(proof.aggregate_signed_size - exchange_signed_size) > POSITION_EPSILON
        or source_signed_size * expected_sign <= POSITION_EPSILON
        or locked_signed_size * expected_sign <= POSITION_EPSILON
        or manual_signed_size * expected_sign > POSITION_EPSILON
        or competing_source_exposure
        or other_position is not None
        or unresolved_order is not None
    ):
        raise LiveCopyPartDeferred("live_source_lifecycle_key_unavailable")
    lifecycle_order = await load_recovered_live_copy_lifecycle_order(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
        source_fill_id=proof.source_opening_fill_id,
        sequence_index=proof.source_opening_sequence_index,
        coin=locked_position.coin,
    )
    if lifecycle_order is None:
        raise LiveCopyPartDeferred("live_source_lifecycle_key_unavailable")
    locked_position.raw_payload = {
        **(locked_position.raw_payload if isinstance(locked_position.raw_payload, dict) else {}),
        "sourceLifecycleOrder": lifecycle_order["raw"],
    }
    for field, value in lifecycle_order["columns"].items():
        setattr(locked_position, field, value)
    await session.flush()
    return locked_position


async def repair_owned_live_source_positions_for_recovery(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_wallet: str,
) -> int:
    """Repair every retained legacy source position before recovery selection."""

    await acquire_live_copy_source_lock(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
    )
    result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.size > POSITION_EPSILON,
        )
        .with_for_update()
    )
    repaired = 0
    for position in result.all():
        if live_copy_position_lifecycle_order_key(position) is not None:
            continue
        try:
            await repair_live_source_position_lifecycle_key(
                session,
                account=account,
                source_wallet=source_wallet,
                position=position,
            )
        except LiveCopyPartDeferred:
            logger.warning(
                "deferred legacy lifecycle repair account=%s source=%s coin=%s",
                account.key,
                source_wallet,
                position.coin,
            )
            continue
        repaired += 1
    return repaired


def live_copy_position_lifecycle_order_key(
    position: TradingPosition,
) -> tuple[int, str, int, Decimal, int, Decimal, str] | None:
    if (
        position.source_lifecycle_timestamp_ms is None
        or position.source_lifecycle_direction_rank is None
        or position.source_lifecycle_position is None
        or position.source_lifecycle_fill_id is None
    ):
        return None
    numeric_fill_id = position.source_lifecycle_fill_id_numeric
    return (
        int(position.source_lifecycle_timestamp_ms),
        position.coin,
        int(position.source_lifecycle_direction_rank),
        source_order_decimal_or_none(position.source_lifecycle_position) or ZERO,
        0 if numeric_fill_id is not None else 1,
        source_order_decimal_or_none(numeric_fill_id) or ZERO,
        position.source_lifecycle_fill_id,
    )


async def recover_live_source_position_attribution(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_wallet: str,
    coin: str,
    side: str,
) -> TradingPosition | None:
    """Restore source attribution only from the current executed market lifecycle."""

    await acquire_live_copy_source_lock(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
    )

    scalar = getattr(session, "scalar", None)
    existing_position = None
    if callable(scalar):
        existing_position = await scalar(
            select(TradingPosition)
            .where(
                TradingPosition.account_key == account.key,
                TradingPosition.account_type == "live",
                TradingPosition.source_wallet == source_wallet,
                TradingPosition.coin == coin,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    if existing_position is not None and existing_position.size > POSITION_EPSILON:
        return existing_position

    if await live_market_is_reserved_by_other_source(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
        coin=coin,
    ):
        raise LiveCopyPartDeferred("live_source_attribution_ambiguous")
    if callable(scalar):
        exchange_position = await scalar(
            select(TradingPosition)
            .where(
                TradingPosition.account_key == account.key,
                TradingPosition.account_type == "live",
                TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
                TradingPosition.coin == coin,
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
    else:
        exchange_position = await load_live_source_position(
            session,
            account_key=account.key,
            source_wallet=LIVE_EXCHANGE_SOURCE,
            coin=coin,
        )
    if (
        exchange_position is None
        or exchange_position.side != side
        or exchange_position.size <= POSITION_EPSILON
    ):
        return None

    proof = await load_live_source_lifecycle_proof(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
        coin=coin,
    )
    lifecycle_order = await load_recovered_live_copy_lifecycle_order(
        session,
        account_key=account.key,
        source_wallet=source_wallet,
        source_fill_id=proof.source_opening_fill_id,
        sequence_index=proof.source_opening_sequence_index,
        coin=coin,
    )
    contribution_by_source = dict(proof.contributions)
    normalized_source = source_wallet.lower()
    source_signed_size = contribution_by_source.get(normalized_source, ZERO)
    if abs(source_signed_size) <= POSITION_EPSILON:
        return None
    exchange_signed_size = (
        exchange_position.size if exchange_position.side == "long" else -exchange_position.size
    )
    competing_source_exposure = any(
        wallet not in {normalized_source, LIVE_EXCHANGE_SOURCE}
        and abs(signed_size) > POSITION_EPSILON
        for wallet, signed_size in proof.contributions
    )
    manual_signed_size = contribution_by_source.get(LIVE_EXCHANGE_SOURCE, ZERO)
    manual_adds_exposure = (
        manual_signed_size > POSITION_EPSILON
        if side == "long"
        else manual_signed_size < -POSITION_EPSILON
    )
    source_side_matches = (
        source_signed_size > POSITION_EPSILON
        if side == "long"
        else source_signed_size < -POSITION_EPSILON
    )
    source_size = abs(source_signed_size)
    aggregate_matches_exchange = (
        abs(proof.aggregate_signed_size - exchange_signed_size) <= POSITION_EPSILON
    )
    source_explains_exchange = source_size + POSITION_EPSILON >= exchange_position.size
    if (
        proof.history_incomplete
        or competing_source_exposure
        or manual_adds_exposure
        or not source_side_matches
        or not aggregate_matches_exchange
        or not source_explains_exchange
        or lifecycle_order is None
    ):
        raise LiveCopyPartDeferred("live_source_attribution_ambiguous")

    recovered_at = datetime.now(UTC)
    recovered_size = min(source_size, exchange_position.size)
    exchange_size = max(exchange_position.size, POSITION_EPSILON)
    recovered_ratio = recovered_size / exchange_size
    exchange_payload = (
        dict(exchange_position.raw_payload)
        if isinstance(exchange_position.raw_payload, dict)
        else {}
    )
    position = TradingPosition(
        account_key=account.key,
        account_type="live",
        source_wallet=source_wallet,
        coin=coin,
        side=side,
        size=recovered_size,
        entry_price=exchange_position.entry_price,
        notional_usd=exchange_position.notional_usd * recovered_ratio,
        leverage=exchange_position.leverage,
        margin_mode=exchange_position.margin_mode,
        margin_usd=exchange_position.margin_usd * recovered_ratio,
        realized_pnl_usd=ZERO,
        fee_usd=ZERO,
        raw_payload={
            **exchange_payload,
            "sourceLifecycleOrder": lifecycle_order["raw"],
            "sourceAttributionRecovery": {
                "exchangePositionId": str(exchange_position.id),
                "lifecycleOpenedAt": (
                    proof.lifecycle_opened_at.isoformat()
                    if proof.lifecycle_opened_at is not None
                    else None
                ),
                "recoveredAt": recovered_at.isoformat(),
                "recoveredSize": str(recovered_size),
                "sourceWallet": source_wallet,
            },
        },
        **lifecycle_order["columns"],
        opened_at=exchange_position.opened_at,
        last_reconciled_at=exchange_position.last_reconciled_at or recovered_at,
    )
    session.add(position)
    await session.flush()
    logger.warning(
        "restored live source position attribution account=%s source=%s coin=%s side=%s",
        account.key,
        source_wallet,
        coin,
        side,
    )
    return position


async def load_live_source_lifecycle_proof(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> LiveSourceLifecycleProof:
    """Load and reconstruct the current exchange lifecycle from executed fills."""

    result = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.coin == coin,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    fills = list(result.all())
    normalized_source = source_wallet.lower()
    source_fill_keys = {
        (str(fill.source_fill_id), int(fill.sequence_index))
        for fill in fills
        if str(fill.source_wallet or "").lower() == normalized_source
        and fill.source_fill_id
        and fill.sequence_index is not None
    }
    if any(
        str(fill.source_wallet or "").lower() == normalized_source
        and (not fill.source_fill_id or fill.sequence_index is None)
        for fill in fills
    ):
        raise LiveCopyPartDeferred("live_source_attribution_ambiguous")

    source_fill_ids = sorted({source_fill_id for source_fill_id, _ in source_fill_keys})
    source_states_by_key: dict[tuple[str, int], LiveCopyFillState] = {}
    wallet_fills_by_id: dict[str, WalletFill] = {}
    if source_fill_ids:
        state_result = await session.scalars(
            select(LiveCopyFillState)
            .where(
                LiveCopyFillState.account_key == account_key,
                LiveCopyFillState.account_type == "live",
                LiveCopyFillState.source_wallet == normalized_source,
                LiveCopyFillState.source_fill_id.in_(source_fill_ids),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        source_states_by_key = {
            (str(state.source_fill_id), int(state.sequence_index)): state
            for state in state_result.all()
        }
        wallet_result = await session.scalars(
            select(WalletFill)
            .where(
                WalletFill.wallet_address == normalized_source,
                WalletFill.external_fill_id.in_(source_fill_ids),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        wallet_fills_by_id = {
            str(wallet_fill.external_fill_id): wallet_fill for wallet_fill in wallet_result.all()
        }
    return reconstruct_live_source_lifecycle(
        order_live_source_lifecycle_fills(
            fills,
            source_wallet=normalized_source,
            source_states_by_key=source_states_by_key,
            wallet_fills_by_id=wallet_fills_by_id,
        ),
        source_wallet=source_wallet,
    )


def order_live_source_lifecycle_fills(
    fills: list[TradingFill],
    *,
    source_wallet: str,
    source_states_by_key: dict[tuple[str, int], LiveCopyFillState],
    wallet_fills_by_id: dict[str, WalletFill],
) -> list[TradingFill]:
    """Order lifecycle history from durable source order keys where available.

    Attribution and legacy-key repair cannot safely infer source ownership from
    exchange insertion order.  Copied fills use their durable source plan key
    and sequence index.  Internal and manual fills retain exchange chronology,
    with reductions before openings at the same exchange timestamp.
    """

    normalized_source = source_wallet.lower()

    def sort_key(fill: TradingFill) -> tuple[Any, ...]:
        wallet = str(fill.source_wallet or "").lower()
        fallback_filled_at = live_copy_datetime_utc(fill.filled_at)
        fallback_created_at = (
            live_copy_datetime_utc(fill.created_at)
            if fill.created_at is not None
            else datetime.min.replace(tzinfo=UTC)
        )
        fallback_identifier = str(fill.exchange_fill_id or fill.source_fill_id or fill.id or "")
        if wallet != normalized_source:
            action_rank = 0 if fill.action in {"close", "reduce", "flip_close"} else 1
            return (
                int(fallback_filled_at.timestamp() * 1000),
                action_rank,
                str(fill.coin or ""),
                ZERO,
                1,
                ZERO,
                fallback_identifier,
                int(fill.sequence_index or 0),
                fallback_filled_at,
                fallback_created_at,
                str(fill.id or ""),
            )

        if not fill.source_fill_id or fill.sequence_index is None:
            raise LiveCopyPartDeferred("live_source_attribution_ambiguous")
        source_fill_id = str(fill.source_fill_id)
        sequence_index = int(fill.sequence_index)
        source_state = source_states_by_key.get((source_fill_id, sequence_index))
        if source_state is not None:
            if (
                source_state.coin != fill.coin
                or source_state.action != fill.action
                or source_state.side != fill.side
            ):
                raise LiveCopyPartDeferred("live_source_attribution_ambiguous")
            source_timestamp_ms = int(source_state.source_timestamp_ms)
            direction_rank = int(source_state.source_order_direction_rank)
            position_rank = source_order_decimal_or_none(source_state.source_order_position) or ZERO
            numeric_fill_id = source_order_decimal_or_none(
                source_state.source_order_fill_id_numeric
            )
            ordered_fill_id = str(source_state.source_fill_id)
        else:
            wallet_fill = wallet_fills_by_id.get(source_fill_id)
            if wallet_fill is None or wallet_fill.coin != fill.coin:
                raise LiveCopyPartDeferred("live_source_attribution_ambiguous")
            canonical_key = source_fill_order_key(paper_source_fill_from_wallet_fill(wallet_fill))
            (
                source_timestamp_ms,
                _coin,
                direction_rank,
                position_rank,
                numeric_rank,
                numeric_fill_id,
                ordered_fill_id,
            ) = canonical_key
            return (
                int(source_timestamp_ms),
                int(direction_rank),
                str(fill.coin or ""),
                position_rank,
                int(numeric_rank),
                numeric_fill_id,
                ordered_fill_id,
                sequence_index,
                fallback_filled_at,
                fallback_created_at,
                str(fill.id or ""),
            )

        return (
            source_timestamp_ms,
            direction_rank,
            str(fill.coin or ""),
            position_rank,
            0 if numeric_fill_id is not None else 1,
            numeric_fill_id or ZERO,
            ordered_fill_id,
            sequence_index,
            fallback_filled_at,
            fallback_created_at,
            str(fill.id or ""),
        )

    return sorted(fills, key=sort_key)


async def load_recovered_live_copy_lifecycle_order(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str | None,
    sequence_index: int | None,
    coin: str,
) -> dict[str, Any] | None:
    if not source_fill_id or sequence_index is None:
        return None
    opening_fills = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.source_wallet == source_wallet,
            TradingFill.source_fill_id == source_fill_id,
            TradingFill.sequence_index == sequence_index,
            TradingFill.coin == coin,
        )
        .with_for_update()
    )
    opening_fill_rows = list(opening_fills.all())
    opening_order_ids = {fill.order_id for fill in opening_fill_rows if fill.order_id is not None}
    if (
        not opening_fill_rows
        or any(fill.order_id is None for fill in opening_fill_rows)
        or len(opening_order_ids) != 1
    ):
        return None
    opening_order_id = next(iter(opening_order_ids))
    state = await session.scalar(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == account_key,
            LiveCopyFillState.source_wallet == source_wallet,
            LiveCopyFillState.source_fill_id == source_fill_id,
            LiveCopyFillState.sequence_index == sequence_index,
        )
        .with_for_update()
    )
    order = await session.scalar(
        select(TradingOrder)
        .where(
            TradingOrder.id == opening_order_id,
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == source_wallet,
            TradingOrder.source_fill_id == source_fill_id,
            TradingOrder.sequence_index == sequence_index,
            TradingOrder.coin == coin,
        )
        .with_for_update()
    )
    if (
        order is None
        or order.action not in {"open", "add", "flip_open"}
        or order.reduce_only
        or any(fill.action != order.action or fill.side != order.side for fill in opening_fill_rows)
    ):
        return None
    wallet_fill = await session.scalar(
        select(WalletFill)
        .where(
            WalletFill.wallet_address == source_wallet,
            WalletFill.external_fill_id == source_fill_id,
            WalletFill.coin == coin,
        )
        .with_for_update()
    )
    wallet_lifecycle_order = live_copy_position_lifecycle_order_from_wallet_fill(wallet_fill)
    if wallet_lifecycle_order is None:
        return None
    state_lifecycle_order = live_copy_position_lifecycle_order_from_state(state)
    if state_lifecycle_order is None:
        return wallet_lifecycle_order
    state_key = (
        state_lifecycle_order["columns"]["source_lifecycle_timestamp_ms"],
        state.coin,
        state_lifecycle_order["columns"]["source_lifecycle_direction_rank"],
        state_lifecycle_order["columns"]["source_lifecycle_position"],
        0
        if state_lifecycle_order["columns"]["source_lifecycle_fill_id_numeric"] is not None
        else 1,
        state_lifecycle_order["columns"]["source_lifecycle_fill_id_numeric"] or ZERO,
        state_lifecycle_order["columns"]["source_lifecycle_fill_id"],
    )
    if state_key != source_fill_order_key(paper_source_fill_from_wallet_fill(wallet_fill)):
        return None
    return state_lifecycle_order


def live_copy_position_lifecycle_order_from_state(
    state: LiveCopyFillState | None,
) -> dict[str, Any] | None:
    if state is None:
        return None
    numeric_fill_id = state.source_order_fill_id_numeric
    raw = {
        "timestampMs": int(state.source_timestamp_ms),
        "coin": state.coin,
        "directionRank": int(state.source_order_direction_rank),
        "positionRank": str(state.source_order_position),
        "fillIdNumeric": str(numeric_fill_id) if numeric_fill_id is not None else None,
        "fillId": state.source_fill_id,
    }
    return {
        "raw": raw,
        "columns": {
            "source_lifecycle_timestamp_ms": int(state.source_timestamp_ms),
            "source_lifecycle_direction_rank": int(state.source_order_direction_rank),
            "source_lifecycle_position": state.source_order_position,
            "source_lifecycle_fill_id_numeric": numeric_fill_id,
            "source_lifecycle_fill_id": state.source_fill_id,
        },
    }


def live_copy_position_lifecycle_order_from_wallet_fill(
    fill: WalletFill | None,
) -> dict[str, Any] | None:
    if fill is None:
        return None
    source_fill = paper_source_fill_from_wallet_fill(fill)
    direction_rank, position_rank, numeric_fill_id = source_fill_order_components(source_fill)
    return {
        "raw": {
            "timestampMs": int(fill.timestamp_ms),
            "coin": fill.coin,
            "directionRank": int(direction_rank),
            "positionRank": str(position_rank),
            "fillIdNumeric": str(numeric_fill_id) if numeric_fill_id is not None else None,
            "fillId": fill.external_fill_id,
        },
        "columns": {
            "source_lifecycle_timestamp_ms": int(fill.timestamp_ms),
            "source_lifecycle_direction_rank": int(direction_rank),
            "source_lifecycle_position": position_rank,
            "source_lifecycle_fill_id_numeric": numeric_fill_id,
            "source_lifecycle_fill_id": fill.external_fill_id,
        },
    }


def reconstruct_live_source_lifecycle(
    fills: list[TradingFill],
    *,
    source_wallet: str,
) -> LiveSourceLifecycleProof:
    """Reconstruct current per-source exposure since the latest proven flat state."""

    normalized_source = source_wallet.lower()
    contributions: dict[str, Decimal] = {}
    aggregate_signed_size = ZERO
    lifecycle_opened_at: datetime | None = None
    source_first_fill_at: datetime | None = None
    source_opening_fill_id: str | None = None
    source_opening_sequence_index: int | None = None
    last_fill_at: datetime | None = None
    history_incomplete = False

    for fill in fills:
        fill_size = abs(fill.size or ZERO)
        if fill_size <= POSITION_EPSILON:
            continue
        wallet = str(fill.source_wallet or "").lower()
        side_sign = Decimal("1") if fill.side == "long" else Decimal("-1")
        opens_exposure = fill.action in {"open", "add", "flip_open"}
        delta = side_sign * fill_size * (Decimal("1") if opens_exposure else Decimal("-1"))
        filled_at = live_copy_datetime_utc(fill.filled_at)

        if abs(aggregate_signed_size) <= POSITION_EPSILON:
            contributions = {}
            aggregate_signed_size = ZERO
            lifecycle_opened_at = filled_at
            source_first_fill_at = filled_at if wallet == normalized_source else None
            source_opening_fill_id = fill.source_fill_id if wallet == normalized_source else None
            source_opening_sequence_index = (
                fill.sequence_index if wallet == normalized_source else None
            )
            history_incomplete = fill.action not in {"open", "flip_open"}
        elif wallet == normalized_source and source_first_fill_at is None:
            source_first_fill_at = filled_at
            source_opening_fill_id = fill.source_fill_id
            source_opening_sequence_index = fill.sequence_index

        previous_signed_size = aggregate_signed_size
        contributions[wallet] = contributions.get(wallet, ZERO) + delta
        if abs(contributions[wallet]) <= POSITION_EPSILON:
            contributions.pop(wallet, None)
        aggregate_signed_size = sum(contributions.values(), ZERO)
        last_fill_at = filled_at

        if (
            abs(previous_signed_size) > POSITION_EPSILON
            and abs(aggregate_signed_size) > POSITION_EPSILON
            and (previous_signed_size > ZERO) != (aggregate_signed_size > ZERO)
        ):
            history_incomplete = True
        if abs(aggregate_signed_size) <= POSITION_EPSILON:
            contributions = {}
            aggregate_signed_size = ZERO
            lifecycle_opened_at = None
            source_first_fill_at = None
            source_opening_fill_id = None
            source_opening_sequence_index = None
            history_incomplete = False

    return LiveSourceLifecycleProof(
        aggregate_signed_size=aggregate_signed_size,
        contributions=tuple(sorted(contributions.items())),
        lifecycle_opened_at=lifecycle_opened_at,
        source_first_fill_at=source_first_fill_at,
        last_fill_at=last_fill_at,
        history_incomplete=history_incomplete,
        source_opening_fill_id=source_opening_fill_id,
        source_opening_sequence_index=source_opening_sequence_index,
    )


def live_copy_datetime_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


async def finalize_live_copy_fill_disposition(
    session: AsyncSession,
    *,
    fill_state: LiveCopyFillState,
) -> bool:
    state_identity = inspect(fill_state).identity
    state_lookup = (
        LiveCopyFillState.id == state_identity[0]
        if state_identity is not None
        else and_(
            LiveCopyFillState.account_key == fill_state.account_key,
            LiveCopyFillState.source_wallet == fill_state.source_wallet,
            LiveCopyFillState.source_fill_id == fill_state.source_fill_id,
            LiveCopyFillState.sequence_index == fill_state.sequence_index,
        )
    )
    refreshed_state = await session.scalar(
        select(LiveCopyFillState)
        .where(state_lookup)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if refreshed_state is not None:
        fill_state = refreshed_state
    if fill_state.outcome in {
        LIVE_COPY_OUTCOME_TERMINAL_SKIP,
        LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    }:
        return True
    order = await session.scalar(
        select(TradingOrder).where(
            TradingOrder.account_key == fill_state.account_key,
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == fill_state.source_wallet,
            TradingOrder.source_fill_id == fill_state.source_fill_id,
            TradingOrder.sequence_index == fill_state.sequence_index,
        )
    )
    if order is None:
        await mark_live_copy_fill_retryable(
            session,
            fill_state=fill_state,
            reason="live_copy_decision_missing",
        )
        return False
    if (
        is_retryable_live_order_submit_failure(order)
        and order.error not in LIVE_COPY_TERMINAL_SKIP_ERRORS
    ):
        await mark_live_copy_fill_retryable(
            session,
            fill_state=fill_state,
            reason=order.error or "live_order_retryable_failure",
        )
        return False
    if order.order_type == "skip":
        reason = order.error.removeprefix("skip:") if order.error else None
        await link_live_copy_fill_state_to_order(
            session,
            fill_state=fill_state,
            order=order,
            terminal_skip=True,
            reason=reason,
        )
        return True
    await link_live_copy_fill_state_to_order(
        session,
        fill_state=fill_state,
        order=order,
        terminal_skip=False,
        reason=order.error,
    )
    return True


async def process_live_copy_recovery(
    session: AsyncSession,
    *,
    source_wallet: str | None = None,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    price_cache: MarketPriceCache | None = None,
    max_sources: int = 100,
    fill_limit_per_source: int = 1000,
    origin: LiveCopyProcessingOrigin = LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if not live_copy_processing_enabled(resolved_settings):
        return PaperCopyBatchResult()

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_live_copy_recovery(
                session,
                source_wallet=source_wallet,
                settings=resolved_settings,
                client=hyperliquid_client,
                trading_client=trading_client,
                price_cache=price_cache,
                max_sources=max_sources,
                fill_limit_per_source=fill_limit_per_source,
                origin=origin,
            )

    allocations = await refresh_paper_copy_allocations(session, settings=resolved_settings)
    active_source_wallets = {
        source for source, allocation in allocations.items() if allocation.active
    }

    total = PaperCopyBatchResult()
    live_client = trading_client or HyperliquidLiveTradingClient(settings=resolved_settings)
    accounts = await load_live_accounts_for_source_copy(session, source_wallet="")
    await bootstrap_missing_live_source_attribution(session, accounts=accounts)
    await synchronize_live_copy_lanes(
        session,
        accounts=accounts,
        active_source_wallets=active_source_wallets,
    )
    await session.commit()
    if source_wallet:
        source_wallets = [source_wallet.lower()]
    else:
        source_wallets = await load_live_copy_recovery_sources(
            session,
            max_sources=max_sources,
        )
    failed_reconciliation_accounts = await refresh_stale_live_copy_accounts(
        session,
        accounts=accounts,
        settings=resolved_settings,
        client=client,
    )
    await synchronize_live_copy_lanes(
        session,
        accounts=accounts,
        active_source_wallets=active_source_wallets,
        protected_account_keys=failed_reconciliation_accounts,
    )
    await session.commit()
    for wallet in source_wallets:
        wallet_accounts = await filter_live_accounts_for_source_allocation(
            session,
            accounts=accounts,
            source_wallet=wallet,
            allocation=allocations.get(wallet),
        )
        if not wallet_accounts:
            continue
        await sync_live_source_margin_settings(
            session,
            source_wallet=wallet,
            settings=resolved_settings,
            info_client=client,
            trading_client=live_client,
        )
        for account in wallet_accounts:
            source_state = await ensure_live_copy_source_state(
                session,
                account_key=account.key,
                source_wallet=wallet,
                reactivate=False,
            )
            await repair_owned_live_source_positions_for_recovery(
                session,
                account=account,
                source_wallet=wallet,
            )
            candidate_rows = await load_live_copy_recovery_candidate_fills(
                session,
                account_key=account.key,
                source_wallet=wallet,
                source_state=source_state,
                limit=fill_limit_per_source,
                max_entry_age_seconds=(resolved_settings.trading_copy_max_entry_age_seconds),
            )
            if not candidate_rows:
                continue
            fills = [paper_source_fill_from_wallet_fill(fill) for fill in candidate_rows]
            try:
                result = await process_live_copy_fills(
                    session,
                    source_wallet=wallet,
                    fills=fills,
                    settings=resolved_settings,
                    client=client,
                    trading_client=live_client,
                    price_cache=price_cache,
                    origin=origin,
                    accounts_override=[account],
                    prechecked_reconciliation_failures=failed_reconciliation_accounts,
                    lanes_synchronized=True,
                )
            except LiveCopyProcessingDeferred as exc:
                logger.info(
                    "live copy recovery deferred account=%s source=%s reason=%s",
                    account.key,
                    wallet,
                    exc,
                )
                continue
            total = combine_batch_results(total, result)
    await session.commit()
    return total


async def sync_live_source_margin_settings(
    session: AsyncSession,
    *,
    source_wallet: str,
    settings: Settings,
    info_client: HyperliquidClient,
    trading_client: HyperliquidLiveTradingClient,
) -> int:
    position_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.size > POSITION_EPSILON,
        )
    )
    positions = list(position_result.all())
    if not positions:
        return 0

    source_states: dict[str, PaperSourceAccountState] = {}
    unified_equity_cache: dict[str, Decimal | None] = {}
    for dex in sorted({dex_from_coin(position.coin) for position in positions}):
        source_states[dex] = await load_source_account_state(
            client=info_client,
            source_wallet=source_wallet,
            dex=dex,
            unified_equity_cache=unified_equity_cache,
        )

    updated = 0
    for position in positions:
        source_state = source_states.get(dex_from_coin(position.coin))
        if source_state is None:
            continue
        leverage = resolve_coin_decimal(source_state.leverage_by_coin, position.coin)
        margin_mode = resolve_coin_margin_mode(
            source_state.margin_mode_by_coin,
            position.coin,
        )
        if (
            leverage is None
            or leverage <= ZERO
            or leverage != leverage.to_integral_value()
            or margin_mode is None
        ):
            logger.warning(
                "live source margin setting unavailable account=%s source=%s coin=%s",
                position.account_key,
                source_wallet,
                position.coin,
            )
            continue
        try:
            changed = await sync_live_position_margin_setting(
                session,
                account_key=position.account_key,
                source_wallet=source_wallet,
                coin=position.coin,
                leverage=leverage,
                margin_mode=margin_mode,
                settings=settings,
                client=trading_client,
            )
        except LiveOrderSubmitError as exc:
            logger.info(
                "live source margin setting sync deferred account=%s source=%s coin=%s error=%s",
                position.account_key,
                source_wallet,
                position.coin,
                exc,
            )
            continue
        updated += int(changed)
    return updated


async def refresh_stale_live_copy_accounts(
    session: AsyncSession,
    *,
    accounts: list[TradingAccount],
    settings: Settings,
    client: HyperliquidClient,
) -> set[str]:
    if not settings.live_trading_reconciliation_enabled:
        return set()
    now = datetime.now(UTC)
    failed_accounts: set[str] = set()
    for account in accounts:
        if not live_copy_account_snapshot_is_stale(account, settings=settings, now=now):
            continue
        try:
            await reconcile_live_trading_account(
                session,
                account=account,
                settings=settings,
                info_client=client,
            )
        except LiveReconciliationError as exc:
            failed_accounts.add(account.key)
            if exc.status_code == 409:
                logger.info(
                    "live copy reconciliation deferred because account execution is busy "
                    "account=%s",
                    account.key,
                )
            else:
                logger.exception(
                    "live copy reconciliation failed; entries blocked and exits continue "
                    "account=%s",
                    account.key,
                )
        except Exception:
            failed_accounts.add(account.key)
            logger.exception(
                "live copy reconciliation failed; entries blocked and exits continue account=%s",
                account.key,
            )
    await session.flush()
    return failed_accounts


def live_copy_processing_enabled(settings: Settings) -> bool:
    return settings.live_trading_enabled


def live_copy_account_snapshot_is_stale(
    account: TradingAccount,
    *,
    settings: Settings,
    now: datetime,
) -> bool:
    if account.last_reconciled_at is None:
        return True
    last_reconciled_at = account.last_reconciled_at
    if last_reconciled_at.tzinfo is None:
        last_reconciled_at = last_reconciled_at.replace(tzinfo=UTC)
    max_age_seconds = max(settings.live_trading_reconciliation_interval_seconds, 1)
    return (now - last_reconciled_at).total_seconds() >= max_age_seconds


async def apply_live_copy_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return live_skip("live_source_fill_id_missing")
    if await live_order_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    ):
        return PaperCopyBatchResult()

    if part.action in {"open", "flip_open"}:
        return await apply_live_open_part(
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
            trading_client=trading_client,
        )
    return await apply_live_close_part(
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
        trading_client=trading_client,
    )


async def apply_live_open_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    coin = str(fill.get("coin") or "")
    raw_source_leverage = resolve_coin_decimal(source_leverages, coin)
    source_leverage = raw_source_leverage or Decimal("1")
    source_margin_mode = resolve_coin_margin_mode(
        source_account_state.margin_mode_by_coin if source_account_state is not None else {},
        coin,
    )
    if source_fill_age_exceeds_entry_limit(fill, settings=settings):
        raise LiveCopyPartTerminal("live_source_fill_too_old")
    if raw_source_leverage is None or raw_source_leverage <= ZERO:
        raise LiveCopyPartDeferred("live_source_leverage_missing")
    if raw_source_leverage != raw_source_leverage.to_integral_value():
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_source_leverage_invalid",
            leverage=source_leverage,
        )
    if source_margin_mode is None:
        raise LiveCopyPartDeferred("live_source_margin_mode_missing")
    if account.status != "enabled":
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_not_enabled",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    if source_perp_equity <= ZERO:
        raise LiveCopyPartDeferred("live_source_equity_missing")
    if not settings.live_trading_enabled:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_trading_disabled",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    dex = dex_from_coin(coin)
    tradable_equity_usd = live_tradable_equity_usd(
        account,
        dex=dex,
        settings=settings,
    )
    if tradable_equity_usd <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_no_tradable_equity",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )

    allocation_equity_usd = live_copy_allocation_equity_usd(
        account,
        dex=dex,
        settings=settings,
    )
    if allocation_equity_usd <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_no_allocation_equity",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )

    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_preexisting_source_add",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    if position is not None and position.side != part.side:
        if part.action == "flip_open":
            raise LiveCopyPartDeferred("live_flip_close_pending")
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_position_side_mismatch",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    if position is None and not allocation.active:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_allocation_inactive",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    if await live_market_is_reserved_by_other_source(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    ):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_market_reserved_by_other_source",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )
    exchange_position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=LIVE_EXCHANGE_SOURCE,
        coin=coin,
    )
    exchange_conflict = live_exchange_position_conflict(
        source_position=position,
        exchange_position=exchange_position,
        side=part.side,
    )
    if exchange_conflict is not None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason=exchange_conflict,
            leverage=source_leverage,
            margin_mode=source_margin_mode,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=settings.live_trading_limit_slippage_bps,
        latency_ms=0,
    )
    if execution_context is None:
        raise LiveCopyPartDeferred("live_execution_price_unavailable")
    if execution_context.price_drift_bps > settings.trading_copy_max_price_drift_bps:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_price_drift_too_high",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
            limit_price=execution_context.execution_price,
        )

    price = execution_context.execution_price
    allocation_usd = allocation_equity_usd * allocation.allocation_pct
    source_exposure_pct = part.source_notional_usd / source_perp_equity
    target_notional = allocation_usd * source_exposure_pct
    target_margin = margin_from_notional(target_notional, source_leverage)
    source_remaining = max(
        allocation_usd
        - await live_open_margin_for_source(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
        ),
        ZERO,
    )
    global_remaining = max(
        allocation_equity_usd * settings.trading_copy_max_total_allocation_pct
        - await live_open_margin_for_account(session, account_key=account.key),
        ZERO,
    )
    capacity_context = {
        "allocationEquityUsd": str(allocation_equity_usd),
        "allocationPct": str(allocation.allocation_pct),
        "allocationUsd": str(allocation_usd),
        "sourceRemainingMarginUsd": str(source_remaining),
        "globalRemainingMarginUsd": str(global_remaining),
        "targetMarginUsd": str(target_margin),
        "targetNotionalUsd": str(target_notional),
    }
    if source_remaining <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_source_allocation_exhausted",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
            limit_price=price,
            margin_usd=ZERO,
            requested_notional_usd=ZERO,
            requested_size=ZERO,
            decision_context=capacity_context,
        )
    if global_remaining <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_total_allocation_exhausted",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
            limit_price=price,
            margin_usd=ZERO,
            requested_notional_usd=ZERO,
            requested_size=ZERO,
            decision_context=capacity_context,
        )
    margin_usd = min(target_margin, source_remaining, global_remaining)
    notional_usd = margin_usd * source_leverage
    margin_usd, notional_usd, _ = adjust_open_sizing_to_min_order(
        target_notional=target_notional,
        margin_usd=margin_usd,
        notional_usd=notional_usd,
        source_remaining=source_remaining,
        global_remaining=global_remaining,
        source_leverage=source_leverage,
        settings=settings,
    )
    if notional_usd < live_min_order_notional_usd(settings):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_below_min_order_notional",
            leverage=source_leverage,
            margin_mode=source_margin_mode,
            limit_price=price,
            margin_usd=margin_usd,
            requested_notional_usd=notional_usd,
            requested_size=notional_usd / price if price > ZERO else ZERO,
            decision_context=capacity_context,
        )

    action = "add" if position is not None and part.action == "open" else part.action
    intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=action,
        side=part.side,
        size=notional_usd / price,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        leverage=source_leverage,
        margin_mode=source_margin_mode,
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=allocation_usd,
        source_perp_equity_usd=source_perp_equity,
        source_exposure_pct=source_exposure_pct,
        created_at=fill_datetime(fill),
    )
    return await submit_live_copy_intent(
        session,
        account=account,
        intent=intent,
        settings=settings,
        trading_client=trading_client,
    )


async def apply_live_close_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    coin = str(fill.get("coin") or "")
    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    if position is None:
        position = await recover_live_source_position_attribution(
            session,
            account=account,
            source_wallet=allocation.source_wallet,
            coin=coin,
            side=part.side,
        )
    if position is None:
        source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_matching_position_missing",
            leverage=source_leverage,
        )
    if position.side != part.side:
        source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_matching_position_missing",
            leverage=source_leverage,
        )
    pending_close_size = await live_pending_close_size_for_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
        since=position.last_reconciled_at,
    )
    available_size = max(position.size - pending_close_size, ZERO)
    if available_size <= POSITION_EPSILON:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_already_pending",
            leverage=position.leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=settings.live_trading_limit_slippage_bps,
        latency_ms=0,
    )
    if execution_context is None:
        raise LiveCopyPartDeferred("live_execution_price_unavailable")
    if execution_context.price_drift_bps > settings.trading_copy_max_price_drift_bps:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_price_drift_too_high",
            leverage=position.leverage,
            limit_price=execution_context.execution_price,
        )

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_account_state,
        coin=coin,
        available_size=available_size,
    )
    if close_size is None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_ratio_missing",
            leverage=position.leverage,
        )
    if close_size <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_size_zero",
            leverage=position.leverage,
        )
    price = execution_context.execution_price
    notional_usd = close_size * price
    leverage = (
        position.leverage
        if position.leverage > ZERO
        else leverage_for_fill(
            fill=fill,
            source_leverages=source_leverages,
        )
    )
    min_order_notional = live_min_order_notional_usd(settings)
    final_source_close = live_source_position_is_final_close(
        source_account_state,
        coin=coin,
        side=part.side,
    )
    close_below_min_order = live_close_below_min_order_notional(
        notional_usd,
        settings=settings,
    )
    aggregate_skip_orders: list[TradingOrder] = []
    if close_below_min_order and not final_source_close:
        aggregate_skip_orders = await load_live_below_min_close_skip_orders(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            coin=coin,
            side=part.side,
            current_source_fill_id=str(fill.get("externalFillId") or ""),
            current_sequence_index=part.sequence_index,
            since=position.last_reconciled_at or position.opened_at,
        )
        aggregate_close_size = live_aggregated_below_min_close_size(
            close_size=close_size,
            previous_skip_orders=aggregate_skip_orders,
            available_size=available_size,
        )
        aggregate_notional_usd = aggregate_close_size * price
        if live_close_below_min_order_notional(
            aggregate_notional_usd,
            settings=settings,
        ):
            return await record_live_skip(
                session,
                account=account,
                allocation=allocation,
                fill=fill,
                part=part,
                reason="live_close_below_min_order_notional",
                leverage=leverage,
                limit_price=price,
                margin_usd=margin_from_notional(notional_usd, leverage),
                requested_notional_usd=notional_usd,
                requested_size=close_size,
            )
        close_size = aggregate_close_size
        notional_usd = aggregate_notional_usd
        close_below_min_order = False
    order_notional_usd = (
        max(notional_usd, min_order_notional) if final_source_close else notional_usd
    )
    intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=part.action,
        side=part.side,
        size=close_size,
        notional_usd=order_notional_usd,
        margin_usd=margin_from_notional(order_notional_usd, leverage),
        leverage=leverage,
        margin_mode=(
            position.margin_mode if position.margin_mode in {"cross", "isolated"} else "cross"
        ),
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=None,
        source_perp_equity_usd=source_perp_equity if source_perp_equity > ZERO else None,
        source_exposure_pct=None,
        created_at=fill_datetime(fill),
    )
    result = await submit_live_copy_intent(
        session,
        account=account,
        intent=intent,
        settings=settings,
        trading_client=trading_client,
    )
    if result.processed_fills > 0:
        await mark_live_close_skips_aggregated(
            session,
            orders=aggregate_skip_orders,
            intent=intent,
        )
    return result


async def load_live_below_min_close_skip_orders(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    side: str,
    current_source_fill_id: str,
    current_sequence_index: int,
    since: datetime | None,
) -> list[TradingOrder]:
    query = select(TradingOrder).where(
        TradingOrder.account_key == account_key,
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet == source_wallet,
        TradingOrder.coin == coin,
        TradingOrder.side == side,
        TradingOrder.reduce_only.is_(True),
        TradingOrder.order_type == "skip",
        TradingOrder.status == "failed",
        TradingOrder.error == "skip:live_close_below_min_order_notional",
        TradingOrder.filled_size <= ZERO,
        TradingOrder.filled_notional_usd <= ZERO,
        or_(
            TradingOrder.source_fill_id != current_source_fill_id,
            TradingOrder.sequence_index != current_sequence_index,
        ),
    )
    if since is not None:
        query = query.where(TradingOrder.created_at >= since)
    result = await session.scalars(query.order_by(TradingOrder.created_at.asc()))
    return list(result.all())


def live_aggregated_below_min_close_size(
    *,
    close_size: Decimal,
    previous_skip_orders: list[TradingOrder],
    available_size: Decimal,
) -> Decimal:
    previous_size = sum(
        (order.requested_size for order in previous_skip_orders if order.requested_size > ZERO),
        ZERO,
    )
    return min(max(close_size + previous_size, ZERO), available_size)


async def mark_live_close_skips_aggregated(
    session: AsyncSession,
    *,
    orders: list[TradingOrder],
    intent: TradeIntent,
) -> None:
    if not orders:
        return
    for order in orders:
        payload = dict(order.raw_payload) if isinstance(order.raw_payload, dict) else {}
        payload.pop("hiddenFromActivity", None)
        order.error = f"skip:{LIVE_CLOSE_AGGREGATED_SKIP_REASON}"
        order.raw_payload = {
            **payload,
            "aggregatedInto": {
                "clientOrderId": intent.client_order_id,
                "sourceFillId": intent.source_fill_id,
                "sequenceIndex": intent.sequence_index,
            },
        }
    await session.flush()


async def submit_live_copy_intent(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    try:
        result = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=settings,
            client=trading_client,
        )
    except LiveCopyEntryLifecycleDeferred as exc:
        if exc.state_reclassified:
            return live_skip(exc.reason)
        raise LiveCopyPartDeferred(exc.reason) from exc
    except LiveOrderSubmitError as exc:
        reason = live_submit_failure_reason(exc)
        await record_live_intent_failure(
            session,
            intent=intent,
            reason=reason,
            error=exc,
        )
        logger.warning(
            "live copy order submit failed account=%s source=%s coin=%s reason=%s",
            account.key,
            intent.source_wallet,
            intent.coin,
            str(exc) or exc.__class__.__name__,
        )
        return live_skip(reason)
    if not result.submitted:
        return live_skip("live_order_not_submitted")
    if result.order.status in {"rejected", "failed", "canceled"}:
        return live_skip(f"live_order_{result.order.status}")
    return PaperCopyBatchResult(processed_fills=1 if result.submitted else 0)


def live_submit_failure_reason(error: LiveOrderSubmitError) -> str:
    message = str(error).casefold()
    if "another live order is already being dispatched" in message:
        return "live_execution_busy"
    if "not enabled for entry execution" in message:
        return "live_account_not_enabled"
    if "complete exchange reconciliation snapshot" in message:
        return "live_reconciliation_incomplete"
    if "fresh exchange reconciliation snapshot" in message:
        return "live_reconciliation_stale"
    if "entry intent expired" in message:
        return "live_entry_intent_expired"
    if "weekly loss" in message:
        return "live_weekly_loss_limit"
    if "orders per minute" in message:
        return "live_order_rate_limit"
    return "live_order_submit_error"


async def record_live_intent_failure(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    reason: str,
    error: LiveOrderSubmitError,
) -> None:
    message = str(error) or error.__class__.__name__
    stmt = insert(TradingOrder).values(
        account_key=intent.account_key,
        account_type="live",
        source_wallet=intent.source_wallet,
        source_fill_id=intent.source_fill_id,
        sequence_index=intent.sequence_index,
        client_order_id=intent.client_order_id,
        coin=intent.coin,
        action=intent.action,
        side=intent.side,
        is_buy=intent.is_buy,
        reduce_only=intent.reduce_only,
        order_type="skip",
        status="failed",
        requested_size=intent.size,
        requested_notional_usd=intent.notional_usd,
        margin_usd=intent.margin_usd,
        leverage=intent.leverage,
        margin_mode=intent.margin_mode,
        limit_price=intent.limit_price,
        filled_size=ZERO,
        filled_notional_usd=ZERO,
        fee_usd=ZERO,
        error=f"skip:{reason}",
        raw_payload={
            "decisionAt": datetime.now(UTC).isoformat(),
            "skipReason": reason,
            "submitError": {
                "message": message,
                "statusCode": error.status_code,
                "type": error.__class__.__name__,
            },
        },
        created_at=intent.created_at,
    )
    await session.execute(
        stmt.on_conflict_do_nothing(constraint="ux_trading_orders_account_source_fill_sequence")
    )


async def record_live_skip(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    reason: str,
    leverage: Decimal | None = None,
    margin_mode: MarginMode = "cross",
    limit_price: Decimal | None = None,
    margin_usd: Decimal | None = None,
    requested_notional_usd: Decimal | None = None,
    requested_size: Decimal | None = None,
    source_fill_age_seconds: float | None = None,
    decision_context: dict[str, Any] | None = None,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return live_skip(reason)
    coin = str(fill.get("coin") or "")
    source_price = decimal_or_zero(fill.get("price"))
    resolved_price = limit_price if limit_price is not None and limit_price > ZERO else source_price
    resolved_notional = (
        requested_notional_usd
        if requested_notional_usd is not None
        else max(part.source_notional_usd, ZERO)
    )
    resolved_size = requested_size if requested_size is not None else max(part.source_size, ZERO)
    if resolved_size <= ZERO and resolved_notional > ZERO and resolved_price > ZERO:
        resolved_size = resolved_notional / resolved_price
    if resolved_notional <= ZERO and resolved_size > ZERO and resolved_price > ZERO:
        resolved_notional = resolved_size * resolved_price
    resolved_leverage = leverage if leverage is not None and leverage > ZERO else Decimal("1")
    reduce_only = part.action in {"reduce", "close", "flip_close"}
    raw_payload: dict[str, Any] = {
        "decisionAt": datetime.now(UTC).isoformat(),
        "marginMode": margin_mode,
        "skipReason": reason,
        "sourceFill": {
            "externalFillId": source_fill_id,
            "coin": coin,
            "price": str(fill.get("price") or ""),
            "time": fill.get("time"),
        },
    }
    if source_fill_age_seconds is not None:
        raw_payload["sourceFillAgeSeconds"] = max(round(source_fill_age_seconds, 3), 0)
    if decision_context:
        raw_payload["decisionContext"] = decision_context

    stmt = insert(TradingOrder).values(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
        client_order_id=build_client_order_id(
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            source_fill_id=source_fill_id,
            sequence_index=part.sequence_index,
            action=part.action,
        ),
        coin=coin,
        action=part.action,
        side=part.side,
        is_buy=trade_is_buy(side=part.side, reduce_only=reduce_only),
        reduce_only=reduce_only,
        order_type="skip",
        status="failed",
        requested_size=resolved_size,
        requested_notional_usd=resolved_notional,
        margin_usd=margin_usd,
        leverage=resolved_leverage,
        margin_mode=margin_mode,
        limit_price=resolved_price if resolved_price > ZERO else None,
        filled_size=ZERO,
        filled_notional_usd=ZERO,
        fee_usd=ZERO,
        error=f"skip:{reason}",
        raw_payload=raw_payload,
        created_at=fill_datetime(fill),
    )
    result = await session.execute(
        stmt.on_conflict_do_nothing(
            constraint="ux_trading_orders_account_source_fill_sequence"
        ).returning(TradingOrder.id)
    )
    inserted_order_id = result.scalar_one_or_none()
    if inserted_order_id is not None:
        return live_skip(reason)
    if reason in LIVE_COPY_RETRYABLE_SKIP_REASONS:
        return PaperCopyBatchResult()

    existing = await session.scalar(
        select(TradingOrder)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == allocation.source_wallet,
            TradingOrder.source_fill_id == source_fill_id,
            TradingOrder.sequence_index == part.sequence_index,
        )
        .with_for_update()
    )
    if (
        existing is None
        or existing.error == f"skip:{reason}"
        or not is_retryable_live_order_submit_failure(existing)
    ):
        return PaperCopyBatchResult()

    existing.coin = coin
    existing.action = part.action
    existing.side = part.side
    existing.is_buy = trade_is_buy(side=part.side, reduce_only=reduce_only)
    existing.reduce_only = reduce_only
    existing.order_type = "skip"
    existing.status = "failed"
    existing.requested_size = resolved_size
    existing.requested_notional_usd = resolved_notional
    existing.margin_usd = margin_usd
    existing.leverage = resolved_leverage
    existing.margin_mode = margin_mode
    existing.limit_price = resolved_price if resolved_price > ZERO else None
    existing.average_fill_price = None
    existing.filled_size = ZERO
    existing.filled_notional_usd = ZERO
    existing.fee_usd = ZERO
    existing.error = f"skip:{reason}"
    existing.raw_payload = raw_payload
    existing.submitted_at = None
    existing.accepted_at = None
    existing.filled_at = None
    await session.flush()
    return live_skip(reason)


async def load_live_copy_recovery_sources(
    session: AsyncSession,
    *,
    max_sources: int,
) -> list[str]:
    if max_sources <= 0:
        return []
    unresolved_order_result = await session.execute(
        select(func.lower(TradingOrder.source_wallet).label("source_wallet"))
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet != "",
            TradingOrder.source_wallet.not_in((LIVE_EXCHANGE_SOURCE, LIVE_MANUAL_TEST_SOURCE)),
            live_copy_unresolved_order_predicate(),
        )
        .distinct()
        .order_by(func.lower(TradingOrder.source_wallet).asc())
        .limit(max_sources)
    )
    sources = [
        str(row.source_wallet).lower() for row in unresolved_order_result.all() if row.source_wallet
    ]
    remaining = max(max_sources - len(sources), 0)
    if remaining <= 0:
        return unique_strings(sources)[:max_sources]
    position_query = (
        select(
            func.lower(TradingPosition.source_wallet).label("source_wallet"),
            func.max(WalletScore.score).label("score"),
        )
        .outerjoin(
            WalletScore,
            WalletScore.wallet_address == func.lower(TradingPosition.source_wallet),
        )
        .where(
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != "",
            TradingPosition.source_wallet.not_in((LIVE_EXCHANGE_SOURCE, LIVE_MANUAL_TEST_SOURCE)),
            TradingPosition.size > POSITION_EPSILON,
        )
        .group_by(func.lower(TradingPosition.source_wallet))
        .order_by(
            func.max(WalletScore.score).desc().nulls_last(),
            func.lower(TradingPosition.source_wallet).asc(),
        )
    )
    if sources:
        position_query = position_query.where(
            func.lower(TradingPosition.source_wallet).not_in(sources)
        )
    position_result = await session.execute(position_query.limit(remaining))
    sources.extend(
        str(row.source_wallet).lower() for row in position_result.all() if row.source_wallet
    )
    sources = unique_strings(sources)
    if len(sources) >= max_sources:
        return sources[:max_sources]
    remaining = max(max_sources - len(sources), 0)
    if remaining <= 0:
        return unique_strings(sources)

    allocation_query = (
        select(
            func.lower(PaperCopyAllocation.source_wallet).label("source_wallet"),
            func.min(PaperCopyAllocation.rank).label("first_rank"),
        )
        .where(
            PaperCopyAllocation.active.is_(True),
            PaperCopyAllocation.source_wallet != "",
        )
        .group_by(func.lower(PaperCopyAllocation.source_wallet))
        .order_by(
            func.min(PaperCopyAllocation.rank).asc(),
            func.lower(PaperCopyAllocation.source_wallet).asc(),
        )
    )
    if sources:
        allocation_query = allocation_query.where(
            func.lower(PaperCopyAllocation.source_wallet).not_in(sources)
        )
    allocation_result = await session.execute(allocation_query.limit(remaining))
    sources.extend(
        str(row.source_wallet).lower() for row in allocation_result.all() if row.source_wallet
    )
    return unique_strings(sources)[:max_sources]


async def load_live_accounts_for_source_copy(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> list[TradingAccount]:
    query = (
        select(TradingAccount)
        .where(
            TradingAccount.account_type == "live",
            TradingAccount.archived_at.is_(None),
            TradingAccount.status.in_(["enabled", "exit_only"]),
        )
        .order_by(TradingAccount.key.asc())
    )
    result = await session.scalars(query)
    return list(result.all())


async def filter_live_accounts_for_source_allocation(
    session: AsyncSession,
    *,
    accounts: list[TradingAccount],
    source_wallet: str,
    allocation: PaperSourceAllocation | None,
) -> list[TradingAccount]:
    """Keep entry-eligible accounts plus accounts with copied exposure to manage."""

    if not accounts:
        return []
    entry_enabled_account_keys = {
        account.key
        for account in accounts
        if account.status == "enabled" and allocation is not None and allocation.active
    }
    exposure_candidate_keys = [
        account.key for account in accounts if account.key not in entry_enabled_account_keys
    ]
    if not exposure_candidate_keys:
        return accounts
    position_accounts = await session.scalars(
        select(TradingPosition.account_key).where(
            TradingPosition.account_key.in_(exposure_candidate_keys),
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.size > POSITION_EPSILON,
        )
    )
    unresolved_order_accounts = await session.scalars(
        select(TradingOrder.account_key).where(
            TradingOrder.account_key.in_(exposure_candidate_keys),
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == source_wallet,
            live_copy_unresolved_order_predicate(),
        )
    )
    owned_account_keys = set(position_accounts.all()) | set(unresolved_order_accounts.all())
    retained_account_keys = entry_enabled_account_keys | owned_account_keys
    return [account for account in accounts if account.key in retained_account_keys]


async def live_order_exists(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
) -> bool:
    existing = await session.scalar(
        select(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == source_wallet,
            TradingOrder.source_fill_id == source_fill_id,
            TradingOrder.sequence_index == sequence_index,
        )
    )
    return existing is not None and not is_retryable_live_order_submit_failure(existing)


async def live_pending_close_size_for_position(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    since: datetime | None,
) -> Decimal:
    query = select(TradingOrder).where(
        TradingOrder.account_key == account_key,
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet == source_wallet,
        TradingOrder.coin == coin,
        TradingOrder.reduce_only.is_(True),
        TradingOrder.status.in_(PENDING_CLOSE_ORDER_STATUSES),
    )
    if since is not None:
        query = query.where(TradingOrder.created_at >= since)
    result = await session.scalars(query)
    return live_pending_close_size_from_orders(result.all())


def live_pending_close_size_from_orders(orders: list[TradingOrder]) -> Decimal:
    total = ZERO
    for order in orders:
        if order.status not in PENDING_CLOSE_ORDER_STATUSES:
            continue
        if order.status == "filled" and order.filled_size > ZERO:
            total += order.filled_size
            continue
        total += order.requested_size
    return total


async def live_market_is_reserved_by_other_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> bool:
    existing_position_id = await session.scalar(
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == coin,
            TradingPosition.source_wallet != source_wallet,
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
            TradingPosition.size > POSITION_EPSILON,
        )
        .limit(1)
    )
    if existing_position_id is not None:
        return True
    existing_entry_order_id = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == coin,
            TradingOrder.source_wallet != source_wallet,
            TradingOrder.source_wallet != LIVE_EXCHANGE_SOURCE,
            TradingOrder.reduce_only.is_(False),
            live_copy_unresolved_order_predicate(),
        )
        .limit(1)
    )
    return existing_entry_order_id is not None


def live_exchange_position_conflict(
    *,
    source_position: TradingPosition | None,
    exchange_position: TradingPosition | None,
    side: str,
) -> str | None:
    if exchange_position is None:
        return None
    if source_position is None:
        return "live_exchange_position_conflict"
    if exchange_position.side != side:
        return "live_exchange_position_side_conflict"
    return None


def live_min_order_notional_usd(settings: Settings) -> Decimal:
    return max(
        settings.trading_copy_min_order_notional_usd,
        settings.live_trading_min_order_notional_usd,
    )


def live_close_below_min_order_notional(
    notional_usd: Decimal,
    *,
    settings: Settings,
) -> bool:
    return notional_usd < live_min_order_notional_usd(settings)


def live_close_size_for_part(
    *,
    position: TradingPosition,
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    coin: str,
    available_size: Decimal | None = None,
) -> Decimal | None:
    position_size = (
        min(position.size, available_size) if available_size is not None else position.size
    )
    if live_source_position_is_final_close(
        source_account_state,
        coin=coin,
        side=part.side,
    ):
        return position_size
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return None
    return min(position_size, position_size * part.close_ratio)


def live_source_position_is_final_close(
    source_account_state: PaperSourceAccountState | None,
    *,
    coin: str,
    side: str,
) -> bool:
    if not source_state_available_for_reconciliation(source_account_state):
        return False
    source_position = resolve_source_current_position(
        source_account_state.positions_by_coin,
        coin,
    )
    return source_position is None or source_position.side != side


async def live_open_margin_for_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.margin_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
        )
    )
    return decimal_or_zero(value)


async def live_open_margin_for_account(session: AsyncSession, *, account_key: str) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.margin_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    return decimal_or_zero(value)


async def gather_two(first: Any, second: Any) -> tuple[Any, Any]:
    return await asyncio.gather(first, second)


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


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
