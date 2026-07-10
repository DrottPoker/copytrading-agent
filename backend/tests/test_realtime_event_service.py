import json
from decimal import Decimal
from typing import Any

import pytest

from app.services.realtime_event_service import (
    EVENTS_ALL_CHANNEL,
    EVENTS_RECENT_KEY,
    EVENTS_RECENT_LIMIT,
    EVENTS_STREAM_KEY,
    latest_event_stream_id,
    list_recent_events,
    normalize_stream_cursor,
    publish_event,
    read_event_stream,
    stream_event,
)


class FakePipeline:
    def __init__(self) -> None:
        self.publications: list[tuple[str, str]] = []
        self.executed = False

    def publish(self, channel: str, payload: str) -> "FakePipeline":
        self.publications.append((channel, payload))
        return self

    async def execute(self) -> list[int]:
        self.executed = True
        return [1 for _publication in self.publications]


class FakeRedis:
    def __init__(self) -> None:
        self.pipeline_instance = FakePipeline()
        self.stream_id: str | bytes = b"1725000000000-3"
        self.recent_rows: list[tuple[Any, dict[Any, Any]]] = []
        self.legacy_rows: list[str | bytes] = []
        self.read_rows: list[tuple[Any, list[tuple[Any, dict[Any, Any]]]]] = []
        self.xadd_call: tuple[str, dict[str, str], int, bool] | None = None
        self.xrevrange_call: tuple[str, str, str, int] | None = None
        self.xread_call: tuple[dict[str, str], int, int] | None = None
        self.lrange_call: tuple[str, int, int] | None = None

    async def xadd(
        self,
        key: str,
        fields: dict[str, str],
        *,
        maxlen: int,
        approximate: bool,
    ) -> str | bytes:
        self.xadd_call = (key, fields, maxlen, approximate)
        return self.stream_id

    def pipeline(self) -> FakePipeline:
        return self.pipeline_instance

    async def xrevrange(
        self,
        key: str,
        *,
        max: str,
        min: str,
        count: int,
    ) -> list[tuple[Any, dict[Any, Any]]]:
        self.xrevrange_call = (key, max, min, count)
        return self.recent_rows

    async def lrange(self, key: str, start: int, end: int) -> list[str | bytes]:
        self.lrange_call = (key, start, end)
        return self.legacy_rows

    async def xread(
        self,
        streams: dict[str, str],
        *,
        count: int,
        block: int,
    ) -> list[tuple[Any, list[tuple[Any, dict[Any, Any]]]]]:
        self.xread_call = (streams, count, block)
        return self.read_rows


@pytest.mark.asyncio
async def test_publish_event_writes_versioned_stream_event_and_broadcasts() -> None:
    redis = FakeRedis()

    event = await publish_event(
        redis,  # type: ignore[arg-type]
        event_type="risk_guard",
        channel="events:risk",
        message="Entry was blocked.",
        payload={"limitUsd": Decimal("50.25")},
        producer="trading-worker:worker-1",
        severity="warning",
        correlation_id="correlation-1",
        dedupe_key="risk:account-1:daily-loss",
    )

    assert event["id"] == "1725000000000-3"
    assert event["schemaVersion"] == 1
    assert event["producer"] == "trading-worker:worker-1"
    assert event["severity"] == "warning"
    assert event["correlationId"] == "correlation-1"
    assert event["dedupeKey"] == "risk:account-1:daily-loss"
    assert event["createdAt"].endswith("+00:00")

    assert redis.xadd_call is not None
    stream_key, fields, maxlen, approximate = redis.xadd_call
    stored_event = json.loads(fields["event"])
    assert stream_key == EVENTS_STREAM_KEY
    assert maxlen == EVENTS_RECENT_LIMIT
    assert approximate is True
    assert "id" not in stored_event
    assert stored_event["payload"] == {"limitUsd": "50.25"}

    assert redis.pipeline_instance.executed is True
    assert [channel for channel, _payload in redis.pipeline_instance.publications] == [
        EVENTS_ALL_CHANNEL,
        "events:risk",
    ]
    published_event = json.loads(redis.pipeline_instance.publications[0][1])
    assert published_event["id"] == "1725000000000-3"
    assert published_event["payload"] == {"limitUsd": "50.25"}


