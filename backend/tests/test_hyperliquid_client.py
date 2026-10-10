import asyncio
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.integrations import hyperliquid_client
from app.integrations.hyperliquid_client import (
    HyperliquidClient,
    HyperliquidInfoRateLimiter,
    RequestPriority,
    info_request_weight,
    info_response_extra_weight,
)


@pytest.mark.asyncio
async def test_user_non_funding_ledger_updates_uses_bounded_info_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HyperliquidClient()
    observed: dict[str, Any] = {}

    async def fake_post_info(payload: dict[str, Any]) -> list[dict[str, Any]]:
        observed.update(payload)
        return [{"delta": {"type": "deposit", "usdc": "100"}, "time": 1500}]

    monkeypatch.setattr(client, "post_info", fake_post_info)

    result = await client.user_non_funding_ledger_updates(
        user="0xuser",
        start_time_ms=1000,
        end_time_ms=2000,
    )

    assert observed == {
        "type": "userNonFundingLedgerUpdates",
        "user": "0xuser",
        "startTime": 1000,
        "endTime": 2000,
    }
    assert len(result) == 1


@pytest.mark.asyncio
async def test_portfolio_uses_user_info_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HyperliquidClient()
    observed: dict[str, Any] = {}

    async def fake_post_info(payload: dict[str, Any]) -> list[list[Any]]:
        observed.update(payload)
        return [["allTime", {"accountValueHistory": [], "pnlHistory": [], "vlm": "0"}]]

    monkeypatch.setattr(client, "post_info", fake_post_info)

    result = await client.portfolio(user="0xuser")

    assert observed == {"type": "portfolio", "user": "0xuser"}
    assert result[0][0] == "allTime"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def limiter_with_clock() -> tuple[HyperliquidInfoRateLimiter, FakeClock]:
    clock = FakeClock()
    return HyperliquidInfoRateLimiter(clock=clock, sleep=clock.sleep), clock


@pytest.mark.parametrize(
    ("payload", "weight"),
    [
        ({"type": "l2Book", "coin": "BTC"}, 2),
        ({"type": "clearinghouseState", "user": "0xuser"}, 2),
        ({"type": "userRole", "user": "0xuser"}, 60),
        ({"type": "userFillsByTime", "user": "0xuser"}, 20),
        ({"type": "somethingNew"}, 20),
    ],
)
def test_info_request_weight_follows_the_documented_weights(
    payload: dict[str, Any],
    weight: int,
) -> None:
    assert info_request_weight(payload) == weight


def test_info_response_extra_weight_counts_returned_items() -> None:
    assert info_response_extra_weight({"type": "userFills"}, [{}] * 45) == 2
    assert info_response_extra_weight({"type": "candleSnapshot"}, [{}] * 130) == 2
    assert info_response_extra_weight({"type": "userFills"}, {"not": "a list"}) == 0
    assert info_response_extra_weight({"type": "clearinghouseState"}, [{}] * 100) == 0


@pytest.mark.asyncio
async def test_rate_limiter_allows_a_burst_then_paces_to_the_budget() -> None:
    limiter, clock = limiter_with_clock()

    # 60 weight per minute refills 1 unit per second with a 15 unit burst.
    await limiter.acquire(15, priority="background", weight_per_minute=60)
    assert clock.sleeps == []

    await limiter.acquire(2, priority="background", weight_per_minute=60)
    assert clock.now == pytest.approx(2)


@pytest.mark.asyncio
async def test_rate_limiter_is_disabled_by_a_zero_budget() -> None:
    limiter, clock = limiter_with_clock()

    for _ in range(100):
        await limiter.acquire(60, priority="background", weight_per_minute=0)

    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_rate_limiter_serves_execution_before_waiting_background_work() -> None:
    clock = FakeClock()

    async def poll(_seconds: float) -> None:
        await asyncio.sleep(0)

    # Waiters poll without moving time; the test moves the clock itself.
    limiter = HyperliquidInfoRateLimiter(clock=clock, sleep=poll)
    await limiter.acquire(15, priority="background", weight_per_minute=60)
    granted: list[str] = []

    async def acquire(priority: RequestPriority) -> None:
        await limiter.acquire(2, priority=priority, weight_per_minute=60)
        granted.append(priority)

    background = asyncio.create_task(acquire("background"))
    await asyncio.sleep(0)
    execution = asyncio.create_task(acquire("execution"))
    await asyncio.sleep(0)

    clock.now = 2
    await asyncio.wait_for(execution, timeout=1)
    assert granted == ["execution"]
    assert not background.done()

    clock.now = 4
    await asyncio.wait_for(background, timeout=1)
    assert granted == ["execution", "background"]


@pytest.mark.asyncio
async def test_rate_limiter_charges_response_weight_against_the_budget() -> None:
    limiter, clock = limiter_with_clock()
    await limiter.acquire(2, priority="background", weight_per_minute=60)

    limiter.charge(13)
    await limiter.acquire(2, priority="background", weight_per_minute=60)

    assert clock.now == pytest.approx(2)


@pytest.mark.asyncio
async def test_post_info_acquires_request_weight_with_client_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    acquired: list[tuple[int, str, int]] = []
    charged: list[int] = []

    class RecordingLimiter:
        async def acquire(self, weight: int, *, priority: str, weight_per_minute: int) -> None:
            acquired.append((weight, priority, weight_per_minute))

        def charge(self, weight: int) -> None:
            charged.append(weight)

    monkeypatch.setattr(hyperliquid_client, "INFO_RATE_LIMITER", RecordingLimiter())
    settings = Settings(_env_file=None, hyperliquid_info_weight_per_minute=300)
    client = HyperliquidClient(settings, priority="execution")
    client._client = httpx.AsyncClient(
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[{}] * 40)),
    )
    try:
        await client.post_info({"type": "userFills", "user": "0xuser"})
    finally:
        await client.aclose()

    assert acquired == [(20, "execution", 300)]
    assert charged == [2]


@pytest.mark.asyncio
async def test_open_client_is_reused_until_closed() -> None:
    client = HyperliquidClient(Settings(_env_file=None)).open()
    pooled = client._client

    assert client.open() is client
    assert client._client is pooled
    await client.aclose()
    assert client._client is None
