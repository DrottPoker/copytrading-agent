from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import (
    AuditLog,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
)
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
)
from app.services import live_trading_service
from app.services.live_trading_service import (
    LiveOrderSubmitError,
    LiveTradingServiceError,
    ensure_live_entry_intent_is_fresh,
    live_account_current_unrealized_pnl,
    live_account_weekly_loss_pct,
    live_account_weekly_net_pnl,
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


class CapturingScalarSession:
    def __init__(self, value: Decimal) -> None:
        self.value = value
        self.statement: Any | None = None

    async def scalar(self, statement: Any) -> Decimal:
        self.statement = statement
        return self.value


class PositionRows:
    def __init__(self, positions: list[TradingPosition]) -> None:
        self.positions = positions

    def all(self) -> list[TradingPosition]:
        return self.positions


class PositionSession:
    def __init__(self, positions: list[TradingPosition]) -> None:
        self.positions = positions

    async def scalars(self, _statement: object) -> PositionRows:
        return PositionRows(self.positions)


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


@pytest.mark.asyncio
async def test_weekly_pnl_window_starts_at_monday_midnight_utc() -> None:
    session = CapturingScalarSession(Decimal("-42.50"))
    now = datetime(2026, 7, 8, 15, 30, tzinfo=UTC)

    value = await live_account_weekly_net_pnl(  # type: ignore[arg-type]
        session,
        account_key="live_test",
        now=now,
    )

    assert value == Decimal("-42.50")
    assert session.statement is not None
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "trading_fills.filled_at >= '2026-07-06 00:00:00+00:00'" in compiled


@pytest.mark.asyncio
async def test_weekly_pnl_normalizes_non_utc_now_before_finding_boundary() -> None:
    session = CapturingScalarSession(Decimal("0"))
    stockholm_summer = timezone(timedelta(hours=2))
    now = datetime(2026, 7, 6, 1, 0, tzinfo=stockholm_summer)

    await live_account_weekly_net_pnl(  # type: ignore[arg-type]
        session,
        account_key="live_test",
        now=now,
    )

    assert session.statement is not None
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "trading_fills.filled_at >= '2026-06-29 00:00:00+00:00'" in compiled


@pytest.mark.asyncio
async def test_current_unrealized_pnl_prefers_exchange_positions_without_double_counting() -> None:
    exchange_position = TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="__exchange__",
        coin="BTC",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("100"),
        notional_usd=Decimal("100"),
        leverage=Decimal("2"),
        margin_usd=Decimal("50"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload={"position": {"unrealizedPnl": "-60"}},
        opened_at=datetime.now(UTC),
    )
    attributed_duplicate = TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        coin="BTC",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("100"),
        notional_usd=Decimal("100"),
        leverage=Decimal("2"),
        margin_usd=Decimal("50"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload={"position": {"unrealizedPnl": "-60"}},
        opened_at=datetime.now(UTC),
    )

    value = await live_account_current_unrealized_pnl(  # type: ignore[arg-type]
        PositionSession([exchange_position, attributed_duplicate]),
        account_key="live_test",
    )

    assert value == Decimal("-60")


def test_weekly_loss_pct_uses_reconstructed_week_start_equity() -> None:
    account = live_account()
    account.equity_usd = Decimal("40")

    assert live_account_weekly_loss_pct(
        account,
        weekly_net_pnl=Decimal("-60"),
    ) == Decimal("0.6")


@pytest.mark.asyncio
async def test_weekly_loss_percentage_guard_includes_current_unrealized_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.equity_usd = Decimal("40")
    account.last_reconciled_at = datetime.now(UTC)
    account.config_payload = {"lastReconciliation": {"status": "complete"}}
    settings = Settings(
        live_trading_max_weekly_loss_pct=Decimal("0.5"),
        live_trading_max_orders_per_minute=0,
    )
    observed: dict[str, object] = {}

    class CommitSession:
        async def commit(self) -> None:
            return None

    async def fake_unrealized(*_args: object, **_kwargs: object) -> Decimal:
        return Decimal("-60")

    async def fake_realized(*_args: object, **_kwargs: object) -> Decimal:
        return Decimal("0")

    async def fake_trip(*_args: object, **kwargs: object) -> None:
        observed.update(kwargs)

    monkeypatch.setattr(
        live_trading_service,
        "live_account_current_unrealized_pnl",
        fake_unrealized,
    )
    monkeypatch.setattr(live_trading_service, "live_account_weekly_net_pnl", fake_realized)
    monkeypatch.setattr(live_trading_service, "trip_live_account_risk", fake_trip)

    with pytest.raises(LiveOrderSubmitError, match="weekly loss percentage guard"):
        await validate_live_entry_risk_guardrails(
            CommitSession(),  # type: ignore[arg-type]
            account=account,
            settings=settings,
        )

    assert observed["rule"] == "max_weekly_loss"
    assert observed["observed"] == "0.6"
    assert observed["limit"] == "0.5"


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
