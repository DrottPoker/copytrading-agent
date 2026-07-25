from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingCloseAllItem,
    TradingCloseAllOperation,
    TradingFill,
    TradingFundingPayment,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
)
from app.integrations.hyperliquid_live_client import LiveOrderResult
from app.services import live_trading_service
from app.services.live_execution_state import build_dispatch_client_order_id
from app.services.live_trading_service import (
    LivePerpState,
    apply_live_order_result,
    apply_order_status_response,
    attach_live_funding_to_closed_trades,
    build_testnet_live_trade_intent,
    cash_flow_adjusted_period_return,
    close_all_live_account_positions,
    create_live_trading_account,
    fetch_live_cash_flows_by_time,
    fetch_live_fills_by_time,
    fetch_live_funding_by_time,
    is_retryable_live_order_submit_failure,
    live_account_key_for_route,
    live_closed_trades_from_fills,
    live_exchange_position_opened_at,
    live_perp_equity_usd,
    live_position_current_notional,
    live_position_mark_price,
    live_position_unrealized_pnl,
    live_position_unrealized_pnl_pct,
    live_tradable_equity_usd,
    manual_live_close_recovery_status,
    map_exchange_order_status,
    parse_live_account_cash_flow,
    parse_live_fill,
    parse_live_funding_payment,
    parse_live_position,
    reset_live_order_for_retry,
    resolve_live_account_wallet_address,
    sync_live_source_positions_from_exchange_positions,
    update_live_account_from_state,
    validate_live_account_can_start,
    validate_live_trading_configuration,
)


def test_apply_order_status_response_maps_filled_order() -> None:
    order = live_order(status="accepted")

    changed = apply_order_status_response(
        order,
        {
            "status": "order",
            "order": {
                "order": {"oid": 123},
                "status": "filled",
                "statusTimestamp": 1_725_000_000_000,
            },
        },
    )

    assert changed is True
    assert order.status == "filled"
    assert order.exchange_order_id == "123"
    assert order.filled_at == datetime.fromtimestamp(1_725_000_000_000 / 1000, UTC)


@pytest.mark.asyncio
async def test_reconciliation_keeps_unrecognized_exchange_status_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = live_order(status="uncertain")
    order.id = uuid4()
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=build_dispatch_client_order_id(order.client_order_id, attempt_number=1),
        attempt_number=1,
        status="uncertain",
        attempt_count=1,
        available_at=datetime.now(UTC),
    )

    class Rows:
        def all(self) -> list[TradingOrder]:
            return [order]

    class Session:
        async def scalars(self, _statement: object) -> Rows:
            return Rows()

        async def flush(self) -> None:
            return None

    class Client:
        async def order_status(self, *, user: str, oid: int | str) -> dict[str, object]:
            assert user == "0xuser"
            assert oid == dispatch.client_order_id
            return {
                "status": "order",
                "order": {
                    "order": {"oid": 123},
                    "status": "futureStatus",
                },
            }

    async def fake_load_dispatch(*_args: object, **_kwargs: object) -> TradingOrderDispatch:
        return dispatch

    monkeypatch.setattr(
        live_trading_service,
        "load_live_order_dispatch",
        fake_load_dispatch,
    )

    result = await live_trading_service.reconcile_live_order_statuses(
        Session(),  # type: ignore[arg-type]
        account=TradingAccount(
            key="live_test",
            account_type="live",
            label="Live Test",
            status="exit_only",
            network="testnet",
        ),
        user_address="0xuser",
        client=Client(),  # type: ignore[arg-type]
    )

    assert result.unresolved_order_ids == (order.id,)
    assert result.errors == {
        order.client_order_id: "Exchange order status is missing or unrecognized."
    }
    assert order.status == "uncertain"
    assert dispatch.status == "uncertain"


@pytest.mark.parametrize(
    ("exchange_status", "local_status"),
    [
        ("triggered", "accepted"),
        ("marginCanceled", "canceled"),
        ("scheduledCancel", "canceled"),
        ("minTradeNtlRejected", "rejected"),
    ],
)
def test_map_exchange_order_status_covers_documented_terminal_variants(
    exchange_status: str,
    local_status: str,
) -> None:
    assert map_exchange_order_status(exchange_status) == local_status


def test_mainnet_account_start_uses_single_live_trading_flag() -> None:
    settings = Settings()
    settings.hyperliquid_network = "mainnet"
    settings.hyperliquid_private_key = "0x" + "1" * 64
    settings.hyperliquid_wallet_address = "0x" + "2" * 40

    with pytest.raises(
        live_trading_service.LiveTradingServiceError,
        match="Live trading is disabled",
    ):
        validate_live_trading_configuration(settings)

    settings.live_trading_enabled = True
    validate_live_trading_configuration(settings)


