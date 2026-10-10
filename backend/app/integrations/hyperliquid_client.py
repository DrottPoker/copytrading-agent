import asyncio
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class HyperliquidClientError(Exception):
    pass


class HyperliquidRateLimitError(HyperliquidClientError):
    pass


RequestPriority = Literal["execution", "background"]

# Hyperliquid info request weights against the shared 1200 per minute IP budget.
LIGHT_INFO_REQUEST_TYPES = frozenset(
    {
        "l2Book",
        "allMids",
        "clearinghouseState",
        "orderStatus",
        "spotClearinghouseState",
        "exchangeStatus",
    }
)
HEAVY_INFO_REQUEST_WEIGHTS = {"userRole": 60}
DEFAULT_INFO_REQUEST_WEIGHT = 20
LIGHT_INFO_REQUEST_WEIGHT = 2
# Responses that cost one extra unit per this many returned items.
ITEM_WEIGHTED_INFO_REQUEST_TYPES = {
    "recentTrades": 20,
    "historicalOrders": 20,
    "userFills": 20,
    "userFillsByTime": 20,
    "fundingHistory": 20,
    "userFunding": 20,
    "nonUserFundingUpdates": 20,
    "userNonFundingLedgerUpdates": 20,
    "twapHistory": 20,
    "userTwapSliceFills": 20,
    "userTwapSliceFillsByTime": 20,
    "candleSnapshot": 60,
}


def info_request_weight(payload: dict[str, Any]) -> int:
    request_type = str(payload.get("type") or "")
    if request_type in LIGHT_INFO_REQUEST_TYPES:
        return LIGHT_INFO_REQUEST_WEIGHT
    return HEAVY_INFO_REQUEST_WEIGHTS.get(request_type, DEFAULT_INFO_REQUEST_WEIGHT)


def info_response_extra_weight(payload: dict[str, Any], result: Any) -> int:
    items_per_unit = ITEM_WEIGHTED_INFO_REQUEST_TYPES.get(str(payload.get("type") or ""))
    if items_per_unit is None or not isinstance(result, list):
        return 0
    return len(result) // items_per_unit


