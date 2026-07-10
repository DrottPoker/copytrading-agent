from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveOrderRejectedError,
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
    live_order_wire_price,
    live_order_wire_values,
    parse_order_response,
)
from app.services.trading_core import TradeIntent


class FakeExchange:
    def __init__(
        self,
        *,
        info: object | None = None,
        leverage_update_response: dict[str, object] | None = None,
    ) -> None:
        if info is not None:
            self.info = info
        self.orders: list[dict[str, object]] = []
        self.leverage_updates: list[dict[str, object]] = []
        self.events: list[str] = []
        self.leverage_update_response = (
            leverage_update_response
            if leverage_update_response is not None
            else {"status": "ok", "response": {"type": "default"}}
        )

    def update_leverage(
        self,
        leverage: int,
        coin: str,
        is_cross: bool = True,
    ) -> dict[str, object]:
        self.events.append("update_leverage")
        self.leverage_updates.append(
            {
                "coin": coin,
                "leverage": leverage,
                "is_cross": is_cross,
            }
        )
        return self.leverage_update_response

    def order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        limit_price: float,
        order_type: dict[str, object],
        *,
        reduce_only: bool,
        cloid: object,
    ) -> dict[str, object]:
        self.events.append("order")
        self.orders.append(
            {
                "coin": coin,
                "is_buy": is_buy,
                "size": size,
                "limit_price": limit_price,
                "order_type": order_type,
                "reduce_only": reduce_only,
                "cloid": cloid,
            }
        )
        return {
            "status": "ok",
            "response": {
                "type": "order",
                "data": {
                    "statuses": [
                        {
                            "filled": {
                                "oid": 123,
                                "totalSz": str(size),
                                "avgPx": str(limit_price),
                            }
                        }
                    ]
                },
            },
        }


class KeyErrorExchange(FakeExchange):
    def order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        limit_price: float,
        order_type: dict[str, object],
        *,
        reduce_only: bool,
        cloid: object,
    ) -> dict[str, object]:
        raise KeyError(coin)


class FakeInfo:
    def __init__(
        self,
        *,
        assets: dict[str, int],
        size_decimals: dict[int, int],
    ) -> None:
        self.assets = assets
        self.asset_to_sz_decimals = size_decimals

    def name_to_asset(self, coin: str) -> int:
        return self.assets[coin]


def test_parse_order_response_filled() -> None:
    result = parse_order_response(
        {
            "status": "ok",
            "response": {
                "data": {
                    "statuses": [
                        {
                            "filled": {
                                "oid": 99,
                                "totalSz": "1.2",
                                "avgPx": "10.5",
                            }
                        }
                    ]
                }
            },
        },
        client_order_id="0xabc",
    )

    assert result.status == "filled"
    assert result.exchange_order_id == "99"
    assert result.filled_size == Decimal("1.2")
    assert result.average_fill_price == Decimal("10.5")


def test_live_client_blocks_when_live_disabled() -> None:
    settings = Settings()
    settings.live_trading_enabled = False
    client = HyperliquidLiveTradingClient(settings=settings)

    with pytest.raises(HyperliquidLiveTradingConfigurationError):
        client.validate_live_configuration()


def test_mainnet_entry_requires_runtime_arming_and_allowlist() -> None:
    settings = mainnet_live_test_settings()
    client = HyperliquidLiveTradingClient(settings=settings)

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="LIVE_TRADING_MAINNET_ARMING_TOKEN",
    ):
        client.validate_entry_guardrails(live_test_intent())

    settings.live_trading_mainnet_arming_token = "ARM_MAINNET_LIVE_TRADING"
    settings.live_trading_mainnet_armed_at = datetime.now(UTC) - timedelta(minutes=1)
    settings.live_trading_mainnet_armed_until = datetime.now(UTC) + timedelta(hours=1)

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="LIVE_TRADING_ALLOWED_COINS",
    ):
        client.validate_entry_guardrails(live_test_intent())

    settings.live_trading_allowed_coins = ["BTC"]
    client.validate_entry_guardrails(live_test_intent())


def test_expired_mainnet_arming_blocks_entries_but_not_reduce_only_exits() -> None:
    settings = mainnet_live_test_settings()
    settings.live_trading_mainnet_arming_token = "ARM_MAINNET_LIVE_TRADING"
    settings.live_trading_mainnet_armed_at = datetime.now(UTC) - timedelta(hours=1)
    settings.live_trading_mainnet_armed_until = datetime.now(UTC) - timedelta(minutes=1)
    settings.live_trading_allowed_coins = ["BTC"]
    client = HyperliquidLiveTradingClient(settings=settings)
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
        wallet_address="0x" + "2" * 40,
    )

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="arming has expired",
    ):
        client.validate_account_order(account=account, intent=live_test_intent())

    account.status = "exit_only"
    client.validate_account_order(
        account=account,
        intent=replace(
            live_test_intent(),
            action="close",
            is_buy=False,
            reduce_only=True,
        ),
    )


