from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.models import (
    AuditLog,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
)
from app.services import live_trading_service
from app.services.copy_risk_rules import AccountLossState, performance_loss_pct
from app.services.live_trading_service import (
    LiveOrderSubmitError,
    LiveTradingServiceError,
    ensure_live_entry_intent_is_fresh,
    live_reconciliation_is_fresh,
    validate_live_account_can_start,
    validate_live_entry_risk_guardrails,
)
from app.services.trading_core import TradeIntent


class NoIoSession:
    async def scalar(self, _statement: object) -> Any:
        raise AssertionError("Fresh intent validation must not query the database.")

    def add(self, _value: Any) -> None:
        raise AssertionError("Fresh intent validation must not add database rows.")

    async def commit(self) -> None:
        raise AssertionError("Fresh intent validation must not commit.")


class ExpiredIntentSession:
    def __init__(self, dispatch: TradingOrderDispatch) -> None:
        self.dispatch = dispatch
        self.added: list[Any] = []
        self.commit_count = 0

    async def scalar(self, _statement: object) -> TradingOrderDispatch:
        return self.dispatch

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1


def live_account(*, status: str = "enabled") -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="testnet",
        wallet_address="0x" + "2" * 40,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_intent(
    *,
    created_at: datetime,
    reduce_only: bool = False,
) -> TradeIntent:
    return TradeIntent(
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
        created_at=created_at,
    )


def live_order(
    *,
    status: str = "ready",
    created_at: datetime | None = None,
) -> TradingOrder:
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
        created_at=created_at or datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("age_seconds", "expected"),
    [(0, True), (90, True), (91, False), (-1, False)],
)
def test_reconciliation_freshness_has_inclusive_bounded_window(
    age_seconds: int,
    expected: bool,
) -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    settings = Settings(live_trading_reconciliation_max_snapshot_age_seconds=90)
    account = live_account(status="disabled")
    account.last_reconciled_at = now - timedelta(seconds=age_seconds)

    assert live_reconciliation_is_fresh(account, settings=settings, now=now) is expected


def test_stale_reconciliation_blocks_account_start() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    settings = Settings(
        live_trading_capital_mode="unified",
        live_trading_reconciliation_max_snapshot_age_seconds=90,
    )
    account = live_account(status="disabled")
    account.last_reconciled_at = now - timedelta(seconds=91)
    account.config_payload = {
        "lastReconciliation": {
            "status": "complete",
            "unifiedAvailableUsd": "100",
            "userAbstraction": "unifiedAccount",
        }
    }

    with pytest.raises(LiveTradingServiceError, match="fresh exchange reconciliation"):
        validate_live_account_can_start(account, settings=settings, now=now)


@pytest.mark.parametrize(
    ("current", "reference", "expected"),
    [
        (Decimal("0.9"), Decimal("1"), Decimal("0.1")),
        (Decimal("1.2"), Decimal("1"), Decimal("0")),
        (Decimal("0.75"), Decimal("1.5"), Decimal("0.5")),
        (Decimal("1"), Decimal("0"), Decimal("0")),
    ],
)
def test_performance_loss_pct_is_relative_to_the_reference_index(
    current: Decimal,
    reference: Decimal,
    expected: Decimal,
) -> None:
    assert performance_loss_pct(current, reference) == expected


def guarded_account() -> TradingAccount:
    account = live_account()
    account.equity_usd = Decimal("1000")
    account.last_reconciled_at = datetime.now(UTC)
    account.config_payload = {"lastReconciliation": {"status": "complete"}}
    return account


def loss_guard_settings() -> Settings:
    return Settings(
        live_trading_max_daily_loss_pct=Decimal("0.10"),
        live_trading_max_weekly_loss_pct=Decimal("0.20"),
        live_trading_max_drawdown_pct=Decimal("0.25"),
        live_trading_max_orders_per_minute=0,
    )