def test_apply_live_order_result_updates_submitted_wire_values() -> None:
    order = live_order(status="submitted")

    apply_live_order_result(
        order,
        LiveOrderResult(
            status="filled",
            client_order_id=order.client_order_id,
            exchange_order_id="123",
            filled_size=Decimal("0.16"),
            average_fill_price=Decimal("63.93"),
            raw_response={"status": "ok"},
            submitted_size=Decimal("0.16"),
            submitted_limit_price=Decimal("63.9300"),
            submitted_notional_usd=Decimal("10.228800"),
        ),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert order.requested_size == Decimal("0.16")
    assert order.limit_price == Decimal("63.9300")
    assert order.requested_notional_usd == Decimal("10.228800")
    assert order.margin_usd == Decimal("10.228800")
    assert order.filled_size == Decimal("0.16")
    assert order.filled_notional_usd == Decimal("10.2288")
    assert order.status == "filled"


def test_apply_live_order_result_classifies_retryable_ioc_rejection() -> None:
    order = live_order(status="submitting")
    dispatch = TradingOrderDispatch(
        id=uuid4(),
        order_id=uuid4(),
        account_key=order.account_key,
        client_order_id=build_dispatch_client_order_id(order.client_order_id, attempt_number=1),
        attempt_number=1,
        status="dispatching",
        attempt_count=1,
        available_at=datetime.now(UTC),
    )

    apply_live_order_result(
        order,
        LiveOrderResult(
            status="rejected",
            client_order_id=dispatch.client_order_id,
            exchange_order_id=None,
            filled_size=None,
            average_fill_price=None,
            raw_response={"status": "ok", "response": {"data": {}}},
            error="Order could not immediately match against resting liquidity.",
        ),
        dispatch=dispatch,
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert order.status == "rejected"
    assert order.raw_payload["exchangeReject"]["code"] == "exchange_ioc_no_match"
    assert is_retryable_live_order_submit_failure(order) is True
    assert dispatch.exchange_error_code == "exchange_ioc_no_match"
    assert dispatch.exchange_response == {"status": "ok", "response": {"data": {}}}


def test_dispatch_client_order_ids_are_unique_per_logical_attempt() -> None:
    logical_id = "0x" + "1" * 32
    first_attempt_id = build_dispatch_client_order_id(logical_id, attempt_number=1)
    second_attempt_id = build_dispatch_client_order_id(logical_id, attempt_number=2)

    assert first_attempt_id.startswith("0x")
    assert len(first_attempt_id) == 34
    assert first_attempt_id != second_attempt_id


@pytest.mark.asyncio
async def test_live_fill_reconciliation_start_time_uses_forced_lookback() -> None:
    class UnexpectedSessionRead:
        async def scalar(self, _statement: object) -> object:
            raise AssertionError("Forced lookback should not read latest fill state.")

    now = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

    start_time_ms = await live_trading_service.live_fill_reconciliation_start_time_ms(
        UnexpectedSessionRead(),
        account_key="live_test",
        settings=Settings(),
        now=now,
        lookback_minutes=4320,
    )

    assert start_time_ms == int((now - timedelta(minutes=4320)).timestamp() * 1000)


def test_retryable_live_order_submit_failure_matches_market_metadata_error() -> None:
    order = live_order(status="failed")
    order.error = "Live order market is not available for exchange submission: xyz:MU."
    order.submitted_at = datetime(2026, 1, 1, tzinfo=UTC)
    order.raw_payload = {
        "submitError": {
            "type": "HyperliquidLiveOrderRejectedError",
            "message": order.error,
        }
    }

    assert is_retryable_live_order_submit_failure(order) is True

    reset_live_order_for_retry(
        order,
        intent=build_testnet_live_trade_intent(
            account=TradingAccount(
                key="live_test",
                account_type="live",
                label="Live Test",
                status="enabled",
                network="testnet",
            ),
            coin="ETH",
            side="long",
            notional_usd=Decimal("100"),
            limit_price=Decimal("100"),
            leverage=Decimal("1"),
            reduce_only=False,
            source_fill_id="fill-1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )

    assert order.status == "planned"
    assert order.error is None
    assert order.submitted_at is None
    assert order.raw_payload["retry"]["reason"] == (
        "market_metadata_available_after_previous_submit_failure"
    )


def test_retryable_live_order_submit_failure_matches_below_min_close_skip() -> None:
    order = live_order(status="failed")
    order.action = "close"
    order.side = "short"
    order.is_buy = True
    order.reduce_only = True
    order.order_type = "skip"
    order.error = "skip:live_close_below_min_order_notional"

    assert is_retryable_live_order_submit_failure(order) is True

    reset_live_order_for_retry(
        order,
        intent=build_testnet_live_trade_intent(
            account=TradingAccount(
                key="live_test",
                account_type="live",
                label="Live Test",
                status="enabled",
                network="testnet",
            ),
            coin="BIO",
            side="short",
            notional_usd=Decimal("10"),
            limit_price=Decimal("0.031077"),
            leverage=Decimal("3"),
            reduce_only=True,
            source_fill_id="fill-1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )

    assert order.status == "planned"
    assert order.error is None
    assert order.order_type == "ioc"
    assert order.coin == "BIO"
    assert order.action == "close"
    assert order.side == "short"
    assert order.reduce_only is True
    assert order.requested_notional_usd == Decimal("10")


def test_retryable_live_order_submit_failure_matches_transient_copy_skip() -> None:
    order = live_order(status="failed")
    order.order_type = "skip"
    order.error = "skip:live_execution_busy"

    assert is_retryable_live_order_submit_failure(order) is True

    reset_live_order_for_retry(
        order,
        intent=build_testnet_live_trade_intent(
            account=TradingAccount(
                key="live_test",
                account_type="live",
                label="Live Test",
                status="enabled",
                network="testnet",
            ),
            coin="HYPE",
            side="long",
            notional_usd=Decimal("10"),
            limit_price=Decimal("100"),
            leverage=Decimal("3"),
            reduce_only=False,
            source_fill_id="fill-1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )

    assert order.status == "planned"
    assert order.error is None
    assert order.raw_payload["retry"]["reason"] == "live_execution_busy"


def test_exchange_position_open_time_uses_earliest_matching_source_position() -> None:
    reconciled_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
    source_opened_at = reconciled_at - timedelta(hours=4)
    existing = live_position(raw_payload={})
    existing.source_wallet = "exchange"
    existing.opened_at = reconciled_at - timedelta(hours=1)
    source = live_position(raw_payload={})
    source.source_wallet = "0xsource"
    source.opened_at = source_opened_at

    assert (
        live_exchange_position_opened_at(
            coin="HYPE",
            side="long",
            existing_position=existing,
            source_positions=[source],
            reconciled_at=reconciled_at,
        )
        == source_opened_at
    )


def test_exchange_position_open_time_resets_when_side_changes() -> None:
    reconciled_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
    existing = live_position(raw_payload={})
    existing.source_wallet = "exchange"
    existing.side = "short"
    existing.opened_at = reconciled_at - timedelta(days=2)

    assert (
        live_exchange_position_opened_at(
            coin="HYPE",
            side="long",
            existing_position=existing,
            source_positions=[],
            reconciled_at=reconciled_at,
        )
        == reconciled_at
    )


def test_retryable_live_order_submit_failure_ignores_exchange_rejection() -> None:
    order = live_order(status="failed")
    order.error = "Insufficient margin."
    order.raw_payload = {
        "submitError": {
            "type": "HyperliquidLiveOrderRejectedError",
            "message": order.error,
        }
    }

    assert is_retryable_live_order_submit_failure(order) is False


@pytest.mark.asyncio
async def test_live_account_recent_order_count_ignores_unsubmitted_skip_rows() -> None:
    class CaptureSession:
        statement = None

        async def scalar(self, statement):
            self.statement = statement
            return 0

    session = CaptureSession()

    count = await live_trading_service.live_account_recent_order_count(
        session,
        account_key="live_test",
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert count == 0
    assert session.statement is not None
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "trading_orders.submitted_at IS NOT NULL" in sql


def test_live_closed_trades_from_fills_groups_complete_trade() -> None:
    opened_at = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fills = [
        live_fill(
            action="open",
            filled_at=opened_at,
            fee_usd=Decimal("0.01"),
            notional_usd=Decimal("60"),
            price=Decimal("60"),
            realized_pnl_usd=Decimal("0"),
            sequence_index=0,
            size=Decimal("1"),
        ),
        live_fill(
            action="add",
            filled_at=datetime(2026, 1, 1, 10, 5, tzinfo=UTC),
            fee_usd=Decimal("0.01"),
            notional_usd=Decimal("30"),
            price=Decimal("60"),
            realized_pnl_usd=Decimal("0"),
            sequence_index=1,
            size=Decimal("0.5"),
        ),
        live_fill(
            action="reduce",
            filled_at=datetime(2026, 1, 1, 10, 10, tzinfo=UTC),
            fee_usd=Decimal("0.01"),
            notional_usd=Decimal("28"),
            price=Decimal("70"),
            realized_pnl_usd=Decimal("1"),
            sequence_index=2,
            size=Decimal("0.4"),
        ),
        live_fill(
            action="close",
            filled_at=datetime(2026, 1, 1, 10, 15, tzinfo=UTC),
            fee_usd=Decimal("0.02"),
            notional_usd=Decimal("77"),
            price=Decimal("70"),
            realized_pnl_usd=Decimal("2"),
            sequence_index=3,
            size=Decimal("1.1"),
        ),
    ]

    trades = live_closed_trades_from_fills(fills)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.size == Decimal("1.5")
    assert trade.entry_price == Decimal("60")
    assert trade.exit_price == Decimal("70")
    assert trade.entry_notional_usd == Decimal("90")
    assert trade.exit_notional_usd == Decimal("105")
    assert trade.realized_pnl_usd == Decimal("3")
    assert trade.fee_usd == Decimal("0.05")
    assert trade.net_pnl_usd == Decimal("2.95")
    assert trade.open_fill_count == 2
    assert trade.close_fill_count == 2
    assert trade.opened_at == opened_at
    assert trade.closed_at == datetime(2026, 1, 1, 10, 15, tzinfo=UTC)


def test_live_closed_trades_from_fills_includes_exchange_trade() -> None:
    opened_at = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    fills = [
        live_fill(
            action="open",
            filled_at=opened_at,
            fee_usd=Decimal("0.004"),
            notional_usd=Decimal("31.103"),
            price=Decimal("1628.7"),
            realized_pnl_usd=Decimal("0"),
            sequence_index=0,
            size=Decimal("0.0191"),
            source_wallet="__exchange__",
            coin="ETH",
            side="short",
        ),
        live_fill(
            action="close",
            filled_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            fee_usd=Decimal("0.014022"),
            notional_usd=Decimal("31.16146"),
            price=Decimal("1581.8"),
            realized_pnl_usd=Decimal("0.925112"),
            sequence_index=1,
            size=Decimal("0.0197"),
            source_wallet="__exchange__",
            coin="ETH",
            side="short",
        ),
    ]

    trades = live_closed_trades_from_fills(fills)

    assert len(trades) == 1
    assert trades[0].coin == "ETH"
    assert trades[0].source_wallet == "__exchange__"


def test_live_closed_trades_from_fills_includes_exchange_close_only_fill() -> None:
    closed_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    fills = [
        live_fill(
            action="close",
            filled_at=closed_at,
            fee_usd=Decimal("0.014022"),
            notional_usd=Decimal("31.16146"),
            price=Decimal("1581.8"),
            realized_pnl_usd=Decimal("0.925112"),
            sequence_index=1,
            size=Decimal("0.0197"),
            source_wallet="__exchange__",
            coin="ETH",
            side="short",
        ),
    ]

    trades = live_closed_trades_from_fills(fills)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.source_wallet == "__exchange__"
    assert trade.source_label == "Exchange position"
    assert trade.coin == "ETH"
    assert trade.side == "short"
    assert trade.entry_price is not None
    assert trade.entry_price == Decimal("1628.76")
    assert trade.exit_price == Decimal("1581.8")
    assert trade.realized_pnl_usd == Decimal("0.925112")
    assert trade.net_pnl_usd == Decimal("0.911090")
    assert trade.open_fill_count == 0
    assert trade.close_fill_count == 1
    assert trade.opened_at == closed_at
    assert trade.closed_at == closed_at


def test_live_closed_trades_from_fills_skips_incomplete_close_only_fill() -> None:
    fills = [
        live_fill(
            action="close",
            filled_at=datetime(2026, 1, 1, 10, 15, tzinfo=UTC),
            fee_usd=Decimal("0.02"),
            notional_usd=Decimal("77"),
            price=Decimal("70"),
            realized_pnl_usd=Decimal("2"),
            sequence_index=3,
            size=Decimal("1.1"),
        )
    ]

    assert live_closed_trades_from_fills(fills) == []


def test_parse_live_fill_uses_tid_for_id_and_infers_side() -> None:
    parsed = parse_live_fill(
        {
            "closedPnl": "0.0",
            "coin": "AVAX",
            "dir": "Open Long",
            "hash": "0xabc",
            "oid": 90542681,
            "px": "18.435",
            "side": "B",
            "sz": "93.53",
            "time": 1681222254710,
            "fee": "0.01",
            "tid": 118906512037719,
        },
        account_key="live_test",
    )

    assert parsed is not None
    assert parsed["exchange_fill_id"] == "hl:live_test:tid:118906512037719"
    assert parsed["exchange_order_id"] == "90542681"
    assert parsed["side"] == "long"
    assert parsed["action"] == "open"
    assert parsed["notional_usd"] == Decimal("1724.22555")


def test_parse_live_funding_payment_preserves_hyperliquid_signed_usdc() -> None:
    parsed = parse_live_funding_payment(
        {
            "delta": {
                "coin": "BTC",
                "fundingRate": "0.0000125",
                "szi": "-0.25",
                "type": "funding",
                "usdc": "0.04",
            },
            "hash": "0xabc",
            "time": 1_767_225_600_000,
        },
        account_key="live_test",
    )

    assert parsed is not None
    assert parsed["coin"] == "BTC"
    assert parsed["amount_usd"] == Decimal("0.04")
    assert parsed["position_size"] == Decimal("-0.25")
    assert parsed["funding_rate"] == Decimal("0.0000125")
    assert parsed["occurred_at"] == datetime(2026, 1, 1, tzinfo=UTC)


def test_attach_live_funding_updates_closed_trade_net_once() -> None:
    fills = [
        live_fill(
            action="open",
            filled_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            fee_usd=Decimal("0.01"),
            notional_usd=Decimal("100"),
            price=Decimal("100"),
            realized_pnl_usd=Decimal("0"),
            sequence_index=0,
            size=Decimal("1"),
        ),
        live_fill(
            action="close",
            filled_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            fee_usd=Decimal("0.01"),
            notional_usd=Decimal("105"),
            price=Decimal("105"),
            realized_pnl_usd=Decimal("5"),
            sequence_index=1,
            size=Decimal("1"),
        ),
    ]
    trades = live_closed_trades_from_fills(fills)
    payment = TradingFundingPayment(
        account_key="live_test",
        account_type="live",
        exchange_event_id="funding-1",
        coin="HYPE",
        amount_usd=Decimal("-0.25"),
        occurred_at=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        raw_payload={},
    )

    funded_trades = attach_live_funding_to_closed_trades(trades, [payment])

    assert funded_trades[0].funding_usd == Decimal("-0.25")
    assert funded_trades[0].net_pnl_usd == Decimal("4.73")


def test_parse_live_position_reads_signed_position_size() -> None:
    snapshot = parse_live_position(
        {
            "position": {
                "coin": "BTC",
                "szi": "-0.25",
                "entryPx": "65000",
                "positionValue": "16250",
                "leverage": {"type": "isolated", "value": "5"},
                "marginUsed": "3250",
            }
        }
    )

    assert snapshot is not None
    assert snapshot.coin == "BTC"
    assert snapshot.side == "short"
    assert snapshot.size == Decimal("0.25")
    assert snapshot.leverage == Decimal("5")
    assert snapshot.margin_mode == "isolated"
    assert snapshot.margin_usd == Decimal("3250")


@pytest.mark.asyncio
async def test_margin_setting_sync_unchanged_settings_skip_execution_lock_and_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
    )
    exchange_position = live_position(raw_payload={})
    exchange_position.leverage = Decimal("1")
    exchange_position.margin_mode = "isolated"
    source_position = live_position(raw_payload={})
    source_position.source_wallet = "0xsource"
    source_position.margin_mode = "cross"

    class ScalarResult:
        def all(self):
            return [exchange_position, source_position]

    class FakeSession:
        scalar_calls = 0

        async def scalars(self, _query):
            self.scalar_calls += 1
            return ScalarResult()

        async def commit(self):
            events.append("commit")

        def add(self, _value):
            events.append("audit")

    class FakeClient:
        async def update_margin_setting(self, **_kwargs):
            events.append("exchange")
            return {"status": "ok"}

    @asynccontextmanager
    async def fake_job_lock(*_args, **_kwargs):
        events.append("job_lock")
        yield

    async def unexpected_account_load(*_args, **_kwargs):
        raise AssertionError("The execution lock should not be acquired.")

    async def fake_load_live_account(*_args, **_kwargs):
        events.append("account_load")
        return account

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account",
        fake_load_live_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        unexpected_account_load,
    )
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_account_identity",
        lambda *_args, **_kwargs: events.append("account_validated"),
    )

    session = FakeSession()
    changed = await live_trading_service.sync_live_position_margin_setting(
        session,
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
        leverage=Decimal("1"),
        margin_mode="isolated",
        settings=Settings(),
        client=FakeClient(),
    )

    assert changed is False
    assert session.scalar_calls == 2
    assert events == ["account_load", "account_validated", "commit"]
    assert source_position.leverage == Decimal("1")
    assert source_position.margin_mode == "isolated"
    assert source_position.raw_payload is not None
    assert source_position.raw_payload["marginSettingSync"]["exchangeResponse"] is None


@pytest.mark.asyncio
async def test_margin_setting_sync_missing_preflight_position_commits_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
    )
    exchange_position = live_position(raw_payload={})
    exchange_position.leverage = Decimal("1")
    exchange_position.margin_mode = "isolated"

    class ScalarResult:
        def all(self):
            return [exchange_position]

    class FakeSession:
        async def scalars(self, _query):
            return ScalarResult()

        async def commit(self):
            events.append("commit")

    async def fake_load_live_account(*_args, **_kwargs):
        return account

    def unexpected_job_lock(*_args, **_kwargs):
        raise AssertionError("Missing positions must not acquire the execution lock.")

    monkeypatch.setattr(live_trading_service, "job_lock", unexpected_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account",
        fake_load_live_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_account_identity",
        lambda *_args, **_kwargs: None,
    )

    changed = await live_trading_service.sync_live_position_margin_setting(
        FakeSession(),
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
        leverage=Decimal("1"),
        margin_mode="isolated",
        settings=Settings(),
    )

    assert changed is False
    assert events == ["commit"]


@pytest.mark.asyncio
async def test_margin_setting_sync_changed_settings_acquire_execution_lock_and_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
    )
    exchange_position = live_position(raw_payload={})
    exchange_position.margin_mode = "cross"
    source_position = live_position(raw_payload={})
    source_position.source_wallet = "0xsource"
    source_position.margin_mode = "cross"

    class ScalarResult:
        def all(self):
            return [exchange_position, source_position]

    class FakeSession:
        async def scalars(self, _query):
            return ScalarResult()

        async def commit(self):
            events.append("commit")

        def add(self, _value):
            events.append("audit")

    class FakeClient:
        async def update_margin_setting(self, **_kwargs):
            events.append("exchange")
            return {"status": "ok"}

    @asynccontextmanager
    async def fake_job_lock(*_args, **_kwargs):
        events.append("job_lock")
        yield

    async def fake_load_live_account_for_update(*_args, **_kwargs):
        return account

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account",
        fake_load_live_account_for_update,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_live_account_for_update,
    )
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_account_identity",
        lambda *_args, **_kwargs: None,
    )

    changed = await live_trading_service.sync_live_position_margin_setting(
        FakeSession(),
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
        leverage=Decimal("1"),
        margin_mode="isolated",
        settings=Settings(),
        client=FakeClient(),
    )

    assert changed is True
    assert events.count("job_lock") == 1
    assert events.count("exchange") == 1
    assert events.index("commit") < events.index("exchange")
    assert exchange_position.leverage == Decimal("1")
    assert exchange_position.margin_mode == "isolated"
    assert source_position.leverage == Decimal("1")
    assert source_position.margin_mode == "isolated"


@pytest.mark.asyncio
async def test_margin_setting_sync_revalidates_settings_after_execution_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
    )
    exchange_position = live_position(raw_payload={})
    exchange_position.margin_mode = "cross"
    source_position = live_position(raw_payload={})
    source_position.source_wallet = "0xsource"
    source_position.margin_mode = "cross"

    class ScalarResult:
        def all(self):
            return [exchange_position, source_position]

    class FakeSession:
        scalar_calls = 0

        async def scalars(self, _query):
            self.scalar_calls += 1
            return ScalarResult()

        async def commit(self):
            events.append("commit")

        def add(self, _value):
            events.append("audit")

    class FakeClient:
        async def update_margin_setting(self, **_kwargs):
            raise AssertionError("Settings must be revalidated before exchange update.")

    @asynccontextmanager
    async def fake_job_lock(*_args, **_kwargs):
        events.append("job_lock")
        exchange_position.leverage = Decimal("1")
        exchange_position.margin_mode = "isolated"
        yield

    async def fake_load_live_account_for_update(*_args, **_kwargs):
        return account

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account",
        fake_load_live_account_for_update,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_live_account_for_update,
    )
    monkeypatch.setattr(
        live_trading_service,
        "validate_live_account_identity",
        lambda *_args, **_kwargs: None,
    )

    session = FakeSession()
    changed = await live_trading_service.sync_live_position_margin_setting(
        session,
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
        leverage=Decimal("1"),
        margin_mode="isolated",
        settings=Settings(),
        client=FakeClient(),
    )

    assert changed is False
    assert session.scalar_calls == 3
    assert events == ["job_lock", "commit", "commit"]
    assert source_position.leverage == Decimal("1")
    assert source_position.margin_mode == "isolated"


