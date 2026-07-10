from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    LiveEntrySafetyControl,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.services.job_lock_service import JobLockAlreadyHeldError, job_lock

LIVE_ENTRY_CONTROL_ID = 1
LIVE_ENTRY_GATE_LOCK_KEY = "live_entry_gate"
LIVE_ENTRY_GATE_TTL_SECONDS = 300
LIVE_ENTRY_CONTROL_WAIT_SECONDS = 10
LIVE_ENTRY_CONTROL_RETRY_SECONDS = 0.1
LIVE_ENTRY_STATES = {"enabled", "paused", "killed"}
UNSENT_ENTRY_STATUSES = {"planned", "ready"}


class LiveEntrySafetyError(RuntimeError):
    pass


@asynccontextmanager
async def live_entry_gate(session: AsyncSession) -> AsyncIterator[None]:
    async with job_lock(
        session,
        key=LIVE_ENTRY_GATE_LOCK_KEY,
        ttl_seconds=LIVE_ENTRY_GATE_TTL_SECONDS,
    ):
        yield


@asynccontextmanager
async def live_entry_control_gate(session: AsyncSession) -> AsyncIterator[None]:
    deadline = monotonic() + LIVE_ENTRY_CONTROL_WAIT_SECONDS
    while True:
        entered = False
        try:
            async with live_entry_gate(session):
                entered = True
                yield
            return
        except JobLockAlreadyHeldError as exc:
            if entered:
                raise
            if monotonic() >= deadline:
                raise LiveEntrySafetyError(
                    "Timed out waiting for the live entry finalization gate. Retry the safety "
                    "action immediately."
                ) from exc
            await asyncio.sleep(LIVE_ENTRY_CONTROL_RETRY_SECONDS)


async def load_live_entry_safety_control(
    session: AsyncSession,
    *,
    for_update: bool = False,
) -> LiveEntrySafetyControl:
    statement = select(LiveEntrySafetyControl).where(
        LiveEntrySafetyControl.id == LIVE_ENTRY_CONTROL_ID
    )
    if for_update:
        statement = statement.with_for_update()
    control = await session.scalar(statement)
    if control is not None:
        return control

    control = LiveEntrySafetyControl(
        id=LIVE_ENTRY_CONTROL_ID,
        entry_state="paused",
        revision=0,
        reason="Safety control initialized in paused state.",
        changed_by="system",
        changed_at=datetime.now(UTC),
    )
    session.add(control)
    await session.flush()
    return control


async def ensure_live_entries_enabled(session: AsyncSession) -> LiveEntrySafetyControl:
    control = await load_live_entry_safety_control(session, for_update=True)
    if control.entry_state != "enabled":
        raise LiveEntrySafetyError(
            f"Live entries are {control.entry_state}. Reduce-only exits remain available."
        )
    return control


async def set_live_entry_safety_state(
    session: AsyncSession,
    *,
    entry_state: str,
    reason: str,
    actor: str,
) -> LiveEntrySafetyControl:
    if entry_state not in LIVE_ENTRY_STATES:
        raise LiveEntrySafetyError("Unsupported live entry safety state.")
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise LiveEntrySafetyError("A safety control reason is required.")

    async with live_entry_control_gate(session):
        control = await load_live_entry_safety_control(session, for_update=True)
        previous_state = control.entry_state
        now = datetime.now(UTC)
        control.entry_state = entry_state
        control.revision += 1
        control.reason = normalized_reason
        control.changed_by = actor
        control.changed_at = now

        affected_accounts: list[str] = []
        canceled_orders = 0
        in_flight_entries = 0
        if entry_state in {"paused", "killed"}:
            accounts = list(
                (
                    await session.scalars(
                        select(TradingAccount)
                        .where(
                            TradingAccount.account_type == "live",
                            TradingAccount.status == "enabled",
                            TradingAccount.archived_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).all()
            )
            for account in accounts:
                apply_live_account_status(
                    account,
                    status="exit_only",
                    reason=f"global_entry_{entry_state}:{normalized_reason}",
                    changed_at=now,
                )
                affected_accounts.append(account.key)
            canceled_orders = await cancel_unsent_live_entries(session)
            in_flight_entries = int(
                await session.scalar(
                    select(func.count(TradingOrder.id)).where(
                        TradingOrder.account_type == "live",
                        TradingOrder.reduce_only.is_(False),
                        TradingOrder.status.in_(
                            [
                                "submitting",
                                "uncertain",
                                "submitted",
                                "accepted",
                                "partially_filled",
                            ]
                        ),
                    )
                )
                or 0
            )

        record_audit_log(
            session,
            actor=actor,
            action=f"live_entries.{entry_state}",
            payload={
                "previousState": previous_state,
                "entryState": entry_state,
                "reason": normalized_reason,
                "revision": control.revision,
                "affectedAccounts": affected_accounts,
                "canceledOrders": canceled_orders,
                "inFlightEntries": in_flight_entries,
            },
        )
        if entry_state in {"paused", "killed"}:
            record_risk_event(
                session,
                event_type=f"live_entries_{entry_state}",
                severity="critical" if entry_state == "killed" else "warning",
                message=f"Live entries were {entry_state}.",
                payload={
                    "reason": normalized_reason,
                    "actor": actor,
                    "revision": control.revision,
                    "affectedAccounts": affected_accounts,
                    "canceledOrders": canceled_orders,
                    "inFlightEntries": in_flight_entries,
                },
            )
        await session.flush()
        return control


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
    dispatches = {
        dispatch.order_id: dispatch
        for dispatch in (
            await session.scalars(
                select(TradingOrderDispatch)
                .where(TradingOrderDispatch.order_id.in_([order.id for order in orders]))
                .with_for_update()
            )
        ).all()
    }
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
