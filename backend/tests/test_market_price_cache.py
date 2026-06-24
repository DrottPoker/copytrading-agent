from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services.market_price_cache import MarketPriceCache
from app.services.paper_trading_service import load_execution_market_prices


class FailingMidsClient:
    async def all_mids(self, *, dex: str | None = None) -> dict[str, str]:
        raise AssertionError("HTTP allMids should not be called when cache is fresh.")


class StaticMidsClient:
    async def all_mids(self, *, dex: str | None = None) -> dict[str, str]:
        return {"ETH": "4000"} if dex is None else {}


@pytest.mark.asyncio
async def test_market_price_cache_returns_fresh_default_mid() -> None:
    cache = MarketPriceCache()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    updated = await cache.update_mids({"BTC": "50000.5"}, now=now)
    result = await cache.get_many({"BTC"}, max_age_seconds=2, now=now + timedelta(seconds=1))

    assert updated == 1
    assert result.prices == {"BTC": Decimal("50000.5")}
    assert result.sources == {"BTC": "websocket_mid"}
    assert result.missing_coins == set()


@pytest.mark.asyncio
async def test_market_price_cache_marks_stale_prices_missing() -> None:
    cache = MarketPriceCache()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    await cache.update_mids({"ETH": "4000"}, now=now)
    result = await cache.get_many({"ETH"}, max_age_seconds=2, now=now + timedelta(seconds=3))

    assert result.prices == {}
    assert result.sources == {}
    assert result.missing_coins == {"ETH"}


@pytest.mark.asyncio
async def test_market_price_cache_prefixes_dex_prices() -> None:
    cache = MarketPriceCache()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    await cache.update_mids({"SKHX": "1745"}, dex="xyz", now=now)
    result = await cache.get_many({"xyz:SKHX"}, max_age_seconds=2, now=now)

    assert result.prices == {"xyz:SKHX": Decimal("1745")}
    assert result.sources == {"xyz:SKHX": "websocket_mid"}
    assert result.missing_coins == set()


@pytest.mark.asyncio
async def test_market_price_cache_requests_missing_dex() -> None:
    cache = MarketPriceCache()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    result = await cache.get_many({"xyz:SKHX"}, max_age_seconds=2, now=now)

    assert result.missing_coins == {"xyz:SKHX"}
    assert "xyz" in await cache.requested_dexes()


@pytest.mark.asyncio
async def test_execution_prices_use_fresh_cache_before_http() -> None:
    cache = MarketPriceCache()
    await cache.update_mids({"BTC": "50000"})
    settings = Settings(paper_copy_latency_ms=0)

    result = await load_execution_market_prices(
        client=FailingMidsClient(),
        fills=[{"coin": "BTC"}],
        settings=settings,
        price_cache=cache,
    )

    assert result.prices == {"BTC": Decimal("50000")}
    assert result.sources == {"BTC": "websocket_mid"}


@pytest.mark.asyncio
async def test_execution_prices_fall_back_to_http_when_cache_missing() -> None:
    settings = Settings(paper_copy_latency_ms=0)

    result = await load_execution_market_prices(
        client=StaticMidsClient(),
        fills=[{"coin": "ETH"}],
        settings=settings,
        price_cache=None,
    )

    assert result.prices == {"ETH": Decimal("4000")}
    assert result.sources == {"ETH": "http_mid"}
