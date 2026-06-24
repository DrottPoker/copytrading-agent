import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.core.config import Settings, get_settings
from app.db.models import TradingAccount
from app.services.trading_core import TradeIntent


class HyperliquidLiveTradingError(RuntimeError):
    pass


class HyperliquidLiveTradingConfigurationError(HyperliquidLiveTradingError):
    pass


class HyperliquidLiveOrderRejectedError(HyperliquidLiveTradingError):
    pass


@dataclass(frozen=True)
class LiveOrderResult:
    status: str
    client_order_id: str
    exchange_order_id: str | None
    filled_size: Decimal | None
    average_fill_price: Decimal | None
    raw_response: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class HyperliquidSdkBindings:
    account: Any
    exchange: Any
    cloid: Any
    constants: Any


ExchangeFactory = Callable[[TradingAccount], Any]
CloidFactory = Callable[[str], Any]


class HyperliquidLiveTradingClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        exchange_factory: ExchangeFactory | None = None,
        cloid_factory: CloidFactory | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._exchange_factory = exchange_factory
        self._cloid_factory = cloid_factory

    async def submit_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> LiveOrderResult:
        self.validate_account_order(account=account, intent=intent)
        return await asyncio.to_thread(self._submit_order_sync, account, intent)

    async def cancel_by_client_order_id(
        self,
        *,
        account: TradingAccount,
        coin: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        self.validate_live_configuration()
        if account.account_type != "live":
            raise HyperliquidLiveTradingConfigurationError(
                "Only live accounts can cancel live orders."
            )
        exchange = self._build_exchange(account)
        cloid = self._build_cloid(client_order_id)
        response = await asyncio.to_thread(exchange.cancel_by_cloid, coin, cloid)
        return response if isinstance(response, dict) else {"response": response}

    def validate_account_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> None:
        self.validate_live_configuration()
        if account.account_type != "live":
            raise HyperliquidLiveTradingConfigurationError(
                "Only live accounts can submit live orders."
            )
        if intent.account_type != "live":
            raise HyperliquidLiveTradingConfigurationError(
                "Live execution requires a live trade intent."
            )
        if intent.account_key != account.key:
            raise HyperliquidLiveTradingConfigurationError(
                "Trade intent account does not match live account."
            )
        if account.status == "disabled":
            raise HyperliquidLiveTradingConfigurationError("Live account is disabled.")
        if account.status == "exit_only" and not intent.reduce_only:
            raise HyperliquidLiveTradingConfigurationError(
                "Live account is exit-only and cannot open or add exposure."
            )
        if intent.size <= Decimal("0"):
            raise HyperliquidLiveTradingConfigurationError("Live order size must be positive.")
        if intent.limit_price <= Decimal("0"):
            raise HyperliquidLiveTradingConfigurationError(
                "Live order limit price must be positive."
            )
        if intent.observed_price is not None and intent.observed_price > Decimal("0"):
            slippage_bps = (
                (intent.limit_price - intent.observed_price).copy_abs()
                / intent.observed_price
                * Decimal("10000")
            )
            if slippage_bps > self.settings.live_trading_max_slippage_bps:
                raise HyperliquidLiveTradingConfigurationError(
                    "Live order limit price exceeds max slippage guard."
                )
        if not intent.reduce_only:
            self.validate_entry_guardrails(intent)

    def validate_entry_guardrails(self, intent: TradeIntent) -> None:
        coin = normalize_live_coin(intent.coin)
        allowed_coins = {
            normalize_live_coin(value) for value in self.settings.live_trading_allowed_coins
        }
        blocked_coins = {
            normalize_live_coin(value) for value in self.settings.live_trading_blocked_coins
        }
        allowed_coins.discard("")
        blocked_coins.discard("")
        if allowed_coins and coin not in allowed_coins:
            raise HyperliquidLiveTradingConfigurationError(
                "Live order coin is not in the allowed coin list."
            )
        if coin in blocked_coins:
            raise HyperliquidLiveTradingConfigurationError(
                "Live order coin is blocked by live trading config."
            )
        if intent.notional_usd < self.settings.live_trading_min_order_notional_usd:
            raise HyperliquidLiveTradingConfigurationError(
                "Live order notional is below the configured minimum."
            )
        if (
            self.settings.live_trading_max_order_notional_usd > Decimal("0")
            and intent.notional_usd > self.settings.live_trading_max_order_notional_usd
        ):
            raise HyperliquidLiveTradingConfigurationError(
                "Live order notional exceeds the configured maximum."
            )

    def validate_live_configuration(self) -> None:
        if not self.settings.live_trading_enabled:
            raise HyperliquidLiveTradingConfigurationError("Live trading is disabled.")
        if not self.settings.live_trading_acknowledged:
            raise HyperliquidLiveTradingConfigurationError(
                "Live trading acknowledgement is missing."
            )
        if (
            self.settings.hyperliquid_network == "mainnet"
            and not self.settings.live_trading_mainnet_acknowledged
        ):
            raise HyperliquidLiveTradingConfigurationError(
                "Mainnet live trading acknowledgement is missing."
            )
        if not self.settings.hyperliquid_private_key:
            raise HyperliquidLiveTradingConfigurationError("Hyperliquid private key is missing.")
        if not self.settings.hyperliquid_wallet_address:
            raise HyperliquidLiveTradingConfigurationError("Hyperliquid wallet address is missing.")

    def _submit_order_sync(
        self,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> LiveOrderResult:
        exchange = self._build_exchange(account)
        if (
            self.settings.live_trading_order_expires_after_ms > 0
            and hasattr(exchange, "set_expires_after")
        ):
            exchange.set_expires_after(
                int(time.time() * 1000) + self.settings.live_trading_order_expires_after_ms
            )
        cloid = self._build_cloid(intent.client_order_id)
        response = exchange.order(
            intent.coin,
            intent.is_buy,
            float(intent.size),
            float(intent.limit_price),
            {"limit": {"tif": "Ioc"}},
            reduce_only=intent.reduce_only,
            cloid=cloid,
        )
        if not isinstance(response, dict):
            raise HyperliquidLiveOrderRejectedError(
                "Hyperliquid order returned an invalid response."
            )
        return parse_order_response(
            response,
            client_order_id=intent.client_order_id,
        )

    def _build_exchange(self, account: TradingAccount) -> Any:
        if self._exchange_factory is not None:
            return self._exchange_factory(account)

        bindings = load_hyperliquid_sdk()
        wallet = bindings.account.from_key(self.settings.hyperliquid_private_key)
        return bindings.exchange(
            wallet,
            base_url=self.settings.hyperliquid_api_url,
            account_address=account.wallet_address or self.settings.hyperliquid_wallet_address,
            vault_address=account.vault_address,
        )

    def _build_cloid(self, client_order_id: str) -> Any:
        if self._cloid_factory is not None:
            return self._cloid_factory(client_order_id)
        return load_hyperliquid_sdk().cloid.from_str(client_order_id)


def load_hyperliquid_sdk() -> HyperliquidSdkBindings:
    try:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants
        from hyperliquid.utils.types import Cloid
    except ImportError as exc:
        raise HyperliquidLiveTradingConfigurationError(
            "Live trading requires the hyperliquid-python-sdk package."
        ) from exc

    return HyperliquidSdkBindings(
        account=Account,
        exchange=Exchange,
        cloid=Cloid,
        constants=constants,
    )


def parse_order_response(
    response: dict[str, Any],
    *,
    client_order_id: str,
) -> LiveOrderResult:
    if response.get("status") == "err":
        return LiveOrderResult(
            status="rejected",
            client_order_id=client_order_id,
            exchange_order_id=None,
            filled_size=None,
            average_fill_price=None,
            raw_response=response,
            error=str(response.get("response") or response.get("error") or "Order rejected."),
        )

    statuses = order_statuses(response)
    first_status = statuses[0] if statuses else {}
    if "error" in first_status:
        return LiveOrderResult(
            status="rejected",
            client_order_id=client_order_id,
            exchange_order_id=None,
            filled_size=None,
            average_fill_price=None,
            raw_response=response,
            error=str(first_status["error"]),
        )

    filled = first_status.get("filled")
    if isinstance(filled, dict):
        return LiveOrderResult(
            status="filled",
            client_order_id=client_order_id,
            exchange_order_id=string_or_none(filled.get("oid")),
            filled_size=decimal_or_none(filled.get("totalSz")),
            average_fill_price=decimal_or_none(filled.get("avgPx")),
            raw_response=response,
        )

    resting = first_status.get("resting")
    if isinstance(resting, dict):
        return LiveOrderResult(
            status="accepted",
            client_order_id=client_order_id,
            exchange_order_id=string_or_none(resting.get("oid")),
            filled_size=None,
            average_fill_price=None,
            raw_response=response,
        )

    return LiveOrderResult(
        status="submitted",
        client_order_id=client_order_id,
        exchange_order_id=None,
        filled_size=None,
        average_fill_price=None,
        raw_response=response,
    )


def order_statuses(response: dict[str, Any]) -> list[dict[str, Any]]:
    nested = response.get("response")
    if not isinstance(nested, dict):
        return []
    data = nested.get("data")
    if not isinstance(data, dict):
        return []
    statuses = data.get("statuses")
    if not isinstance(statuses, list):
        return []
    return [status for status in statuses if isinstance(status, dict)]


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def normalize_live_coin(value: str) -> str:
    return str(value or "").strip().casefold()


def utc_now() -> datetime:
    return datetime.now(UTC)
