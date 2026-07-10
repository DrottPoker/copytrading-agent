import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, status
from redis.exceptions import RedisError
from starlette.responses import StreamingResponse

from app.integrations.redis_client import get_redis
from app.schemas.event import LiveEvent, LiveEventListResponse
from app.services.realtime_event_service import (
    latest_event_stream_id,
    list_recent_events,
    normalize_stream_cursor,
    read_event_stream,
)

router = APIRouter(tags=["events"])


@router.get("/events/recent", response_model=LiveEventListResponse)
async def recent_events_route(
    limit: int = Query(default=100, ge=1, le=250),
) -> LiveEventListResponse:
    redis = get_redis()
    try:
        events = await list_recent_events(redis, limit=limit)
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis is not available.",
        ) from exc

    live_events = [LiveEvent.model_validate(event) for event in events]
    return LiveEventListResponse(items=live_events, total=len(live_events))


@router.get("/events")
async def events_route(request: Request) -> StreamingResponse:
    return StreamingResponse(
        stream_events(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def stream_events(request: Request) -> AsyncIterator[str]:
    redis = get_redis()
    last_event_id = normalize_stream_cursor(request.headers.get("last-event-id"))
    if last_event_id == "$":
        last_event_id = await latest_event_stream_id(redis)
    yield sse_event(
        {
            "type": "system",
            "channel": "events:system",
            "message": "SSE connected.",
            "payload": {},
        }
    )
    while not await request.is_disconnected():
        events = await read_event_stream(
            redis,
            last_event_id=last_event_id,
            block_ms=10_000,
        )
        if not events:
            yield ": keepalive\n\n"
            continue
        for event in events:
            last_event_id = str(event["id"])
            yield sse_event(event, event_id=last_event_id)


def sse_event(payload: dict[str, object], *, event_id: str | None = None) -> str:
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"{id_line}event: message\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
