import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

from app.core.config import Settings, get_settings, mainnet_live_entry_arming_error
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
    submitted_size: Decimal | None = None
    submitted_limit_price: Decimal | None = None
    submitted_notional_usd: Decimal | None = None
    error: str | None = None


@dataclass(frozen=True)
class LiveMarketPrecision:
    size_decimals: int
    price_decimals: int


@dataclass(frozen=True)
class LiveOrderWireValues:
    size: Decimal
    limit_price: Decimal
    notional_usd: Decimal
    size_decimals: int
    price_decimals: int


@dataclass(frozen=True)
class HyperliquidSdkBindings:
    account: Any
    exchange: Any
    cloid: Any
    constants: Any


ExchangeFactory = Callable[[TradingAccount], Any]
CloidFactory = Callable[[str], Any]
PERP_MAX_PRICE_DECIMALS = 6
SPOT_MAX_PRICE_DECIMALS = 8
SPOT_ASSET_OFFSET = 10_000
BUILDER_PERP_ASSET_OFFSET = 110_000
PRICE_SIGNIFICANT_DIGITS = 5
FALLBACK_SIZE_DECIMALS = 8
FALLBACK_PRICE_DECIMALS = 6


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
        self.validate_live_configuration(
            allow_when_stopped=self.settings.live_trading_reduce_only_when_stopped
        )
        if account.account_type != "live":
            raise HyperliquidLiveTradingConfigurationError(
                "Only live accounts can cancel live orders."
            )
        exchange = self._build_exchange(account, coin=coin)
        order_coin = resolve_live_order_exchange_coin(exchange, coin)
        cloid = self._build_cloid(client_order_id)
        response = await asyncio.to_thread(
            exchange.cancel_by_cloid,
            order_coin,
            cloid,
        )
        return response if isinstance(response, dict) else {"response": response}

    def validate_account_order(
        self,
        *,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> None:
        self.validate_live_configuration(
            allow_when_stopped=(
                intent.reduce_only and self.settings.live_trading_reduce_only_when_stopped
            )
        )
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
        if account.network != self.settings.hyperliquid_network:
            raise HyperliquidLiveTradingConfigurationError(
                "Live account network does not match the configured network."
            )
        if account.status == "disabled" and not (
            intent.reduce_only and self.settings.live_trading_reduce_only_when_stopped
        ):
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
        if (
            not intent.reduce_only
            and intent.notional_usd < self.settings.live_trading_min_order_notional_usd
        ):
            raise HyperliquidLiveTradingConfigurationError(
                "Live order notional is below the Hyperliquid minimum."
            )
        if not intent.reduce_only:
            validated_live_entry_leverage(
                intent.leverage,
                max_leverage=self.settings.live_trading_max_leverage,
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
        self.validate_entry_activation()
        coin = normalize_live_coin(intent.coin)
        allowed_coins = {
            normalize_live_coin(value) for value in self.settings.live_trading_allowed_coins
        }
        blocked_coins = {
            normalize_live_coin(value) for value in self.settings.live_trading_blocked_coins
        }
        allowed_coins.discard("")
        blocked_coins.discard("")
        if self.settings.hyperliquid_network == "mainnet" and not allowed_coins:
            raise HyperliquidLiveTradingConfigurationError(
                "Mainnet live entries require a non-empty LIVE_TRADING_ALLOWED_COINS list."
            )
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

    def validate_live_configuration(self, *, allow_when_stopped: bool = False) -> None:
        if not self.settings.live_trading_enabled and not allow_when_stopped:
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

    def validate_entry_activation(self) -> None:
        arming_error = mainnet_live_entry_arming_error(self.settings)
        if arming_error is not None:
            raise HyperliquidLiveTradingConfigurationError(arming_error)

    def _submit_order_sync(
        self,
        account: TradingAccount,
        intent: TradeIntent,
    ) -> LiveOrderResult:
        exchange = self._build_exchange(account, coin=intent.coin)
        order_coin = resolve_live_order_exchange_coin(exchange, intent.coin)
        validate_live_order_market(exchange, order_coin, display_coin=intent.coin)
        wire_values = live_order_wire_values(
            intent,
            exchange=exchange,
            market_coin=order_coin,
            min_order_notional_usd=max(
                self.settings.trading_copy_min_order_notional_usd,
                self.settings.live_trading_min_order_notional_usd,
            ),
            min_order_notional_buffer_usd=(
                self.settings.live_trading_min_order_notional_buffer_usd
            ),
            adjust_to_min_order=(
                intent.reduce_only or self.settings.trading_copy_adjust_small_orders_to_min_order
            ),
        )
        if (
            not intent.reduce_only
            and self.settings.live_trading_max_order_notional_usd > Decimal("0")
            and wire_values.notional_usd > self.settings.live_trading_max_order_notional_usd
        ):
            raise HyperliquidLiveTradingConfigurationError(
                "Live order notional exceeds the configured maximum after lot rounding."
            )
        if self.settings.live_trading_order_expires_after_ms > 0 and hasattr(
            exchange, "set_expires_after"
        ):
            exchange.set_expires_after(
                int(time.time() * 1000) + self.settings.live_trading_order_expires_after_ms
            )
        leverage_update: dict[str, Any] | None = None
        if not intent.reduce_only:
            exchange_leverage = validated_live_entry_leverage(
                intent.leverage,
                max_leverage=self.settings.live_trading_max_leverage,
            )
            leverage_update = apply_live_entry_exchange_leverage(
                exchange,
                coin=order_coin,
                leverage=exchange_leverage,
            )
        cloid = self._build_cloid(intent.client_order_id)
        try:
            response = exchange.order(
                order_coin,
                intent.is_buy,
                float(wire_values.size),
                float(wire_values.limit_price),
                {"limit": {"tif": "Ioc"}},
                reduce_only=intent.reduce_only,
                cloid=cloid,
            )
        except KeyError as exc:
            raise HyperliquidLiveOrderRejectedError(
                live_order_market_unavailable_message(intent.coin)
            ) from exc
        if not isinstance(response, dict):
            raise HyperliquidLiveOrderRejectedError(
                "Hyperliquid order returned an invalid response."
            )
        response_payload = dict(response)
        response_payload["clientOrderRequest"] = live_order_wire_payload(wire_values)
        if leverage_update is not None:
            response_payload["leverageUpdate"] = leverage_update
        result = parse_order_response(
            response_payload,
            client_order_id=intent.client_order_id,
        )
        return replace(
            result,
            submitted_size=wire_values.size,
            submitted_limit_price=wire_values.limit_price,
            submitted_notional_usd=wire_values.notional_usd,
        )

    def _build_exchange(self, account: TradingAccount, *, coin: str | None = None) -> Any:
        if self._exchange_factory is not None:
            return self._exchange_factory(account)

        bindings = load_hyperliquid_sdk()
        wallet = bindings.account.from_key(self.settings.hyperliquid_private_key)
        return bindings.exchange(
            wallet,
            base_url=self.settings.hyperliquid_api_url,
            account_address=account.wallet_address or self.settings.hyperliquid_wallet_address,
            vault_address=account.vault_address,
            perp_dexs=live_order_perp_dexs(coin),
        )

    def _build_cloid(self, client_order_id: str) -> Any:
        if self._cloid_factory is not None:
            return self._cloid_factory(client_order_id)
        return load_hyperliquid_sdk().cloid.from_str(client_order_id)


def validated_live_entry_leverage(
    leverage: Decimal,
    *,
    max_leverage: Decimal,
) -> int:
    if not leverage.is_finite() or leverage <= Decimal("0"):
        raise HyperliquidLiveTradingConfigurationError(
            "Live order leverage must be a positive finite number."
        )
    integral_leverage = leverage.to_integral_value()
    if leverage != integral_leverage:
        raise HyperliquidLiveTradingConfigurationError(
            "Live order leverage must be a whole number for exchange execution."
        )
    if leverage > max_leverage:
        raise HyperliquidLiveTradingConfigurationError(
            "Live order leverage exceeds the configured maximum."
        )
    return int(integral_leverage)


def apply_live_entry_exchange_leverage(
    exchange: Any,
    *,
    coin: str,
    leverage: int,
) -> dict[str, Any]:
    update_leverage = getattr(exchange, "update_leverage", None)
    if not callable(update_leverage):
        raise HyperliquidLiveOrderRejectedError(
            "Hyperliquid exchange client cannot enforce entry leverage."
        )
    try:
        response = update_leverage(leverage, coin, is_cross=True)
    except Exception as exc:
        raise HyperliquidLiveOrderRejectedError(
            "Hyperliquid leverage update failed before order submission."
        ) from exc
    if not isinstance(response, dict) or response.get("status") != "ok":
        detail = (
            response.get("response") or response.get("error")
            if isinstance(response, dict)
            else response
        )
        raise HyperliquidLiveOrderRejectedError(
            f"Hyperliquid leverage update was rejected before order submission: "
            f"{detail or 'invalid response'}."
        )
    return {
        "coin": coin,
        "leverage": leverage,
        "isCross": True,
        "response": response,
    }


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


def live_order_wire_values(
    intent: TradeIntent,
    *,
    exchange: Any | None = None,
    market_coin: str | None = None,
    min_order_notional_usd: Decimal = Decimal("0"),
    min_order_notional_buffer_usd: Decimal = Decimal("0"),
    adjust_to_min_order: bool = False,
) -> LiveOrderWireValues:
    precision = live_market_precision(exchange, market_coin or intent.coin)
    size_decimals = precision.size_decimals if precision else FALLBACK_SIZE_DECIMALS
    price_decimals = precision.price_decimals if precision else FALLBACK_PRICE_DECIMALS
    limit_price = live_order_wire_price(
        intent.limit_price,
        is_buy=intent.is_buy,
        max_decimal_places=price_decimals,
    )
    size = live_order_wire_size(
        intent.size,
        size_decimals=size_decimals,
        rounding=ROUND_DOWN,
    )
    notional_usd = size * limit_price
    exchange_min_order_notional = max(min_order_notional_usd, Decimal("0"))
    min_order_buffer = max(min_order_notional_buffer_usd, Decimal("0"))
    wire_min_order_notional = (
        exchange_min_order_notional
        if intent.reduce_only
        else exchange_min_order_notional + min_order_buffer
    )

    if (
        adjust_to_min_order
        and wire_min_order_notional > Decimal("0")
        and notional_usd < wire_min_order_notional
    ):
        adjusted_size = max(
            live_order_wire_size(
                intent.size,
                size_decimals=size_decimals,
                rounding=ROUND_UP,
            ),
            live_order_wire_min_size(
                wire_min_order_notional,
                limit_price=limit_price,
                size_decimals=size_decimals,
            ),
        )
        adjusted_notional = adjusted_size * limit_price
        if adjusted_notional >= wire_min_order_notional:
            size = adjusted_size
            notional_usd = adjusted_notional

    if size <= Decimal("0"):
        raise HyperliquidLiveTradingConfigurationError(
            "Live order size is below Hyperliquid lot precision."
        )
    if limit_price <= Decimal("0"):
        raise HyperliquidLiveTradingConfigurationError(
            "Live order limit price is below Hyperliquid tick precision."
        )
    minimum_after_rounding = (
        wire_min_order_notional
        if not intent.reduce_only and adjust_to_min_order
        else exchange_min_order_notional
    )
    if minimum_after_rounding > Decimal("0") and notional_usd < minimum_after_rounding:
        raise HyperliquidLiveTradingConfigurationError(
            "Live order notional is below the configured minimum after lot rounding."
        )

    return LiveOrderWireValues(
        size=size,
        limit_price=limit_price,
        notional_usd=notional_usd,
        size_decimals=size_decimals,
        price_decimals=price_decimals,
    )


def live_market_precision(exchange: Any | None, coin: str) -> LiveMarketPrecision | None:
    info = getattr(exchange, "info", None)
    if info is None:
        return None
    asset = live_asset_id(info, coin)
    size_decimals = live_size_decimals(info, asset=asset, coin=coin)
    if size_decimals is None:
        return None
    max_price_decimals = (
        SPOT_MAX_PRICE_DECIMALS if asset_is_spot(asset) else PERP_MAX_PRICE_DECIMALS
    )
    return LiveMarketPrecision(
        size_decimals=max(size_decimals, 0),
        price_decimals=max(max_price_decimals - size_decimals, 0),
    )


def validate_live_order_market(
    exchange: Any | None,
    coin: str,
    *,
    display_coin: str | None = None,
) -> None:
    info = getattr(exchange, "info", None)
    if info is None or not live_info_has_market_metadata(info):
        return
    if live_asset_id(info, coin) is not None:
        return
    raise HyperliquidLiveOrderRejectedError(
        live_order_market_unavailable_message(display_coin or coin)
    )


def live_info_has_market_metadata(info: Any) -> bool:
    for attr_name in ("name_to_asset", "coin_to_asset", "name_to_coin", "meta"):
        if getattr(info, attr_name, None) is not None:
            return True
    return False


def live_order_market_unavailable_message(coin: str) -> str:
    return f"Live order market is not available for exchange submission: {coin}."


def live_order_exchange_coin(coin: str) -> str:
    value = str(coin or "").strip()
    if ":" not in value:
        return value
    return value.split(":", maxsplit=1)[1].strip()


def resolve_live_order_exchange_coin(exchange: Any | None, coin: str) -> str:
    fallback = live_order_exchange_coin(coin)
    info = getattr(exchange, "info", None)
    if info is None or not live_info_has_market_metadata(info):
        return fallback
    for candidate in live_order_exchange_coin_candidates(coin):
        if live_asset_id(info, candidate) is not None:
            return candidate
    return fallback


def live_order_exchange_coin_candidates(coin: str) -> list[str]:
    value = str(coin or "").strip()
    base = live_order_exchange_coin(value)
    candidates = [base]
    if value and value != base:
        candidates.append(value)
    return unique_values(candidates)


def live_order_perp_dexs(coin: str | None) -> list[str] | None:
    value = str(coin or "").strip()
    if ":" not in value:
        return None
    dex = value.split(":", maxsplit=1)[0].strip()
    if not dex:
        return None
    return [dex]


def live_asset_id(info: Any, coin: str) -> Any | None:
    name_to_asset = getattr(info, "name_to_asset", None)
    if callable(name_to_asset):
        try:
            return name_to_asset(coin)
        except Exception:
            pass

    for attr_name in ("name_to_asset", "coin_to_asset"):
        mapping = getattr(info, attr_name, None)
        asset = mapping_value(mapping, coin)
        if asset is not None:
            return asset

    name_to_coin = getattr(info, "name_to_coin", None)
    canonical_coin = mapping_value(name_to_coin, coin)
    if canonical_coin is not None:
        coin_to_asset = getattr(info, "coin_to_asset", None)
        asset = mapping_value(coin_to_asset, canonical_coin)
        if asset is not None:
            return asset
    return None


def live_size_decimals(info: Any, *, asset: Any | None, coin: str) -> int | None:
    asset_to_sz_decimals = getattr(info, "asset_to_sz_decimals", None)
    value = mapping_value(asset_to_sz_decimals, asset)
    if value is None and asset is not None:
        value = mapping_value(asset_to_sz_decimals, str(asset))
    if value is None:
        meta = getattr(info, "meta", None)
        value = size_decimals_from_meta(meta, coin)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def mapping_value(mapping: Any, key: Any) -> Any | None:
    if key is None or mapping is None:
        return None
    if isinstance(mapping, dict):
        return mapping.get(key)
    if isinstance(mapping, (list, tuple)) and isinstance(key, int) and 0 <= key < len(mapping):
        return mapping[key]
    return None


def size_decimals_from_meta(meta: Any, coin: str) -> Any | None:
    if not isinstance(meta, dict):
        return None
    universe = meta.get("universe")
    if not isinstance(universe, list):
        return None
    for asset in universe:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "") == coin:
            return asset.get("szDecimals")
    return None


def asset_is_spot(asset: Any | None) -> bool:
    try:
        asset_id = int(asset)
    except (TypeError, ValueError):
        return False
    return SPOT_ASSET_OFFSET <= asset_id < BUILDER_PERP_ASSET_OFFSET


def unique_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def live_order_wire_size(
    size: Decimal,
    *,
    size_decimals: int,
    rounding: str,
) -> Decimal:
    return size.quantize(decimal_quantum(size_decimals), rounding=rounding)


def live_order_wire_min_size(
    notional_usd: Decimal,
    *,
    limit_price: Decimal,
    size_decimals: int,
) -> Decimal:
    if limit_price <= Decimal("0"):
        return Decimal("0")
    return live_order_wire_size(
        notional_usd / limit_price,
        size_decimals=size_decimals,
        rounding=ROUND_UP,
    )


def live_order_wire_price(
    price: Decimal,
    *,
    is_buy: bool,
    max_decimal_places: int,
) -> Decimal:
    rounding = ROUND_DOWN if is_buy else ROUND_UP
    significant_price = round_to_significant_digits(
        price,
        digits=PRICE_SIGNIFICANT_DIGITS,
        rounding=rounding,
    )
    return significant_price.quantize(
        decimal_quantum(max_decimal_places),
        rounding=rounding,
    )


def round_to_significant_digits(
    value: Decimal,
    *,
    digits: int,
    rounding: str,
) -> Decimal:
    if value <= Decimal("0"):
        return value
    quantize_exponent = value.adjusted() - digits + 1
    return value.quantize(Decimal("1").scaleb(quantize_exponent), rounding=rounding)


def decimal_quantum(decimal_places: int) -> Decimal:
    return Decimal("1").scaleb(-max(decimal_places, 0))


def live_order_wire_payload(values: LiveOrderWireValues) -> dict[str, Any]:
    return {
        "size": decimal_to_plain_string(values.size),
        "limitPrice": decimal_to_plain_string(values.limit_price),
        "notionalUsd": decimal_to_plain_string(values.notional_usd),
        "sizeDecimals": values.size_decimals,
        "priceDecimals": values.price_decimals,
    }


def decimal_to_plain_string(value: Decimal) -> str:
    return format(value.normalize(), "f")


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
