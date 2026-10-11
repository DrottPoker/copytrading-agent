import asyncio
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.integrations.hyperliquid_info_budget import INFO_BUDGET, RequestPriority

logger = logging.getLogger(__name__)


class HyperliquidClientError(Exception):
    pass


class HyperliquidRateLimitError(HyperliquidClientError):
    pass


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


# Item-weighted responses return at most this many items per request.
MAX_INFO_RESPONSE_ITEMS = 2000


def info_response_max_extra_weight(payload: dict[str, Any]) -> int:
    """Extra weight a full response would cost, reserved before the request is sent."""

    items_per_unit = ITEM_WEIGHTED_INFO_REQUEST_TYPES.get(str(payload.get("type") or ""))
    return 0 if items_per_unit is None else MAX_INFO_RESPONSE_ITEMS // items_per_unit


def info_response_extra_weight(payload: dict[str, Any], result: Any) -> int:
    items_per_unit = ITEM_WEIGHTED_INFO_REQUEST_TYPES.get(str(payload.get("type") or ""))
    if items_per_unit is None or not isinstance(result, list):
        return 0
    return len(result) // items_per_unit


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

    async def __aenter__(self) -> HyperliquidClient:
        return self.open()

    def open(self) -> HyperliquidClient:
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
        # A large response must not push other processes' copy execution into a wait,
        # so its worst-case weight is reserved up front and the unused part refunded.
        reserved_weight = weight + info_response_max_extra_weight(payload)
        request_type = str(payload.get("type") or "unknown")
        for attempt in range(attempts):
            grant = await INFO_BUDGET.acquire(
                reserved_weight,
                priority=self.priority,
                request_type=request_type,
                weight_per_minute=self.settings.hyperliquid_info_weight_per_minute,
            )
            try:
                response = await self._post_info_once(payload)
            except BaseException:
                await INFO_BUDGET.settle(grant, used_weight=weight)
                raise
            last_response = response

            if not 200 <= response.status_code < 300:
                await INFO_BUDGET.settle(grant, used_weight=weight)
                if response.status_code == 429:
                    INFO_BUDGET.record_rate_limited()
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
                raise HyperliquidClientError(
                    f"Hyperliquid info request failed with status {response.status_code}."
                )

            try:
                result = response.json()
            except BaseException:
                await INFO_BUDGET.settle(grant, used_weight=weight)
                raise
            await INFO_BUDGET.settle(
                grant,
                used_weight=weight + info_response_extra_weight(payload, result),
            )
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

    async def l2_book(self, *, coin: str) -> dict[str, Any]:
        result = await self.post_info({"type": "l2Book", "coin": coin})
        return result if isinstance(result, dict) else {}

    async def funding_history(
        self,
        *,
        coin: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_time_ms,
        }
        if end_time_ms is not None:
            payload["endTime"] = end_time_ms
        result = await self.post_info(payload)
        return (
            [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []
        )

    async def meta(self, *, dex: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "meta"}
        if dex:
            payload["dex"] = dex
        result = await self.post_info(payload)
        return result if isinstance(result, dict) else {}

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

    async def user_role(self, *, user: str) -> dict[str, Any]:
        result = await self.post_info({"type": "userRole", "user": user})
        if not isinstance(result, dict):
            raise HyperliquidClientError("Hyperliquid userRole returned an unexpected shape.")
        return result

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
        except TypeError, ValueError:
            return None
        return max(0.0, retry_at.timestamp() - time.time())
    return max(0.0, seconds)
