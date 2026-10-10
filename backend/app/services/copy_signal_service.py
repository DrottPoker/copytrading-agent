"""Shared reading of source wallet fills, account state, and market prices.

Both copy engines, the paper simulator and live execution, interpret a source
fill the same way: split it into open, add, close, and flip parts, read the
source's equity, leverage, margin mode, and open positions, price the copy from
current mids, and check entry age and price drift. Keeping this here gives the
two engines one definition of what the source did.
"""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeGuard

from app.core.config import Settings
from app.db.models import WalletFill
from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.copy_execution_cache import SourceAccountStateCache
from app.services.live_trading_service import (
    live_spot_usdc_available,
    live_spot_usdc_total,
    user_abstraction_is_unified,
)
from app.services.market_price_cache import MarketPriceCache
from app.services.source_fill_ordering import source_fill_order_key
from app.services.trading_core import MarginMode, TradeSide, safe_leverage
from app.services.wallet_current_state_service import object_or_empty

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
ONE = Decimal("1")
POSITION_EPSILON = Decimal("0.00000001")
BPS_DENOMINATOR = Decimal("10000")


SOURCE_EQUITY_ACTIONS = frozenset({"open", "add", "flip_open"})


@dataclass(frozen=True)
class SourceAccountState:
    dex: str
    perp_equity: Decimal
    leverage_by_coin: dict[str, Decimal]
    positions_by_coin: dict[str, SourceCurrentPosition]
    margin_mode_by_coin: dict[str, MarginMode] = field(default_factory=dict)
    skip_reason: str | None = None


@dataclass(frozen=True)
class SourceCurrentPosition:
    coin: str
    side: str
    size: Decimal


@dataclass(frozen=True)
class SourceFillPart:
    action: str
    side: TradeSide
    source_size: Decimal
    source_notional_usd: Decimal
    sequence_index: int
    close_ratio: Decimal | None = None
    start_position: Decimal | None = None


@dataclass(frozen=True)
class ExecutionContext:
    source_price: Decimal
    observed_price: Decimal
    execution_price: Decimal
    price_drift_bps: Decimal
    slippage_bps: Decimal
    latency_ms: int
    price_source: str


@dataclass(frozen=True)
class ExecutionMarketPrices:
    prices: dict[str, Decimal]
    sources: dict[str, str]


SourceStateCache = SourceAccountStateCache[SourceAccountState]


# Per-batch unified equity lookups, shared by concurrent per-dex state loads.
UnifiedEquityCache = dict[str, "asyncio.Task[Decimal | None]"]


def source_fill_from_wallet_fill(fill: WalletFill) -> dict[str, Any]:
    return {
        "id": str(fill.id),
        "externalFillId": fill.external_fill_id,
        "coin": fill.coin,
        "side": fill.side,
        "price": str(fill.price),
        "size": str(fill.size),
        "notionalUsd": str(fill.notional_usd) if fill.notional_usd is not None else None,
        "feeUsd": str(fill.fee_usd) if fill.fee_usd is not None else None,
        "pnlUsd": str(fill.pnl_usd) if fill.pnl_usd is not None else None,
        "timestampMs": fill.timestamp_ms,
        "sourceTimestampMs": fill.source_timestamp_ms,
        "ingestLatencyMs": fill.ingest_latency_ms,
        "rawJson": fill.raw_json,
    }


def source_state_available_for_reconciliation(
    source_state: SourceAccountState | None,
) -> TypeGuard[SourceAccountState]:
    if source_state is None:
        return False
    return source_state.skip_reason not in {
        "source_account_state_fetch_failed",
        "source_account_margin_summary_missing",
    }


def resolve_source_current_position(
    positions_by_coin: dict[str, SourceCurrentPosition],
    coin: str,
) -> SourceCurrentPosition | None:
    candidates = coin_symbol_candidates(coin)
    for candidate in candidates:
        position = positions_by_coin.get(candidate)
        if position is not None:
            return position

    casefold_index = {
        key.casefold(): position for key, position in positions_by_coin.items() if key.strip()
    }
    for candidate in candidates:
        position = casefold_index.get(candidate.casefold())
        if position is not None:
            return position

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, position in positions_by_coin.items():
        if normalize_coin_symbol(key) in normalized_candidates:
            return position
    return None


