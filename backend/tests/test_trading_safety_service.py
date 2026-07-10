from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.db.models import (
    AuditLog,
    LiveEntrySafetyControl,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.services import trading_safety_service
from app.services.job_lock_service import JobLockAlreadyHeldError
from app.services.trading_safety_service import (
    LiveEntrySafetyError,
    apply_live_account_status,
    cancel_unsent_live_entries,
    ensure_live_entries_enabled,
    live_entry_control_gate,
    load_live_entry_safety_control,
    set_live_entry_safety_state,
    trip_live_account_risk,
)


class FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class FakeSession:
    def __init__(
        self,
        *,
        scalar_values: list[Any] | None = None,
        scalars_values: list[list[Any]] | None = None,
    ) -> None:
        self.scalar_values = list(scalar_values or [])
        self.scalars_values = list(scalars_values or [])
        self.added: list[Any] = []
        self.flush_count = 0
        self.commit_count = 0

    async def scalar(self, _statement: object) -> Any:
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, _statement: object) -> FakeScalarResult:
        rows = self.scalars_values.pop(0) if self.scalars_values else []
        return FakeScalarResult(rows)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def commit(self) -> None:
        self.commit_count += 1


def live_account(*, status: str = "enabled", lifecycle_version: int = 0) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="testnet",
        lifecycle_version=lifecycle_version,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_order(*, status: str = "ready") -> TradingOrder:
    return TradingOrder(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin="BTC",
        action="open",
        side="long",
        is_buy=True,
        reduce_only=False,
        order_type="ioc",
        status=status,
        requested_size=Decimal("0.5"),
        requested_notional_usd=Decimal("50"),
        filled_size=Decimal("0"),
        filled_notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_missing_safety_control_is_created_paused() -> None:
    session = FakeSession()

    control = await load_live_entry_safety_control(session)  # type: ignore[arg-type]

    assert control.entry_state == "paused"
    assert control.revision == 0
    assert control.changed_by == "system"
    assert control.changed_at.tzinfo == UTC
    assert session.added == [control]
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_entry_gate_requires_enabled_control() -> None:
    paused = LiveEntrySafetyControl(
        id=1,
        entry_state="paused",
        revision=3,
        changed_by="operator",
        changed_at=datetime.now(UTC),
    )
    paused_session = FakeSession(scalar_values=[paused])

    with pytest.raises(LiveEntrySafetyError, match="Live entries are paused"):
        await ensure_live_entries_enabled(paused_session)  # type: ignore[arg-type]

    enabled = LiveEntrySafetyControl(
        id=1,
        entry_state="enabled",
        revision=4,
        changed_by="operator",
        changed_at=datetime.now(UTC),
    )
    enabled_session = FakeSession(scalar_values=[enabled])

    assert await ensure_live_entries_enabled(enabled_session) is enabled  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_safety_control_waits_for_short_entry_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    @asynccontextmanager
    async def contended_gate(_session: object) -> AsyncIterator[None]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise JobLockAlreadyHeldError("entry finalization")
        yield

    monkeypatch.setattr(trading_safety_service, "live_entry_gate", contended_gate)
    monkeypatch.setattr(trading_safety_service, "LIVE_ENTRY_CONTROL_RETRY_SECONDS", 0)

    entered = False
    async with live_entry_control_gate(FakeSession()):  # type: ignore[arg-type]
        entered = True

    assert entered is True
    assert attempts == 3


def test_apply_live_account_status_updates_lifecycle_metadata() -> None:
    account = live_account(status="enabled", lifecycle_version=7)
    changed_at = datetime(2026, 7, 10, 12, tzinfo=UTC)

    apply_live_account_status(
        account,
        status="exit_only",
        reason="risk_trip:max_daily_loss",
        changed_at=changed_at,
    )

    assert account.status == "exit_only"
    assert account.lifecycle_version == 8
    assert account.status_changed_at == changed_at
    assert account.status_reason == "risk_trip:max_daily_loss"


@pytest.mark.asyncio
async def test_cancel_unsent_entries_cancels_matching_dispatches() -> None:
    order = live_order()
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=order.client_order_id,
        status="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
    )
    session = FakeSession(scalars_values=[[order], [dispatch]])

    canceled = await cancel_unsent_live_entries(  # type: ignore[arg-type]
        session,
        account_key=order.account_key,
        reason="Risk control stopped entries.",
    )

    assert canceled == 1
    assert order.status == "canceled"
    assert order.error == "Risk control stopped entries."
    assert dispatch.status == "canceled"
    assert dispatch.completed_at is not None
    assert dispatch.last_error == order.error
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_pausing_global_entries_moves_live_accounts_to_exit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = LiveEntrySafetyControl(
        id=1,
        entry_state="enabled",
        revision=8,
        changed_by="operator",
        changed_at=datetime.now(UTC),
    )
    account = live_account()
    session = FakeSession(scalars_values=[[account]])

    @asynccontextmanager
    async def fake_gate(_session: object) -> AsyncIterator[None]:
        yield

    async def fake_load_control(*_args: object, **_kwargs: object) -> LiveEntrySafetyControl:
        return control

    async def fake_cancel(*_args: object, **_kwargs: object) -> int:
        return 2

    monkeypatch.setattr(trading_safety_service, "live_entry_gate", fake_gate)
    monkeypatch.setattr(
        trading_safety_service,
        "load_live_entry_safety_control",
        fake_load_control,
    )
    monkeypatch.setattr(trading_safety_service, "cancel_unsent_live_entries", fake_cancel)

    updated = await set_live_entry_safety_state(  # type: ignore[arg-type]
        session,
        entry_state="paused",
        reason="Operator maintenance",
        actor="admin",
    )

    assert updated is control
    assert control.entry_state == "paused"
    assert control.revision == 9
    assert control.reason == "Operator maintenance"
    assert account.status == "exit_only"
    assert account.lifecycle_version == 1
    assert account.status_reason == "global_entry_paused:Operator maintenance"
    risk_event = next(value for value in session.added if isinstance(value, RiskEvent))
    audit_log = next(value for value in session.added if isinstance(value, AuditLog))
    assert risk_event.event_type == "live_entries_paused"
    assert risk_event.severity == "warning"
    assert risk_event.payload["affectedAccounts"] == ["live_test"]
    assert risk_event.payload["canceledOrders"] == 2
    assert audit_log.action == "live_entries.paused"
    assert audit_log.actor == "admin"