def test_live_client_rejects_entry_above_configured_max_leverage() -> None:
    settings = live_test_settings()
    settings.live_trading_max_leverage = Decimal("3")
    client = HyperliquidLiveTradingClient(settings=settings)

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="leverage exceeds",
    ):
        client.validate_account_order(
            account=live_test_account(status="enabled"),
            intent=live_test_intent(leverage=Decimal("4")),
        )


@pytest.mark.asyncio
async def test_live_client_rejects_fractional_entry_leverage() -> None:
    exchange = FakeExchange()
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )

    with pytest.raises(
        HyperliquidLiveTradingConfigurationError,
        match="whole number",
    ):
        await client.submit_order(
            account=live_test_account(status="enabled"),
            intent=live_test_intent(leverage=Decimal("2.5")),
        )

    assert exchange.leverage_updates == []
    assert exchange.orders == []


@pytest.mark.asyncio
async def test_live_client_submits_ioc_order_with_fake_exchange() -> None:
    exchange = FakeExchange()
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="testnet",
        wallet_address="0x" + "2" * 40,
    )
    intent = TradeIntent(
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
        limit_price=Decimal("100.25"),
        source_price=Decimal("100"),
        observed_price=Decimal("100"),
        price_drift_bps=Decimal("0"),
        price_source="test",
        allocation_pct=Decimal("0.2"),
        allocation_usd=Decimal("100"),
        source_perp_equity_usd=Decimal("1000"),
        source_exposure_pct=Decimal("0.05"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert exchange.events == ["update_leverage", "order"]
    assert exchange.leverage_updates == [
        {
            "coin": "BTC",
            "leverage": 5,
            "is_cross": True,
        }
    ]
    assert exchange.orders == [
        {
            "coin": "BTC",
            "is_buy": True,
            "size": 0.5,
            "limit_price": 100.25,
            "order_type": {"limit": {"tif": "Ioc"}},
            "reduce_only": False,
            "cloid": "0x" + "a" * 32,
        }
    ]
    assert result.raw_response["leverageUpdate"] == {
        "coin": "BTC",
        "leverage": 5,
        "isCross": True,
        "response": {"status": "ok", "response": {"type": "default"}},
    }


@pytest.mark.asyncio
async def test_live_client_does_not_submit_when_leverage_update_is_rejected() -> None:
    exchange = FakeExchange(
        leverage_update_response={
            "status": "err",
            "response": "Leverage update rejected",
        }
    )
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )

    with pytest.raises(
        HyperliquidLiveOrderRejectedError,
        match="leverage update was rejected before order submission",
    ):
        await client.submit_order(
            account=live_test_account(status="enabled"),
            intent=live_test_intent(leverage=Decimal("3")),
        )

    assert exchange.events == ["update_leverage"]
    assert exchange.leverage_updates == [
        {
            "coin": "BTC",
            "leverage": 3,
            "is_cross": True,
        }
    ]
    assert exchange.orders == []


def test_live_order_wire_price_rounds_without_worse_limit() -> None:
    assert live_order_wire_price(
        Decimal("63.930449"),
        is_buy=True,
        max_decimal_places=4,
    ) == Decimal("63.9300")
    assert live_order_wire_price(
        Decimal("63.930449"),
        is_buy=False,
        max_decimal_places=4,
    ) == Decimal("63.9310")


def test_live_order_wire_values_adjust_min_order_after_lot_rounding() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"HYPE": 0},
            size_decimals={0: 2},
        )
    )
    intent = live_test_intent(
        coin="HYPE",
        size=Decimal("0.157447565719185"),
        limit_price=Decimal("63.930449"),
        notional_usd=Decimal("10.07"),
        source_price=Decimal("63.930449"),
        observed_price=Decimal("63.930449"),
    )

    values = live_order_wire_values(
        intent,
        exchange=exchange,
        min_order_notional_usd=Decimal("10"),
        adjust_to_min_order=True,
    )

    assert values.size == Decimal("0.16")
    assert values.limit_price == Decimal("63.9300")
    assert values.notional_usd == Decimal("10.228800")
    assert values.size_decimals == 2
    assert values.price_decimals == 4


