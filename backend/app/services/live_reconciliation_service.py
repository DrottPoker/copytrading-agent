"""One reconciliation of a live account against the exchange, with its run record."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingFill,
    TradingReconciliationRun,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.hyperliquid_account_mode import (
    live_spot_usdc_available,
    live_spot_usdc_total,
    normalize_user_abstraction,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_account_access import (
    live_account_user_address,
)
from app.services.live_account_state import (
    LIVE_CAPITAL_MODE_UNIFIED,
    LIVE_PERFORMANCE_BACKFILL_VERSION,
    account_last_reconciliation,
    live_capital_mode,
    live_performance_backfill_version,
    live_reconciliation_status,
)
from app.services.live_copy_state_service import (
    acquire_live_copy_lifecycle_lock,
)
from app.services.live_exchange_fetch import (
    LiveCashFlowFetchResult,
    LiveFillFetchResult,
    LiveFundingFetchResult,
    LivePerpSnapshot,
    LivePerpState,
    fetch_live_cash_flows_by_time,
    fetch_live_fills_by_time,
    fetch_live_funding_by_time,
    fetch_live_perp_states,
    fetch_live_portfolio_history,
    fetch_live_spot_state,
    fetch_live_user_abstraction,
    normalize_live_perp_snapshot,
    remote_state_error,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    max_optional_numeric,
    merge_raw_payload,
)
from app.services.live_fill_reconciliation import (
    LiveOrderReconciliationResult,
    live_cash_flow_reconciliation_start_time_ms,
    live_fill_reconciliation_start_time_ms,
    live_funding_reconciliation_start_time_ms,
    mark_live_cash_flow_backfill_complete,
    reconcile_live_account_cash_flows,
    reconcile_live_fills,
    reconcile_live_funding_payments,
    reconcile_live_order_statuses,
    still_unresolved_live_order_ids,
    update_live_orders_from_reconciled_fills,
)
from app.services.live_performance_service import (
    backfill_live_account_performance_history,
    ensure_live_account_performance_baseline,
    record_live_account_performance_snapshot,
    thin_live_account_performance_snapshots,
)
from app.services.live_position_reconciliation import (
    LivePositionReconciliationResult,
    reconcile_live_positions,
)
from app.services.live_signer_service import (
    refresh_live_account_signer,
)
from app.services.live_trading_errors import (
    LiveReconciliationError,
    LiveTradingServiceError,
)
from app.services.trading_safety_service import (
    apply_live_account_status,
    record_audit_log,
    record_risk_event,
)

ZERO = Decimal("0")


LIVE_RECONCILIATION_RUN_RETENTION_DAYS = 30


@dataclass(frozen=True)
class LiveReconciliationResult:
    account_key: str
    user_address: str
    run_id: UUID | None = None
    fetched_fills: int = 0
    inserted_fills: int = 0
    fetched_funding_payments: int = 0
    inserted_funding_payments: int = 0
    fetched_cash_flows: int = 0
    inserted_cash_flows: int = 0
    updated_orders: int = 0
    open_positions: int = 0
    removed_positions: int = 0
    status: str = "complete"
    incomplete_components: tuple[str, ...] = ()
    component_errors: dict[str, str] = field(default_factory=dict)
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def reconcile_live_trading_account(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    lookback_minutes: int | None = None,
) -> LiveReconciliationResult:
    try:
        async with job_lock(
            session,
            key=f"live_execution:{account.key}",
            ttl_seconds=300,
        ):
            return await run_live_trading_account_reconciliation(
                session,
                account=account,
                settings=settings,
                info_client=info_client,
                lookback_minutes=lookback_minutes,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveReconciliationError(
            "Live execution or reconciliation is already running for this account.",
            status_code=409,
        ) from exc


async def run_live_trading_account_reconciliation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    lookback_minutes: int | None = None,
    force_signer_check: bool = False,
) -> LiveReconciliationResult:
    """Run under the caller-held live_execution job lock.

    Network reads complete before the lifecycle lock. The subsequent fill and
    position mutation transaction shares the lifecycle fence with attribution
    recovery, so source ownership cannot be derived from rows reconciliation is
    concurrently replacing.
    """

    if account.account_type != "live":
        raise LiveReconciliationError("Only live accounts can be reconciled.")
    user_address = live_account_user_address(account, settings=settings)
    reconciled_at = datetime.now(UTC)
    run = TradingReconciliationRun(
        account_key=account.key,
        status="running",
        started_at=reconciled_at,
        components={},
    )
    session.add(run)
    await session.commit()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    client_entered = False
    try:
        if client_created:
            await client.__aenter__()
            client_entered = True
        # Committed on its own so a failed reconciliation keeps the signer result.
        if await refresh_live_account_signer(
            account,
            client=client,
            settings=settings,
            now=reconciled_at,
            force=force_signer_check,
        ):
            await session.commit()
        order_result = await reconcile_live_order_statuses(
            session,
            account=account,
            user_address=user_address,
            client=client,
        )
        start_time_ms = await live_fill_reconciliation_start_time_ms(
            session,
            account_key=account.key,
            settings=settings,
            now=reconciled_at,
            lookback_minutes=lookback_minutes,
        )
        fill_result = await fetch_live_fills_by_time(
            client,
            user=user_address,
            start_time_ms=start_time_ms,
        )
        funding_start_time_ms = await live_funding_reconciliation_start_time_ms(
            session,
            account_key=account.key,
            settings=settings,
            now=reconciled_at,
            lookback_minutes=lookback_minutes,
        )
        funding_result = await fetch_live_funding_by_time(
            client,
            user=user_address,
            start_time_ms=funding_start_time_ms,
        )
        portfolio_history_result = await fetch_live_portfolio_history(
            client,
            user=user_address,
        )
        cash_flow_start_time_ms = await live_cash_flow_reconciliation_start_time_ms(
            session,
            account=account,
            settings=settings,
            now=reconciled_at,
            lookback_minutes=lookback_minutes,
        )
        cash_flow_result = await fetch_live_cash_flows_by_time(
            client,
            user=user_address,
            start_time_ms=cash_flow_start_time_ms,
            end_time_ms=int(reconciled_at.timestamp() * 1000),
        )
        historical_cash_flow_result: LiveCashFlowFetchResult | None = None
        if (
            live_performance_backfill_version(account) < LIVE_PERFORMANCE_BACKFILL_VERSION
            and portfolio_history_result.complete
            and portfolio_history_result.account_values
        ):
            historical_cash_flow_result = (
                cash_flow_result
                if cash_flow_start_time_ms == 0
                else await fetch_live_cash_flows_by_time(
                    client,
                    user=user_address,
                    start_time_ms=0,
                    end_time_ms=int(reconciled_at.timestamp() * 1000),
                )
            )
        perp_snapshot = await fetch_live_perp_states(client, user_address=user_address)
        spot_state = await fetch_live_spot_state(client, user_address=user_address)
        user_abstraction = await fetch_live_user_abstraction(
            client,
            user_address=user_address,
        )
        await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
        inserted_fills = await reconcile_live_fills(
            session,
            account=account,
            fills=list(fill_result.fills),
        )
        inserted_funding_payments = await reconcile_live_funding_payments(
            session,
            account=account,
            payments=list(funding_result.payments),
        )
        inserted_cash_flows = await reconcile_live_account_cash_flows(
            session,
            account=account,
            user_address=user_address,
            updates=[
                *cash_flow_result.updates,
                *(
                    historical_cash_flow_result.updates
                    if historical_cash_flow_result is not None
                    else ()
                ),
            ],
        )
        if cash_flow_start_time_ms == 0 and cash_flow_result.complete:
            mark_live_cash_flow_backfill_complete(account, reconciled_at=reconciled_at)
        updated_orders_from_fills = await update_live_orders_from_reconciled_fills(
            session,
            account_key=account.key,
        )
        await recompute_live_account_fill_totals(session, account=account)
        position_result = await reconcile_live_positions(
            session,
            account=account,
            perp_states=perp_snapshot,
            reconciled_at=reconciled_at,
        )
        unresolved_order_ids = await still_unresolved_live_order_ids(
            session,
            order_ids=order_result.unresolved_order_ids,
        )

        component_errors = reconciliation_component_errors(
            order_result=order_result,
            unresolved_order_ids=unresolved_order_ids,
            fill_result=fill_result,
            funding_result=funding_result,
            cash_flow_result=cash_flow_result,
            perp_snapshot=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
        )
        incomplete_components = tuple(sorted(component_errors))
        reconciliation_status = "partial" if incomplete_components else "complete"
        updated_orders = order_result.updated_orders + updated_orders_from_fills
        components = reconciliation_components_payload(
            order_result=order_result,
            unresolved_order_ids=unresolved_order_ids,
            fill_result=fill_result,
            funding_result=funding_result,
            cash_flow_result=cash_flow_result,
            perp_snapshot=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
            position_result=position_result,
        )
        await session.refresh(
            account,
            attribute_names=[
                "status",
                "lifecycle_version",
                "status_changed_at",
                "status_reason",
            ],
            with_for_update=True,
        )
        previous_reconciliation_status = live_reconciliation_status(account)
        if reconciliation_status == "complete":
            history_backfilled = await backfill_live_account_performance_history(
                session,
                account=account,
                user_address=user_address,
                portfolio_history=portfolio_history_result,
                cash_flow_history=historical_cash_flow_result,
                reconciled_at=reconciled_at,
            )
            if not history_backfilled:
                await ensure_live_account_performance_baseline(
                    session,
                    account=account,
                )
        update_live_account_from_state(
            account,
            perp_states=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
            reconciled_at=reconciled_at,
            settings=settings,
            reconciliation_status=reconciliation_status,
            incomplete_components=incomplete_components,
            component_errors=component_errors,
        )
        if reconciliation_status == "complete":
            await record_live_account_performance_snapshot(
                session,
                account=account,
                recorded_at=reconciled_at,
            )
        if (
            reconciliation_status != "complete"
            and account.status == "enabled"
            and previous_reconciliation_status != reconciliation_status
        ):
            payload = {
                "accountKey": account.key,
                "incompleteComponents": list(incomplete_components),
                "componentErrors": component_errors,
                "accountStatus": account.status,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_partial",
                severity="warning",
                message=(
                    "Live account reconciliation is partial. New entries are blocked until "
                    "a complete snapshot succeeds."
                ),
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.reconciliation_partial",
                payload=payload,
            )
        elif position_result.open_positions > 0 and account.status == "disabled":
            apply_live_account_status(
                account,
                status="exit_only",
                reason=(
                    "external_exposure_detected"
                    if reconciliation_status == "complete"
                    else "external_exposure_detected_during_partial_reconciliation"
                ),
            )
            payload = {
                "accountKey": account.key,
                "openPositions": position_result.open_positions,
                "reconciliationStatus": reconciliation_status,
                "incompleteComponents": list(incomplete_components),
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_external_exposure_detected",
                severity="critical",
                message="Disabled live account entered exit-only after exposure was detected.",
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.external_exposure_detected",
                payload=payload,
            )
        run.status = reconciliation_status
        run.completed_at = datetime.now(UTC)
        run.components = components
        run.fetched_fills = len(fill_result.fills)
        run.inserted_fills = inserted_fills
        run.updated_orders = updated_orders
        run.open_positions = position_result.open_positions
        run.removed_positions = position_result.removed_positions
        run.error = None
        await prune_live_reconciliation_runs(session, account_key=account.key)
        await thin_live_account_performance_snapshots(session, account_key=account.key)
        await session.commit()
    except LiveTradingServiceError as exc:
        await mark_live_reconciliation_run_failed(
            session,
            run_id=run.id,
            account_key=account.key,
            attempted_at=reconciled_at,
            error=str(exc) or exc.__class__.__name__,
        )
        raise
    except Exception as exc:
        await mark_live_reconciliation_run_failed(
            session,
            run_id=run.id,
            account_key=account.key,
            attempted_at=reconciled_at,
            error=str(exc) or exc.__class__.__name__,
        )
        raise LiveReconciliationError(
            f"Live reconciliation failed: {exc.__class__.__name__}.",
            status_code=502,
        ) from exc
    finally:
        if client_entered:
            await client.__aexit__(None, None, None)
    return LiveReconciliationResult(
        account_key=account.key,
        user_address=user_address,
        run_id=run.id,
        fetched_fills=len(fill_result.fills),
        inserted_fills=inserted_fills,
        fetched_funding_payments=len(funding_result.payments),
        inserted_funding_payments=inserted_funding_payments,
        fetched_cash_flows=(
            len(cash_flow_result.updates)
            + (
                len(historical_cash_flow_result.updates)
                if historical_cash_flow_result is not None
                else 0
            )
        ),
        inserted_cash_flows=inserted_cash_flows,
        updated_orders=updated_orders,
        open_positions=position_result.open_positions,
        removed_positions=position_result.removed_positions,
        status=reconciliation_status,
        incomplete_components=incomplete_components,
        component_errors=component_errors,
        reconciled_at=reconciled_at,
    )


async def recompute_live_account_fill_totals(
    session: AsyncSession,
    *,
    account: TradingAccount,
) -> None:
    totals = await session.execute(
        select(
            func.coalesce(func.sum(TradingFill.realized_pnl_usd), ZERO),
            func.coalesce(func.sum(TradingFill.fee_usd), ZERO),
        ).where(
            TradingFill.account_key == account.key,
            TradingFill.account_type == "live",
        )
    )
    realized_pnl_usd, fee_usd = totals.one()
    account.realized_pnl_usd = decimal_or_none(realized_pnl_usd) or ZERO
    account.fee_usd = decimal_or_none(fee_usd) or ZERO
    await session.flush()


def reconciliation_component_errors(
    *,
    order_result: LiveOrderReconciliationResult,
    unresolved_order_ids: tuple[UUID, ...],
    fill_result: LiveFillFetchResult,
    funding_result: LiveFundingFetchResult,
    cash_flow_result: LiveCashFlowFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if unresolved_order_ids:
        errors["orders"] = f"{len(unresolved_order_ids)} live order statuses remain unresolved."
    if not fill_result.complete:
        errors["fills"] = fill_result.error or "Live fill history is incomplete."
    if not funding_result.complete:
        errors["funding"] = funding_result.error or "Live funding history is incomplete."
    if not cash_flow_result.complete:
        errors["cash_flows"] = (
            cash_flow_result.error or "Live account cash flow history is incomplete."
        )
    errors.update(perp_snapshot.component_errors)
    if not perp_snapshot.complete and not any(key.startswith("perp") for key in errors):
        errors["positions"] = "One or more perp position scopes are incomplete."
    spot_error = remote_state_error(spot_state)
    if spot_error:
        errors["spot"] = spot_error
    user_abstraction_error = remote_state_error(user_abstraction)
    if user_abstraction_error:
        errors["user_abstraction"] = user_abstraction_error
    return errors


def reconciliation_components_payload(
    *,
    order_result: LiveOrderReconciliationResult,
    unresolved_order_ids: tuple[UUID, ...],
    fill_result: LiveFillFetchResult,
    funding_result: LiveFundingFetchResult,
    cash_flow_result: LiveCashFlowFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
    position_result: LivePositionReconciliationResult,
) -> dict[str, Any]:
    return {
        "orders": {
            "status": "partial" if unresolved_order_ids else "complete",
            "updated": order_result.updated_orders,
            "unresolved": len(unresolved_order_ids),
            "errors": order_result.errors,
        },
        "fills": {
            "status": "complete" if fill_result.complete else "partial",
            "fetched": len(fill_result.fills),
            "pages": fill_result.pages,
            "nextStartTimeMs": fill_result.next_start_time_ms,
            "error": fill_result.error,
        },
        "funding": {
            "status": "complete" if funding_result.complete else "partial",
            "fetched": len(funding_result.payments),
            "pages": funding_result.pages,
            "nextStartTimeMs": funding_result.next_start_time_ms,
            "error": funding_result.error,
        },
        "cashFlows": {
            "status": "complete" if cash_flow_result.complete else "partial",
            "fetched": len(cash_flow_result.updates),
            "pages": cash_flow_result.pages,
            "nextStartTimeMs": cash_flow_result.next_start_time_ms,
            "error": cash_flow_result.error,
        },
        "perpCatalog": {
            "status": "complete" if perp_snapshot.catalog_complete else "partial",
            "requestedDexes": [dex or "default" for dex in perp_snapshot.requested_dexes],
            "error": perp_snapshot.catalog_error,
        },
        "perpStates": {
            state.dex or "default": {
                "status": "complete" if state.complete else "partial",
                "error": state.error,
            }
            for state in perp_snapshot.states
        },
        "positions": {
            "status": "complete" if position_result.complete else "partial",
            "authoritativeDexes": [dex or "default" for dex in position_result.authoritative_dexes],
            "open": position_result.open_positions,
            "removed": position_result.removed_positions,
        },
        "spot": {
            "status": "partial" if remote_state_error(spot_state) else "complete",
            "error": remote_state_error(spot_state),
        },
        "userAbstraction": {
            "status": "partial" if remote_state_error(user_abstraction) else "complete",
            "error": remote_state_error(user_abstraction),
        },
    }


async def mark_live_reconciliation_run_failed(
    session: AsyncSession,
    *,
    run_id: UUID,
    account_key: str,
    attempted_at: datetime,
    error: str,
) -> None:
    await session.rollback()
    run = await session.get(TradingReconciliationRun, run_id)
    account = await session.scalar(
        select(TradingAccount).where(TradingAccount.key == account_key).with_for_update()
    )
    if run is not None:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.error = error
        run.components = {
            "reconciliation": {
                "status": "failed",
                "error": error,
            }
        }
    if account is not None:
        previous_reconciliation_status = live_reconciliation_status(account)
        account.config_payload = merge_raw_payload(
            account.config_payload,
            {
                "lastReconciliationAttempt": {
                    "attemptedAt": attempted_at.isoformat(),
                    "componentErrors": {"reconciliation": error},
                    "incompleteComponents": ["reconciliation"],
                    "status": "failed",
                }
            },
        )
        if account.status == "enabled" and previous_reconciliation_status != "failed":
            payload = {
                "accountKey": account.key,
                "error": error,
                "accountStatus": account.status,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_failed",
                severity="warning",
                message=(
                    "Live account reconciliation failed. New entries are blocked until "
                    "a complete snapshot succeeds."
                ),
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.reconciliation_failed",
                payload=payload,
            )
    await prune_live_reconciliation_runs(session, account_key=account_key)
    await session.commit()


async def prune_live_reconciliation_runs(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime | None = None,
) -> None:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=LIVE_RECONCILIATION_RUN_RETENTION_DAYS)
    await session.execute(
        delete(TradingReconciliationRun).where(
            TradingReconciliationRun.account_key == account_key,
            TradingReconciliationRun.started_at < cutoff,
        )
    )


def update_live_account_from_state(
    account: TradingAccount,
    *,
    perp_states: LivePerpSnapshot | list[LivePerpState],
    spot_state: dict[str, Any] | None = None,
    user_abstraction: Any = None,
    reconciled_at: datetime,
    settings: Settings,
    reconciliation_status: str = "complete",
    incomplete_components: tuple[str, ...] = (),
    component_errors: dict[str, str] | None = None,
) -> None:
    perp_snapshot = normalize_live_perp_snapshot(perp_states)
    previous = account_last_reconciliation(account)
    previous_states = {
        str(state.get("dex") or "default"): dict(state)
        for state in previous.get("perpStates", [])
        if isinstance(state, dict)
    }
    state_by_dex = {state.dex: state for state in perp_snapshot.states}
    requested_dexes = set(perp_snapshot.requested_dexes)
    state_keys = {dex or "default" for dex in requested_dexes}
    if not perp_snapshot.catalog_complete:
        state_keys.update(previous_states)

    state_summaries: list[dict[str, Any]] = []
    for key in sorted(state_keys, key=lambda value: (value != "default", value)):
        dex = "" if key == "default" else key
        state = state_by_dex.get(dex)
        if state is not None and state.complete:
            state_summaries.append(
                live_perp_state_summary(
                    state,
                    reconciled_at=reconciled_at,
                )
            )
            continue
        previous_summary = previous_states.get(key, {})
        error = (
            state.error
            if state is not None
            else perp_snapshot.catalog_error or "Perp dex was not refreshed."
        )
        state_summaries.append(
            {
                **previous_summary,
                "accountValue": str(previous_summary.get("accountValue") or ZERO),
                "dex": key,
                "error": error,
                "stale": True,
                "status": "partial",
                "withdrawable": str(previous_summary.get("withdrawable") or ZERO),
            }
        )

    perp_equity = sum(
        (decimal_or_none(state.get("accountValue")) or ZERO for state in state_summaries),
        ZERO,
    )
    perp_withdrawable = sum(
        (decimal_or_none(state.get("withdrawable")) or ZERO for state in state_summaries),
        ZERO,
    )
    state_time: Any = None
    for summary in state_summaries:
        state_time = max_optional_numeric(state_time, summary.get("time"))

    capital_mode = live_capital_mode(settings)
    resolved_spot_state = spot_state or {}
    spot_error = remote_state_error(resolved_spot_state)
    if spot_error:
        previous_spot_total = decimal_or_none(previous.get("spotUsdcTotalUsd"))
        previous_spot_available = decimal_or_none(previous.get("spotUsdcAvailableUsd"))
        spot_usdc_total = (
            previous_spot_total
            if previous_spot_total is not None
            else (
                decimal_or_none(account.equity_usd)
                if capital_mode == LIVE_CAPITAL_MODE_UNIFIED
                else ZERO
            )
        )
        spot_usdc_available = (
            previous_spot_available
            if previous_spot_available is not None
            else (
                decimal_or_none(account.cash_balance_usd)
                if capital_mode == LIVE_CAPITAL_MODE_UNIFIED
                else ZERO
            )
        )
        spot_usdc_total = spot_usdc_total or ZERO
        spot_usdc_available = spot_usdc_available or ZERO
        stored_spot_state = previous.get("spotState")
    else:
        spot_usdc_total = live_spot_usdc_total(resolved_spot_state)
        spot_usdc_available = live_spot_usdc_available(resolved_spot_state)
        stored_spot_state = resolved_spot_state

    user_abstraction_error = remote_state_error(user_abstraction)
    if user_abstraction_error:
        stored_user_abstraction = previous.get("userAbstraction")
        stored_user_abstraction_raw = previous.get("userAbstractionRaw")
    else:
        stored_user_abstraction = normalize_user_abstraction(user_abstraction)
        stored_user_abstraction_raw = user_abstraction

    if capital_mode == LIVE_CAPITAL_MODE_UNIFIED:
        account.equity_usd = spot_usdc_total
        account.cash_balance_usd = spot_usdc_available
        tradable_equity = spot_usdc_available
    else:
        account.equity_usd = perp_equity + spot_usdc_total
        account.cash_balance_usd = perp_withdrawable + spot_usdc_available
        tradable_equity = perp_equity
    if reconciliation_status == "complete":
        account.last_reconciled_at = reconciled_at
    errors = component_errors or {}
    attempt_payload = {
        "attemptedAt": reconciled_at.isoformat(),
        "componentErrors": errors,
        "incompleteComponents": list(incomplete_components),
        "status": reconciliation_status,
    }
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {
            "lastReconciliation": {
                "attemptedAt": reconciled_at.isoformat(),
                "capitalMode": capital_mode,
                "componentErrors": errors,
                "incompleteComponents": list(incomplete_components),
                "perpEquityUsd": str(perp_equity),
                "perpStates": state_summaries,
                "perpWithdrawableUsd": str(perp_withdrawable),
                "spotState": stored_spot_state,
                "spotStateError": spot_error,
                "spotUsdcAvailableUsd": str(spot_usdc_available),
                "spotUsdcTotalUsd": str(spot_usdc_total),
                "status": reconciliation_status,
                "tradableEquityUsd": str(tradable_equity),
                "time": state_time,
                "unifiedAvailableUsd": str(spot_usdc_available),
                "unifiedEquityUsd": str(spot_usdc_total),
                "userAbstraction": stored_user_abstraction,
                "userAbstractionError": user_abstraction_error,
                "userAbstractionRaw": stored_user_abstraction_raw,
            },
            "lastReconciliationAttempt": attempt_payload,
        },
    )


def live_perp_state_summary(
    state: LivePerpState,
    *,
    reconciled_at: datetime,
) -> dict[str, Any]:
    payload = state.payload
    margin_summary = payload.get("marginSummary")
    if not isinstance(margin_summary, dict):
        margin_summary = payload.get("crossMarginSummary")
    state_equity = (
        decimal_or_none(margin_summary.get("accountValue"))
        if isinstance(margin_summary, dict)
        else ZERO
    )
    state_withdrawable = decimal_or_none(payload.get("withdrawable")) or ZERO
    return {
        "accountValue": str(state_equity or ZERO),
        "dex": state.dex or "default",
        "error": None,
        "marginSummary": margin_summary if isinstance(margin_summary, dict) else None,
        "reconciledAt": reconciled_at.isoformat(),
        "stale": False,
        "status": "complete",
        "time": payload.get("time"),
        "withdrawable": str(state_withdrawable),
    }