def test_live_position_market_values_read_raw_payload() -> None:
    position = live_position(
        raw_payload={
            "position": {
                "positionValue": "97.75",
                "unrealizedPnl": "-0.90",
                "returnOnEquity": "-0.084",
            }
        }
    )

    assert live_position_current_notional(position) == Decimal("97.75")
    assert live_position_mark_price(position) == Decimal("61.86708860759493670886075949")
    assert live_position_unrealized_pnl(position) == Decimal("-0.90")
    assert live_position_unrealized_pnl_pct(position) == Decimal("-0.084")


def test_live_position_unrealized_pct_falls_back_to_margin() -> None:
    position = live_position(
        raw_payload={
            "position": {
                "positionValue": "97.75",
                "unrealizedPnl": "1.95",
            }
        }
    )

    assert live_position_unrealized_pnl_pct(position) == Decimal("0.195")


def test_sync_live_source_positions_from_exchange_mark_updates_unrealized() -> None:
    reconciled_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    exchange_position = live_position(
        raw_payload={
            "position": {
                "positionValue": "99.54",
                "unrealizedPnl": "4.80",
                "returnOnEquity": "0.48",
            }
        }
    )
    exchange_position.id = uuid4()
    source_position = TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        coin="HYPE",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("60"),
        notional_usd=Decimal("60"),
        leverage=Decimal("10"),
        margin_usd=Decimal("6"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload={"source": "live_fill"},
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    updated = sync_live_source_positions_from_exchange_positions(
        source_positions=[source_position],
        exchange_positions=[exchange_position],
        reconciled_at=reconciled_at,
    )

    assert updated.updated_positions == 1
    assert updated.stale_positions == []
    assert live_position_mark_price(source_position) == Decimal("63")
    assert live_position_current_notional(source_position) == Decimal("63")
    assert live_position_unrealized_pnl(source_position) == Decimal("3")
    assert live_position_unrealized_pnl_pct(source_position) == Decimal("0.5")
    assert source_position.last_reconciled_at == reconciled_at


def test_sync_live_source_positions_marks_missing_exchange_market_stale() -> None:
    source_position = TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        coin="ETH",
        side="short",
        size=Decimal("0.02"),
        entry_price=Decimal("1600"),
        notional_usd=Decimal("32"),
        leverage=Decimal("20"),
        margin_usd=Decimal("1.6"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload={"source": "live_fill"},
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = sync_live_source_positions_from_exchange_positions(
        source_positions=[source_position],
        exchange_positions=[],
        reconciled_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )

    assert result.updated_positions == 0
    assert result.stale_positions == [source_position]


def test_sync_live_source_positions_scales_source_exposure_to_exchange_size() -> None:
    reconciled_at = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    exchange_position = live_position(
        raw_payload={
            "position": {
                "positionValue": "31.5",
                "unrealizedPnl": "1.5",
                "returnOnEquity": "0.25",
            }
        }
    )
    exchange_position.id = uuid4()
    exchange_position.size = Decimal("0.5")
    source_position = TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        coin="HYPE",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("60"),
        notional_usd=Decimal("60"),
        leverage=Decimal("10"),
        margin_usd=Decimal("6"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload={"source": "live_fill"},
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = sync_live_source_positions_from_exchange_positions(
        source_positions=[source_position],
        exchange_positions=[exchange_position],
        reconciled_at=reconciled_at,
    )

    assert result.updated_positions == 1
    assert result.stale_positions == []
    assert source_position.size == Decimal("0.5")
    assert source_position.notional_usd == Decimal("30.0")
    assert source_position.margin_usd == Decimal("3.0")
    assert live_position_current_notional(source_position) == Decimal("31.5")
    assert live_position_unrealized_pnl(source_position) == Decimal("1.5")


def test_manual_live_close_recovery_marks_missing_position_filled() -> None:
    order = live_order(status="failed")

    status = manual_live_close_recovery_status(
        close_size_before_submit=Decimal("1"),
        current_position=None,
        order=order,
    )

    assert status == "filled"


def test_manual_live_close_recovery_marks_reduced_position_partial() -> None:
    order = live_order(status="failed")
    current_position = live_position(raw_payload={})
    current_position.size = Decimal("0.4")

    status = manual_live_close_recovery_status(
        close_size_before_submit=Decimal("1"),
        current_position=current_position,
        order=order,
    )

    assert status == "partially_filled"


def test_manual_live_close_recovery_ignores_unchanged_position() -> None:
    order = live_order(status="failed")
    current_position = live_position(raw_payload={})
    current_position.size = Decimal("1")

    status = manual_live_close_recovery_status(
        close_size_before_submit=Decimal("1"),
        current_position=current_position,
        order=order,
    )

    assert status is None


@pytest.mark.asyncio
async def test_close_all_keeps_account_exit_only_when_positions_remain(monkeypatch) -> None:
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )
    position = live_position(raw_payload={"position": {"positionValue": "100"}})
    order = live_order(status="submitted")
    operation = TradingCloseAllOperation(
        id=uuid4(),
        account_key=account.key,
        status="pending",
        requested_at=datetime.now(UTC),
    )
    item = TradingCloseAllItem(
        id=uuid4(),
        operation_id=operation.id,
        position_id=position.id,
        coin=position.coin,
        status="pending",
        attempt_count=0,
    )

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object):
        yield

    async def fake_get_operation(*_args: object, **_kwargs: object) -> object:
        return operation

    async def fake_get_item(*_args: object, **_kwargs: object) -> object:
        return item

    async def fake_refresh_items(*_args: object, **_kwargs: object) -> None:
        return None

    async def fake_incomplete_count(*_args: object, **_kwargs: object) -> int:
        return 1

    async def fake_reconcile_live_trading_account(*_args: object, **_kwargs: object) -> object:
        return live_trading_service.LiveReconciliationResult(
            account_key=account.key,
            user_address="0x" + "1" * 40,
            status="complete",
        )

    async def fake_load_live_exchange_positions(*_args: object, **_kwargs: object) -> list[object]:
        return [position]

    async def fake_load_live_close_mids(*_args: object, **_kwargs: object) -> dict[str, Decimal]:
        return {"HYPE": Decimal("100")}

    async def fake_submit_live_trade_intent(*_args: object, **_kwargs: object) -> object:
        return live_trading_service.LiveOrderLifecycleResult(
            order=order,
            exchange_result=None,
            submitted=True,
        )

    monkeypatch.setattr(
        live_trading_service,
        "reconcile_live_trading_account",
        fake_reconcile_live_trading_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "load_live_exchange_positions",
        fake_load_live_exchange_positions,
    )
    monkeypatch.setattr(live_trading_service, "load_live_close_mids", fake_load_live_close_mids)
    monkeypatch.setattr(
        live_trading_service,
        "submit_live_trade_intent",
        fake_submit_live_trade_intent,
    )
    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)

    async def fake_load_live_account_for_update(*_args: object, **_kwargs: object) -> object:
        return account

    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_live_account_for_update,
    )

    async def fake_cancel_unsent_live_entries(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(
        live_trading_service,
        "cancel_unsent_live_entries",
        fake_cancel_unsent_live_entries,
    )
    monkeypatch.setattr(
        live_trading_service,
        "get_or_create_live_close_all_operation",
        fake_get_operation,
    )
    monkeypatch.setattr(
        live_trading_service,
        "get_or_create_live_close_all_item",
        fake_get_item,
    )
    monkeypatch.setattr(
        live_trading_service,
        "refresh_live_close_all_items",
        fake_refresh_items,
    )
    monkeypatch.setattr(
        live_trading_service,
        "live_close_all_incomplete_item_count",
        fake_incomplete_count,
    )

    class FlushSession:
        async def commit(self) -> None:
            return None

        async def flush(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def get(self, model: object, row_id: object) -> object | None:
            if model is TradingCloseAllItem and row_id == item.id:
                return item
            if model is TradingCloseAllOperation and row_id == operation.id:
                return operation
            return None

    result = await close_all_live_account_positions(
        FlushSession(),
        account=account,
        settings=Settings(),
        info_client=object(),
        trading_client=object(),
    )

    assert result.submitted_orders == 1
    assert result.failed_orders == 1
    assert result.status == "exit_only"
    assert account.status == "exit_only"


def test_build_testnet_live_trade_intent_is_reduce_only_when_requested() -> None:
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="testnet",
    )

    intent = build_testnet_live_trade_intent(
        account=account,
        coin="BTC",
        side="short",
        notional_usd=Decimal("10"),
        limit_price=Decimal("100"),
        leverage=Decimal("2"),
        reduce_only=True,
        margin_mode="isolated",
        source_fill_id="manual-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert intent.action == "close"
    assert intent.reduce_only is True
    assert intent.is_buy is True
    assert intent.size == Decimal("0.1")
    assert intent.margin_usd == Decimal("5")
    assert intent.margin_mode == "isolated"


def test_live_account_key_is_generated_from_wallet_route() -> None:
    assert (
        live_account_key_for_route(wallet_address="0x1234567890abcdef1234567890abcdef12345678")
        == "live_0x1234567890abcdef1234567890abcdef12345678"
    )


def test_live_account_key_includes_vault_route_hash() -> None:
    key = live_account_key_for_route(
        wallet_address="0x1234567890abcdef1234567890abcdef12345678",
        vault_address="0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
    )

    assert key.startswith("live_0x1234567890abcdef1234567890abcdef12345678_")
    assert len(key) <= 64


def test_resolve_live_account_wallet_address_uses_config_fallback() -> None:
    settings = Settings()
    settings.hyperliquid_wallet_address = "0x" + "2" * 40

    assert resolve_live_account_wallet_address(wallet_address=None, settings=settings) == (
        "0x" + "2" * 40
    )


def test_update_live_account_from_state_includes_spot_usdc() -> None:
    settings = Settings()
    settings.live_trading_capital_mode = "unified"
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="disabled",
        network="mainnet",
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )

    update_live_account_from_state(
        account,
        perp_states=[
            LivePerpState(
                dex="",
                payload={
                    "marginSummary": {"accountValue": "0.0"},
                    "withdrawable": "0.0",
                    "time": 1,
                },
            )
        ],
        spot_state={
            "balances": [
                {"coin": "USDC", "total": "199.8", "hold": "0.0"},
                {"coin": "USDE", "total": "3", "hold": "0.0"},
            ],
            "tokenToAvailableAfterMaintenance": [[0, "199.8"]],
        },
        user_abstraction="unifiedAccount",
        reconciled_at=datetime(2026, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    assert account.equity_usd == Decimal("199.8")
    assert account.cash_balance_usd == Decimal("199.8")
    assert live_perp_equity_usd(account) == Decimal("0.0")
    assert live_tradable_equity_usd(account, settings=settings, dex="xyz") == Decimal("199.8")
    assert account.config_payload["lastReconciliation"]["capitalMode"] == "unified"
    assert account.config_payload["lastReconciliation"]["userAbstraction"] == "unifiedAccount"


def test_unified_tradable_equity_respects_available_after_maintenance() -> None:
    settings = Settings()
    settings.live_trading_capital_mode = "unified"
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="disabled",
        network="mainnet",
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )

    update_live_account_from_state(
        account,
        perp_states=[
            LivePerpState(
                dex="",
                payload={
                    "marginSummary": {"accountValue": "0.0"},
                    "withdrawable": "0.0",
                    "time": 1,
                },
            )
        ],
        spot_state={
            "balances": [{"coin": "USDC", "total": "200", "hold": "0"}],
            "tokenToAvailableAfterMaintenance": [[0, "0"]],
        },
        user_abstraction="unifiedAccount",
        reconciled_at=datetime(2026, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    assert account.equity_usd == Decimal("200")
    assert account.cash_balance_usd == Decimal("0")
    assert live_tradable_equity_usd(account, settings=settings) == Decimal("0")


def test_update_live_account_from_state_includes_hip3_perp_equity() -> None:
    settings = Settings()
    settings.live_trading_capital_mode = "standard_per_dex"
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="disabled",
        network="mainnet",
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )

    update_live_account_from_state(
        account,
        perp_states=[
            LivePerpState(
                dex="",
                payload={
                    "marginSummary": {"accountValue": "0.0"},
                    "withdrawable": "0.0",
                    "time": 1,
                },
            ),
            LivePerpState(
                dex="xyz",
                payload={
                    "marginSummary": {"accountValue": "199.8"},
                    "withdrawable": "199.8",
                    "time": 2,
                },
            ),
        ],
        spot_state={"balances": []},
        reconciled_at=datetime(2026, 1, 1, tzinfo=UTC),
        settings=settings,
    )

    assert account.equity_usd == Decimal("199.8")
    assert account.cash_balance_usd == Decimal("199.8")
    assert live_perp_equity_usd(account) == Decimal("199.8")
    assert live_perp_equity_usd(account, dex="") == Decimal("0.0")
    assert live_perp_equity_usd(account, dex="xyz") == Decimal("199.8")
    assert live_tradable_equity_usd(account, settings=settings, dex="") == Decimal("0.0")
    assert live_tradable_equity_usd(account, settings=settings, dex="xyz") == Decimal("199.8")


@pytest.mark.asyncio
async def test_create_live_trading_account_returns_existing_wallet_route(monkeypatch) -> None:
    settings = Settings()
    settings.hyperliquid_wallet_address = "0x" + "2" * 40
    existing = TradingAccount(
        key="live_existing",
        account_type="live",
        label="Existing",
        status="disabled",
        network="mainnet",
        wallet_address=None,
        vault_address=None,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )

    async def fake_find_existing_live_account_for_route(_session, **kwargs):
        assert kwargs["wallet_address"] == "0x" + "2" * 40
        assert kwargs["vault_address"] is None
        assert kwargs["include_config_wallet_fallback"] is True
        return existing

    monkeypatch.setattr(
        live_trading_service,
        "find_existing_live_account_for_route",
        fake_find_existing_live_account_for_route,
    )

    account = await create_live_trading_account(
        object(),
        key=None,
        label="New Label",
        wallet_address=None,
        vault_address=None,
        status="disabled",
        settings=settings,
    )

    assert account is existing
    assert account.wallet_address == "0x" + "2" * 40


@pytest.mark.asyncio
async def test_fetch_live_fills_by_time_paginates_full_pages() -> None:
    client = FakeFillClient()

    result = await fetch_live_fills_by_time(client, user="0xuser", start_time_ms=1000)

    assert len(result.fills) == 501
    assert result.complete is True
    assert result.pages == 2
    assert client.start_times == [1000, 1499]


@pytest.mark.asyncio
async def test_fetch_live_fills_marks_safety_limit_as_partial() -> None:
    class FullPageClient:
        async def user_fills_by_time(
            self,
            *,
            user: str,
            start_time_ms: int,
            aggregate_by_time: bool = False,
        ) -> list[dict[str, object]]:
            return [
                {
                    "aggregateByTime": aggregate_by_time,
                    "time": start_time_ms + index,
                    "user": user,
                }
                for index in range(500)
            ]

    result = await fetch_live_fills_by_time(
        FullPageClient(),  # type: ignore[arg-type]
        user="0xuser",
        start_time_ms=1000,
        max_pages=2,
    )

    assert len(result.fills) == 999
    assert result.complete is False
    assert result.pages == 2
    assert result.next_start_time_ms == 1998
    assert result.error == "Fill reconciliation reached the 2-page safety limit."


@pytest.mark.asyncio
async def test_fetch_live_funding_by_time_paginates_without_duplicates() -> None:
    class FundingClient:
        def __init__(self) -> None:
            self.start_times: list[int] = []

        async def user_funding(
            self,
            *,
            user: str,
            start_time_ms: int,
        ) -> list[dict[str, object]]:
            self.start_times.append(start_time_ms)
            if len(self.start_times) == 1:
                return [
                    {
                        "delta": {"coin": "BTC", "usdc": str(index)},
                        "hash": f"0x{index}",
                        "time": start_time_ms + index,
                    }
                    for index in range(500)
                ]
            return [
                {
                    "delta": {"coin": "BTC", "usdc": "499"},
                    "hash": "0x499",
                    "time": 1499,
                    "user": user,
                },
                {
                    "delta": {"coin": "BTC", "usdc": "500"},
                    "hash": "0x500",
                    "time": 1500,
                },
            ]

    client = FundingClient()
    result = await fetch_live_funding_by_time(  # type: ignore[arg-type]
        client,
        user="0xuser",
        start_time_ms=1000,
    )

    assert len(result.payments) == 501
    assert result.complete is True
    assert result.pages == 2
    assert client.start_times == [1000, 1499]


@pytest.mark.asyncio
async def test_fetch_live_cash_flows_by_time_uses_bounded_history_window() -> None:
    class CashFlowClient:
        async def user_non_funding_ledger_updates(
            self,
            *,
            user: str,
            start_time_ms: int,
            end_time_ms: int,
        ) -> list[dict[str, object]]:
            assert user == "0xuser"
            assert start_time_ms == 1000
            assert end_time_ms == 2000
            return [
                {
                    "delta": {"type": "deposit", "usdc": "100"},
                    "hash": "0xdeposit",
                    "time": 1500,
                }
            ]

    result = await fetch_live_cash_flows_by_time(  # type: ignore[arg-type]
        CashFlowClient(),
        user="0xuser",
        start_time_ms=1000,
        end_time_ms=2000,
    )

    assert result.complete is True
    assert result.pages == 1
    assert len(result.updates) == 1


def test_parse_live_account_cash_flow_tracks_external_capital_only() -> None:
    deposit = parse_live_account_cash_flow(
        {
            "delta": {"type": "deposit", "usdc": "1000"},
            "hash": "0xdeposit",
            "time": 1_750_000_000_000,
        },
        account_key="live_test",
        user_address="0x" + "1" * 40,
    )
    withdrawal = parse_live_account_cash_flow(
        {
            "delta": {"type": "withdraw", "usdc": "100", "fee": "1"},
            "hash": "0xwithdrawal",
            "time": 1_750_000_000_100,
        },
        account_key="live_test",
        user_address="0x" + "1" * 40,
    )
    internal = parse_live_account_cash_flow(
        {
            "delta": {"type": "spotTransfer", "usdc": "500"},
            "hash": "0xinternal",
            "time": 1_750_000_000_200,
        },
        account_key="live_test",
        user_address="0x" + "1" * 40,
    )
    spot_transfer = parse_live_account_cash_flow(
        {
            "delta": {
                "type": "spotTransfer",
                "token": "USDC",
                "amount": "50",
                "user": "0x" + "1" * 40,
                "destination": "0x" + "2" * 40,
                "fee": "0.1",
            },
            "hash": "0xspot",
            "time": 1_750_000_000_300,
        },
        account_key="live_test",
        user_address="0x" + "1" * 40,
    )

    assert deposit is not None
    assert deposit["amount_usd"] == Decimal("1000")
    assert deposit["flow_type"] == "deposit"
    assert withdrawal is not None
    assert withdrawal["amount_usd"] == Decimal("-101")
    assert withdrawal["flow_type"] == "withdrawal"
    assert internal is None
    assert spot_transfer is not None
    assert spot_transfer["amount_usd"] == Decimal("-50.1")
    assert spot_transfer["flow_type"] == "spot_transfer_out"


def test_cash_flow_adjusted_return_does_not_dilute_prior_performance() -> None:
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    ended_at = started_at + timedelta(hours=1)

    result = cash_flow_adjusted_period_return(
        start_equity=Decimal("1000"),
        end_equity=Decimal("2100"),
        cash_flows=[(ended_at, Decimal("1000"))],
        started_at=started_at,
        ended_at=ended_at,
    )

    assert result == Decimal("0.1")


def test_partial_spot_reconciliation_preserves_last_authoritative_capital() -> None:
    settings = Settings()
    settings.live_trading_capital_mode = "unified"
    previous_reconciled_at = datetime(2026, 1, 1, tzinfo=UTC)
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="disabled",
        network="mainnet",
        equity_usd=Decimal("200"),
        cash_balance_usd=Decimal("190"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        last_reconciled_at=previous_reconciled_at,
        config_payload={
            "lastReconciliation": {
                "spotState": {"balances": [{"coin": "USDC", "total": "200"}]},
                "spotUsdcAvailableUsd": "190",
                "spotUsdcTotalUsd": "200",
                "unifiedAvailableUsd": "190",
                "unifiedEquityUsd": "200",
            }
        },
    )

    update_live_account_from_state(
        account,
        perp_states=[LivePerpState(dex="", payload={"marginSummary": {"accountValue": "0"}})],
        spot_state={"error": {"message": "spot timed out", "type": "TimeoutError"}},
        reconciled_at=datetime(2026, 1, 2, tzinfo=UTC),
        settings=settings,
        reconciliation_status="partial",
        incomplete_components=("spot",),
        component_errors={"spot": "spot timed out"},
    )

    assert account.equity_usd == Decimal("200")
    assert account.cash_balance_usd == Decimal("190")
    assert account.last_reconciled_at == previous_reconciled_at
    assert account.config_payload["lastReconciliation"]["status"] == "partial"
    assert account.config_payload["lastReconciliationAttempt"]["status"] == "partial"


def test_partial_reconciliation_blocks_starting_live_account() -> None:
    settings = Settings()
    settings.live_trading_capital_mode = "unified"
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="disabled",
        network="mainnet",
        equity_usd=Decimal("200"),
        cash_balance_usd=Decimal("190"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        config_payload={
            "lastReconciliation": {
                "unifiedAvailableUsd": "190",
                "userAbstraction": "unifiedAccount",
            },
            "lastReconciliationAttempt": {
                "incompleteComponents": ["perp:xyz"],
                "status": "partial",
            },
        },
    )

    with pytest.raises(
        live_trading_service.LiveTradingServiceError,
        match="complete exchange reconciliation",
    ):
        validate_live_account_can_start(account, settings=settings)


def live_order(*, status: str) -> TradingOrder:
    return TradingOrder(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin="ETH",
        action="open",
        side="long",
        is_buy=True,
        reduce_only=False,
        order_type="ioc",
        status=status,
        requested_size=Decimal("1"),
        requested_notional_usd=Decimal("100"),
        filled_size=Decimal("0"),
        filled_notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_fill(
    *,
    action: str,
    filled_at: datetime,
    fee_usd: Decimal,
    notional_usd: Decimal,
    price: Decimal,
    realized_pnl_usd: Decimal,
    sequence_index: int,
    size: Decimal,
    source_wallet: str = "0xsource",
    coin: str = "HYPE",
    side: str = "long",
) -> TradingFill:
    return TradingFill(
        id=uuid4(),
        order_id=None,
        account_key="live_test",
        account_type="live",
        source_wallet=source_wallet,
        source_fill_id=f"source-fill-{sequence_index}",
        sequence_index=sequence_index,
        exchange_fill_id=f"exchange-fill-{sequence_index}",
        coin=coin,
        action=action,
        side=side,
        price=price,
        size=size,
        notional_usd=notional_usd,
        fee_usd=fee_usd,
        realized_pnl_usd=realized_pnl_usd,
        filled_at=filled_at,
        created_at=filled_at,
    )


def live_position(*, raw_payload: dict[str, object]) -> TradingPosition:
    return TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet="__exchange__",
        coin="HYPE",
        side="long",
        size=Decimal("1.58"),
        entry_price=Decimal("61.7158"),
        notional_usd=Decimal("97.75"),
        leverage=Decimal("10"),
        margin_usd=Decimal("10"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload=raw_payload,
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class FakeFillClient:
    def __init__(self) -> None:
        self.start_times: list[int] = []

    async def user_fills_by_time(
        self,
        *,
        user: str,
        start_time_ms: int,
        aggregate_by_time: bool = False,
    ) -> list[dict[str, object]]:
        self.start_times.append(start_time_ms)
        if len(self.start_times) == 1:
            return [{"time": 1000 + index} for index in range(500)]
        return [{"time": 1500, "user": user, "aggregateByTime": aggregate_by_time}]