def test_live_order_wire_values_adds_buffer_after_price_and_lot_rounding() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"HYPE": 0},
            size_decimals={0: 4},
        )
    )
    intent = live_test_intent(
        coin="HYPE",
        size=Decimal("0.064797"),
        limit_price=Decimal("154.32804"),
        notional_usd=Decimal("10"),
        source_price=Decimal("154.32804"),
        observed_price=Decimal("154.32804"),
    )

    values = live_order_wire_values(
        intent,
        exchange=exchange,
        min_order_notional_usd=Decimal("10"),
        min_order_notional_buffer_usd=Decimal("0.10"),
        adjust_to_min_order=True,
    )

    assert values.size == Decimal("0.0655")
    assert values.limit_price == Decimal("154.32")
    assert values.notional_usd == Decimal("10.107960")
    assert values.size_decimals == 4
    assert values.price_decimals == 2


def test_live_order_wire_values_adjusts_reduce_only_dust_close_to_min_order() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"BIO": 0},
            size_decimals={0: 0},
        )
    )
    intent = live_test_intent(
        coin="BIO",
        action="close",
        side="short",
        is_buy=True,
        reduce_only=True,
        size=Decimal("303"),
        limit_price=Decimal("0.031077"),
        notional_usd=Decimal("9.416331"),
        source_price=Decimal("0.031077"),
        observed_price=Decimal("0.031077"),
    )

    values = live_order_wire_values(
        intent,
        exchange=exchange,
        min_order_notional_usd=Decimal("10"),
        adjust_to_min_order=True,
    )

    assert values.size == Decimal("322")
    assert values.limit_price == Decimal("0.031077")
    assert values.notional_usd == Decimal("10.006794")


@pytest.mark.asyncio
async def test_live_client_submits_hyperliquid_wire_safe_values() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"HYPE": 0},
            size_decimals={0: 2},
        )
    )
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(
        coin="HYPE",
        size=Decimal("0.157447565719185"),
        limit_price=Decimal("63.930449"),
        notional_usd=Decimal("10.07"),
        source_price=Decimal("63.930449"),
        observed_price=Decimal("63.930449"),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert result.submitted_size == Decimal("0.16")
    assert result.submitted_limit_price == Decimal("63.9300")
    assert result.submitted_notional_usd == Decimal("10.228800")
    assert exchange.orders[0]["size"] == 0.16
    assert exchange.orders[0]["limit_price"] == 63.93
    assert result.raw_response["clientOrderRequest"] == {
        "size": "0.16",
        "limitPrice": "63.93",
        "notionalUsd": "10.2288",
        "sizeDecimals": 2,
        "priceDecimals": 4,
    }


@pytest.mark.asyncio
async def test_live_client_submits_prefixed_dex_market_as_sdk_base_coin() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"SNDK": 110000},
            size_decimals={110000: 4},
        )
    )
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(
        coin="xyz:SNDK",
        size=Decimal("0.004752"),
        limit_price=Decimal("2104.4004"),
        notional_usd=Decimal("10"),
        source_price=Decimal("2104.4004"),
        observed_price=Decimal("2104.4004"),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert exchange.orders[0]["coin"] == "SNDK"
    assert result.submitted_size == Decimal("0.0048")
    assert result.submitted_limit_price == Decimal("2104.4")
    assert result.submitted_notional_usd == Decimal("10.10112")


@pytest.mark.asyncio
async def test_live_client_submits_prefixed_dex_market_as_sdk_prefixed_coin() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"xyz:SNDK": 110000},
            size_decimals={110000: 4},
        )
    )
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(
        coin="xyz:SNDK",
        size=Decimal("0.004752"),
        limit_price=Decimal("2104.4004"),
        notional_usd=Decimal("10"),
        source_price=Decimal("2104.4004"),
        observed_price=Decimal("2104.4004"),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert exchange.orders[0]["coin"] == "xyz:SNDK"
    assert result.submitted_size == Decimal("0.0048")
    assert result.submitted_limit_price == Decimal("2104.4")
    assert result.submitted_notional_usd == Decimal("10.10112")
    assert result.raw_response["clientOrderRequest"]["priceDecimals"] == 2


@pytest.mark.asyncio
async def test_live_client_rejects_market_missing_from_sdk_metadata() -> None:
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"HYPE": 0},
            size_decimals={0: 2},
        )
    )
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(
        coin="xyz:SNDK",
        size=Decimal("0.004752"),
        limit_price=Decimal("2104.4004"),
        notional_usd=Decimal("10"),
        source_price=Decimal("2104.4004"),
        observed_price=Decimal("2104.4004"),
    )

    with pytest.raises(
        HyperliquidLiveOrderRejectedError,
        match="Live order market is not available for exchange submission: xyz:SNDK.",
    ):
        await client.submit_order(account=account, intent=intent)

    assert exchange.orders == []


