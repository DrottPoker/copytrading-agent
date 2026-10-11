"""Rate budget and usage measurement for Hyperliquid info requests.

Hyperliquid limits info requests by weight per IP address, so every process on
the host draws from one token bucket in Redis. Copy execution may take the last
tokens, while background work must leave a reserve. When Redis is unreachable a
process falls back to its own local budget until Redis answers again. Every
request is also measured: weight used, time spent waiting, and 429 responses.
"""

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.core.config import Settings

logger = logging.getLogger(__name__)

RequestPriority = Literal["execution", "background"]
BudgetMode = Literal["shared", "local"]
# Hyperliquid's REST weight limit per IP address, shared by every process on the host.
HYPERLIQUID_IP_WEIGHT_LIMIT_PER_MINUTE = 1200
PRIORITIES: tuple[RequestPriority, ...] = ("execution", "background")

USAGE_HISTORY_MINUTES = 60
RECENT_USAGE_MINUTES = 15
TOP_REQUEST_TYPES = 5

# Redis answers in milliseconds, but the timeout also counts time the event loop
# spends on other work, so it is generous enough that a busy loop is not a failure.
SHARED_BUDGET_REDIS_CONNECT_TIMEOUT_SECONDS = 1.0
SHARED_BUDGET_REDIS_TIMEOUT_SECONDS = 2.0
# After a Redis failure the process uses its local budget for this long.
SHARED_BUDGET_RETRY_SECONDS = 30.0
# Waiters re-check at least this often, so a refund or fallback is noticed.
SHARED_BUDGET_MAX_POLL_SECONDS = 1.0
SHARED_BUDGET_KEY_TTL_SECONDS = 3600

# Refills by Redis time, so every process sees the same bucket. Returns the
# milliseconds to wait before a take can succeed, 0 when it was granted.
SHARED_BUCKET_SCRIPT = """
local key = KEYS[1]
local op = ARGV[1]
local amount = tonumber(ARGV[2])
local floor = tonumber(ARGV[3])
local rate = tonumber(ARGV[4])
local capacity = tonumber(ARGV[5])
local ttl = tonumber(ARGV[6])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) + tonumber(clock[2]) / 1000000
local state = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(state[1])
local updated_at = tonumber(state[2])
if tokens == nil or updated_at == nil then
  tokens = capacity
  updated_at = now
end
tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * rate)
local wait_ms = 0
if op == 'take' then
  if tokens - amount >= floor then
    tokens = tokens - amount
  else
    wait_ms = math.max(1, math.ceil((amount + floor - tokens) / rate * 1000))
  end
else
  tokens = math.min(capacity, tokens + amount)
end
redis.call('HSET', key, 'tokens', tostring(tokens), 'updated_at', tostring(now))
redis.call('EXPIRE', key, ttl)
return wait_ms
"""


