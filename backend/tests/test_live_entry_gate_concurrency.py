import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.models import (
    AuditLog,
    LiveEntrySafetyControl,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.integrations.hyperliquid_live_client import LiveOrderResult
from app.services import live_trading_service, trading_safety_service
from app.services.live_trading_service import (
    LiveAccountDeleteError,
    LiveOrderLifecycleResult,
    LiveReconciliationResult,
    LiveTradingServiceError,
    delete_live_trading_account,
    start_live_trading_account,
    stop_live_trading_account,
    submit_live_trade_intent,
)
from app.services.trading_core import TradeIntent
from app.services.trading_safety_service import set_live_entry_safety_state


class ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class SubmitSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.flush_count = 0

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1


class SafetySession:
    def __init__(self, *, account: TradingAccount, order: TradingOrder) -> None:
        self.account = account
        self.order = order
        self.scalars_count = 0
        self.added: list[Any] = []
        self.commit_count = 0
        self.flush_count = 0

    async def scalars(self, _statement: object) -> ScalarRows:
        self.scalars_count += 1
        if self.scalars_count == 1:
            return ScalarRows([self.account] if self.account.status == "enabled" else [])
        if self.scalars_count == 2:
            return ScalarRows([self.order] if self.order.status in {"planned", "ready"} else [])
        raise AssertionError("Unexpected safety query.")

    async def scalar(self, _statement: object) -> int:
        return int(
            self.order.status
            in {"submitting", "uncertain", "submitted", "accepted", "partially_filled"}
        )

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1


class StartSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commit_count = 0
        self.flush_count = 0

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1


class BlockingLiveClient:
    def __init__(self, *, started: asyncio.Event, release: asyncio.Event) -> None:
        self.started = started
        self.release = release

    def validate_account_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> None:
        assert account.status == "enabled"
        assert intent.reduce_only is False

    async def submit_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> LiveOrderResult:
        assert account.key == intent.account_key
        self.started.set()
        await self.release.wait()
        return LiveOrderResult(
            status="accepted",
            client_order_id=intent.client_order_id,
            exchange_order_id="exchange-order-1",
            filled_size=None,
            average_fill_price=None,
            raw_response={"status": "ok"},
        )


def live_account() -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="testnet",
        wallet_address="0x" + "2" * 40,
        lifecycle_version=0,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_intent() -> TradeIntent:
    return TradeIntent(
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
        size=Decimal("0.5"),
        notional_usd=Decimal("50"),
        margin_usd=Decimal("10"),
        leverage=Decimal("5"),
        limit_price=Decimal("100"),
        source_price=Decimal("100"),
        observed_price=Decimal("100"),
        price_drift_bps=Decimal("0"),
        price_source="test",
        allocation_pct=Decimal("0.2"),
        allocation_usd=Decimal("100"),
        source_perp_equity_usd=Decimal("1000"),
        source_exposure_pct=Decimal("0.05"),
        created_at=datetime.now(UTC),
    )


def live_order(intent: TradeIntent) -> TradingOrder:
    return TradingOrder(
        id=uuid4(),
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
        reduce_only=False,
        order_type="ioc",
        status="ready",
        requested_size=intent.size,
        requested_notional_usd=intent.notional_usd,
        margin_usd=intent.margin_usd,
        leverage=intent.leverage,
        limit_price=intent.limit_price,
        filled_size=Decimal("0"),
        filled_notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def make_testnet_settings() -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_state", ["paused", "killed"])
async def test_global_safety_transition_persists_while_dispatched_entry_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    entry_state: str,
) -> None:
    gate = asyncio.Lock()
    submit_started = asyncio.Event()
    release_submit = asyncio.Event()
    account = live_account()
    intent = live_intent()
    order = live_order(intent)
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=order.client_order_id,
        status="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
    )
    control = LiveEntrySafetyControl(
        id=1,
        entry_state="enabled",
        revision=1,
        changed_by="operator",
        changed_at=datetime.now(UTC),
    )
    submit_session = SubmitSession()
    safety_session = SafetySession(account=account, order=order)

    @asynccontextmanager
    async def fake_job_lock(
        _session: object,
        *,
        key: str,
        ttl_seconds: int,
    ) -> AsyncIterator[None]:
        assert ttl_seconds > 0
        if key == trading_safety_service.LIVE_ENTRY_GATE_LOCK_KEY:
            await gate.acquire()
            try:
                yield
            finally:
                gate.release()
            return
        yield

    async def fake_try_acquire(
        _session: object,
        *,
        key: str,
        owner: str,
        ttl_seconds: int,
    ) -> bool:
        assert key == trading_safety_service.LIVE_ENTRY_GATE_LOCK_KEY
        assert owner
        assert ttl_seconds > 0
        if gate.locked():
            return False
        await gate.acquire()
        return True

    async def fake_release(
        _session: object,
        *,
        key: str,
        owner: str,
    ) -> None:
        assert key == trading_safety_service.LIVE_ENTRY_GATE_LOCK_KEY
        assert owner
        gate.release()

    async def fake_release_safely(*_args: object, **_kwargs: object) -> None:
        if gate.locked():
            gate.release()

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_load_order(*_args: object, **_kwargs: object) -> TradingOrder:
        return order

    async def fake_entries_enabled(*_args: object, **_kwargs: object) -> LiveEntrySafetyControl:
        assert control.entry_state == "enabled"
        return control

    async def fake_guardrails(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_prepare(*_args: object, **_kwargs: object):
        return order, dispatch, False

    async def fake_load_control(*_args: object, **_kwargs: object) -> LiveEntrySafetyControl:
        return control

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(trading_safety_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(live_trading_service, "try_acquire_job_lock", fake_try_acquire)
    monkeypatch.setattr(live_trading_service, "release_job_lock", fake_release)
    monkeypatch.setattr(
        live_trading_service,
        "release_job_lock_safely",
        fake_release_safely,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_order_by_client_order_id",
        fake_load_order,
    )
    monkeypatch.setattr(live_trading_service, "ensure_live_entries_enabled", fake_entries_enabled)
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_entry_state_guardrails",
        fake_guardrails,
    )
    monkeypatch.setattr(live_trading_service, "prepare_live_order_dispatch", fake_prepare)
    monkeypatch.setattr(
        trading_safety_service,
        "load_live_entry_safety_control",
        fake_load_control,
    )

    submit_task = asyncio.create_task(
        submit_live_trade_intent(
            submit_session,  # type: ignore[arg-type]
            account=account,
            intent=intent,
            settings=make_testnet_settings(),
            client=BlockingLiveClient(started=submit_started, release=release_submit),  # type: ignore[arg-type]
        )
    )
    await asyncio.wait_for(submit_started.wait(), timeout=1)

    assert gate.locked() is False
    assert order.status == "submitting"
    assert dispatch.status == "dispatching"

    updated_control = await asyncio.wait_for(
        set_live_entry_safety_state(  # type: ignore[arg-type]
            safety_session,
            entry_state=entry_state,
            reason="Operator emergency action",
            actor="admin",
        ),
        timeout=1,
    )
    await safety_session.commit()

    assert updated_control.entry_state == entry_state
    assert account.status == "exit_only"
    assert order.status == "submitting"
    assert dispatch.status == "dispatching"
    assert safety_session.commit_count == 1
    audit_log = next(value for value in safety_session.added if isinstance(value, AuditLog))
    risk_event = next(value for value in safety_session.added if isinstance(value, RiskEvent))
    assert audit_log.payload["canceledOrders"] == 0
    assert audit_log.payload["inFlightEntries"] == 1
    assert risk_event.payload["canceledOrders"] == 0
    assert risk_event.payload["inFlightEntries"] == 1

    release_submit.set()
    result = await asyncio.wait_for(submit_task, timeout=1)

    assert isinstance(result, LiveOrderLifecycleResult)
    assert result.submitted is True
    assert order.status == "accepted"
    assert dispatch.status == "completed"
    assert account.status == "exit_only"


@pytest.mark.asyncio
async def test_start_reconciliation_network_work_does_not_hold_global_entry_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Lock()
    reconciliation_started = asyncio.Event()
    release_reconciliation = asyncio.Event()
    account = live_account()
    account.status = "disabled"
    session = StartSession()

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def fake_entry_gate(_session: object) -> AsyncIterator[None]:
        await gate.acquire()
        try:
            yield
        finally:
            gate.release()

    async def fake_entries_enabled(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_reconciliation(*_args: object, **_kwargs: object) -> LiveReconciliationResult:
        reconciliation_started.set()
        await release_reconciliation.wait()
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            status="complete",
        )

    async def fake_incomplete_close(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(
        live_trading_service,
        "validate_live_trading_configuration",
        lambda *_: None,
    )
    monkeypatch.setattr(live_trading_service, "ensure_live_entries_enabled", fake_entries_enabled)
    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(live_trading_service, "live_entry_gate", fake_entry_gate)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "run_live_trading_account_reconciliation",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_account_can_start",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        live_trading_service,
        "live_account_has_incomplete_close_operation",
        fake_incomplete_close,
    )

    start_task = asyncio.create_task(
        start_live_trading_account(
            session,  # type: ignore[arg-type]
            account_key=account.key,
            settings=make_testnet_settings(),
            info_client=object(),  # type: ignore[arg-type]
            actor="admin",
        )
    )
    await asyncio.wait_for(reconciliation_started.wait(), timeout=1)

    await asyncio.wait_for(gate.acquire(), timeout=1)
    gate.release()

    release_reconciliation.set()
    started_account = await asyncio.wait_for(start_task, timeout=1)

    assert started_account.status == "enabled"
    assert started_account.status_reason == "start_after_complete_reconciliation"


@pytest.mark.asyncio
async def test_start_does_not_overwrite_newer_lifecycle_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    session = StartSession()

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    @asynccontextmanager
    async def lifecycle_changing_entry_gate(
        _session: object,
    ) -> AsyncIterator[None]:
        account.lifecycle_version += 1
        account.status_reason = "newer_stop_command"
        yield

    async def fake_entries_enabled(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_reconciliation(*_args: object, **_kwargs: object) -> LiveReconciliationResult:
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            status="complete",
        )

    monkeypatch.setattr(
        live_trading_service,
        "validate_live_trading_configuration",
        lambda *_: None,
    )
    monkeypatch.setattr(live_trading_service, "ensure_live_entries_enabled", fake_entries_enabled)
    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "live_entry_gate",
        lifecycle_changing_entry_gate,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "run_live_trading_account_reconciliation",
        fake_reconciliation,
    )

    with pytest.raises(LiveTradingServiceError, match="lifecycle changed"):
        await start_live_trading_account(
            session,  # type: ignore[arg-type]
            account_key=account.key,
            settings=make_testnet_settings(),
            info_client=object(),  # type: ignore[arg-type]
            actor="admin",
        )

    assert account.status == "disabled"
    assert account.lifecycle_version == 1
    assert account.status_reason == "newer_stop_command"


