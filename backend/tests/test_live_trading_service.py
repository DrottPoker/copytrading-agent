from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount, TradingOrder
from app.services import live_trading_service
from app.services.live_trading_service import (
    apply_order_status_response,
    build_testnet_live_trade_intent,
    create_live_trading_account,
    fetch_live_fills_by_time,
    live_account_key_for_route,
    parse_live_fill,
    parse_live_position,
    resolve_live_account_wallet_address,
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
