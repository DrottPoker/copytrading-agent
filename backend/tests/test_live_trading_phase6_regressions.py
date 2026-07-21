from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import (
    AuditLog,
    LiveCopyFillState,
    LiveCopySourceState,
    RiskEvent,
    TradingAccount,
    TradingCloseAllOperation,
    TradingOrder,
    TradingPosition,
    TradingReconciliationRun,
    WalletFill,
)
from app.services import live_trading_service
from app.services.live_trading_service import (
    LiveCloseAllResult,
    LiveCopyEntryLifecycleDeferred,
    LiveFillFetchResult,
    LiveFundingFetchResult,
    LiveOrderLifecycleResult,
    LiveOrderReconciliationResult,
    LiveOrderSubmitError,
    LivePerpSnapshot,
    LivePositionReconciliationResult,
    LiveReconciliationResult,
    close_live_account_position,
    complete_live_close_all_operation,
    live_copy_capacity_order_reservation,
    mark_live_reconciliation_run_failed,
    resume_live_close_all_operations,
    run_live_trading_account_reconciliation,
    validate_live_copy_entry_capacity_gate,
    validate_live_copy_entry_lifecycle_gate,
    validate_live_entry_risk_guardrails,
)
from app.services.trading_core import TradeIntent
from app.services.trading_safety_service import apply_live_account_status


class FakeScalarRows:
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
        self.scalar_statements: list[Any] = []
        self.scalars_statements: list[Any] = []
        self.added: list[Any] = []
        self.commit_count = 0
        self.rollback_count = 0
        self.flush_count = 0
        self.refresh_count = 0

    async def scalar(self, statement: Any) -> Any:
        self.scalar_statements.append(statement)
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def get(self, _model: Any, _key: Any) -> Any:
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def scalars(self, statement: Any) -> FakeScalarRows:
        self.scalars_statements.append(statement)
        rows = self.scalars_values.pop(0) if self.scalars_values else []
        return FakeScalarRows(rows)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    async def flush(self) -> None:
        self.flush_count += 1

    async def refresh(self, _value: Any, **_kwargs: Any) -> None:
        self.refresh_count += 1


def live_account(*, status: str, lifecycle_version: int = 0) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="testnet",
        wallet_address="0x" + "2" * 40,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        lifecycle_version=lifecycle_version,
        status_changed_at=datetime.now(UTC),
        status_reason=f"initial_{status}",
    )


def live_position(*, coin: str) -> TradingPosition:
    return TradingPosition(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet=live_trading_service.LIVE_EXCHANGE_SOURCE,
        coin=coin,
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("100"),
        notional_usd=Decimal("100"),
        leverage=Decimal("2"),
        margin_usd=Decimal("50"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime.now(UTC),
    )


def live_order(*, reduce_only: bool = True, status: str = "filled") -> TradingOrder:
    return TradingOrder(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin="BTC",
        action="close" if reduce_only else "open",
        side="long",
        is_buy=not reduce_only,
        reduce_only=reduce_only,
        order_type="ioc",
        status=status,
        requested_size=Decimal("1"),
        requested_notional_usd=Decimal("100"),
        filled_size=Decimal("1") if status == "filled" else Decimal("0"),
        filled_notional_usd=Decimal("100") if status == "filled" else Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_intent(*, reduce_only: bool = False, action: str | None = None) -> TradeIntent:
    return TradeIntent(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin="BTC",
        action=action or ("close" if reduce_only else "open"),
        side="long",
        is_buy=not reduce_only,
        reduce_only=reduce_only,
        size=Decimal("1"),
        notional_usd=Decimal("100"),
        margin_usd=Decimal("50"),
        leverage=Decimal("2"),
        limit_price=Decimal("100"),
        source_price=Decimal("100"),
        observed_price=Decimal("100"),
        price_drift_bps=Decimal("0"),
        price_source="phase6_regression_test",
        allocation_pct=Decimal("0.2"),
        allocation_usd=Decimal("100"),
        source_perp_equity_usd=Decimal("1000"),
        source_exposure_pct=Decimal("0.1"),
        created_at=datetime.now(UTC),
    )


def live_copy_source_state(
    *,
    activated_at: datetime,
    entry_eligible: bool = True,
) -> LiveCopySourceState:
    return LiveCopySourceState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        status="active",
        entry_eligible=entry_eligible,
        activated_at=activated_at,
        baseline_completed_at=activated_at,
        baseline_fill_ids=[],
        preexisting_markets={},
    )


def planned_live_copy_state(
    *,
    activated_at: datetime,
    action: str = "open",
) -> LiveCopyFillState:
    return LiveCopyFillState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        expected_part_count=1,
        plan_version=1,
        coin="BTC",
        action=action,
        side="long",
        source_timestamp_ms=int(activated_at.timestamp() * 1000),
        source_order_direction_rank=1,
        source_order_position=Decimal("0"),
        source_order_fill_id_numeric=None,
        origin="realtime",
        outcome="retryable",
        reason="processing",
        attempt_count=1,
        first_seen_at=activated_at,
        first_observed_at=activated_at,
        fill_complete=False,
    )


