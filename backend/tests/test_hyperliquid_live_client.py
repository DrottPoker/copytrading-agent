from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
    parse_order_response,
)
from app.services.trading_core import TradeIntent


class FakeExchange:
    def __init__(self) -> None:
        self.orders: list[dict[str, object]] = []

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
                                "totalSz": "0.5",
                                "avgPx": "100.25",
                            }
                        }
                    ]
                },
            },
        }


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
async def test_live_client_allows_reduce_only_below_entry_minimum() -> None:
    settings = live_test_settings()
    settings.live_trading_min_order_notional_usd = Decimal("100")
    exchange = FakeExchange()
    client = HyperliquidLiveTradingClient(
        settings=settings,
        exchange_factory=lambda _account: exchange,
        cloid_factory=lambda value: value,
    )
    account = live_test_account(status="exit_only")
    intent = live_test_intent(
        action="close",
        is_buy=False,
        reduce_only=True,
        notional_usd=Decimal("5"),
    )

    result = await client.submit_order(account=account, intent=intent)

    assert result.status == "filled"
    assert exchange.orders[0]["reduce_only"] is True


def live_test_settings() -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    settings.hyperliquid_private_key = "0x" + "1" * 64
    settings.hyperliquid_wallet_address = "0x" + "2" * 40
    settings.live_trading_enabled = True
    settings.live_trading_acknowledged = True
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
    action: str = "open",
    is_buy: bool = True,
    reduce_only: bool = False,
    notional_usd: Decimal = Decimal("50"),
) -> TradeIntent:
    return TradeIntent(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id="0x" + "a" * 32,
        coin="BTC",
        action=action,
        side="long",
        is_buy=is_buy,
        reduce_only=reduce_only,
        size=Decimal("0.5"),
        notional_usd=notional_usd,
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
