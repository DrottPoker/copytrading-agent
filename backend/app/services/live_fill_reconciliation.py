"""Bring a live account's fills, funding, ledger rows, and order statuses in line
with the exchange.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingAccountCashFlow,
    TradingFill,
    TradingFundingPayment,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
)
from app.db.session import affected_row_count
from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.live_account_state import (
    LIVE_CASH_FLOW_BACKFILL_VERSION,
    live_cash_flow_backfill_version,
)
from app.services.live_closed_trades import (
    live_fill_chronology_key,
    live_fill_is_close,
    live_fill_is_open,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    live_fill_row,
    map_exchange_order_status,
    match_live_fill_order,
    merge_raw_payload,
    ms_to_datetime,
    parse_live_account_cash_flow,
    parse_live_fill,
    parse_live_funding_payment,
    string_or_none,
)
from app.services.live_execution_state import (
    RECONCILABLE_ORDER_STATUSES,
    load_live_order_dispatch,
    record_live_order_exchange_result,
    record_live_order_status_lookup,
)
from app.services.live_position_reconciliation import (
    apply_live_source_fill_to_position,
)
from app.services.live_trading_constants import (
    LIVE_EXCHANGE_SOURCE,
    POSITION_EPSILON,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


ACTIVE_ORDER_STATUSES = RECONCILABLE_ORDER_STATUSES


@dataclass(frozen=True)
class LiveOrderReconciliationResult:
    updated_orders: int = 0
    unresolved_order_ids: tuple[UUID, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)


async def still_unresolved_live_order_ids(
    session: AsyncSession,
    *,
    order_ids: tuple[UUID, ...],
) -> tuple[UUID, ...]:
    if not order_ids:
        return ()
    result = await session.scalars(
        select(TradingOrder.id).where(
            TradingOrder.id.in_(order_ids),
            TradingOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
    )
    return tuple(result.all())


async def reconcile_live_order_statuses(
    session: AsyncSession,
    *,
    account: TradingAccount,
    user_address: str,
    client: HyperliquidClient,
) -> LiveOrderReconciliationResult:
    result = await session.scalars(
        select(TradingOrder)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
        .order_by(TradingOrder.created_at.asc())
    )
    orders = list(result.all())
    updated = 0
    unresolved_order_ids: list[UUID] = []
    errors: dict[str, str] = {}
    lookup_started_at = datetime.now(UTC)
    for order in orders:
        dispatch = await load_live_order_dispatch(session, order_id=order.id)
        if dispatch is None:
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = "Live order dispatch attempt is missing."
            continue
        if (
            dispatch.status == "uncertain"
            and dispatch.last_status_lookup_at is not None
            and dispatch.last_status_lookup_at >= lookup_started_at - timedelta(seconds=30)
        ):
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = "Exchange status was already checked for this attempt."
            continue
        try:
            status_response = await client.order_status(
                user=user_address,
                oid=dispatch.client_order_id,
            )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            record_live_order_status_lookup(dispatch, error=exc)
            order.raw_payload = merge_raw_payload(
                order.raw_payload,
                {"orderStatusError": {"message": message, "type": exc.__class__.__name__}},
            )
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = message
            updated += 1
            continue
        mapped_status = mapped_exchange_order_status(status_response)
        record_live_order_status_lookup(dispatch, response=status_response)
        changed = apply_order_status_response(order, status_response)
        if mapped_status is not None:
            if dispatch.status != "completed":
                dispatch.status = "completed"
                dispatch.completed_at = datetime.now(UTC)
                changed = True
            record_live_order_exchange_result(
                dispatch,
                exchange_status=mapped_status,
                error_code=None,
                error_message=None,
                raw_response=status_response,
            )
        else:
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = "Exchange order status is missing or unrecognized."
        if changed:
            updated += 1
    await session.flush()
    return LiveOrderReconciliationResult(
        updated_orders=updated,
        unresolved_order_ids=tuple(unresolved_order_ids),
        errors=errors,
    )


def apply_order_status_response(order: TradingOrder, response: dict[str, Any]) -> bool:
    before_status = order.status
    before_exchange_id = order.exchange_order_id
    order.raw_payload = merge_raw_payload(order.raw_payload, {"orderStatusResponse": response})
    if response.get("status") != "order":
        return before_status != order.status or before_exchange_id != order.exchange_order_id

    payload = response.get("order")
    if not isinstance(payload, dict):
        return False
    exchange_order = payload.get("order")
    if isinstance(exchange_order, dict):
        order.exchange_order_id = (
            string_or_none(exchange_order.get("oid")) or order.exchange_order_id
        )
    mapped_status = map_exchange_order_status(string_or_none(payload.get("status")))
    status_time = ms_to_datetime(decimal_or_none(payload.get("statusTimestamp")))
    if mapped_status is not None:
        order.status = mapped_status
    if mapped_status in {"accepted", "filled"} and status_time is not None:
        order.accepted_at = order.accepted_at or status_time
    if mapped_status == "filled" and status_time is not None:
        order.filled_at = order.filled_at or status_time
    return before_status != order.status or before_exchange_id != order.exchange_order_id


def mapped_exchange_order_status(response: dict[str, Any]) -> str | None:
    if response.get("status") != "order":
        return None
    payload = response.get("order")
    if not isinstance(payload, dict):
        return None
    return map_exchange_order_status(string_or_none(payload.get("status")))


async def reconcile_live_fills(
    session: AsyncSession,
    *,
    account: TradingAccount,
    fills: list[dict[str, Any]],
) -> int:
    orders = await load_live_orders_for_fill_matching(session, account_key=account.key)
    orders_by_oid = {order.exchange_order_id: order for order in orders if order.exchange_order_id}
    orders_by_cloid = await load_live_orders_by_attempt_cloid(
        session,
        account_key=account.key,
    )
    repaired_attributions = await repair_unattributed_live_fills_from_order_ledger(
        session,
        account_key=account.key,
        orders_by_oid=orders_by_oid,
        orders_by_cloid=orders_by_cloid,
    )
    if repaired_attributions:
        logger.warning(
            "repaired live fill source attribution account=%s fills=%s",
            account.key,
            repaired_attributions,
        )
    inventory_repairs = await repair_unattributed_live_fills_from_source_inventory(
        session,
        account_key=account.key,
    )
    if inventory_repairs:
        logger.warning(
            "repaired live fill source attribution from inventory account=%s fills=%s",
            account.key,
            inventory_repairs,
        )
    inserted = 0
    for fill in fills:
        parsed = parse_live_fill(fill, account_key=account.key)
        if parsed is None:
            continue
        matched_order = match_live_fill_order(
            parsed,
            orders_by_oid=orders_by_oid,
            orders_by_cloid=orders_by_cloid,
        )
        if matched_order is not None and parsed.get("exchange_order_id"):
            matched_order.exchange_order_id = (
                matched_order.exchange_order_id or parsed["exchange_order_id"]
            )
        attributed_position = None
        if matched_order is None:
            attributed_position = await resolve_unique_live_fill_source_position(
                session,
                account_key=account.key,
                parsed_fill=parsed,
            )
        row = live_fill_row(
            parsed,
            account=account,
            order=matched_order,
            attributed_position=attributed_position,
        )
        stmt = insert(TradingFill).values(**row)
        stmt = stmt.on_conflict_do_nothing(constraint="ux_trading_fills_exchange_fill_id")
        result = await session.execute(stmt)
        inserted_row = affected_row_count(result) > 0
        inserted += int(inserted_row)
        if inserted_row:
            await apply_live_source_fill_to_position(
                session,
                account=account,
                order=matched_order,
                attributed_position=attributed_position,
                parsed_fill=parsed,
            )
    await session.flush()
    return inserted


async def repair_unattributed_live_fills_from_order_ledger(
    session: AsyncSession,
    *,
    account_key: str,
    orders_by_oid: dict[str, TradingOrder],
    orders_by_cloid: dict[str, TradingOrder],
) -> int:
    """Attach exchange-imported fills when durable order identity now proves ownership."""

    identity_filters = []
    if orders_by_oid:
        identity_filters.append(TradingFill.raw_payload.op("->>")("oid").in_(tuple(orders_by_oid)))
    if orders_by_cloid:
        identity_filters.append(
            TradingFill.raw_payload.op("->>")("cloid").in_(tuple(orders_by_cloid))
        )
    if not identity_filters:
        return 0

    result = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.order_id.is_(None),
            TradingFill.source_wallet == LIVE_EXCHANGE_SOURCE,
            or_(*identity_filters),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    repaired = 0
    for fill in result.all():
        raw_payload = fill.raw_payload if isinstance(fill.raw_payload, dict) else None
        if raw_payload is None:
            continue
        parsed_fill = parse_live_fill(raw_payload, account_key=account_key)
        if parsed_fill is None:
            continue
        order = match_live_fill_order(
            parsed_fill,
            orders_by_oid=orders_by_oid,
            orders_by_cloid=orders_by_cloid,
        )
        if order is None or order.account_key != account_key or order.coin != fill.coin:
            continue
        fill.order_id = order.id
        fill.source_wallet = order.source_wallet
        fill.source_fill_id = order.source_fill_id
        fill.sequence_index = order.sequence_index
        fill.action = order.action
        fill.side = order.side
        fill.raw_payload = merge_raw_payload(
            raw_payload,
            {
                "sourceAttributionRepair": {
                    "logicalOrderId": str(order.id),
                    "sourceFillId": order.source_fill_id,
                    "sourceWallet": order.source_wallet,
                }
            },
        )
        repaired += 1
    if repaired:
        await session.flush()
    return repaired


async def repair_unattributed_live_fills_from_source_inventory(
    session: AsyncSession,
    *,
    account_key: str,
) -> int:
    """Attach unmatched exchange closes when fill inventory proves one owner."""

    has_unattributed_close = await session.scalar(
        select(
            exists().where(
                TradingFill.account_key == account_key,
                TradingFill.account_type == "live",
                TradingFill.source_wallet == LIVE_EXCHANGE_SOURCE,
                TradingFill.action.in_(("reduce", "close", "flip_close")),
            )
        )
    )
    if not has_unattributed_close:
        return 0

    result = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
        )
        .order_by(
            TradingFill.filled_at.asc(),
            TradingFill.created_at.asc(),
            TradingFill.id.asc(),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    fills = list(result.all())
    attributions = infer_live_exchange_fill_source_attributions(fills)
    repaired_at = datetime.now(UTC).isoformat()
    repaired = 0
    for fill in fills:
        source_wallet = attributions.get(fill.id)
        if source_wallet is None:
            continue
        fill.source_wallet = source_wallet
        fill.raw_payload = merge_raw_payload(
            fill.raw_payload,
            {
                "sourceAttributionRepair": {
                    "method": "source_fill_inventory",
                    "repairedAt": repaired_at,
                    "sourceWallet": source_wallet,
                }
            },
        )
        repaired += 1
    await session.flush()
    return repaired


def infer_live_exchange_fill_source_attributions(
    fills: list[TradingFill],
) -> dict[UUID, str]:
    inventories: dict[tuple[str, str, str], dict[str, Decimal]] = {}
    attributions: dict[UUID, str] = {}
    for fill in sorted(fills, key=live_fill_chronology_key):
        fill_size = decimal_or_none(fill.size) or ZERO
        if fill_size <= ZERO:
            continue
        market_key = (fill.account_key, fill.coin, fill.side)
        market_inventory = inventories.setdefault(market_key, {})
        source_wallet = fill.source_wallet.lower()

        if live_fill_is_open(fill):
            if not is_internal_live_source(source_wallet):
                market_inventory[source_wallet] = (
                    market_inventory.get(source_wallet, ZERO) + fill_size
                )
            continue
        if not live_fill_is_close(fill):
            continue

        if source_wallet == LIVE_EXCHANGE_SOURCE:
            owners = [
                wallet for wallet, size in market_inventory.items() if size > POSITION_EPSILON
            ]
            if len(owners) != 1:
                continue
            source_wallet = owners[0]
            if fill_size > market_inventory[source_wallet] + POSITION_EPSILON:
                continue
            attributions[fill.id] = source_wallet

        owned_size = market_inventory.get(source_wallet, ZERO)
        if owned_size > ZERO:
            market_inventory[source_wallet] = max(owned_size - fill_size, ZERO)
    return attributions


def is_internal_live_source(source_wallet: str) -> bool:
    return source_wallet.startswith("__")


async def resolve_unique_live_fill_source_position(
    session: AsyncSession,
    *,
    account_key: str,
    parsed_fill: dict[str, Any],
) -> TradingPosition | None:
    if parsed_fill["action"] not in {"reduce", "close", "flip_close"}:
        return None
    result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
            TradingPosition.coin == parsed_fill["coin"],
            TradingPosition.side == parsed_fill["side"],
            TradingPosition.size > POSITION_EPSILON,
        )
        .with_for_update()
    )
    positions = list(result.all())
    if len(positions) != 1:
        return None
    position = positions[0]
    return position if parsed_fill["size"] <= position.size + POSITION_EPSILON else None


async def reconcile_live_funding_payments(
    session: AsyncSession,
    *,
    account: TradingAccount,
    payments: list[dict[str, Any]],
) -> int:
    inserted = 0
    for payment in payments:
        parsed = parse_live_funding_payment(payment, account_key=account.key)
        if parsed is None:
            continue
        stmt = insert(TradingFundingPayment).values(
            account_key=account.key,
            account_type="live",
            **parsed,
        )
        stmt = stmt.on_conflict_do_nothing(constraint="ux_trading_funding_payments_account_event")
        result = await session.execute(stmt)
        inserted += int(affected_row_count(result) > 0)
    await session.flush()
    return inserted


async def reconcile_live_account_cash_flows(
    session: AsyncSession,
    *,
    account: TradingAccount,
    user_address: str,
    updates: list[dict[str, Any]],
) -> int:
    inserted = 0
    for update in updates:
        parsed = parse_live_account_cash_flow(
            update,
            account_key=account.key,
            user_address=user_address,
        )
        if parsed is None:
            continue
        stmt = insert(TradingAccountCashFlow).values(
            account_key=account.key,
            account_type="live",
            **parsed,
        )
        stmt = stmt.on_conflict_do_nothing(constraint="ux_trading_account_cash_flows_account_event")
        result = await session.execute(stmt)
        inserted += int(affected_row_count(result) > 0)
    await session.flush()
    return inserted


async def load_live_orders_for_fill_matching(
    session: AsyncSession,
    *,
    account_key: str,
) -> list[TradingOrder]:
    result = await session.scalars(
        select(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
        )
    )
    return list(result.all())


async def load_live_orders_by_attempt_cloid(
    session: AsyncSession,
    *,
    account_key: str,
) -> dict[str, TradingOrder]:
    """Resolve exchange fill CLOIDs through dispatch attempts, never logical IDs."""

    result = await session.execute(
        select(TradingOrderDispatch.client_order_id, TradingOrder)
        .join(TradingOrder, TradingOrder.id == TradingOrderDispatch.order_id)
        .where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
        )
    )
    return {client_order_id: order for client_order_id, order in result.all() if client_order_id}


async def update_live_orders_from_reconciled_fills(
    session: AsyncSession,
    *,
    account_key: str,
) -> int:
    order_result = await session.scalars(
        select(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
        )
    )
    orders = {order.id: order for order in order_result.all()}
    if not orders:
        return 0

    fill_result = await session.execute(
        select(
            TradingFill.order_id,
            func.sum(TradingFill.size),
            func.sum(TradingFill.notional_usd),
            func.sum(TradingFill.fee_usd),
            func.max(TradingFill.filled_at),
        )
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.order_id.in_(list(orders)),
        )
        .group_by(TradingFill.order_id)
    )
    updated = 0
    for order_id, filled_size, filled_notional, fee_usd, filled_at in fill_result.all():
        order = orders.get(order_id) if order_id is not None else None
        if order is None:
            continue
        size = filled_size or ZERO
        notional = filled_notional or ZERO
        before_status = order.status
        order.filled_size = size
        order.filled_notional_usd = notional
        order.fee_usd = fee_usd or ZERO
        if size > ZERO:
            order.average_fill_price = notional / size
            order.filled_at = filled_at or order.filled_at
            if size >= order.requested_size:
                order.status = "filled"
            elif order.status != "filled":
                order.status = "partially_filled"
        if order.status != before_status or size > ZERO:
            updated += 1
    await session.flush()
    return updated


async def live_fill_reconciliation_start_time_ms(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
    now: datetime,
    lookback_minutes: int | None = None,
) -> int:
    if lookback_minutes is not None:
        start_at = now - timedelta(minutes=max(lookback_minutes, 1))
        return int(start_at.timestamp() * 1000)
    latest_fill_at = await session.scalar(
        select(func.max(TradingFill.filled_at)).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
        )
    )
    if latest_fill_at is not None:
        start_at = latest_fill_at - timedelta(minutes=5)
    else:
        start_at = now - timedelta(minutes=settings.live_trading_reconciliation_lookback_minutes)
    return int(start_at.timestamp() * 1000)


async def live_funding_reconciliation_start_time_ms(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
    now: datetime,
    lookback_minutes: int | None = None,
) -> int:
    if lookback_minutes is not None:
        start_at = now - timedelta(minutes=max(lookback_minutes, 1))
        return int(start_at.timestamp() * 1000)
    latest_payment_at = await session.scalar(
        select(func.max(TradingFundingPayment.occurred_at)).where(
            TradingFundingPayment.account_key == account_key,
        )
    )
    if latest_payment_at is not None:
        start_at = latest_payment_at - timedelta(hours=1)
    else:
        earliest_fill_at = await session.scalar(
            select(func.min(TradingFill.filled_at)).where(
                TradingFill.account_key == account_key,
                TradingFill.account_type == "live",
            )
        )
        start_at = earliest_fill_at or (
            now - timedelta(minutes=settings.live_trading_reconciliation_lookback_minutes)
        )
    return int(start_at.timestamp() * 1000)


async def live_cash_flow_reconciliation_start_time_ms(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    now: datetime,
    lookback_minutes: int | None = None,
) -> int:
    if lookback_minutes is not None:
        start_at = now - timedelta(minutes=max(lookback_minutes, 1))
        return int(start_at.timestamp() * 1000)
    if live_cash_flow_backfill_version(account) < LIVE_CASH_FLOW_BACKFILL_VERSION:
        return 0
    latest_cash_flow_at = await session.scalar(
        select(func.max(TradingAccountCashFlow.occurred_at)).where(
            TradingAccountCashFlow.account_key == account.key,
        )
    )
    if latest_cash_flow_at is not None:
        start_at = latest_cash_flow_at - timedelta(minutes=5)
    else:
        created_at = account.created_at
        utc_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
        day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        prior_snapshot_at = account.last_reconciled_at or created_at
        start_at = min(
            prior_snapshot_at or week_start,
            week_start,
        )
        if created_at is not None:
            start_at = max(start_at, created_at)
        if prior_snapshot_at is None and created_at is None:
            start_at = now - timedelta(
                minutes=settings.live_trading_reconciliation_lookback_minutes
            )
    return int(start_at.timestamp() * 1000)


def mark_live_cash_flow_backfill_complete(
    account: TradingAccount,
    *,
    reconciled_at: datetime,
) -> None:
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {
            "cashFlowTracking": {
                "historicalBackfillCompletedAt": reconciled_at.isoformat(),
                "historicalBackfillVersion": LIVE_CASH_FLOW_BACKFILL_VERSION,
            }
        },
    )