async def load_source_account_states(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    fills: list[dict[str, Any]],
    cache: SourceStateCache | None = None,
) -> dict[str, SourceAccountState]:
    """Load source account state per perp dex, reusing cached state when it is safe.

    Cached state is only used when every fill is a pure entry and the cache
    already holds leverage and margin mode for each copied coin. Exits, flips,
    and coins the source did not hold yet always read fresh state, so close
    sizing and new margin settings never depend on an older snapshot.
    """

    dexes = {dex_from_coin(fill.get("coin")) for fill in fills}
    if not dexes:
        dexes.add("")

    states: dict[str, SourceAccountState] = {}
    missing_dexes: list[str] = []
    entry_only = fills_are_entry_only(fills)
    for dex in sorted(dexes):
        cached = cache.get(source_wallet, dex) if cache is not None and entry_only else None
        if cached is not None and cached_state_covers_fills(cached.state, fills=fills, dex=dex):
            states[dex] = cached.state
        else:
            missing_dexes.append(dex)

    unified_equity_cache: UnifiedEquityCache = {}
    requested_at = cache.now() if cache is not None else 0.0
    fetched = await asyncio.gather(
        *(
            load_source_account_state(
                client=client,
                source_wallet=source_wallet,
                dex=dex,
                unified_equity_cache=unified_equity_cache,
            )
            for dex in missing_dexes
        )
    )
    for dex, state in zip(missing_dexes, fetched, strict=True):
        states[dex] = state
        if cache is not None and state.skip_reason != "source_account_state_fetch_failed":
            cache.store(source_wallet, dex, state, requested_at=requested_at)
    return states


def fills_are_entry_only(fills: list[dict[str, Any]]) -> bool:
    parts = [part for fill in fills for part in plan_source_fill(fill)]
    return bool(parts) and all(part.action in SOURCE_EQUITY_ACTIONS for part in parts)


def cached_state_covers_fills(
    state: SourceAccountState,
    *,
    fills: list[dict[str, Any]],
    dex: str,
) -> bool:
    if state.skip_reason is not None:
        return False
    for fill in fills:
        coin = str(fill.get("coin") or "")
        if dex_from_coin(coin) != dex:
            continue
        if resolve_coin_decimal(state.leverage_by_coin, coin) is None:
            return False
        if resolve_coin_margin_mode(state.margin_mode_by_coin, coin) is None:
            return False
    return True


async def warm_source_state_cache(
    *,
    client: HyperliquidClient,
    cache: SourceStateCache,
    source_wallets: set[str],
    refresh_after_seconds: float,
) -> int:
    """Refetch aging source states so later adds can skip the network lookup.

    Only states with open source positions are kept warm, because entries on
    coins the source does not hold yet always need a fresh state anyway.
    Returns the number of states fetched.
    """

    cache.retain(source_wallets)
    due = cache.due_for_refresh(
        source_wallets,
        refresh_after_seconds=refresh_after_seconds,
        keep_warm=lambda state: bool(state.positions_by_coin),
    )
    if not due:
        return 0
    unified_equity_cache: UnifiedEquityCache = {}
    requested_at = cache.now()
    fetched = await asyncio.gather(
        *(
            load_source_account_state(
                client=client,
                source_wallet=wallet,
                dex=dex,
                unified_equity_cache=unified_equity_cache,
            )
            for wallet, dex in due
        )
    )
    for (wallet, dex), state in zip(due, fetched, strict=True):
        if state.skip_reason != "source_account_state_fetch_failed":
            cache.store(wallet, dex, state, requested_at=requested_at)
    return len(due)