@pytest.mark.asyncio
async def test_risk_trip_is_durable_and_moves_account_to_exit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account(lifecycle_version=2)
    session = FakeSession()

    async def fake_cancel(*_args: object, **kwargs: object) -> int:
        assert kwargs["account_key"] == account.key
        assert kwargs["reason"] == "Entry canceled after risk trip: max_weekly_loss."
        return 3

    monkeypatch.setattr(trading_safety_service, "cancel_unsent_live_entries", fake_cancel)

    await trip_live_account_risk(  # type: ignore[arg-type]
        session,
        account=account,
        rule="max_weekly_loss",
        message="Weekly loss limit reached.",
        observed="-151.25",
        limit="150",
    )

    assert account.status == "exit_only"
    assert account.lifecycle_version == 3
    assert account.status_reason == "risk_trip:max_weekly_loss"
    risk_event = next(value for value in session.added if isinstance(value, RiskEvent))
    audit_log = next(value for value in session.added if isinstance(value, AuditLog))
    assert risk_event.event_type == "live_account_risk_trip"
    assert risk_event.severity == "critical"
    assert risk_event.message == "Weekly loss limit reached."
    assert risk_event.payload == {
        "accountKey": "live_test",
        "rule": "max_weekly_loss",
        "observed": "-151.25",
        "limit": "150",
        "canceledOrders": 3,
        "lifecycleVersion": 3,
    }
    assert audit_log.actor == "risk_engine"
    assert audit_log.action == "live_account.risk_trip"
    assert audit_log.payload == risk_event.payload
    assert session.flush_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_state", "reason"),
    [("unknown", "Reason"), ("paused", "   ")],
)
async def test_invalid_safety_state_changes_are_rejected(
    entry_state: str,
    reason: str,
) -> None:
    with pytest.raises(LiveEntrySafetyError):
        await set_live_entry_safety_state(  # type: ignore[arg-type]
            FakeSession(),
            entry_state=entry_state,
            reason=reason,
            actor="admin",
        )
