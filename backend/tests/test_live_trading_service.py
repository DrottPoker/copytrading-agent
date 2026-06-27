from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import TradingAccount, TradingFill, TradingOrder, TradingPosition
from app.integrations.hyperliquid_live_client import LiveOrderResult
from app.services import live_trading_service
from app.services.live_trading_service import (
    LivePerpState,
    apply_live_order_result,
    apply_order_status_response,
    build_testnet_live_trade_intent,
    create_live_trading_account,
    fetch_live_fills_by_time,
    live_account_key_for_route,
    live_closed_trades_from_fills,
    live_perp_equity_usd,
    live_position_current_notional,
    live_position_mark_price,
    live_position_unrealized_pnl,
    live_position_unrealized_pnl_pct,
    live_tradable_equity_usd,
    manual_live_close_recovery_status,
    parse_live_fill,
    parse_live_position,
    resolve_live_account_wallet_address,
    sync_live_source_positions_from_exchange_positions,
    update_live_account_from_state,
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


def test_parse_live_position_reads_signed_position_size() -> None:
    snapshot = parse_live_position(
        {
            "position": {
                "coin": "BTC",
                "szi": "-0.25",
                "entryPx": "65000",
                "positionValue": "16250",
                "leverage": {"value": "5"},
                "marginUsed": "3250",
            }
        }
    )

    assert snapshot is not None
    assert snapshot.coin == "BTC"
    assert snapshot.side == "short"
    assert snapshot.size == Decimal("0.25")
    assert snapshot.leverage == Decimal("5")
    assert snapshot.margin_usd == Decimal("3250")


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

    assert updated == 1
    assert live_position_mark_price(source_position) == Decimal("63")
    assert live_position_current_notional(source_position) == Decimal("63")
    assert live_position_unrealized_pnl(source_position) == Decimal("3")
    assert live_position_unrealized_pnl_pct(source_position) == Decimal("0.5")
    assert source_position.last_reconciled_at == reconciled_at


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
        source_fill_id="manual-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert intent.action == "close"
    assert intent.reduce_only is True
    assert intent.is_buy is True
    assert intent.size == Decimal("0.1")
    assert intent.margin_usd == Decimal("5")


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

    fills = await fetch_live_fills_by_time(client, user="0xuser", start_time_ms=1000)

    assert len(fills) == 501
    assert client.start_times == [1000, 1500]


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
) -> TradingFill:
    return TradingFill(
        id=uuid4(),
        order_id=None,
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id=f"source-fill-{sequence_index}",
        sequence_index=sequence_index,
        exchange_fill_id=f"exchange-fill-{sequence_index}",
        coin="HYPE",
        action=action,
        side="long",
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