async def load_source_account_state(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    dex: str,
    unified_equity_cache: UnifiedEquityCache | None = None,
) -> SourceAccountState:
    try:
        clearinghouse_state = await client.clearinghouse_state(
            user=source_wallet,
            dex=dex or None,
        )
    except Exception as exc:
        logger.warning(
            "paper copy source account fetch failed wallet=%s dex=%s error=%s",
            source_wallet,
            dex or "default",
            exc,
        )
        return SourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin={},
            positions_by_coin={},
            skip_reason="source_account_state_fetch_failed",
        )

    leverage_by_coin = parse_source_leverages(clearinghouse_state)
    margin_mode_by_coin = parse_source_margin_modes(clearinghouse_state)
    positions_by_coin = parse_source_current_positions(clearinghouse_state)
    margin_summary_raw = clearinghouse_state.get("marginSummary")
    if not isinstance(margin_summary_raw, dict):
        return SourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            margin_mode_by_coin=margin_mode_by_coin,
            skip_reason="source_account_margin_summary_missing",
        )

    perp_equity = decimal_or_none(margin_summary_raw.get("accountValue"))
    if perp_equity is None:
        return SourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            margin_mode_by_coin=margin_mode_by_coin,
            skip_reason="source_perp_equity_missing",
        )
    if perp_equity <= ZERO:
        unified_equity = await load_source_unified_equity_cached(
            client=client,
            source_wallet=source_wallet,
            cache=unified_equity_cache,
        )
        if unified_equity is not None and unified_equity > ZERO:
            return SourceAccountState(
                dex=dex,
                perp_equity=unified_equity,
                leverage_by_coin=leverage_by_coin,
                positions_by_coin=positions_by_coin,
                margin_mode_by_coin=margin_mode_by_coin,
            )
        return SourceAccountState(
            dex=dex,
            perp_equity=ZERO,
            leverage_by_coin=leverage_by_coin,
            positions_by_coin=positions_by_coin,
            margin_mode_by_coin=margin_mode_by_coin,
            skip_reason="source_perp_equity_zero",
        )

    return SourceAccountState(
        dex=dex,
        perp_equity=perp_equity,
        leverage_by_coin=leverage_by_coin,
        positions_by_coin=positions_by_coin,
        margin_mode_by_coin=margin_mode_by_coin,
    )


async def load_source_unified_equity_cached(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    cache: UnifiedEquityCache | None,
) -> Decimal | None:
    if cache is None:
        return await load_source_unified_equity(client=client, source_wallet=source_wallet)
    key = source_wallet.lower()
    task = cache.get(key)
    if task is None:
        task = asyncio.create_task(
            load_source_unified_equity(client=client, source_wallet=source_wallet)
        )
        cache[key] = task
    return await task


async def load_source_unified_equity(
    *,
    client: HyperliquidClient,
    source_wallet: str,
) -> Decimal | None:
    try:
        user_abstraction = await client.user_abstraction(user=source_wallet)
    except Exception as exc:
        logger.warning(
            "paper copy source abstraction fetch failed wallet=%s error=%s",
            source_wallet,
            exc,
        )
        return None
    if not user_abstraction_is_unified(user_abstraction):
        return None
    try:
        spot_state = await client.spot_clearinghouse_state(user=source_wallet)
    except Exception as exc:
        logger.warning(
            "paper copy source spot state fetch failed wallet=%s error=%s",
            source_wallet,
            exc,
        )
        return None
    total = live_spot_usdc_total(spot_state)
    if total > ZERO:
        return total
    available = live_spot_usdc_available(spot_state)
    return available if available > ZERO else None


def parse_source_leverages(payload: dict[str, Any]) -> dict[str, Decimal]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    leverages: dict[str, Decimal] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        leverage = object_or_empty(position.get("leverage"))
        leverage_value = decimal_or_none(leverage.get("value"))
        if coin and leverage_value is not None and leverage_value > ZERO:
            leverages[coin] = leverage_value
    return leverages


def parse_source_margin_modes(payload: dict[str, Any]) -> dict[str, MarginMode]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    margin_modes: dict[str, MarginMode] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        leverage = object_or_empty(position.get("leverage"))
        margin_mode = normalize_margin_mode(leverage.get("type"))
        if coin and margin_mode is not None:
            margin_modes[coin] = margin_mode
    return margin_modes


def normalize_margin_mode(value: Any) -> MarginMode | None:
    normalized = str(value or "").strip().casefold()
    if normalized == "cross":
        return "cross"
    if normalized == "isolated":
        return "isolated"
    return None


