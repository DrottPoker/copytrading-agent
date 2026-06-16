import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request, status
from redis.exceptions import RedisError
from starlette.responses import StreamingResponse

from app.integrations.redis_client import get_redis
from app.schemas.event import LiveEvent, LiveEventListResponse
from app.services.realtime_event_service import EVENTS_ALL_CHANNEL, list_recent_events

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
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(EVENTS_ALL_CHANNEL)
        yield sse_event(
            {
                "type": "system",
                "channel": "events:system",
                "message": "SSE connected.",
                "payload": {},
            }
        )
        while not await request.is_disconnected():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=10)
            if message and message.get("type") == "message":
                yield f"event: message\ndata: {message['data']}\n\n"
            else:
                yield ": keepalive\n\n"
    finally:
        await pubsub.unsubscribe(EVENTS_ALL_CHANNEL)
        await pubsub.aclose()


def sse_event(payload: dict[str, object]) -> str:
    return f"event: message\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
