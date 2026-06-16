import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

EVENTS_ALL_CHANNEL = "events:all"
EVENTS_RECENT_KEY = "events:recent"
EVENTS_RECENT_LIMIT = 250


def _json_default(value: Any) -> str:
    return str(value)


async def publish_event(
    redis: Redis,
    *,
    event_type: str,
    channel: str,
    message: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "id": str(uuid4()),
        "type": event_type,
        "channel": channel,
        "message": message,
        "payload": payload or {},
        "createdAt": datetime.now(UTC).isoformat(),
    }
    serialized = json.dumps(event, default=_json_default, separators=(",", ":"))
    pipe = redis.pipeline()
    pipe.lpush(EVENTS_RECENT_KEY, serialized)
    pipe.ltrim(EVENTS_RECENT_KEY, 0, EVENTS_RECENT_LIMIT - 1)
    pipe.publish(EVENTS_ALL_CHANNEL, serialized)
    pipe.publish(channel, serialized)
    await pipe.execute()
    return event


async def list_recent_events(redis: Redis, *, limit: int = 100) -> list[dict[str, Any]]:
    raw_events = await redis.lrange(EVENTS_RECENT_KEY, 0, max(0, limit - 1))
    events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        try:
            event = json.loads(raw_event)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events
