import json
import re
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

EVENTS_ALL_CHANNEL = "events:all"
EVENTS_RECENT_KEY = "events:recent"
EVENTS_STREAM_KEY = "events:stream:v1"
EVENTS_RECENT_LIMIT = 250
EVENT_SCHEMA_VERSION = 1
STREAM_ID_PATTERN = re.compile(r"^\d+-\d+$")


def _json_default(value: Any) -> str:
    return str(value)


async def publish_event(
    redis: Redis,
    *,
    event_type: str,
    channel: str,
    message: str,
    payload: dict[str, Any] | None = None,
    producer: str = "backend",
    severity: str = "info",
    correlation_id: str | None = None,
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    event = {
        "schemaVersion": EVENT_SCHEMA_VERSION,
        "type": event_type,
        "channel": channel,
        "message": message,
        "payload": payload or {},
        "producer": producer,
        "severity": severity,
        "correlationId": correlation_id,
        "dedupeKey": dedupe_key,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    stored_event = json.dumps(event, default=_json_default, separators=(",", ":"))
    stream_id = await redis.xadd(
        EVENTS_STREAM_KEY,
        {"event": stored_event},
        maxlen=EVENTS_RECENT_LIMIT,
        approximate=True,
    )
    event["id"] = normalize_redis_value(stream_id)
    serialized = json.dumps(event, default=_json_default, separators=(",", ":"))
    pipe = redis.pipeline()
    pipe.publish(EVENTS_ALL_CHANNEL, serialized)
    if channel != EVENTS_ALL_CHANNEL:
        pipe.publish(channel, serialized)
    await pipe.execute()
    return event


async def list_recent_events(redis: Redis, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = await redis.xrevrange(
        EVENTS_STREAM_KEY,
        max="+",
        min="-",
        count=max(0, limit),
    )
    events = [event for stream_id, fields in rows if (event := stream_event(stream_id, fields))]
    if events:
        return events
    return await list_legacy_recent_events(redis, limit=limit)


async def read_event_stream(
    redis: Redis,
    *,
    last_event_id: str | None,
    block_ms: int = 10_000,
    count: int = 100,
) -> list[dict[str, Any]]:
    cursor = normalize_stream_cursor(last_event_id)
    streams = await redis.xread(
        {EVENTS_STREAM_KEY: cursor},
        count=max(1, count),
        block=max(1, block_ms),
    )
    events: list[dict[str, Any]] = []
    for _stream_name, rows in streams:
        for stream_id, fields in rows:
            event = stream_event(stream_id, fields)
            if event is not None:
                events.append(event)
    return events


async def latest_event_stream_id(redis: Redis) -> str:
    rows = await redis.xrevrange(
        EVENTS_STREAM_KEY,
        max="+",
        min="-",
        count=1,
    )
    if not rows:
        return "0-0"
    stream_id, _fields = rows[0]
    return normalize_redis_value(stream_id)


def stream_event(stream_id: Any, fields: dict[Any, Any]) -> dict[str, Any] | None:
    raw_event = fields.get("event")
    if raw_event is None:
        raw_event = fields.get(b"event")
    if raw_event is None:
        return None
    try:
        event = json.loads(normalize_redis_value(raw_event))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    event["id"] = normalize_redis_value(stream_id)
    event.setdefault("schemaVersion", EVENT_SCHEMA_VERSION)
    return event


async def list_legacy_recent_events(
    redis: Redis,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    raw_events = await redis.lrange(EVENTS_RECENT_KEY, 0, max(0, limit - 1))
    events: list[dict[str, Any]] = []
    for raw_event in raw_events:
        try:
            event = json.loads(normalize_redis_value(raw_event))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            continue
        if isinstance(event, dict):
            event.setdefault("schemaVersion", EVENT_SCHEMA_VERSION)
            events.append(event)
    return events


def normalize_stream_cursor(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized == "$" or STREAM_ID_PATTERN.fullmatch(normalized):
        return normalized
    return "$"


def normalize_redis_value(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)
