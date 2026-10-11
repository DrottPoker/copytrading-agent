import asyncio
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.integrations import hyperliquid_client
from app.integrations.hyperliquid_client import (
    HyperliquidClient,
    HyperliquidRateLimitError,
    info_request_weight,
    info_response_extra_weight,
)
from app.integrations.hyperliquid_info_budget import (
    HyperliquidInfoBudget,
    HyperliquidInfoRateLimiter,
    InfoBudgetGrant,
    InfoUsageMeter,
    RequestPriority,
    SharedInfoBudget,
    SharedInfoBudgetUnavailableError,
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


class RecordingBudget:
    def __init__(self) -> None:
        self.acquired: list[tuple[int, str, str, int]] = []
        self.settled: list[tuple[int, int]] = []
        self.rate_limited = 0

    async def acquire(
        self,
        weight: int,
        *,
        priority: RequestPriority,
        request_type: str,
        weight_per_minute: int,
    ) -> InfoBudgetGrant:
        self.acquired.append((weight, priority, request_type, weight_per_minute))
        return InfoBudgetGrant(weight=weight, priority=priority, request_type=request_type)

    async def settle(self, grant: InfoBudgetGrant, *, used_weight: int) -> None:
        self.settled.append((grant.weight, used_weight))

    def record_rate_limited(self) -> None:
        self.rate_limited += 1


def client_with_responses(
    settings: Settings,
    responses: list[httpx.Response],
    *,
    priority: RequestPriority = "background",
) -> HyperliquidClient:
    client = HyperliquidClient(settings, priority=priority)
    client._client = httpx.AsyncClient(
        base_url="https://example.invalid",
        transport=httpx.MockTransport(lambda _request: responses.pop(0)),
    )
    return client


@pytest.mark.asyncio
async def test_post_info_acquires_request_weight_with_client_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = RecordingBudget()
    monkeypatch.setattr(hyperliquid_client, "INFO_BUDGET", budget)
    settings = Settings(_env_file=None, hyperliquid_info_weight_per_minute=300)
    client = client_with_responses(
        settings,
        [httpx.Response(200, json=[{}] * 40)],
        priority="execution",
    )
    try:
        await client.post_info({"type": "userFills", "user": "0xuser"})
    finally:
        await client.aclose()

    # A full page would cost 100 extra, so that is reserved and 98 returned.
    assert budget.acquired == [(120, "execution", "userFills", 300)]
    assert budget.settled == [(120, 22)]


@pytest.mark.asyncio
async def test_rate_limited_attempts_are_settled_once_and_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = RecordingBudget()
    monkeypatch.setattr(hyperliquid_client, "INFO_BUDGET", budget)
    settings = Settings(
        _env_file=None,
        hyperliquid_info_request_retries=1,
        hyperliquid_info_retry_base_delay_seconds=0,
    )
    client = client_with_responses(
        settings,
        [httpx.Response(429), httpx.Response(429)],
    )
    try:
        with pytest.raises(HyperliquidRateLimitError):
            await client.post_info({"type": "portfolio", "user": "0xuser"})
    finally:
        await client.aclose()

    assert budget.settled == [(20, 20), (20, 20)]
    assert budget.rate_limited == 2


def test_usage_meter_reports_complete_minutes_by_priority_and_type() -> None:
    now = [600.0]
    meter = InfoUsageMeter(clock=lambda: now[0])
    meter.record_request(priority="execution", wait_seconds=0.5)
    meter.record_weight(priority="execution", request_type="l2Book", weight=2)
    meter.record_request(priority="background", wait_seconds=3)
    meter.record_weight(priority="background", request_type="userFillsByTime", weight=120)
    meter.record_rate_limited()
    # The current minute is still running, so it is not reported yet.
    assert (
        meter.snapshot(budget_mode="local", budget_weight_per_minute=550)["weightLastMinute"] == 0
    )

    now[0] = 660.0
    snapshot = meter.snapshot(budget_mode="local", budget_weight_per_minute=550)

    assert snapshot["weightLastMinute"] == 122
    assert snapshot["peakWeightPerMinute60m"] == 122
    assert snapshot["weightPerMinute15m"] == pytest.approx(122 / 15, abs=0.1)
    assert snapshot["executionWeightPerMinute15m"] == pytest.approx(2 / 15, abs=0.1)
    assert snapshot["requests15m"] == 2
    assert snapshot["executionWaitSeconds15m"] == 0.5
    assert snapshot["backgroundWaitSeconds15m"] == 3
    assert snapshot["rateLimited60m"] == 1
    assert snapshot["topRequestTypes15m"][0] == {"type": "userFillsByTime", "weight": 120}


def test_usage_meter_forgets_minutes_older_than_an_hour() -> None:
    now = [0.0]
    meter = InfoUsageMeter(clock=lambda: now[0])
    meter.record_weight(priority="background", request_type="portfolio", weight=20)

    now[0] = 61 * 60.0
    meter.record_weight(priority="background", request_type="portfolio", weight=20)
    now[0] = 62 * 60.0

    assert (
        meter.snapshot(budget_mode="local", budget_weight_per_minute=550)["peakWeightPerMinute60m"]
        == 20
    )


@pytest.mark.asyncio
async def test_budget_measures_waits_and_charges_response_weight() -> None:
    limiter, clock = limiter_with_clock()
    wall_clock = [600.0]
    budget = HyperliquidInfoBudget(
        limiter=limiter,
        meter=InfoUsageMeter(clock=lambda: wall_clock[0]),
        clock=clock,
    )

    first = await budget.acquire(
        15,
        priority="background",
        request_type="userFills",
        weight_per_minute=60,
    )
    await budget.settle(first, used_weight=20)
    second = await budget.acquire(
        2,
        priority="background",
        request_type="portfolio",
        weight_per_minute=60,
    )
    await budget.settle(second, used_weight=2)
    wall_clock[0] = 660.0
    snapshot = budget.usage_snapshot(weight_per_minute=60)

    # The response weight of the first request delays the second by its extra 5 units.
    assert clock.now == pytest.approx(7)
    assert snapshot["weightLastMinute"] == 22
    assert snapshot["backgroundWaitSeconds15m"] == pytest.approx(7)


@pytest.mark.asyncio
async def test_open_client_is_reused_until_closed() -> None:
    client = HyperliquidClient(Settings(_env_file=None)).open()
    pooled = client._client

    assert client.open() is client
    assert client._client is pooled
    await client.aclose()
    assert client._client is None


class FakeSharedBudget:
    """Stands in for the Redis bucket: grants takes until told Redis is down."""

    weight_per_minute = 1100

    def __init__(self) -> None:
        self.available = True
        self.takes: list[tuple[int, str]] = []
        self.adjustments: list[int] = []
        self.waits: list[float] = []

    async def take(self, weight: int, *, priority: RequestPriority) -> float:
        if not self.available:
            raise SharedInfoBudgetUnavailableError("connection refused")
        self.takes.append((weight, priority))
        return self.waits.pop(0) if self.waits else 0.0

    async def adjust(self, weight: int) -> None:
        if not self.available:
            raise SharedInfoBudgetUnavailableError("connection refused")
        self.adjustments.append(weight)


def budget_with_shared(shared: FakeSharedBudget) -> tuple[HyperliquidInfoBudget, FakeClock]:
    clock = FakeClock()
    budget = HyperliquidInfoBudget(
        limiter=HyperliquidInfoRateLimiter(clock=clock, sleep=clock.sleep),
        meter=InfoUsageMeter(clock=lambda: 600.0),
        clock=clock,
        sleep=clock.sleep,
    )
    budget.use_shared(shared)  # type: ignore[arg-type]
    return budget, clock


@pytest.mark.asyncio
async def test_shared_budget_takes_from_redis_and_refunds_unused_weight() -> None:
    shared = FakeSharedBudget()
    shared.waits = [0.4, 0.0]
    budget, clock = budget_with_shared(shared)

    grant = await budget.acquire(
        120,
        priority="background",
        request_type="userFillsByTime",
        weight_per_minute=550,
    )
    await budget.settle(grant, used_weight=35)

    assert grant.mode == "shared"
    # The first take was refused, so the request waited and took again.
    assert shared.takes == [(120, "background"), (120, "background")]
    assert clock.now == pytest.approx(0.4)
    assert shared.adjustments == [85]
    assert budget.usage_snapshot(weight_per_minute=550)["budgetMode"] == "shared"
    assert budget.usage_snapshot(weight_per_minute=550)["budgetWeightPerMinute"] == 1100


@pytest.mark.asyncio
async def test_unreachable_redis_falls_back_to_the_local_budget_until_retry() -> None:
    shared = FakeSharedBudget()
    shared.available = False
    budget, clock = budget_with_shared(shared)

    first = await budget.acquire(
        2, priority="execution", request_type="l2Book", weight_per_minute=60
    )
    shared.available = True
    during_fallback = await budget.acquire(
        2,
        priority="execution",
        request_type="l2Book",
        weight_per_minute=60,
    )
    snapshot = budget.usage_snapshot(weight_per_minute=550)
    clock.now += 31
    after_retry = await budget.acquire(
        2,
        priority="execution",
        request_type="l2Book",
        weight_per_minute=60,
    )

    assert (first.mode, during_fallback.mode, after_retry.mode) == ("local", "local", "shared")
    assert snapshot["budgetMode"] == "local"
    assert snapshot["budgetWeightPerMinute"] == 550
    assert shared.takes == [(2, "execution")]


@pytest.mark.asyncio
async def test_failed_refund_switches_to_the_local_budget() -> None:
    shared = FakeSharedBudget()
    budget, _clock = budget_with_shared(shared)
    grant = await budget.acquire(
        120,
        priority="background",
        request_type="userFills",
        weight_per_minute=550,
    )
    shared.available = False

    await budget.settle(grant, used_weight=20)
    shared.available = True
    next_grant = await budget.acquire(
        20,
        priority="background",
        request_type="portfolio",
        weight_per_minute=550,
    )

    assert next_grant.mode == "local"


@pytest.mark.asyncio
async def test_shared_take_leaves_the_execution_reserve_to_copy_execution() -> None:
    calls: list[tuple[str, int, int]] = []

    class RecordingScriptRedis:
        def register_script(self, _script: str) -> Any:
            async def run(*, keys: list[str], args: list[Any]) -> int:
                calls.append((args[0], args[1], args[2]))
                return 0

            return run

    shared = SharedInfoBudget(
        RecordingScriptRedis(),  # type: ignore[arg-type]
        key="hyperliquid:info_budget:mainnet",
        weight_per_minute=1100,
        burst_weight=275,
        execution_reserve_weight=60,
    )

    await shared.take(20, priority="execution")
    await shared.take(120, priority="background")
    await shared.take(500, priority="background")
    await shared.adjust(-15)

    # A take larger than the bucket is capped so it can still be granted.
    assert calls == [
        ("take", 20, 0),
        ("take", 120, 60),
        ("take", 275, 0),
        ("adjust", -15, 0),
    ]
