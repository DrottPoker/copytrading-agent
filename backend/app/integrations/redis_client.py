import asyncio
from functools import lru_cache
from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings, get_settings


@lru_cache
def get_redis(redis_url: str | None = None) -> Redis:
    return Redis.from_url(redis_url or get_settings().redis_url, decode_responses=True)


async def check_redis(settings: Settings | None = None) -> dict[str, Any]:
    resolved_settings = settings or get_settings()
    try:
        client = get_redis(resolved_settings.redis_url)

        async def ping() -> None:
            await client.ping()

        await asyncio.wait_for(ping(), timeout=3)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": exc.__class__.__name__}