@pytest.mark.asyncio
async def test_publish_event_does_not_publish_all_channel_twice() -> None:
    redis = FakeRedis()

    await publish_event(
        redis,  # type: ignore[arg-type]
        event_type="system",
        channel=EVENTS_ALL_CHANNEL,
        message="Worker started.",
    )

    assert [channel for channel, _payload in redis.pipeline_instance.publications] == [
        EVENTS_ALL_CHANNEL
    ]


@pytest.mark.asyncio
async def test_list_recent_events_reads_stream_and_skips_invalid_rows() -> None:
    redis = FakeRedis()
    redis.recent_rows = [
        (
            b"20-1",
            {
                b"event": json.dumps(
                    {
                        "type": "fill",
                        "channel": "events:fills",
                        "message": "Fill stored.",
                        "payload": {},
                    }
                ).encode()
            },
        ),
        (b"20-0", {b"event": b"not-json"}),
        (b"19-0", {b"other": b"missing-event"}),
    ]
    redis.legacy_rows = [json.dumps({"type": "legacy"})]

    events = await list_recent_events(redis, limit=25)  # type: ignore[arg-type]

    assert events == [
        {
            "type": "fill",
            "channel": "events:fills",
            "message": "Fill stored.",
            "payload": {},
            "id": "20-1",
            "schemaVersion": 1,
        }
    ]
    assert redis.xrevrange_call == (EVENTS_STREAM_KEY, "+", "-", 25)
    assert redis.lrange_call is None


@pytest.mark.asyncio
async def test_list_recent_events_falls_back_to_legacy_list() -> None:
    redis = FakeRedis()
    redis.legacy_rows = [
        json.dumps({"id": "legacy-1", "type": "system"}).encode(),
        b"not-json",
        json.dumps(["not", "an", "event"]),
    ]

    events = await list_recent_events(redis, limit=2)  # type: ignore[arg-type]

    assert events == [{"id": "legacy-1", "type": "system", "schemaVersion": 1}]
    assert redis.lrange_call == (EVENTS_RECENT_KEY, 0, 1)


@pytest.mark.asyncio
async def test_read_event_stream_uses_safe_cursor_and_decodes_rows() -> None:
    redis = FakeRedis()
    redis.read_rows = [
        (
            b"events:stream:v1",
            [
                (
                    b"30-2",
                    {
                        b"event": json.dumps(
                            {
                                "schemaVersion": 1,
                                "type": "worker_loop_error",
                                "channel": "events:system",
                            }
                        ).encode()
                    },
                ),
                (b"30-3", {b"event": b"invalid"}),
            ],
        )
    ]

    events = await read_event_stream(  # type: ignore[arg-type]
        redis,
        last_event_id="invalid cursor",
        block_ms=250,
        count=5,
    )

    assert redis.xread_call == ({EVENTS_STREAM_KEY: "$"}, 5, 250)
    assert events == [
        {
            "schemaVersion": 1,
            "type": "worker_loop_error",
            "channel": "events:system",
            "id": "30-2",
        }
    ]


@pytest.mark.asyncio
async def test_latest_event_stream_id_closes_initial_subscription_race() -> None:
    redis = FakeRedis()
    redis.recent_rows = [(b"30-7", {b"event": b"{}"})]

    stream_id = await latest_event_stream_id(redis)  # type: ignore[arg-type]

    assert stream_id == "30-7"
    assert redis.xrevrange_call == (EVENTS_STREAM_KEY, "+", "-", 1)

    redis.recent_rows = []
    assert await latest_event_stream_id(redis) == "0-0"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "$"),
        ("", "$"),
        ("bad", "$"),
        ("$", "$"),
        (" 42-7 ", "42-7"),
    ],
)
def test_normalize_stream_cursor_accepts_only_redis_stream_ids(
    value: str | None,
    expected: str,
) -> None:
    assert normalize_stream_cursor(value) == expected


def test_stream_event_overwrites_untrusted_id_and_rejects_invalid_payloads() -> None:
    parsed = stream_event(
        b"50-1",
        {b"event": b'{"id":"untrusted","type":"system"}'},
    )

    assert parsed == {
        "id": "50-1",
        "type": "system",
        "schemaVersion": 1,
    }
    assert stream_event("50-2", {"event": "[]"}) is None
    assert stream_event("50-3", {"event": "{"}) is None
    assert stream_event("50-4", {"other": "missing"}) is None