def parse_source_current_positions(
    payload: dict[str, Any],
) -> dict[str, SourceCurrentPosition]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    positions: dict[str, SourceCurrentPosition] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        signed_size = decimal_or_none(position.get("szi"))
        if not coin or signed_size is None or signed_size.copy_abs() <= POSITION_EPSILON:
            continue
        positions[coin] = SourceCurrentPosition(
            coin=coin,
            side="long" if signed_size > ZERO else "short",
            size=signed_size.copy_abs(),
        )
    return positions


async def load_execution_market_prices(
    *,
    client: HyperliquidClient,
    fills: list[dict[str, Any]],
    settings: Settings,
    latency_ms: int | None = None,
    price_cache: MarketPriceCache | None = None,
) -> ExecutionMarketPrices:
    if not settings.trading_copy_use_live_mid_price:
        return ExecutionMarketPrices(prices={}, sources={})
    resolved_latency_ms = settings.paper_copy_latency_ms if latency_ms is None else latency_ms
    if resolved_latency_ms > 0:
        await asyncio.sleep(resolved_latency_ms / 1000)

    coins = {str(fill.get("coin") or "") for fill in fills}
    coins.discard("")
    if not coins:
        return ExecutionMarketPrices(prices={}, sources={})

    market_prices: dict[str, Decimal] = {}
    price_sources: dict[str, str] = {}
    missing_coins = set(coins)
    if price_cache is not None and settings.trading_copy_market_price_cache_enabled:
        cached = await price_cache.get_many(
            missing_coins,
            max_age_seconds=settings.trading_copy_market_price_cache_stale_seconds,
        )
        market_prices.update(cached.prices)
        price_sources.update(cached.sources)
        missing_coins = cached.missing_coins

    if not missing_coins:
        return ExecutionMarketPrices(prices=market_prices, sources=price_sources)

    try:
        mids = await client.all_mids()
    except Exception as exc:
        logger.warning("paper copy allMids fetch failed error=%s", exc)
        mids = {}

    for coin in missing_coins:
        price = resolve_coin_decimal(mids, coin)
        if price is not None and price > ZERO:
            market_prices[coin] = price
            price_sources[coin] = "http_mid"

    missing_dexes = {
        dex_from_coin(coin)
        for coin in missing_coins
        if coin not in market_prices and dex_from_coin(coin)
    }
    if price_cache is not None and settings.trading_copy_market_price_cache_enabled:
        await price_cache.request_dexes(missing_dexes)
    for dex in sorted(missing_dexes):
        dex_prices = await load_dex_market_prices(client=client, dex=dex)
        for coin in missing_coins:
            if coin in market_prices or dex_from_coin(coin) != dex:
                continue
            price = resolve_coin_decimal(dex_prices, coin)
            if price is not None and price > ZERO:
                market_prices[coin] = price
                price_sources[coin] = "http_mid"
    return ExecutionMarketPrices(prices=market_prices, sources=price_sources)


async def load_dex_market_prices(
    *,
    client: HyperliquidClient,
    dex: str,
) -> dict[str, Decimal]:
    try:
        mids = await client.all_mids(dex=dex)
    except Exception as exc:
        logger.warning("paper copy dex allMids fetch failed dex=%s error=%s", dex, exc)
    else:
        prices = market_prices_from_mids(mids)
        if prices:
            return prices

    try:
        payload = await client.meta_and_asset_ctxs(dex=dex)
    except Exception as exc:
        logger.warning("paper copy dex market price fetch failed dex=%s error=%s", dex, exc)
        return {}
    return market_prices_from_meta_and_asset_ctxs(payload)


def market_prices_from_mids(mids: dict[str, Any]) -> dict[str, Decimal]:
    prices: dict[str, Decimal] = {}
    for coin, raw_price in mids.items():
        price = decimal_or_none(raw_price)
        if coin and price is not None and price > ZERO:
            prices[str(coin)] = price
    return prices


