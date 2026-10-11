"""Shared Hyperliquid info budget on a real Redis."""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from redis.asyncio import Redis

from app.core.config import Settings
from app.integrations.hyperliquid_info_budget import (
    INFO_BUDGET,
    SharedInfoBudget,
    close_shared_info_budget,
    configure_shared_info_budget,
)

pytestmark = pytest.mark.integration

KEY = "hyperliquid:info_budget:test"


def budget(client: Redis) -> SharedInfoBudget:
    return SharedInfoBudget(
        client,
        key=KEY,
        weight_per_minute=600,
        burst_weight=200,
        execution_reserve_weight=60,
    )


@pytest_asyncio.fixture
async def redis_clients(integration_redis: Redis) -> AsyncIterator[tuple[Redis, Redis]]:
    # Two clients stand in for two processes on the same host.
    url = os.environ["TEST_REDIS_URL"]
    first = Redis.from_url(url, decode_responses=True)
    second = Redis.from_url(url, decode_responses=True)
    try:
        yield first, second
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_background_work_leaves_the_reserve_to_execution(
    redis_clients: tuple[Redis, Redis],
) -> None:
    maintenance, trading = (budget(client) for client in redis_clients)

    # A full bucket of 200 grants 120, leaving 80.
    assert await maintenance.take(120, priority="background") == 0
    # Background work may not go below the 60 reserve, so 30 more must wait.
    background_wait = await maintenance.take(30, priority="background")
    # Copy execution in another process may use the reserve.
    assert await trading.take(30, priority="execution") == 0

    # 10 units refill per second at 600 per minute; 30 + 60 - 80 = 10 missing.
    assert background_wait == pytest.approx(1.0, abs=0.1)


@pytest.mark.asyncio
async def test_refunds_return_weight_to_every_process(
    redis_clients: tuple[Redis, Redis],
) -> None:
    maintenance, trading = (budget(client) for client in redis_clients)
    assert await maintenance.take(120, priority="background") == 0
    assert await trading.take(30, priority="background") > 0

    # The page held few rows, so most of the reservation goes back.
    await maintenance.adjust(100)

    assert await trading.take(30, priority="background") == 0


@pytest.mark.asyncio
async def test_the_process_budget_uses_redis_when_configured(
    integration_redis: Redis,
) -> None:
    settings = Settings(_env_file=None, hyperliquid_network="testnet").model_copy(
        update={"redis_url": os.environ["TEST_REDIS_URL"]}
    )
    configure_shared_info_budget(settings)
    try:
        grant = await INFO_BUDGET.acquire(
            20,
            priority="background",
            request_type="portfolio",
            weight_per_minute=settings.hyperliquid_info_weight_per_minute,
        )
        await INFO_BUDGET.settle(grant, used_weight=20)
        tokens = await integration_redis.hget("hyperliquid:info_budget:testnet", "tokens")
    finally:
        await close_shared_info_budget()

    assert grant.mode == "shared"
    assert tokens is not None
    assert float(tokens) == pytest.approx(255, abs=1)


@pytest.mark.asyncio
async def test_an_unreachable_redis_falls_back_to_the_local_budget() -> None:
    # REDIS_URL from the environment wins over constructor values, so it is replaced.
    # Nothing listens on port 1, so the connection fails within its short timeout.
    settings = Settings(_env_file=None).model_copy(update={"redis_url": "redis://127.0.0.1:1/0"})
    configure_shared_info_budget(settings)
    try:
        grant = await INFO_BUDGET.acquire(
            2,
            priority="execution",
            request_type="l2Book",
            weight_per_minute=settings.hyperliquid_info_weight_per_minute,
        )
        snapshot = INFO_BUDGET.usage_snapshot(
            weight_per_minute=settings.hyperliquid_info_weight_per_minute
        )
    finally:
        await close_shared_info_budget()

    assert grant.mode == "local"
    assert snapshot["budgetMode"] == "local"
    assert snapshot["budgetWeightPerMinute"] == 550
