import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")


@dataclass(frozen=True)
class CachedMarketPrice:
    coin: str
    price: Decimal
    dex: str
    updated_at: datetime


@dataclass(frozen=True)
class MarketPriceCacheResult:
    prices: dict[str, Decimal]
    sources: dict[str, str]
    missing_coins: set[str]


class MarketPriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, CachedMarketPrice] = {}
        self._requested_dexes: set[str] = {""}
        self._lock = asyncio.Lock()

    async def update_mids(
        self,
        mids: dict[str, Any],
        *,
        dex: str = "",
        now: datetime | None = None,
    ) -> int:
        updated_at = now or datetime.now(UTC)
        normalized_dex = normalize_dex(dex)
        updates: dict[str, CachedMarketPrice] = {}
        for raw_coin, raw_price in mids.items():
            coin = str(raw_coin or "").strip()
            price = decimal_or_none(raw_price)
            if not coin or price is None or price <= ZERO:
                continue
            cache_key = cache_key_for_coin(coin, dex=normalized_dex)
            updates[cache_key] = CachedMarketPrice(
                coin=cache_key,
                price=price,
                dex=normalized_dex,
                updated_at=updated_at,
            )

        if not updates:
            return 0

        async with self._lock:
            self._prices.update(updates)
        return len(updates)

    async def get_many(
        self,
        coins: set[str],
        *,
        max_age_seconds: float,
        now: datetime | None = None,
    ) -> MarketPriceCacheResult:
        requested_coins = {str(coin or "").strip() for coin in coins}
        requested_coins.discard("")
        if not requested_coins:
            return MarketPriceCacheResult(prices={}, sources={}, missing_coins=set())

        now = now or datetime.now(UTC)
        prices: dict[str, Decimal] = {}
        sources: dict[str, str] = {}
        missing: set[str] = set()

        async with self._lock:
            for coin in requested_coins:
                cached = self._prices.get(coin)
                if cached is None or price_age_seconds(cached, now=now) > max_age_seconds:
                    missing.add(coin)
                    continue
                prices[coin] = cached.price
                sources[coin] = "websocket_mid"

        await self.request_dexes(dex_from_coin(coin) for coin in missing)
        return MarketPriceCacheResult(
            prices=prices,
            sources=sources,
            missing_coins=missing,
        )

    async def request_dexes(self, dexes: Any) -> set[str]:
        if dexes is None:
            return set()
        values = [dexes] if isinstance(dexes, str) else dexes
        normalized = {normalize_dex(dex) for dex in values}
        normalized.discard("")
        if not normalized:
            return set()
        async with self._lock:
            before = set(self._requested_dexes)
            self._requested_dexes.update(normalized)
            return self._requested_dexes - before

    async def requested_dexes(self) -> set[str]:
        async with self._lock:
            return set(self._requested_dexes)


def cache_key_for_coin(coin: str, *, dex: str) -> str:
    normalized_coin = str(coin or "").strip()
    normalized_dex = normalize_dex(dex)
    if normalized_dex and ":" not in normalized_coin:
        return f"{normalized_dex}:{normalized_coin}"
    return normalized_coin


def price_age_seconds(price: CachedMarketPrice, *, now: datetime) -> float:
    updated_at = price.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return max(0.0, (now - updated_at).total_seconds())


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def dex_from_coin(value: Any) -> str:
    coin = str(value or "").strip()
    if ":" not in coin:
        return ""
    return normalize_dex(coin.split(":", maxsplit=1)[0])


def normalize_dex(value: Any) -> str:
    return str(value or "").strip()