class HyperliquidInfoRateLimiter:
    """Weight-based token bucket where execution requests go before background work.

    The budget is per process. Background callers yield while an execution
    request is waiting, so copy execution never queues behind imports or
    reconciliation in the same worker.
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


INFO_RATE_LIMITER = HyperliquidInfoRateLimiter()


class HyperliquidClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        priority: RequestPriority = "background",
    ) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.hyperliquid_api_url
        self.priority = priority
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HyperliquidClient":
        return self.open()

    def open(self) -> "HyperliquidClient":
        """Start a pooled HTTP client so requests reuse warm connections."""

        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=20)
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def post_info(self, payload: dict[str, Any]) -> Any:
        attempts = self.settings.hyperliquid_info_request_retries + 1
        last_response: httpx.Response | None = None

        weight = info_request_weight(payload)
        for attempt in range(attempts):
            await INFO_RATE_LIMITER.acquire(
                weight,
                priority=self.priority,
                weight_per_minute=self.settings.hyperliquid_info_weight_per_minute,
            )
            response = await self._post_info_once(payload)
            last_response = response

            if response.status_code == 429 or response.status_code >= 500:
                if attempt < attempts - 1:
                    delay_seconds = self._retry_delay_seconds(response, attempt=attempt)
                    logger.warning(
                        "Hyperliquid info request retrying status=%s attempt=%s/%s delay=%.2fs",
                        response.status_code,
                        attempt + 1,
                        attempts,
                        delay_seconds,
                    )
                    await asyncio.sleep(delay_seconds)
                    continue
                if response.status_code == 429:
                    raise HyperliquidRateLimitError(
                        f"Hyperliquid info request was rate limited after {attempts} attempts."
                    )
                raise HyperliquidClientError(
                    "Hyperliquid info request failed after retries with status "
                    f"{response.status_code}."
                )

            if response.status_code >= 400:
                raise HyperliquidClientError(
                    f"Hyperliquid info request failed with status {response.status_code}."
                )

            result = response.json()
            INFO_RATE_LIMITER.charge(info_response_extra_weight(payload, result))
            return result

        raise HyperliquidClientError(
            "Hyperliquid info request failed."
            if last_response is None
            else f"Hyperliquid info request failed with status {last_response.status_code}."
        )

    async def _post_info_once(self, payload: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post("/info", json=payload)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=20) as client:
            return await client.post("/info", json=payload)

    def _retry_delay_seconds(self, response: httpx.Response, *, attempt: int) -> float:
        retry_after = parse_retry_after_seconds(response.headers.get("retry-after"))
        if retry_after is not None:
            return min(retry_after, self.settings.hyperliquid_info_retry_max_delay_seconds)

        base_delay = self.settings.hyperliquid_info_retry_base_delay_seconds
        max_delay = self.settings.hyperliquid_info_retry_max_delay_seconds
        return min(base_delay * (2**attempt), max_delay)

    async def user_fills_by_time(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
        aggregate_by_time: bool = False,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "userFillsByTime",
            "user": user,
            "startTime": start_time_ms,
            "aggregateByTime": aggregate_by_time,
        }
        if end_time_ms is not None:
            payload["endTime"] = end_time_ms

        result = await self.post_info(payload)
        if not isinstance(result, list):
            raise HyperliquidClientError(
                "Hyperliquid userFillsByTime returned an unexpected shape."
            )
        return [item for item in result if isinstance(item, dict)]

    async def user_funding(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "userFunding",
            "user": user,
            "startTime": start_time_ms,
        }
        if end_time_ms is not None:
            payload["endTime"] = end_time_ms

        result = await self.post_info(payload)
        if not isinstance(result, list):
            raise HyperliquidClientError("Hyperliquid userFunding returned an unexpected shape.")
        return [item for item in result if isinstance(item, dict)]

    async def user_non_funding_ledger_updates(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "userNonFundingLedgerUpdates",
            "user": user,
            "startTime": start_time_ms,
        }
        if end_time_ms is not None:
            payload["endTime"] = end_time_ms

        result = await self.post_info(payload)
        if not isinstance(result, list):
            raise HyperliquidClientError(
                "Hyperliquid userNonFundingLedgerUpdates returned an unexpected shape."
            )
        return [item for item in result if isinstance(item, dict)]

    async def order_status(self, *, user: str, oid: int | str) -> dict[str, Any]:
        result = await self.post_info({"type": "orderStatus", "user": user, "oid": oid})
        if not isinstance(result, dict):
            raise HyperliquidClientError("Hyperliquid orderStatus returned an unexpected shape.")
        return result

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict[str, Any]:
        payload = {"type": "clearinghouseState", "user": user}
        if dex:
            payload["dex"] = dex
        result = await self.post_info(payload)
        if not isinstance(result, dict):
            raise HyperliquidClientError(
                "Hyperliquid clearinghouseState returned an unexpected shape."
            )
        return result

    async def perp_dexs(self) -> list[Any]:
        result = await self.post_info({"type": "perpDexs"})
        if not isinstance(result, list):
            raise HyperliquidClientError("Hyperliquid perpDexs returned an unexpected shape.")
        return result

    async def meta_and_asset_ctxs(self, *, dex: str | None = None) -> list[Any]:
        payload: dict[str, Any] = {"type": "metaAndAssetCtxs"}
        if dex:
            payload["dex"] = dex
        result = await self.post_info(payload)
        if not isinstance(result, list):
            raise HyperliquidClientError(
                "Hyperliquid metaAndAssetCtxs returned an unexpected shape."
            )
        return result

    async def all_mids(self, *, dex: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "allMids"}
        if dex:
            payload["dex"] = dex
        result = await self.post_info(payload)
        if not isinstance(result, dict):
            raise HyperliquidClientError("Hyperliquid allMids returned an unexpected shape.")
        return result

    async def spot_clearinghouse_state(self, *, user: str) -> dict[str, Any]:
        result = await self.post_info({"type": "spotClearinghouseState", "user": user})
        if not isinstance(result, dict):
            raise HyperliquidClientError(
                "Hyperliquid spotClearinghouseState returned an unexpected shape."
            )
        return result

    async def user_abstraction(self, *, user: str) -> Any:
        return await self.post_info({"type": "userAbstraction", "user": user})

    async def portfolio(self, *, user: str) -> list[Any]:
        result = await self.post_info({"type": "portfolio", "user": user})
        if not isinstance(result, list):
            raise HyperliquidClientError("Hyperliquid portfolio returned an unexpected shape.")
        return result


def parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, retry_at.timestamp() - time.time())
    return max(0.0, seconds)
