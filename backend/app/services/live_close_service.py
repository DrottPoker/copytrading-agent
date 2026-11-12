"""Close live positions: the durable close-all operation, the hard stop, and close order pricing."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingCloseAllItem,
    TradingCloseAllOperation,
    TradingOrder,
    TradingPosition,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
)
from app.services.copy_risk_rules import (
    HARD_STOP_RULE,
    hard_stop_breached,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_account_access import (
    live_account_has_incomplete_close_operation,
    live_account_nonterminal_order_count,
    load_live_account,
    load_live_account_for_update,
    load_live_order_by_client_order_id,
)
from app.services.live_exchange_fetch import (
    live_coin_with_dex,
    live_dex_from_coin,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    merge_raw_payload,
)
from app.services.live_order_submission import submit_live_trade_intent
from app.services.live_performance_service import (
    live_account_loss_state,
)
from app.services.live_position_reconciliation import (
    load_live_exchange_positions,
)
from app.services.live_reconciliation_service import (
    reconcile_live_trading_account,
)
from app.services.live_trading_errors import (
    LiveTradingServiceError,
)
from app.services.trading_core import (
    MARGIN_MODES,
    TradeIntent,
    build_copy_trade_intent,
    margin_from_notional,
    parse_margin_mode,
    parse_trade_side,
)
from app.services.trading_safety_service import (
    apply_live_account_status,
    cancel_unsent_live_entries,
    record_audit_log,
    record_risk_event,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class LiveCloseAllResult:
    account_key: str
    operation_id: UUID
    operation_status: str
    submitted_orders: int
    failed_orders: int
    status: str


async def close_all_live_account_positions(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
) -> LiveCloseAllResult:
    if account.account_type != "live":
        raise LiveTradingServiceError("Only live accounts can close live positions.")
    try:
        async with job_lock(
            session,
            key=f"live_close_all:{account.key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account.key}",
                ttl_seconds=120,
            ):
                account = await load_live_account_for_update(
                    session,
                    account_key=account.key,
                )
                if account.status != "exit_only":
                    apply_live_account_status(
                        account,
                        status="exit_only",
                        reason="close_all_requested",
                    )
                await cancel_unsent_live_entries(
                    session,
                    account_key=account.key,
                    reason="Entry canceled because close-all was requested.",
                )
                await session.commit()
            return await run_live_close_all_operation(
                session,
                account=account,
                settings=settings,
                info_client=info_client,
                trading_client=trading_client,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveTradingServiceError(
            "A close-all operation is already running for this account.",
            status_code=409,
        ) from exc


async def run_live_close_all_operation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None,
    trading_client: HyperliquidLiveTradingClient | None,
) -> LiveCloseAllResult:
    operation = await get_or_create_live_close_all_operation(
        session,
        account_key=account.key,
    )
    if account.status != "exit_only":
        apply_live_account_status(
            account,
            status="exit_only",
            reason="close_all_running",
        )
    operation.status = "running"
    operation.last_error = None
    await session.commit()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    try:
        initial_reconciliation = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        await session.commit()
        await refresh_live_close_all_items(session, operation_id=operation.id)
        await session.commit()
        positions = await load_live_exchange_positions(session, account_key=account.key)
        await session.commit()
        if not positions and initial_reconciliation.status == "complete":
            return await complete_live_close_all_operation(
                session,
                account=account,
                operation=operation,
                submitted_orders=0,
            )
        if not positions:
            operation.status = "partially_completed"
            operation.last_error = (
                "Close-all could not prove the exchange account is flat because "
                "reconciliation was partial."
            )
            await session.commit()
            return LiveCloseAllResult(
                account_key=account.key,
                operation_id=operation.id,
                operation_status=operation.status,
                submitted_orders=0,
                failed_orders=1,
                status=account.status,
            )

        mids = await load_live_close_mids(client, positions=positions)
        live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
        submitted = 0
        for position in positions:
            item = await get_or_create_live_close_all_item(
                session,
                operation=operation,
                position=position,
            )
            if item.status == "uncertain":
                continue

            mid_price = decimal_or_none(mids.get(position.coin))
            if mid_price is None or mid_price <= ZERO:
                item.status = "failed"
                item.error = "Live close price is unavailable."
                await session.commit()
                continue

            item.attempt_count += 1
            item.status = "submitting"
            item.error = None
            await session.commit()
            intent = build_live_close_position_intent(
                account=account,
                position=position,
                mid_price=mid_price,
                settings=settings,
                source_fill_id=(f"close-all-{operation.id}-{item.id}-{item.attempt_count}"),
            )
            try:
                result = await submit_live_trade_intent(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                    client=live_client,
                )
            except LiveTradingServiceError as exc:
                order = await load_live_order_by_client_order_id(
                    session,
                    client_order_id=intent.client_order_id,
                )
                refreshed_item = await session.get(TradingCloseAllItem, item.id)
                if refreshed_item is not None:
                    refreshed_item.order_id = order.id if order is not None else None
                    refreshed_item.status = (
                        "uncertain"
                        if order is not None and order.status in {"submitting", "uncertain"}
                        else "failed"
                    )
                    refreshed_item.error = str(exc) or exc.__class__.__name__
                    await session.commit()
                continue

            refreshed_item = await session.get(TradingCloseAllItem, item.id)
            if refreshed_item is not None:
                refreshed_item.order_id = result.order.id
                refreshed_item.status = close_all_item_status_for_order(result.order.status)
                refreshed_item.error = result.order.error
                await session.commit()
            submitted += int(result.submitted)

        final_reconciliation = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        await session.commit()
        remaining_positions = await load_live_exchange_positions(session, account_key=account.key)
        await refresh_live_close_all_items(session, operation_id=operation.id)
        if not remaining_positions and final_reconciliation.status == "complete":
            return await complete_live_close_all_operation(
                session,
                account=account,
                operation=operation,
                submitted_orders=submitted,
            )

        failed = await live_close_all_incomplete_item_count(
            session,
            operation_id=operation.id,
        )
        operation.status = "partially_completed"
        operation.last_error = (
            f"{len(remaining_positions)} live positions remain open."
            if final_reconciliation.status == "complete"
            else "Close-all cannot confirm flat exposure because reconciliation is partial."
        )
        if account.status != "exit_only":
            apply_live_account_status(
                account,
                status="exit_only",
                reason="close_all_incomplete",
            )
        await session.commit()
        return LiveCloseAllResult(
            account_key=account.key,
            operation_id=operation.id,
            operation_status=operation.status,
            submitted_orders=submitted,
            failed_orders=max(failed, len(remaining_positions)),
            status=account.status,
        )
    except BaseException as exc:
        await session.rollback()
        persisted_operation = await session.get(TradingCloseAllOperation, operation.id)
        if persisted_operation is not None:
            persisted_operation.status = "failed"
            persisted_operation.last_error = str(exc) or exc.__class__.__name__
            await session.commit()
        raise
    finally:
        if client_created:
            await client.__aexit__(None, None, None)


LIVE_HARD_STOP_MARKER = "hardStop"


LIVE_HARD_STOP_ACCOUNT_STATUSES = frozenset({"enabled", "exit_only"})


async def enforce_live_hard_stop(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    now: datetime | None = None,
) -> LiveCloseAllResult | None:
    """Close every position and stop the account once drawdown reaches the hard stop.

    Runs after each reconciliation, so it acts on the equity the exchange just
    reported. The close-all operation it starts is durable and resumed by the
    reconciliation loop until the account is flat. Returns None when the hard
    stop is off, not reached, or already being carried out.
    """

    if (
        settings.live_trading_hard_stop_drawdown_pct <= ZERO
        or account.account_type != "live"
        or account.status not in LIVE_HARD_STOP_ACCOUNT_STATUSES
    ):
        return None
    resolved_now = now or datetime.now(UTC)
    loss_state = await live_account_loss_state(
        session,
        account_key=account.key,
        now=resolved_now,
        since=account.loss_limits_reset_at,
    )
    if not hard_stop_breached(loss_state, settings=settings) or loss_state is None:
        return None
    positions = await load_live_exchange_positions(session, account_key=account.key)
    if not positions or await live_account_has_incomplete_close_operation(
        session,
        account_key=account.key,
    ):
        await session.commit()
        return None

    existing_payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    if not isinstance(existing_payload.get(LIVE_HARD_STOP_MARKER), dict):
        # One event per hard stop, not one per retried close-all.
        payload = {
            "accountKey": account.key,
            "rule": HARD_STOP_RULE,
            "observed": str(loss_state.drawdown_pct),
            "limit": str(settings.live_trading_hard_stop_drawdown_pct),
            "openPositions": len(positions),
        }
        account.config_payload = merge_raw_payload(
            account.config_payload,
            {LIVE_HARD_STOP_MARKER: {**payload, "trippedAt": resolved_now.isoformat()}},
        )
        record_risk_event(
            session,
            event_type="live_hard_stop",
            severity="critical",
            message="Hard stop drawdown reached; closing every position and stopping the account.",
            payload=payload,
        )
        record_audit_log(
            session,
            actor="risk_engine",
            action="live_account.hard_stop",
            payload=payload,
        )
    await session.commit()
    return await close_all_live_account_positions(
        session,
        account=account,
        settings=settings,
        info_client=info_client,
        trading_client=trading_client,
    )


async def get_or_create_live_close_all_operation(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingCloseAllOperation:
    operation = await session.scalar(
        select(TradingCloseAllOperation)
        .where(
            TradingCloseAllOperation.account_key == account_key,
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            ),
        )
        .order_by(TradingCloseAllOperation.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if operation is not None:
        return operation
    operation = TradingCloseAllOperation(
        account_key=account_key,
        status="pending",
        requested_at=datetime.now(UTC),
    )
    session.add(operation)
    await session.flush()
    return operation


async def get_or_create_live_close_all_item(
    session: AsyncSession,
    *,
    operation: TradingCloseAllOperation,
    position: TradingPosition,
) -> TradingCloseAllItem:
    item = await session.scalar(
        select(TradingCloseAllItem)
        .where(
            TradingCloseAllItem.operation_id == operation.id,
            TradingCloseAllItem.position_id == position.id,
        )
        .with_for_update()
    )
    if item is not None:
        return item
    item = TradingCloseAllItem(
        operation_id=operation.id,
        position_id=position.id,
        coin=position.coin,
        status="pending",
        attempt_count=0,
    )
    session.add(item)
    await session.flush()
    await session.commit()
    return item


def close_all_item_status_for_order(order_status: str) -> str:
    if order_status == "filled":
        return "completed"
    if order_status in {"submitting", "uncertain", "submitted", "accepted", "partially_filled"}:
        return "uncertain"
    if order_status in {"rejected", "canceled", "failed"}:
        return "failed"
    return "submitting"


async def refresh_live_close_all_items(
    session: AsyncSession,
    *,
    operation_id: UUID,
) -> None:
    items_result = await session.scalars(
        select(TradingCloseAllItem).where(
            TradingCloseAllItem.operation_id == operation_id,
        )
    )
    items = list(items_result.all())
    order_ids = [item.order_id for item in items if item.order_id is not None]
    orders: dict[UUID, TradingOrder] = {}
    if order_ids:
        orders_result = await session.scalars(
            select(TradingOrder).where(TradingOrder.id.in_(order_ids))
        )
        orders = {order.id: order for order in orders_result.all()}
    for item in items:
        if item.order_id is None:
            continue
        order = orders.get(item.order_id)
        if order is None:
            item.status = "failed"
            item.error = "Live order is missing."
            continue
        item.status = close_all_item_status_for_order(order.status)
        item.error = order.error
    await session.flush()


async def live_close_all_incomplete_item_count(
    session: AsyncSession,
    *,
    operation_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count(TradingCloseAllItem.id)).where(
            TradingCloseAllItem.operation_id == operation_id,
            TradingCloseAllItem.status.not_in(["completed", "skipped"]),
        )
    )
    return int(value or 0)


async def complete_live_close_all_operation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    operation: TradingCloseAllOperation,
    submitted_orders: int,
) -> LiveCloseAllResult:
    pending_orders = await live_account_nonterminal_order_count(
        session,
        account_key=account.key,
    )
    if pending_orders > 0:
        if account.status != "exit_only":
            apply_live_account_status(
                account,
                status="exit_only",
                reason="close_all_pending_order_work",
            )
        operation.status = "partially_completed"
        operation.last_error = f"{pending_orders} non-terminal live orders remain."
        await session.commit()
        return LiveCloseAllResult(
            account_key=account.key,
            operation_id=operation.id,
            operation_status=operation.status,
            submitted_orders=submitted_orders,
            failed_orders=pending_orders,
            status=account.status,
        )
    apply_live_account_status(
        account,
        status="disabled",
        reason="close_all_complete_and_flat",
    )
    operation.status = "completed"
    operation.completed_at = datetime.now(UTC)
    operation.last_error = None
    record_audit_log(
        session,
        actor="execution_engine",
        action="live_account.close_all_completed",
        payload={
            "accountKey": account.key,
            "operationId": str(operation.id),
            "submittedOrders": submitted_orders,
            "lifecycleVersion": account.lifecycle_version,
        },
    )
    await session.commit()
    return LiveCloseAllResult(
        account_key=account.key,
        operation_id=operation.id,
        operation_status=operation.status,
        submitted_orders=submitted_orders,
        failed_orders=0,
        status=account.status,
    )


async def resume_live_close_all_operations(
    session: AsyncSession,
    *,
    settings: Settings,
    limit: int = 10,
) -> list[LiveCloseAllResult]:
    account_keys_result = await session.scalars(
        select(TradingCloseAllOperation.account_key)
        .where(
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            )
        )
        .distinct()
        .limit(limit)
    )
    account_keys = list(account_keys_result.all())
    await session.commit()
    results: list[LiveCloseAllResult] = []
    for account_key in account_keys:
        try:
            account = await load_live_account(session, account_key=account_key)
            await session.commit()
            result = await close_all_live_account_positions(
                session,
                account=account,
                settings=settings,
            )
        except LiveTradingServiceError:
            await session.rollback()
            continue
        results.append(result)
    return results


async def load_live_close_mids(
    client: HyperliquidClient,
    *,
    positions: list[TradingPosition],
) -> dict[str, Any]:
    dexes = sorted({live_dex_from_coin(position.coin) for position in positions})
    mids_by_coin: dict[str, Any] = {}
    for dex in dexes:
        mids = await client.all_mids(dex=dex or None)
        for raw_coin, raw_price in mids.items():
            coin = str(raw_coin or "").strip()
            if not coin:
                continue
            mids_by_coin[live_coin_with_dex(coin=coin, dex=dex)] = raw_price
            if not dex:
                mids_by_coin[coin] = raw_price
    return mids_by_coin


def build_live_close_position_intent(
    *,
    account: TradingAccount,
    position: TradingPosition,
    mid_price: Decimal,
    settings: Settings,
    source_fill_prefix: str = "close-all",
    source_fill_id: str | None = None,
    price_source: str = "live_close_all",
) -> TradeIntent:
    now = datetime.now(UTC)
    limit_price = close_limit_price(
        mid_price=mid_price,
        side=position.side,
        max_slippage_bps=settings.live_trading_max_slippage_bps,
    )
    notional_usd = limit_price * position.size
    leverage = position.leverage if position.leverage > ZERO else Decimal("1")
    return build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=position.source_wallet,
        source_fill_id=(
            source_fill_id
            if source_fill_id is not None
            else f"{source_fill_prefix}-{position.coin}-{uuid4().hex}"
        ),
        sequence_index=0,
        coin=position.coin,
        action="close",
        side=parse_trade_side(position.side),
        size=position.size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        margin_mode=(
            parse_margin_mode(position.margin_mode)
            if position.margin_mode in MARGIN_MODES
            else "cross"
        ),
        limit_price=limit_price,
        source_price=mid_price,
        observed_price=mid_price,
        price_drift_bps=ZERO,
        price_source=price_source,
        allocation_pct=None,
        allocation_usd=None,
        source_perp_equity_usd=None,
        source_exposure_pct=None,
        created_at=now,
    )


def close_limit_price(
    *,
    mid_price: Decimal,
    side: str,
    max_slippage_bps: Decimal,
) -> Decimal:
    slippage_ratio = max_slippage_bps / Decimal("10000")
    if side == "long":
        return mid_price * (Decimal("1") - slippage_ratio)
    return mid_price * (Decimal("1") + slippage_ratio)
