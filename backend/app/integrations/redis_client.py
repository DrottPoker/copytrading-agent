import asyncio
import logging
from typing import Any

from redis.asyncio import Redis

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)
_redis_clients: dict[str, Redis] = {}


def get_redis(redis_url: str | None = None) -> Redis:
    resolved_url = redis_url or get_settings().redis_url
    client = _redis_clients.get(resolved_url)
    if client is None:
        client = Redis.from_url(resolved_url, decode_responses=True)
        _redis_clients[resolved_url] = client
    return client


def clear_redis_clients() -> None:
    _redis_clients.clear()


async def close_redis_clients() -> None:
    clients = tuple(_redis_clients.values())
    _redis_clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("redis client shutdown failed", exc_info=True)


get_redis.cache_clear = clear_redis_clients  # type: ignore[attr-defined]


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
