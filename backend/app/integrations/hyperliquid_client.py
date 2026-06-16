import asyncio
import logging
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class HyperliquidClientError(Exception):
    pass


class HyperliquidRateLimitError(HyperliquidClientError):
    pass


class HyperliquidInfoRateLimiter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def wait(self, *, min_interval_seconds: float) -> None:
        if min_interval_seconds <= 0:
            return

        async with self._lock:
            if self._last_request_at > 0:
                elapsed = time.monotonic() - self._last_request_at
                if elapsed < min_interval_seconds:
                    await asyncio.sleep(min_interval_seconds - elapsed)
            self._last_request_at = time.monotonic()


INFO_RATE_LIMITER = HyperliquidInfoRateLimiter()


class HyperliquidClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.hyperliquid_api_url
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HyperliquidClient":
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

        for attempt in range(attempts):
            await self._respect_min_request_interval()
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
                        "Hyperliquid info request was rate limited after "
                        f"{attempts} attempts."
                    )
                raise HyperliquidClientError(
                    "Hyperliquid info request failed after retries with status "
                    f"{response.status_code}."
                )

            if response.status_code >= 400:
                raise HyperliquidClientError(
                    f"Hyperliquid info request failed with status {response.status_code}."
                )

            return response.json()

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

    async def _respect_min_request_interval(self) -> None:
        await INFO_RATE_LIMITER.wait(
            min_interval_seconds=self.settings.hyperliquid_info_min_request_interval_seconds
        )

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