def source_wallet_fill(*, activated_at: datetime, start_position: str = "0") -> WalletFill:
    return WalletFill(
        wallet_address="0xsource",
        external_fill_id="fill-1",
        coin="BTC",
        side="buy",
        price=Decimal("100"),
        size=Decimal("1"),
        timestamp_ms=int(activated_at.timestamp() * 1000),
        received_at=activated_at,
        raw_json={"dir": "Open Long", "startPosition": start_position},
    )


def attributed_live_position(
    *, source_wallet: str, size: Decimal = Decimal("1")
) -> TradingPosition:
    return TradingPosition(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet=source_wallet,
        coin="BTC",
        side="long",
        size=size,
        entry_price=Decimal("100"),
        notional_usd=Decimal("100"),
        leverage=Decimal("2"),
        margin_usd=Decimal("50"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime.now(UTC),
        source_lifecycle_timestamp_ms=1,
        source_lifecycle_direction_rank=1,
        source_lifecycle_position=Decimal("0"),
        source_lifecycle_fill_id="opening",
    )


@pytest.mark.asyncio
async def test_final_gate_allows_planned_open_to_dispatch_as_owned_add() -> None:
    activated_at = datetime.now(UTC)
    source_state = live_copy_source_state(activated_at=activated_at)
    plan_state = planned_live_copy_state(activated_at=activated_at, action="open")
    source_position = attributed_live_position(source_wallet="0xsource")
    exchange_position = attributed_live_position(
        source_wallet=live_trading_service.LIVE_EXCHANGE_SOURCE
    )
    session = FakeSession(
        scalar_values=[
            source_state,
            source_wallet_fill(activated_at=activated_at),
            None,
            None,
            None,
            None,
            None,
        ],
        scalars_values=[[plan_state], [source_position, exchange_position], []],
    )
    intent = live_intent(action="add")

    await validate_live_copy_entry_lifecycle_gate(
        session,  # type: ignore[arg-type]
        account=live_account(status="enabled"),
        intent=intent,
    )

    assert plan_state.outcome == "retryable"
    assert plan_state.reason == "processing"


@pytest.mark.asyncio
async def test_final_gate_allows_retained_same_market_add() -> None:
    activated_at = datetime.now(UTC)
    source_state = live_copy_source_state(
        activated_at=activated_at,
        entry_eligible=False,
    )
    plan_state = planned_live_copy_state(activated_at=activated_at, action="open")
    plan_state.source_order_position = Decimal("1")
    source_position = attributed_live_position(source_wallet="0xsource")
    exchange_position = attributed_live_position(
        source_wallet=live_trading_service.LIVE_EXCHANGE_SOURCE
    )
    session = FakeSession(
        scalar_values=[
            source_state,
            source_wallet_fill(activated_at=activated_at, start_position="1"),
            None,
            None,
            None,
            None,
            None,
        ],
        scalars_values=[
            [plan_state],
            [source_position, exchange_position],
            [source_position],
        ],
    )

    await validate_live_copy_entry_lifecycle_gate(
        session,  # type: ignore[arg-type]
        account=live_account(status="enabled"),
        intent=live_intent(action="add"),
    )

    assert plan_state.outcome == "retryable"
    assert plan_state.reason == "processing"
    assert session.commit_count == 0


@pytest.mark.asyncio
async def test_final_gate_blocks_retained_flip_open_without_order() -> None:
    activated_at = datetime.now(UTC)
    source_state = live_copy_source_state(
        activated_at=activated_at,
        entry_eligible=False,
    )
    plan_state = planned_live_copy_state(activated_at=activated_at, action="flip_open")
    plan_state.side = "short"
    plan_state.source_order_direction_rank = 0
    plan_state.source_order_position = Decimal("-1")
    source_position = attributed_live_position(source_wallet="0xsource")
    exchange_position = attributed_live_position(
        source_wallet=live_trading_service.LIVE_EXCHANGE_SOURCE
    )
    wallet_fill = source_wallet_fill(activated_at=activated_at, start_position="1")
    wallet_fill.side = "sell"
    wallet_fill.raw_json = {"dir": "Long > Short", "startPosition": "1"}
    session = FakeSession(
        scalar_values=[
            source_state,
            wallet_fill,
            None,
            None,
            None,
            None,
            None,
        ],
        scalars_values=[[plan_state], [source_position, exchange_position]],
    )

    with pytest.raises(
        LiveCopyEntryLifecycleDeferred,
        match="live_source_lifecycle_reclassified",
    ):
        await validate_live_copy_entry_lifecycle_gate(
            session,  # type: ignore[arg-type]
            account=live_account(status="enabled"),
            intent=replace(
                live_intent(action="flip_open"),
                side="short",
                is_buy=False,
            ),
        )

    assert plan_state.outcome == "baseline_ignored"
    assert plan_state.reason == "live_retained_source_new_market"
    assert plan_state.trading_order_id is None
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_final_gate_blocks_materialized_canonical_predecessor() -> None:
    activated_at = datetime.now(UTC)
    source_state = live_copy_source_state(activated_at=activated_at)
    plan_state = planned_live_copy_state(activated_at=activated_at)
    predecessor = planned_live_copy_state(activated_at=activated_at)
    predecessor.source_fill_id = "earlier-fill"
    predecessor.source_timestamp_ms -= 1
    session = FakeSession(
        scalar_values=[
            source_state,
            source_wallet_fill(activated_at=activated_at),
            None,
            predecessor,
        ],
        scalars_values=[[plan_state]],
    )

    with pytest.raises(LiveCopyEntryLifecycleDeferred, match="live_prior_source_fill_pending"):
        await validate_live_copy_entry_lifecycle_gate(
            session,  # type: ignore[arg-type]
            account=live_account(status="enabled"),
            intent=live_intent(),
        )

    assert session.added == []


@pytest.mark.asyncio
async def test_final_gate_terminalizes_unattributed_same_side_exchange_exposure() -> None:
    activated_at = datetime.now(UTC)
    source_state = live_copy_source_state(activated_at=activated_at)
    plan_state = planned_live_copy_state(activated_at=activated_at)
    exchange_position = attributed_live_position(
        source_wallet=live_trading_service.LIVE_EXCHANGE_SOURCE
    )
    session = FakeSession(
        scalar_values=[
            source_state,
            source_wallet_fill(activated_at=activated_at),
            None,
            None,
            None,
            None,
            None,
        ],
        scalars_values=[[plan_state], [exchange_position], []],
    )

    with pytest.raises(LiveCopyEntryLifecycleDeferred, match="live_exchange_position_conflict"):
        await validate_live_copy_entry_lifecycle_gate(
            session,  # type: ignore[arg-type]
            account=live_account(status="enabled"),
            intent=live_intent(),
        )

    assert plan_state.outcome == "terminal_skip"
    assert plan_state.reason == "live_exchange_position_conflict"
    assert plan_state.trading_order_id is None


def test_capacity_reserves_partial_and_unmaterialized_filled_order_remainders() -> None:
    partial = live_order(reduce_only=False, status="accepted")
    partial.requested_size = Decimal("1")
    partial.margin_usd = Decimal("100")
    filled_without_rows = live_order(reduce_only=False, status="filled")
    filled_without_rows.requested_size = Decimal("1")
    filled_without_rows.filled_size = Decimal("0")
    filled_without_rows.margin_usd = Decimal("100")

    assert live_copy_capacity_order_reservation(
        partial,
        materialized_size=Decimal("0.4"),
    ) == Decimal("60")
    assert live_copy_capacity_order_reservation(
        filled_without_rows,
        materialized_size=Decimal("0"),
    ) == Decimal("100")


@pytest.mark.asyncio
async def test_capacity_gate_rejects_stale_second_entry_and_ignores_dust_positions() -> None:
    account = live_account(status="enabled")
    account.equity_usd = Decimal("1000")
    account.config_payload = {"lastReconciliation": {"unifiedEquityUsd": "1000"}}
    held_position = attributed_live_position(source_wallet="0xsource", size=Decimal("1"))
    held_position.coin = "ETH"
    held_position.margin_usd = Decimal("100")
    dust_position = attributed_live_position(
        source_wallet="0xsource",
        size=live_trading_service.POSITION_EPSILON,
    )
    dust_position.margin_usd = Decimal("1000")
    intent = replace(live_intent(), margin_usd=Decimal("150"))
    session = FakeSession(scalars_values=[[held_position, dust_position], []])

    with pytest.raises(LiveCopyEntryLifecycleDeferred, match="live_allocation_capacity_changed"):
        await validate_live_copy_entry_capacity_gate(
            session,  # type: ignore[arg-type]
            account=account,
            intent=intent,
            settings=Settings(),
        )


@pytest.mark.asyncio
async def test_capacity_gate_excludes_current_client_order_and_bypasses_reductions() -> None:
    account = live_account(status="enabled")
    account.equity_usd = Decimal("1000")
    account.config_payload = {"lastReconciliation": {"unifiedEquityUsd": "1000"}}
    current_order = live_order(reduce_only=False, status="accepted")
    current_order.margin_usd = Decimal("1000")
    session = FakeSession(scalars_values=[[], [current_order], []])

    await validate_live_copy_entry_capacity_gate(
        session,  # type: ignore[arg-type]
        account=account,
        intent=live_intent(),
        settings=Settings(),
    )

    sql = str(
        session.scalars_statements[1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "client_order_id != '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'" in sql
    assert "sum(trading_fills.size)" in sql

    reduced_session = FakeSession()
    await validate_live_copy_entry_capacity_gate(
        reduced_session,  # type: ignore[arg-type]
        account=account,
        intent=live_intent(reduce_only=True),
        settings=Settings(),
    )
    assert reduced_session.scalars_statements == []


@pytest.mark.asyncio
async def test_manual_close_from_disabled_keeps_exit_only_with_another_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account(status="disabled", lifecycle_version=4)
    target_position = live_position(coin="BTC")
    remaining_position = live_position(coin="ETH")
    exchange_positions = {
        target_position.id: target_position,
        remaining_position.id: remaining_position,
    }
    close_order = live_order()
    session = FakeSession()
    reconciliation_count = 0

    async def fake_load_position(*_args: Any, position_id: Any, **_kwargs: Any) -> TradingPosition:
        return exchange_positions[position_id]

    async def fake_get_position(*_args: Any, position_id: Any, **_kwargs: Any) -> Any:
        return exchange_positions.get(position_id)

    async def fake_load_account(*_args: Any, **_kwargs: Any) -> TradingAccount:
        return account

    async def fake_stop_account(*_args: Any, **kwargs: Any) -> TradingAccount:
        assert kwargs["force_exit_only"] is True
        apply_live_account_status(
            account,
            status="exit_only",
            reason="manual_close_detected_exposure",
        )
        return account

    async def fake_reconcile(*_args: Any, **_kwargs: Any) -> LiveReconciliationResult:
        nonlocal reconciliation_count
        reconciliation_count += 1
        if reconciliation_count == 2:
            exchange_positions.pop(target_position.id)
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            open_positions=len(exchange_positions),
            status="complete",
        )

    async def fake_load_mids(*_args: Any, **_kwargs: Any) -> dict[str, Decimal]:
        return {"BTC": Decimal("100")}

    async def fake_submit(*_args: Any, **_kwargs: Any) -> LiveOrderLifecycleResult:
        return LiveOrderLifecycleResult(
            order=close_order,
            exchange_result=None,
            submitted=True,
        )

    monkeypatch.setattr(live_trading_service, "load_live_position", fake_load_position)
    monkeypatch.setattr(live_trading_service, "get_live_position", fake_get_position)
    monkeypatch.setattr(live_trading_service, "load_live_account", fake_load_account)
    monkeypatch.setattr(live_trading_service, "stop_live_trading_account", fake_stop_account)
    monkeypatch.setattr(live_trading_service, "reconcile_live_trading_account", fake_reconcile)
    monkeypatch.setattr(live_trading_service, "load_live_close_mids", fake_load_mids)
    monkeypatch.setattr(
        live_trading_service,
        "build_live_close_position_intent",
        lambda **_kwargs: live_intent(reduce_only=True),
    )
    monkeypatch.setattr(live_trading_service, "submit_live_trade_intent", fake_submit)

    result = await close_live_account_position(  # type: ignore[arg-type]
        session,
        position_id=target_position.id,
        settings=Settings(),
        info_client=object(),  # type: ignore[arg-type]
    )

    assert result.submitted is True
    assert reconciliation_count == 2
    assert target_position.id not in exchange_positions
    assert remaining_position.id in exchange_positions
    assert account.status == "exit_only"
    assert account.lifecycle_version == 5
    assert account.status_reason == "manual_close_detected_exposure"


@pytest.mark.parametrize(
    (
        "initial_status",
        "expected_status",
        "expected_lifecycle_version",
        "expected_reason",
        "expected_event_type",
        "previous_reconciliation_status",
    ),
    [
        (
            "disabled",
            "exit_only",
            8,
            "external_exposure_detected_during_partial_reconciliation",
            "live_external_exposure_detected",
            None,
        ),
        (
            "enabled",
            "enabled",
            7,
            "initial_enabled",
            "live_reconciliation_partial",
            None,
        ),
        (
            "enabled",
            "enabled",
            7,
            "initial_enabled",
            None,
            "partial",
        ),
    ],
)
@pytest.mark.asyncio
async def test_partial_reconciliation_preserves_enabled_lifecycle_and_protects_external_exposure(
    monkeypatch: pytest.MonkeyPatch,
    initial_status: str,
    expected_status: str,
    expected_lifecycle_version: int,
    expected_reason: str,
    expected_event_type: str | None,
    previous_reconciliation_status: str | None,
) -> None:
    account = live_account(status=initial_status, lifecycle_version=7)
    if previous_reconciliation_status is not None:
        account.config_payload = {
            "lastReconciliationAttempt": {"status": previous_reconciliation_status}
        }
    session = FakeSession()
    order_result = LiveOrderReconciliationResult()
    fill_result = LiveFillFetchResult(
        fills=(),
        complete=False,
        pages=1,
        error="Fill history is incomplete.",
    )
    funding_result = LiveFundingFetchResult(payments=(), complete=True, pages=1)
    perp_snapshot = LivePerpSnapshot(states=(), requested_dexes=())
    position_result = LivePositionReconciliationResult(
        open_positions=1,
        removed_positions=0,
        complete=True,
    )

    async def return_order_result(*_args: Any, **_kwargs: Any) -> LiveOrderReconciliationResult:
        return order_result

    async def return_zero(*_args: Any, **_kwargs: Any) -> int:
        return 0

    async def return_none(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def return_fill_result(*_args: Any, **_kwargs: Any) -> LiveFillFetchResult:
        return fill_result

    async def return_funding_result(*_args: Any, **_kwargs: Any) -> LiveFundingFetchResult:
        return funding_result

    async def return_perp_snapshot(*_args: Any, **_kwargs: Any) -> LivePerpSnapshot:
        return perp_snapshot

    async def return_empty_state(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def return_position_result(*_args: Any, **_kwargs: Any) -> Any:
        return position_result

    async def return_empty_ids(*_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    monkeypatch.setattr(
        live_trading_service,
        "reconcile_live_order_statuses",
        return_order_result,
    )
    monkeypatch.setattr(
        live_trading_service,
        "live_fill_reconciliation_start_time_ms",
        return_zero,
    )
    monkeypatch.setattr(
        live_trading_service,
        "live_funding_reconciliation_start_time_ms",
        return_zero,
    )
    monkeypatch.setattr(live_trading_service, "fetch_live_fills_by_time", return_fill_result)
    monkeypatch.setattr(
        live_trading_service,
        "fetch_live_funding_by_time",
        return_funding_result,
    )
    monkeypatch.setattr(live_trading_service, "reconcile_live_fills", return_zero)
    monkeypatch.setattr(
        live_trading_service,
        "reconcile_live_funding_payments",
        return_zero,
    )
    monkeypatch.setattr(
        live_trading_service,
        "update_live_orders_from_reconciled_fills",
        return_zero,
    )
    monkeypatch.setattr(
        live_trading_service,
        "recompute_live_account_fill_totals",
        return_none,
    )
    monkeypatch.setattr(live_trading_service, "fetch_live_perp_states", return_perp_snapshot)
    monkeypatch.setattr(live_trading_service, "fetch_live_spot_state", return_empty_state)
    monkeypatch.setattr(live_trading_service, "fetch_live_user_abstraction", return_empty_state)
    monkeypatch.setattr(live_trading_service, "reconcile_live_positions", return_position_result)
    monkeypatch.setattr(live_trading_service, "still_unresolved_live_order_ids", return_empty_ids)
    monkeypatch.setattr(
        live_trading_service,
        "reconciliation_component_errors",
        lambda **_kwargs: {"fills": "Fill history is incomplete."},
    )
    monkeypatch.setattr(
        live_trading_service,
        "reconciliation_components_payload",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        live_trading_service,
        "update_live_account_from_state",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(live_trading_service, "prune_live_reconciliation_runs", return_none)

    result = await run_live_trading_account_reconciliation(  # type: ignore[arg-type]
        session,
        account=account,
        settings=Settings(hyperliquid_network="testnet"),
        info_client=object(),  # type: ignore[arg-type]
    )

    assert result.status == "partial"
    assert result.open_positions == 1
    assert account.status == expected_status
    assert account.lifecycle_version == expected_lifecycle_version
    assert account.status_reason == expected_reason
    risk_events = [value for value in session.added if isinstance(value, RiskEvent)]
    audit_logs = [value for value in session.added if isinstance(value, AuditLog)]
    if expected_event_type is None:
        assert risk_events == []
        assert audit_logs == []
    elif initial_status == "disabled":
        risk_event = risk_events[0]
        audit_log = audit_logs[0]
        assert risk_event.event_type == expected_event_type
        assert risk_event.payload["reconciliationStatus"] == "partial"
        assert audit_log.action == "live_account.external_exposure_detected"
    else:
        risk_event = risk_events[0]
        audit_log = audit_logs[0]
        assert risk_event.event_type == expected_event_type
        assert risk_event.severity == "warning"
        assert risk_event.payload["accountStatus"] == "enabled"
        assert audit_log.action == "live_account.reconciliation_partial"


@pytest.mark.asyncio
async def test_close_all_flat_completion_stays_exit_only_with_nonterminal_order() -> None:
    account = live_account(status="exit_only", lifecycle_version=3)
    operation = TradingCloseAllOperation(
        id=uuid4(),
        account_key=account.key,
        status="running",
        requested_at=datetime.now(UTC),
    )
    session = FakeSession(scalar_values=[1])

    result = await complete_live_close_all_operation(  # type: ignore[arg-type]
        session,
        account=account,
        operation=operation,
        submitted_orders=1,
    )

    assert result.operation_status == "partially_completed"
    assert result.status == "exit_only"
    assert result.failed_orders == 1
    assert account.status == "exit_only"
    assert account.lifecycle_version == 3
    assert operation.status == "partially_completed"
    assert operation.completed_at is None
    assert operation.last_error == "1 non-terminal live orders remain."
    assert not any(
        isinstance(value, AuditLog) and value.action == "live_account.close_all_completed"
        for value in session.added
    )


@pytest.mark.asyncio
async def test_failed_close_all_operation_is_selected_for_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account(status="exit_only")
    operation_id = uuid4()
    session = FakeSession(scalars_values=[[account.key]])
    recovery_calls: list[str] = []

    async def fake_load_account(*_args: Any, **_kwargs: Any) -> TradingAccount:
        return account

    async def fake_close_all(*_args: Any, **kwargs: Any) -> LiveCloseAllResult:
        recovery_calls.append(kwargs["account"].key)
        return LiveCloseAllResult(
            account_key=account.key,
            operation_id=operation_id,
            operation_status="completed",
            submitted_orders=1,
            failed_orders=0,
            status="disabled",
        )

    monkeypatch.setattr(live_trading_service, "load_live_account", fake_load_account)
    monkeypatch.setattr(live_trading_service, "close_all_live_account_positions", fake_close_all)

    results = await resume_live_close_all_operations(  # type: ignore[arg-type]
        session,
        settings=Settings(),
    )

    compiled = str(
        session.scalars_statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "'failed'" in compiled
    assert recovery_calls == [account.key]
    assert [result.operation_id for result in results] == [operation_id]


@pytest.mark.parametrize(
    ("reconciliation_status", "expected_message"),
    [
        (
            "partial",
            "complete exchange reconciliation snapshot",
        ),
        (
            "stale",
            "fresh exchange reconciliation snapshot",
        ),
    ],
)
@pytest.mark.asyncio
async def test_reconciliation_guard_blocks_entry_without_changing_enabled_lifecycle(
    reconciliation_status: str,
    expected_message: str,
) -> None:
    account = live_account(status="enabled", lifecycle_version=9)
    account.config_payload = {
        "lastReconciliationAttempt": {
            "status": "partial" if reconciliation_status == "partial" else "complete"
        }
    }
    account.last_reconciled_at = (
        datetime.now(UTC)
        if reconciliation_status == "partial"
        else datetime.now(UTC) - timedelta(seconds=91)
    )
    session = FakeSession()

    with pytest.raises(
        LiveOrderSubmitError,
        match=expected_message,
    ):
        await validate_live_entry_risk_guardrails(  # type: ignore[arg-type]
            session,
            account=account,
            settings=Settings(),
        )

    assert account.status == "enabled"
    assert account.lifecycle_version == 9
    assert account.status_reason == "initial_enabled"
    assert session.added == []
    assert session.commit_count == 1


@pytest.mark.parametrize(
    ("previous_reconciliation_status", "expects_event"),
    [(None, True), ("failed", False)],
)
@pytest.mark.asyncio
async def test_failed_reconciliation_blocks_entries_without_changing_enabled_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    previous_reconciliation_status: str | None,
    expects_event: bool,
) -> None:
    account = live_account(status="enabled", lifecycle_version=11)
    if previous_reconciliation_status is not None:
        account.config_payload = {
            "lastReconciliationAttempt": {"status": previous_reconciliation_status}
        }
    run = TradingReconciliationRun(
        account_key=account.key,
        status="running",
        components={},
        started_at=datetime.now(UTC),
    )
    session = FakeSession(scalar_values=[run, account])

    async def return_none(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(live_trading_service, "prune_live_reconciliation_runs", return_none)

    await mark_live_reconciliation_run_failed(  # type: ignore[arg-type]
        session,
        run_id=run.id,
        account_key=account.key,
        attempted_at=datetime.now(UTC),
        error="Temporary Hyperliquid timeout.",
    )

    assert run.status == "failed"
    assert account.status == "enabled"
    assert account.lifecycle_version == 11
    assert account.status_reason == "initial_enabled"
    risk_events = [value for value in session.added if isinstance(value, RiskEvent)]
    audit_logs = [value for value in session.added if isinstance(value, AuditLog)]
    if expects_event:
        risk_event = risk_events[0]
        audit_log = audit_logs[0]
        assert risk_event.event_type == "live_reconciliation_failed"
        assert risk_event.severity == "warning"
        assert risk_event.payload["accountStatus"] == "enabled"
        assert audit_log.action == "live_account.reconciliation_failed"
    else:
        assert risk_events == []
        assert audit_logs == []
