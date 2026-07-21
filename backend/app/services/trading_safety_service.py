from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)

UNSENT_ENTRY_STATUSES = {"planned", "ready"}


def apply_live_account_status(
    account: TradingAccount,
    *,
    status: str,
    reason: str,
    changed_at: datetime | None = None,
) -> None:
    if status not in {"disabled", "enabled", "exit_only"}:
        raise ValueError("Unsupported live account status.")
    now = changed_at or datetime.now(UTC)
    account.status = status
    account.lifecycle_version = int(account.lifecycle_version or 0) + 1
    account.status_changed_at = now
    account.status_reason = reason


async def cancel_unsent_live_entries(
    session: AsyncSession,
    *,
    account_key: str | None = None,
    exclude_order_id: Any | None = None,
    reason: str = "Entry canceled by protective account state.",
) -> int:
    statement = (
        select(TradingOrder)
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.reduce_only.is_(False),
            TradingOrder.status.in_(UNSENT_ENTRY_STATUSES),
        )
        .with_for_update()
    )
    if account_key is not None:
        statement = statement.where(TradingOrder.account_key == account_key)
    if exclude_order_id is not None:
        statement = statement.where(TradingOrder.id != exclude_order_id)
    orders = list((await session.scalars(statement)).all())
    if not orders:
        return 0

    now = datetime.now(UTC)
    dispatches: dict[object, TradingOrderDispatch] = {}
    for dispatch in (
        await session.scalars(
            select(TradingOrderDispatch)
            .where(TradingOrderDispatch.order_id.in_([order.id for order in orders]))
            .with_for_update()
        )
    ).all():
        previous = dispatches.get(dispatch.order_id)
        if previous is None or dispatch.attempt_number > previous.attempt_number:
            dispatches[dispatch.order_id] = dispatch
    for order in orders:
        order.status = "canceled"
        order.error = reason
        dispatch = dispatches.get(order.id)
        if dispatch is not None:
            dispatch.status = "canceled"
            dispatch.completed_at = now
            dispatch.last_error = reason
    await session.flush()
    return len(orders)


async def trip_live_account_risk(
    session: AsyncSession,
    *,
    account: TradingAccount,
    rule: str,
    message: str,
    observed: str | int | float | None = None,
    limit: str | int | float | None = None,
) -> None:
    now = datetime.now(UTC)
    if account.status == "enabled":
        apply_live_account_status(
            account,
            status="exit_only",
            reason=f"risk_trip:{rule}",
            changed_at=now,
        )
    canceled_orders = await cancel_unsent_live_entries(
        session,
        account_key=account.key,
        reason=f"Entry canceled after risk trip: {rule}.",
    )
    payload = {
        "accountKey": account.key,
        "rule": rule,
        "observed": observed,
        "limit": limit,
        "canceledOrders": canceled_orders,
        "lifecycleVersion": account.lifecycle_version,
    }
    record_risk_event(
        session,
        event_type="live_account_risk_trip",
        severity="critical",
        message=message,
        payload=payload,
    )
    record_audit_log(
        session,
        actor="risk_engine",
        action="live_account.risk_trip",
        payload=payload,
    )
    await session.flush()


def record_risk_event(
    session: AsyncSession,
    *,
    event_type: str,
    severity: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> RiskEvent:
    event = RiskEvent(
        event_type=event_type,
        severity=severity,
        message=message,
        payload=payload,
    )
    session.add(event)
    return event


def record_audit_log(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(actor=actor, action=action, payload=payload)
    session.add(entry)
    return entry
