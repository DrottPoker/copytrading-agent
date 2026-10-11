"""Rate budget and usage measurement for Hyperliquid info requests.

Hyperliquid limits info requests by weight per IP address. Every request of
this process passes the budget here, which paces it and records how much
weight the process used, how long requests waited, and how often Hyperliquid
answered 429.
"""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import Settings

RequestPriority = Literal["execution", "background"]
# Hyperliquid's REST weight limit per IP address, shared by every process on the host.
HYPERLIQUID_IP_WEIGHT_LIMIT_PER_MINUTE = 1200
PRIORITIES: tuple[RequestPriority, ...] = ("execution", "background")

USAGE_HISTORY_MINUTES = 60
RECENT_USAGE_MINUTES = 15
TOP_REQUEST_TYPES = 5


class HyperliquidInfoRateLimiter:
    """Weight-based token bucket where execution requests go before background work.

    Background callers yield while an execution request is waiting, so copy
    execution never queues behind imports or reconciliation in the same process.
    """

    def __init__(self, *, clock: Any = time.monotonic, sleep: Any = asyncio.sleep) -> None:
        self._clock = clock
        self._sleep = sleep
        self._tokens: float | None = None
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

    def _refill(self, *, rate: float, capacity: float) -> None:
        now = self._clock()
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


@dataclass(frozen=True)
class InfoBudgetGrant:
    """Weight taken for one request, settled against its actual weight later."""

    weight: int
    priority: RequestPriority
    request_type: str


class HyperliquidInfoBudget:
    """The info request budget of this process, with its usage measurement."""

    def __init__(
        self,
        *,
        limiter: HyperliquidInfoRateLimiter | None = None,
        meter: InfoUsageMeter | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.limiter = limiter or HyperliquidInfoRateLimiter()
        self.meter = meter or InfoUsageMeter()
        self._clock = clock

    async def acquire(
        self,
        weight: int,
        *,
        priority: RequestPriority,
        request_type: str,
        weight_per_minute: int,
    ) -> InfoBudgetGrant:
        started = self._clock()
        await self.limiter.acquire(weight, priority=priority, weight_per_minute=weight_per_minute)
        self.meter.record_request(priority=priority, wait_seconds=self._clock() - started)
        return InfoBudgetGrant(weight=weight, priority=priority, request_type=request_type)

    async def settle(self, grant: InfoBudgetGrant, *, used_weight: int) -> None:
        """Charge weight that only the response revealed and record the request's weight."""

        self.limiter.charge(used_weight - grant.weight)
        self.meter.record_weight(
            priority=grant.priority,
            request_type=grant.request_type,
            weight=used_weight,
        )

    def record_rate_limited(self) -> None:
        self.meter.record_rate_limited()

    def usage_snapshot(self, *, weight_per_minute: int) -> dict[str, Any]:
        return self.meter.snapshot(budget_mode="local", budget_weight_per_minute=weight_per_minute)


INFO_BUDGET = HyperliquidInfoBudget()


def info_usage_snapshot(settings: Settings) -> dict[str, Any]:
    """This process's info request usage for heartbeats and ops health."""

    return INFO_BUDGET.usage_snapshot(weight_per_minute=settings.hyperliquid_info_weight_per_minute)
