from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.db.models import (
    AuditLog,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.services import trading_safety_service
from app.services.trading_safety_service import (
    apply_live_account_status,
    cancel_unsent_live_entries,
    trip_live_account_risk,
)


class FakeScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class FakeSession:
    def __init__(self, *, scalars_values: list[list[Any]] | None = None) -> None:
        self.scalars_values = list(scalars_values or [])
        self.added: list[Any] = []
        self.flush_count = 0

    async def scalars(self, _statement: object) -> FakeScalarResult:
        rows = self.scalars_values.pop(0) if self.scalars_values else []
        return FakeScalarResult(rows)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


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
async def test_risk_trip_is_durable_and_moves_account_to_exit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account(lifecycle_version=2)
    session = FakeSession()

    async def fake_cancel(*_args: object, **kwargs: object) -> int:
        assert kwargs["account_key"] == account.key
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
    assert risk_event.payload["canceledOrders"] == 3
    assert audit_log.action == "live_account.risk_trip"
    assert audit_log.payload == risk_event.payload
    assert session.flush_count == 1