class CommitSession:
    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("loss_state", "rule", "message", "observed", "limit"),
    [
        (
            AccountLossState(Decimal("0.12"), Decimal("0.12"), Decimal("0.12")),
            "max_daily_loss",
            "rolling 24 hour loss",
            "0.12",
            "0.10",
        ),
        (
            AccountLossState(Decimal("0.05"), Decimal("0.21"), Decimal("0.21")),
            "max_weekly_loss",
            "rolling 7 day loss",
            "0.21",
            "0.20",
        ),
        (
            AccountLossState(Decimal("0"), Decimal("0"), Decimal("0.30")),
            "max_drawdown",
            "drawdown from peak",
            "0.30",
            "0.25",
        ),
    ],
)
async def test_account_loss_guards_trip_the_account(
    monkeypatch: pytest.MonkeyPatch,
    loss_state: AccountLossState,
    rule: str,
    message: str,
    observed: str,
    limit: str,
) -> None:
    tripped: dict[str, object] = {}

    async def fake_loss_state(*_args: object, **_kwargs: object) -> AccountLossState:
        return loss_state

    async def fake_trip(*_args: object, **kwargs: object) -> None:
        tripped.update(kwargs)

    monkeypatch.setattr(live_trading_service, "live_account_loss_state", fake_loss_state)
    monkeypatch.setattr(live_trading_service, "trip_live_account_risk", fake_trip)

    with pytest.raises(LiveOrderSubmitError, match=message):
        await validate_live_entry_risk_guardrails(
            CommitSession(),  # type: ignore[arg-type]
            account=guarded_account(),
            settings=loss_guard_settings(),
        )

    assert tripped["rule"] == rule
    assert tripped["observed"] == observed
    assert tripped["limit"] == limit