class HyperliquidInfoRateLimiter:
    """Weight-based token bucket where execution requests go before background work.

    Background callers yield while an execution request is waiting, so copy
    execution never queues behind imports or reconciliation in the same process.
    """

    def __init__(self, *, clock: Any = time.monotonic, sleep: Any = asyncio.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._tokens: float | None = None
        self._capacity = 0.0
        self._updated_at = 0.0
        self._waiting_execution = 0

    async def acquire(
        self,
        weight: int,
        *,
        priority: RequestPriority,
        weight_per_minute: int,
    ) -> None:
        if weight_per_minute <= 0:
            return
        rate = weight_per_minute / 60
        capacity = max(float(weight), weight_per_minute / 4)
        if priority == "execution":
            self._waiting_execution += 1
        try:
            while True:
                self._refill(rate=rate, capacity=capacity)
                assert self._tokens is not None
                may_take = priority == "execution" or self._waiting_execution == 0
                if may_take and self._tokens >= weight:
                    self._tokens -= weight
                    return
                missing = max(weight - self._tokens, 0.0)
                await self._sleep(max(missing / rate, 0.05))
        finally:
            if priority == "execution":
                self._waiting_execution -= 1

    def charge(self, weight: int) -> None:
        """Record extra weight that is only known from the response size."""

        if weight > 0 and self._tokens is not None:
            self._tokens -= weight

    def refund(self, weight: int) -> None:
        """Return reserved weight the response did not use."""

        if weight > 0 and self._tokens is not None:
            self._tokens = min(self._capacity, self._tokens + weight)

    def _refill(self, *, rate: float, capacity: float) -> None:
        now = self._clock()
        self._capacity = capacity
        if self._tokens is None:
            self._tokens = capacity
        else:
            self._tokens = min(capacity, self._tokens + (now - self._updated_at) * rate)
        self._updated_at = now


@dataclass
class MinuteUsage:
    weight: Counter[str] = field(default_factory=Counter)
    requests: Counter[str] = field(default_factory=Counter)
    wait_seconds: dict[str, float] = field(default_factory=dict)
    weight_by_type: Counter[str] = field(default_factory=Counter)
    rate_limited: int = 0
    shared_fallbacks: int = 0


class InfoUsageMeter:
    """Per-minute record of this process's info requests over the last hour."""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._clock = clock
        self._minutes: dict[int, MinuteUsage] = {}

    def record_request(self, *, priority: RequestPriority, wait_seconds: float) -> None:
        usage = self._current_minute()
        usage.requests[priority] += 1
        usage.wait_seconds[priority] = usage.wait_seconds.get(priority, 0.0) + wait_seconds

    def record_weight(self, *, priority: RequestPriority, request_type: str, weight: int) -> None:
        if weight <= 0:
            return
        usage = self._current_minute()
        usage.weight[priority] += weight
        usage.weight_by_type[request_type] += weight

    def record_rate_limited(self) -> None:
        self._current_minute().rate_limited += 1

    def record_shared_fallback(self) -> None:
        self._current_minute().shared_fallbacks += 1

    def snapshot(self, *, budget_mode: str, budget_weight_per_minute: int) -> dict[str, Any]:
        """Usage over complete minutes, so a partial current minute never reads low."""

        current_minute = self._minute()
        last_hour = self._complete_minutes(current_minute, USAGE_HISTORY_MINUTES)
        recent = last_hour[-RECENT_USAGE_MINUTES:]
        last_minute = last_hour[-1]
        recent_types: Counter[str] = Counter()
        for usage in recent:
            recent_types.update(usage.weight_by_type)
        return {
            "budgetMode": budget_mode,
            "budgetWeightPerMinute": budget_weight_per_minute,
            "weightLastMinute": sum(last_minute.weight.values()),
            "weightPerMinute15m": per_minute(sum(sum(u.weight.values()) for u in recent)),
            "executionWeightPerMinute15m": per_minute(sum(u.weight["execution"] for u in recent)),
            "backgroundWeightPerMinute15m": per_minute(sum(u.weight["background"] for u in recent)),
            "peakWeightPerMinute60m": max(sum(u.weight.values()) for u in last_hour),
            "requests15m": sum(sum(u.requests.values()) for u in recent),
            "executionWaitSeconds15m": round(
                sum(u.wait_seconds.get("execution", 0.0) for u in recent), 2
            ),
            "backgroundWaitSeconds15m": round(
                sum(u.wait_seconds.get("background", 0.0) for u in recent), 2
            ),
            "rateLimited60m": sum(u.rate_limited for u in last_hour),
            "sharedFallbacks60m": sum(u.shared_fallbacks for u in last_hour),
            "topRequestTypes15m": [
                {"type": request_type, "weight": weight}
                for request_type, weight in recent_types.most_common(TOP_REQUEST_TYPES)
            ],
        }

    def _minute(self) -> int:
        return int(self._clock() // 60)

    def _current_minute(self) -> MinuteUsage:
        minute = self._minute()
        usage = self._minutes.get(minute)
        if usage is None:
            usage = MinuteUsage()
            self._minutes[minute] = usage
            cutoff = minute - USAGE_HISTORY_MINUTES
            for old_minute in [key for key in self._minutes if key < cutoff]:
                del self._minutes[old_minute]
        return usage

    def _complete_minutes(self, current_minute: int, count: int) -> list[MinuteUsage]:
        return [
            self._minutes.get(minute) or MinuteUsage()
            for minute in range(current_minute - count, current_minute)
        ]


def per_minute(total: float) -> float:
    return round(total / RECENT_USAGE_MINUTES, 1)


class SharedInfoBudgetUnavailableError(Exception):
    """Redis could not be reached, so the shared budget cannot be used."""


class SharedInfoBudget:
    """Token bucket in Redis that every process on the host draws from."""

    def __init__(
        self,
        redis: Redis,
        *,
        key: str,
        weight_per_minute: int,
        burst_weight: int,
        execution_reserve_weight: int,
    ) -> None:
        self.redis = redis
        self.key = key
        self.weight_per_minute = weight_per_minute
        self.burst_weight = burst_weight
        self.execution_reserve_weight = execution_reserve_weight
        self._script = redis.register_script(SHARED_BUCKET_SCRIPT)

    async def take(self, weight: int, *, priority: RequestPriority) -> float:
        """Take weight if available; otherwise return the seconds to wait first."""

        amount = min(weight, self.burst_weight)
        # Background work leaves the reserve for copy execution.
        floor = 0 if priority == "execution" else self.execution_reserve_weight
        floor = min(floor, self.burst_weight - amount)
        wait_ms = await self._run("take", amount=amount, floor=floor)
        return wait_ms / 1000

    async def adjust(self, weight: int) -> None:
        """Return unused reserved weight, or charge weight beyond the reservation."""

        if weight != 0:
            await self._run("adjust", amount=weight, floor=0)

    async def aclose(self) -> None:
        await self.redis.aclose()

    async def _run(self, op: str, *, amount: int, floor: int) -> int:
        try:
            result = await self._script(
                keys=[self.key],
                args=[
                    op,
                    amount,
                    floor,
                    self.weight_per_minute / 60,
                    self.burst_weight,
                    SHARED_BUDGET_KEY_TTL_SECONDS,
                ],
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise SharedInfoBudgetUnavailableError(str(exc) or type(exc).__name__) from exc
        return int(result)


@dataclass(frozen=True)
class InfoBudgetGrant:
    """Weight taken for one request, settled against its actual weight later."""

    weight: int
    priority: RequestPriority
    request_type: str
    mode: BudgetMode = "local"


class HyperliquidInfoBudget:
    """The info request budget of this process, with its usage measurement."""

    def __init__(
        self,
        *,
        limiter: HyperliquidInfoRateLimiter | None = None,
        meter: InfoUsageMeter | None = None,
        clock: Any = time.monotonic,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.limiter = limiter or HyperliquidInfoRateLimiter()
        self.meter = meter or InfoUsageMeter()
        self.shared: SharedInfoBudget | None = None
        self._clock = clock
        self._sleep = sleep
        self._shared_unavailable_until: float | None = None
        self._waiting_execution = 0

    def use_shared(self, shared: SharedInfoBudget | None) -> None:
        self.shared = shared
        self._shared_unavailable_until = None

    def shared_active(self) -> bool:
        return self.shared is not None and not self._shared_retry_pending()

    def _shared_retry_pending(self) -> bool:
        until = self._shared_unavailable_until
        return until is not None and self._clock() < until

    async def acquire(
        self,
        weight: int,
        *,
        priority: RequestPriority,
        request_type: str,
        weight_per_minute: int,
    ) -> InfoBudgetGrant:
        """Take weight from the shared budget, or from the local one while Redis is down."""

        started = self._clock()
        if priority == "execution":
            self._waiting_execution += 1
        try:
            mode = await self._take(weight, priority=priority, weight_per_minute=weight_per_minute)
        finally:
            if priority == "execution":
                self._waiting_execution -= 1
        self.meter.record_request(priority=priority, wait_seconds=self._clock() - started)
        return InfoBudgetGrant(
            weight=weight,
            priority=priority,
            request_type=request_type,
            mode=mode,
        )

    async def settle(self, grant: InfoBudgetGrant, *, used_weight: int) -> None:
        """Settle the reservation against the actual weight and record the request."""

        unused = grant.weight - used_weight
        if grant.mode == "shared" and self.shared is not None:
            try:
                await self.shared.adjust(unused)
            except SharedInfoBudgetUnavailableError as exc:
                self._mark_shared_unavailable(exc)
        elif unused > 0:
            self.limiter.refund(unused)
        else:
            self.limiter.charge(-unused)
        self.meter.record_weight(
            priority=grant.priority,
            request_type=grant.request_type,
            weight=used_weight,
        )

    def record_rate_limited(self) -> None:
        self.meter.record_rate_limited()

    def usage_snapshot(self, *, weight_per_minute: int) -> dict[str, Any]:
        if self.shared is not None and self.shared_active():
            return self.meter.snapshot(
                budget_mode="shared",
                budget_weight_per_minute=self.shared.weight_per_minute,
            )
        return self.meter.snapshot(budget_mode="local", budget_weight_per_minute=weight_per_minute)

    async def _take(
        self,
        weight: int,
        *,
        priority: RequestPriority,
        weight_per_minute: int,
    ) -> BudgetMode:
        shared = self.shared
        if shared is not None and self.shared_active():
            try:
                await self._take_shared(shared, weight, priority=priority)
            except SharedInfoBudgetUnavailableError as exc:
                self._mark_shared_unavailable(exc)
            else:
                if self._shared_unavailable_until is not None:
                    self._shared_unavailable_until = None
                    logger.info("shared Hyperliquid info budget is available again")
                return "shared"
        await self.limiter.acquire(weight, priority=priority, weight_per_minute=weight_per_minute)
        return "local"

    async def _take_shared(
        self,
        shared: SharedInfoBudget,
        weight: int,
        *,
        priority: RequestPriority,
    ) -> None:
        while True:
            # Background work also yields to execution requests waiting in this process.
            if priority == "background" and self._waiting_execution > 0:
                await self._sleep(0.05)
                continue
            wait_seconds = await shared.take(weight, priority=priority)
            if wait_seconds <= 0:
                return
            await self._sleep(min(wait_seconds, SHARED_BUDGET_MAX_POLL_SECONDS))

    def _mark_shared_unavailable(self, error: Exception) -> None:
        if not self._shared_retry_pending():
            logger.warning(
                "shared Hyperliquid info budget unavailable, using the local budget for %ss: %s",
                SHARED_BUDGET_RETRY_SECONDS,
                error,
            )
            self.meter.record_shared_fallback()
        self._shared_unavailable_until = self._clock() + SHARED_BUDGET_RETRY_SECONDS


INFO_BUDGET = HyperliquidInfoBudget()


def info_usage_snapshot(settings: Settings) -> dict[str, Any]:
    """This process's info request usage for heartbeats and ops health."""

    return INFO_BUDGET.usage_snapshot(weight_per_minute=settings.hyperliquid_info_weight_per_minute)


def shared_info_budget_key(settings: Settings) -> str:
    return f"hyperliquid:info_budget:{settings.hyperliquid_network}"


def configure_shared_info_budget(settings: Settings) -> None:
    """Draw this process's info requests from the bucket every process shares."""

    if not settings.hyperliquid_info_shared_budget_enabled:
        INFO_BUDGET.use_shared(None)
        return
    client = Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_timeout=SHARED_BUDGET_REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=SHARED_BUDGET_REDIS_CONNECT_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), 0),
    )
    INFO_BUDGET.use_shared(
        SharedInfoBudget(
            client,
            key=shared_info_budget_key(settings),
            weight_per_minute=settings.hyperliquid_info_shared_weight_per_minute,
            burst_weight=settings.hyperliquid_info_shared_burst_weight,
            execution_reserve_weight=settings.hyperliquid_info_execution_reserve_weight,
        )
    )


async def close_shared_info_budget() -> None:
    shared = INFO_BUDGET.shared
    INFO_BUDGET.use_shared(None)
    if shared is not None:
        await shared.aclose()