@pytest.mark.asyncio
async def test_live_client_translates_sdk_market_key_error() -> None:
    exchange = KeyErrorExchange()
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(coin="xyz:SNDK")

    with pytest.raises(
        HyperliquidLiveOrderRejectedError,
        match="Live order market is not available for exchange submission: xyz:SNDK.",
    ):
        await client.submit_order(account=account, intent=intent)


@pytest.mark.asyncio
async def test_live_client_exit_only_blocks_entries() -> None:
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: FakeExchange(),
        cloid_factory=lambda value: value,
    )
    account = TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="exit_only",
        network="testnet",
    )
    intent = TradeIntent(
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
        limit_price=Decimal("100.25"),
        source_price=None,
        observed_price=None,
        price_drift_bps=None,
        price_source=None,
        allocation_pct=None,
        allocation_usd=None,
        source_perp_equity_usd=None,
        source_exposure_pct=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(HyperliquidLiveTradingConfigurationError):
        await client.submit_order(account=account, intent=intent)


@pytest.mark.asyncio
async def test_live_client_blocks_network_mismatch() -> None:
    settings = live_test_settings()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: FakeExchange(),
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    account.network = "mainnet"
    intent = live_test_intent()

    with pytest.raises(HyperliquidLiveTradingConfigurationError):
        await client.submit_order(account=account, intent=intent)


@pytest.mark.asyncio
async def test_live_client_blocks_entry_above_max_notional() -> None:
    settings = live_test_settings()
    settings.live_trading_max_order_notional_usd = Decimal("25")
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: FakeExchange(),
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="enabled")
    intent = live_test_intent(notional_usd=Decimal("50"))

    with pytest.raises(HyperliquidLiveTradingConfigurationError):
        await client.submit_order(account=account, intent=intent)


@pytest.mark.asyncio
async def test_live_client_submits_reduce_only_dust_close_with_min_wire_size() -> None:
    settings = live_test_settings()
    settings.trading_copy_adjust_small_orders_to_min_order = False
    settings.live_trading_min_order_notional_usd = Decimal("10")
    exchange = FakeExchange(
        info=FakeInfo(
            assets={"BIO": 0},
            size_decimals={0: 0},
        )
    )
    exchange.update_leverage = None  # type: ignore[method-assign]
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="exit_only")
    intent = live_test_intent(
        coin="BIO",
        action="close",
        side="short",
        is_buy=True,
        reduce_only=True,
        size=Decimal("303"),
        limit_price=Decimal("0.031077"),
        notional_usd=Decimal("9.416331"),
        source_price=Decimal("0.031077"),
        observed_price=Decimal("0.031077"),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert exchange.events == ["order"]
    assert exchange.leverage_updates == []
    assert exchange.orders[0]["size"] == 322.0
    assert exchange.orders[0]["reduce_only"] is True


def live_test_settings() -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    settings.hyperliquid_private_key = "0x" + "1" * 64
    settings.hyperliquid_wallet_address = "0x" + "2" * 40
    settings.live_trading_enabled = True
    settings.live_trading_acknowledged = True
    return settings


def mainnet_live_test_settings() -> Settings:
    settings = live_test_settings()
    settings.hyperliquid_network = "mainnet"
    settings.live_trading_mainnet_acknowledged = True
    return settings


def live_test_account(*, status: str) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="testnet",
        wallet_address="0x" + "2" * 40,
    )


def live_test_intent(
    *,
    coin: str = "BTC",
    action: str = "open",
    side: str = "long",
    is_buy: bool = True,
    reduce_only: bool = False,
    size: Decimal = Decimal("0.5"),
    notional_usd: Decimal = Decimal("50"),
    limit_price: Decimal = Decimal("100.25"),
    source_price: Decimal = Decimal("100"),
    observed_price: Decimal = Decimal("100"),
    leverage: Decimal = Decimal("5"),
) -> TradeIntent:
    return TradeIntent(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin=coin,
        action=action,
        side=side,
        is_buy=is_buy,
        reduce_only=reduce_only,
        size=size,
        notional_usd=notional_usd,
        margin_usd=Decimal("10"),
        leverage=leverage,
        limit_price=limit_price,
        source_price=source_price,
        observed_price=observed_price,
        price_drift_bps=Decimal("0"),
        price_source="test",
        allocation_pct=Decimal("0.2"),
        allocation_usd=Decimal("100"),
        source_perp_equity_usd=Decimal("1000"),
        source_exposure_pct=Decimal("0.05"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
