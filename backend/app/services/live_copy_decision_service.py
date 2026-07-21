"""Read-only execution diagnostics for live-copy decision rows."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradingOrder, TradingOrderDispatch

type LiveCopyDecisionIdentity = tuple[str, str, str, int]


@dataclass(frozen=True, slots=True)
class LiveCopyDecisionExecutionDiagnostics:
    order_record_id: UUID | None = None
    logical_order_status: str | None = None
    logical_order_error: str | None = None
    latest_dispatch_attempt_number: int | None = None
    latest_dispatch_client_order_id: str | None = None
    latest_dispatch_status: str | None = None
    latest_exchange_status: str | None = None
    latest_exchange_error_code: str | None = None
    latest_exchange_error_message: str | None = None
    latest_exchange_response: dict[str, Any] | None = None
    submit_attempt_count: int = 0
    status_lookup_count: int = 0
    last_status_lookup_at: datetime | None = None
    last_status_lookup_error: str | None = None


async def load_live_copy_decision_execution_diagnostics(
    session: AsyncSession,
    *,
    decision_identities: set[LiveCopyDecisionIdentity],
) -> dict[LiveCopyDecisionIdentity, LiveCopyDecisionExecutionDiagnostics]:
    """Load logical orders and their latest persisted exchange attempts in bulk."""

    if not decision_identities:
        return {}
    order_result = await session.scalars(
        select(TradingOrder).where(
            tuple_(
                TradingOrder.account_key,
                TradingOrder.source_wallet,
                TradingOrder.source_fill_id,
                TradingOrder.sequence_index,
            ).in_(decision_identities)
        )
    )
    orders = {order.id: order for order in order_result.all()}
    if not orders:
        return {}
    order_ids = set(orders)
    dispatch_result = await session.scalars(
        select(TradingOrderDispatch)
        .where(TradingOrderDispatch.order_id.in_(order_ids))
        .order_by(
            TradingOrderDispatch.order_id.asc(),
            TradingOrderDispatch.attempt_number.desc(),
        )
    )
    latest_by_order: dict[UUID, TradingOrderDispatch] = {}
    dispatches_by_order: dict[UUID, list[TradingOrderDispatch]] = {}
    for dispatch in dispatch_result.all():
        latest_by_order.setdefault(dispatch.order_id, dispatch)
        dispatches_by_order.setdefault(dispatch.order_id, []).append(dispatch)

    diagnostics: dict[LiveCopyDecisionIdentity, LiveCopyDecisionExecutionDiagnostics] = {}
    for order_id, order in orders.items():
        dispatch = latest_by_order.get(order_id)
        identity = (
            order.account_key,
            order.source_wallet,
            order.source_fill_id,
            int(order.sequence_index),
        )
        diagnostics[identity] = LiveCopyDecisionExecutionDiagnostics(
            order_record_id=order.id,
            logical_order_status=order.status,
            logical_order_error=order.error,
            latest_dispatch_attempt_number=(dispatch.attempt_number if dispatch else None),
            latest_dispatch_client_order_id=(dispatch.client_order_id if dispatch else None),
            latest_dispatch_status=(dispatch.status if dispatch else None),
            latest_exchange_status=(dispatch.exchange_status if dispatch else None),
            latest_exchange_error_code=(dispatch.exchange_error_code if dispatch else None),
            latest_exchange_error_message=(dispatch.exchange_error_message if dispatch else None),
            latest_exchange_response=(dispatch.exchange_response if dispatch else None),
            submit_attempt_count=sum(
                max(int(candidate.attempt_count or 0), 0)
                for candidate in dispatches_by_order.get(order_id, [])
            ),
            status_lookup_count=(dispatch.status_lookup_count if dispatch else 0),
            last_status_lookup_at=(dispatch.last_status_lookup_at if dispatch else None),
            last_status_lookup_error=(dispatch.last_status_lookup_error if dispatch else None),
        )
    return diagnostics


def live_copy_decision_identity(decision: Any) -> LiveCopyDecisionIdentity:
    return (
        str(decision.account_key),
        str(decision.source_wallet),
        str(decision.source_fill_id),
        int(decision.sequence_index),
    )