def market_prices_from_meta_and_asset_ctxs(payload: list[Any]) -> dict[str, Decimal]:
    if len(payload) < 2:
        return {}
    meta = payload[0]
    contexts = payload[1]
    if not isinstance(meta, dict) or not isinstance(contexts, list):
        return {}
    universe = meta.get("universe")
    if not isinstance(universe, list):
        return {}

    prices: dict[str, Decimal] = {}
    for asset, context in zip(universe, contexts, strict=False):
        if not isinstance(asset, dict) or not isinstance(context, dict):
            continue
        coin = str(asset.get("name") or "")
        price = (
            decimal_or_none(context.get("midPx"))
            or decimal_or_none(context.get("markPx"))
            or decimal_or_none(context.get("oraclePx"))
        )
        if coin and price is not None and price > ZERO:
            prices[coin] = price
    return prices


def plan_source_fill(fill: dict[str, Any]) -> list[SourceFillPart]:
    raw_json = raw_json_from_fill(fill)
    direction = str(raw_json.get("dir") or "")
    source_size = decimal_or_zero(fill.get("size"))
    source_price = decimal_or_zero(fill.get("price"))
    source_notional = decimal_or_zero(fill.get("notionalUsd")) or source_price * source_size
    start_position = decimal_or_none(raw_json.get("startPosition"))

    if source_size <= ZERO or source_price <= ZERO or source_notional <= ZERO:
        return []
    if direction == "Open Long":
        return [
            SourceFillPart(
                action="open",
                side="long",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Open Short":
        return [
            SourceFillPart(
                action="open",
                side="short",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Close Long":
        return close_parts(
            side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Close Short":
        return close_parts(
            side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Long > Short":
        return flip_parts(
            close_side="long",
            open_side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    if direction == "Short > Long":
        return flip_parts(
            close_side="short",
            open_side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    return []


def close_parts(
    *,
    side: TradeSide,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
    action: str,
) -> list[SourceFillPart]:
    close_ratio = close_ratio_from_start_position(source_size, start_position)
    return [
        SourceFillPart(
            action=action,
            side=side,
            source_size=source_size,
            source_notional_usd=source_notional,
            sequence_index=0,
            close_ratio=close_ratio,
            start_position=start_position,
        )
    ]


def flip_parts(
    *,
    close_side: TradeSide,
    open_side: TradeSide,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
) -> list[SourceFillPart]:
    if start_position is None:
        return close_parts(
            side=close_side,
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="flip_close",
        )

    close_size = min(source_size, start_position.copy_abs())
    open_size = max(source_size - close_size, ZERO)
    parts: list[SourceFillPart] = []
    if close_size > ZERO:
        close_notional = proportional_value(source_notional, close_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_close",
                side=close_side,
                source_size=close_size,
                source_notional_usd=close_notional,
                sequence_index=0,
                close_ratio=close_ratio_from_start_position(close_size, start_position),
                start_position=start_position,
            )
        )
    if open_size > ZERO:
        open_notional = proportional_value(source_notional, open_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_open",
                side=open_side,
                source_size=open_size,
                source_notional_usd=open_notional,
                sequence_index=1,
                start_position=ZERO,
            )
        )
    return parts


def leverage_for_fill(
    *,
    fill: dict[str, Any],
    source_leverages: dict[str, Decimal],
) -> Decimal:
    coin = str(fill.get("coin") or "")
    return safe_leverage(resolve_coin_decimal(source_leverages, coin))


def resolve_coin_decimal(values_by_coin: dict[str, Any], coin: str) -> Decimal | None:
    candidates = coin_symbol_candidates(coin)
    if not candidates:
        return None

    for candidate in candidates:
        value = decimal_or_none(values_by_coin.get(candidate))
        if value is not None:
            return value

    casefold_index = {
        str(key).casefold(): value for key, value in values_by_coin.items() if str(key).strip()
    }
    for candidate in candidates:
        value = decimal_or_none(casefold_index.get(candidate.casefold()))
        if value is not None:
            return value

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, raw_value in values_by_coin.items():
        if normalize_coin_symbol(str(key)) in normalized_candidates:
            value = decimal_or_none(raw_value)
            if value is not None:
                return value
    return None


def resolve_coin_margin_mode(
    values_by_coin: dict[str, MarginMode],
    coin: str,
) -> MarginMode | None:
    candidates = coin_symbol_candidates(coin)
    if not candidates:
        return None

    for candidate in candidates:
        value = normalize_margin_mode(values_by_coin.get(candidate))
        if value is not None:
            return value

    casefold_index = {
        str(key).casefold(): value for key, value in values_by_coin.items() if str(key).strip()
    }
    for candidate in candidates:
        value = normalize_margin_mode(casefold_index.get(candidate.casefold()))
        if value is not None:
            return value

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, raw_value in values_by_coin.items():
        if normalize_coin_symbol(str(key)) in normalized_candidates:
            value = normalize_margin_mode(raw_value)
            if value is not None:
                return value
    return None


def coin_symbol_candidates(coin: str) -> list[str]:
    value = str(coin or "").strip()
    if not value:
        return []

    candidates = [value]
    if ":" in value:
        candidates.append(value.rsplit(":", maxsplit=1)[-1])
    if "/" in value:
        candidates.append(value.split("/", maxsplit=1)[0])

    expanded: list[str] = []
    for candidate in candidates:
        if candidate:
            expanded.append(candidate)
            expanded.append(candidate.upper())
            expanded.append(candidate.lower())
    return unique_strings(expanded)


def normalize_coin_symbol(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def close_ratio_from_start_position(
    source_size: Decimal,
    start_position: Decimal | None,
) -> Decimal | None:
    if start_position is None or start_position.copy_abs() <= ZERO:
        return None
    return min(source_size / start_position.copy_abs(), Decimal("1"))


def sorted_source_fills(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(fills, key=source_fill_sort_key)


def source_fill_sort_key(
    fill: dict[str, Any],
) -> tuple[int, str, int, Decimal, int, Decimal, str]:
    """Return the durable ordering used for every source fill lifecycle."""

    return source_fill_order_key(fill)


def part_requires_source_equity(part: SourceFillPart) -> bool:
    return part.action in SOURCE_EQUITY_ACTIONS


def is_preexisting_source_add(start_position: Decimal | None, *, side: str) -> bool:
    if start_position is None:
        return False
    if side == "long":
        return start_position > POSITION_EPSILON
    return start_position < -POSITION_EPSILON


def proportional_value(value: Decimal, part: Decimal, total: Decimal) -> Decimal:
    if total <= ZERO:
        return ZERO
    return value * part / total


def build_execution_context(
    *,
    fill: dict[str, Any],
    part: SourceFillPart,
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    slippage_bps: Decimal | None = None,
    latency_ms: int | None = None,
) -> ExecutionContext | None:
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return None

    coin = str(fill.get("coin") or "")
    observed_price = (
        market_prices.prices.get(coin) if settings.trading_copy_use_live_mid_price else None
    )
    if observed_price is None:
        if settings.trading_copy_use_live_mid_price:
            return None
        observed_price = source_price
    if observed_price <= ZERO:
        return None

    resolved_slippage_bps = (
        settings.paper_copy_slippage_bps if slippage_bps is None else slippage_bps
    )
    resolved_latency_ms = settings.paper_copy_latency_ms if latency_ms is None else latency_ms
    execution_price = apply_adverse_slippage(
        price=observed_price,
        side=part.side,
        action=part.action,
        slippage_bps=resolved_slippage_bps,
    )
    if execution_price <= ZERO:
        return None

    return ExecutionContext(
        source_price=source_price,
        observed_price=observed_price,
        execution_price=execution_price,
        price_drift_bps=adverse_price_drift_bps(
            source_price=source_price,
            observed_price=observed_price,
            side=part.side,
            action=part.action,
        ),
        slippage_bps=resolved_slippage_bps,
        latency_ms=resolved_latency_ms,
        price_source=(
            market_prices.sources.get(coin, "live_mid")
            if settings.trading_copy_use_live_mid_price
            else "source_fill"
        ),
    )


def apply_adverse_slippage(
    *,
    price: Decimal,
    side: str,
    action: str,
    slippage_bps: Decimal,
) -> Decimal:
    adjustment = slippage_bps / BPS_DENOMINATOR
    if is_buy_execution(side=side, action=action):
        return price * (Decimal("1") + adjustment)
    return price * max(Decimal("1") - adjustment, ZERO)


def is_buy_execution(*, side: str, action: str) -> bool:
    is_open = action in {"open", "add", "flip_open"}
    if side == "long":
        return is_open
    return not is_open


def adverse_price_drift_bps(
    *,
    source_price: Decimal,
    observed_price: Decimal,
    side: str,
    action: str,
) -> Decimal:
    if source_price <= ZERO:
        return ZERO
    drift = observed_price - source_price
    if is_buy_execution(side=side, action=action):
        adverse_drift = max(drift, ZERO)
    else:
        adverse_drift = max(-drift, ZERO)
    return adverse_drift / source_price * BPS_DENOMINATOR


def raw_json_from_fill(fill: dict[str, Any]) -> dict[str, Any]:
    raw_json = fill.get("rawJson")
    return raw_json if isinstance(raw_json, dict) else {}


def dex_from_coin(value: Any) -> str:
    coin = str(value or "").strip()
    if ":" not in coin:
        return ""
    dex = coin.split(":", maxsplit=1)[0].strip()
    return dex


def side_from_fill_direction(value: Any) -> TradeSide:
    direction = str(value or "")
    if "Short" in direction:
        return "short"
    return "long"


def fill_datetime(fill: dict[str, Any]) -> datetime:
    timestamp_ms = int(fill.get("timestampMs") or 0)
    if timestamp_ms <= 0:
        return datetime.now(UTC)
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def source_fill_age_exceeds_entry_limit(
    fill: dict[str, Any],
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    max_age_seconds = settings.trading_copy_max_entry_age_seconds
    if max_age_seconds <= 0:
        return False
    age_seconds = source_fill_age_seconds(fill, now=now)
    return age_seconds is not None and age_seconds > max_age_seconds


def source_fill_age_seconds(
    fill: dict[str, Any],
    *,
    now: datetime | None = None,
) -> float | None:
    timestamp_ms = int(fill.get("timestampMs") or 0)
    if timestamp_ms <= 0:
        return None
    observed_at = now or datetime.now(UTC)
    fill_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
    return (observed_at - fill_at).total_seconds()


def timestamp_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, ValueError:
        return None


def decimal_or_zero(value: Any) -> Decimal:
    return decimal_or_none(value) or ZERO


async def load_mark_prices(
    *,
    client: HyperliquidClient,
    coins: Iterable[str],
    price_cache: MarketPriceCache | None = None,
    max_age_seconds: float = 15.0,
) -> dict[str, Decimal]:
    """Current mids for the coins, from the cache first and then allMids per dex."""

    wanted = {coin for coin in coins if coin}
    if not wanted:
        return {}
    market_prices: dict[str, Decimal] = {}
    missing_coins = set(wanted)
    if price_cache is not None:
        cached = await price_cache.get_many(wanted, max_age_seconds=max_age_seconds)
        market_prices.update(cached.prices)
        missing_coins = cached.missing_coins
        if not missing_coins:
            return market_prices

    try:
        mids = await client.all_mids()
    except Exception as exc:
        logger.warning("allMids fetch failed error=%s", exc)
        mids = {}
    if price_cache is not None:
        await price_cache.update_mids(mids)
    for coin in missing_coins:
        price = resolve_coin_decimal(mids, coin)
        if price is not None and price > ZERO:
            market_prices[coin] = price

    missing_dexes = {
        dex_from_coin(coin)
        for coin in missing_coins
        if coin not in market_prices and dex_from_coin(coin)
    }
    for dex in sorted(missing_dexes):
        dex_prices = await load_dex_market_prices(client=client, dex=dex)
        if price_cache is not None:
            await price_cache.update_mids(dex_prices, dex=dex)
        for coin in missing_coins:
            if coin in market_prices or dex_from_coin(coin) != dex:
                continue
            price = resolve_coin_decimal(dex_prices, coin)
            if price is not None and price > ZERO:
                market_prices[coin] = price
    return market_prices