@pytest.mark.asyncio
async def test_account_loss_guards_allow_entries_below_the_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_loss_state(*_args: object, **_kwargs: object) -> AccountLossState:
        return AccountLossState(Decimal("0.09"), Decimal("0.19"), Decimal("0.24"))

    async def fail_trip(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Losses below every limit must not trip the account.")

    monkeypatch.setattr(live_trading_service, "live_account_loss_state", fake_loss_state)
    monkeypatch.setattr(live_trading_service, "trip_live_account_risk", fail_trip)

    await validate_live_entry_risk_guardrails(
        CommitSession(),  # type: ignore[arg-type]
        account=guarded_account(),
        settings=loss_guard_settings(),
    )


@pytest.mark.asyncio
async def test_account_loss_guards_skip_accounts_without_performance_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_history(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(live_trading_service, "live_account_loss_state", no_history)

    await validate_live_entry_risk_guardrails(
        CommitSession(),  # type: ignore[arg-type]
        account=guarded_account(),
        settings=loss_guard_settings(),
    )


@pytest.mark.asyncio
async def test_fresh_live_entry_intent_needs_no_database_mutation() -> None:
    settings = Settings(live_trading_entry_intent_ttl_seconds=30)

    await ensure_live_entry_intent_is_fresh(  # type: ignore[arg-type]
        NoIoSession(),
        intent=live_intent(created_at=datetime.now(UTC) - timedelta(seconds=5)),
        order=None,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_expired_new_live_entry_intent_is_rejected_without_database_mutation() -> None:
    settings = Settings(live_trading_entry_intent_ttl_seconds=30)

    with pytest.raises(
        LiveOrderSubmitError,
        match="Live entry intent expired before exchange submission",
    ):
        await ensure_live_entry_intent_is_fresh(  # type: ignore[arg-type]
            NoIoSession(),
            intent=live_intent(created_at=datetime.now(UTC) - timedelta(seconds=31)),
            order=None,
            settings=settings,
        )


@pytest.mark.asyncio
async def test_expired_retryable_live_entry_cancels_order_and_dispatch() -> None:
    settings = Settings(live_trading_entry_intent_ttl_seconds=30)
    order = live_order(
        status="failed",
        created_at=datetime.now(UTC) - timedelta(seconds=31),
    )
    order.error = "skip:live_execution_busy"
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=order.client_order_id,
        status="pending",
        attempt_count=0,
        available_at=datetime.now(UTC),
    )
    session = ExpiredIntentSession(dispatch)

    with pytest.raises(
        LiveOrderSubmitError,
        match="Live entry intent expired before exchange submission",
    ):
        await ensure_live_entry_intent_is_fresh(  # type: ignore[arg-type]
            session,
            intent=live_intent(created_at=datetime.now(UTC)),
            order=order,
            settings=settings,
        )

    assert order.status == "canceled"
    assert order.error == "Live entry intent expired before exchange submission."
    assert dispatch.status == "canceled"
    assert dispatch.completed_at is not None
    assert dispatch.last_error == order.error
    audit_log = next(value for value in session.added if isinstance(value, AuditLog))
    assert audit_log.actor == "execution_engine"
    assert audit_log.action == "live_entry.expired"
    assert audit_log.payload == {
        "accountKey": "live_test",
        "orderId": str(order.id),
    }
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_expired_rejected_live_entry_reports_the_prior_exchange_rejection() -> None:
    settings = Settings(live_trading_entry_intent_ttl_seconds=30)
    order = live_order(
        status="rejected",
        created_at=datetime.now(UTC) - timedelta(seconds=31),
    )
    order.error = "Cannot increase position when open interest is at cap."
    order.submitted_at = datetime.now(UTC) - timedelta(seconds=20)
    order.raw_payload = {
        "exchangeReject": {
            "code": "exchange_open_interest_cap",
            "transient": True,
            "retryExhausted": False,
        }
    }
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=order.client_order_id,
        status="pending",
        attempt_count=2,
        available_at=datetime.now(UTC),
    )
    session = ExpiredIntentSession(dispatch)

    expected_error = (
        "Live entry retry window expired after Hyperliquid rejected an earlier exchange attempt."
    )
    with pytest.raises(LiveOrderSubmitError, match=expected_error):
        await ensure_live_entry_intent_is_fresh(  # type: ignore[arg-type]
            session,
            intent=live_intent(created_at=datetime.now(UTC)),
            order=order,
            settings=settings,
        )

    assert order.status == "canceled"
    assert order.error == expected_error
    assert dispatch.status == "canceled"
    assert dispatch.last_error == expected_error


@pytest.mark.asyncio
async def test_fresh_retryable_live_entry_uses_durable_order_created_at() -> None:
    settings = Settings(live_trading_entry_intent_ttl_seconds=30)
    order = live_order(
        status="failed",
        created_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    order.error = "skip:live_execution_busy"

    await ensure_live_entry_intent_is_fresh(  # type: ignore[arg-type]
        NoIoSession(),
        intent=live_intent(created_at=datetime.now(UTC) - timedelta(seconds=31)),
        order=order,
        settings=settings,
    )


def stopped_live_settings(*, allow_reduce_only: bool) -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    settings.hyperliquid_private_key = "0x" + "1" * 64
    settings.hyperliquid_wallet_address = "0x" + "2" * 40
    settings.live_trading_enabled = False
    settings.live_trading_reduce_only_when_stopped = allow_reduce_only
    return settings


def test_global_live_trading_flag_blocks_reduce_only_execution_when_false() -> None:
    settings = stopped_live_settings(allow_reduce_only=True)
    client = HyperliquidLiveTradingClient(settings=settings)
    account = live_account(status="disabled")

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="Live trading is disabled",
    ):
        client.validate_account_order(
            account=account,
            intent=live_intent(created_at=datetime.now(UTC), reduce_only=True),
        )


def test_reduce_only_exit_is_allowed_for_stopped_account_when_live_is_enabled() -> None:
    settings = stopped_live_settings(allow_reduce_only=True)
    settings.live_trading_enabled = True
    client = HyperliquidLiveTradingClient(settings=settings)
    account = live_account(status="disabled")

    client.validate_account_order(
        account=account,
        intent=live_intent(created_at=datetime.now(UTC), reduce_only=True),
    )