@pytest.mark.asyncio
async def test_stop_on_disabled_account_advances_lifecycle_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    session = StartSession()

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_cancel(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(live_trading_service, "cancel_unsent_live_entries", fake_cancel)

    stopped = await stop_live_trading_account(
        session,  # type: ignore[arg-type]
        account_key=account.key,
        actor="admin",
    )

    assert stopped.status == "disabled"
    assert stopped.lifecycle_version == 1
    assert stopped.status_reason == "stopped_by_dashboard"


@pytest.mark.asyncio
async def test_archive_reconciles_before_trusting_flat_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    account.last_reconciled_at = datetime.now(UTC)
    account.config_payload = {
        "lastReconciliation": {
            "status": "complete",
            "unifiedAvailableUsd": "100",
        }
    }
    session = StartSession()
    reconciliation_called = False

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_reconciliation(*_args: object, **_kwargs: object) -> LiveReconciliationResult:
        nonlocal reconciliation_called
        reconciliation_called = True
        account.status = "exit_only"
        account.lifecycle_version += 1
        account.status_reason = "external_exposure_detected"
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            status="complete",
            open_positions=1,
        )

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "run_live_trading_account_reconciliation",
        fake_reconciliation,
    )

    with pytest.raises(LiveAccountDeleteError, match="Disable the live account"):
        await delete_live_trading_account(
            session,  # type: ignore[arg-type]
            account_key=account.key,
            settings=make_testnet_settings(),
            info_client=object(),  # type: ignore[arg-type]
            actor="admin",
        )

    assert reconciliation_called is True
    assert account.archived_at is None
    assert account.status == "exit_only"
