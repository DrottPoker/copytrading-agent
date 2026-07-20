import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from redis.exceptions import RedisError

from app import main
from app.api import routes_events, routes_health
from app.integrations import redis_client
from app.workers import monitor_worker


class FakeRedisClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeRequest:
    def __init__(self, last_event_id: str | None = "0-0") -> None:
        self.headers = {} if last_event_id is None else {"last-event-id": last_event_id}

    async def is_disconnected(self) -> bool:
        return False


def health_settings() -> SimpleNamespace:
    return SimpleNamespace(
        app_name="Copy agent",
        app_version="test",
        app_env="test",
        system_mode="paper",
        paper_trading_enabled=True,
        live_trading_enabled=False,
        worker_run_in_api_process=False,
        worker_role="all",
        hyperliquid_network="mainnet",
        active_copy_wallets=0,
        max_realtime_wallets=100,
    )


def test_get_redis_shares_resolved_url_and_shutdown_closes_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[FakeRedisClient] = []
    redis_url = "redis://redis.example:6379/0"

    def create_client(*_args: object, **_kwargs: object) -> FakeRedisClient:
        client = FakeRedisClient()
        created.append(client)
        return client

    monkeypatch.setattr(redis_client, "Redis", SimpleNamespace(from_url=create_client))
    monkeypatch.setattr(
        redis_client,
        "get_settings",
        lambda: SimpleNamespace(redis_url=redis_url),
    )
    redis_client.clear_redis_clients()

    implicit_client = redis_client.get_redis()
    explicit_client = redis_client.get_redis(redis_url)

    assert implicit_client is explicit_client
    assert len(created) == 1

    asyncio.run(redis_client.close_redis_clients())

    assert created[0].close_calls == 1
    assert redis_client.get_redis(redis_url) is not implicit_client
    redis_client.clear_redis_clients()


@pytest.mark.asyncio
async def test_health_and_ready_keep_redis_outage_degraded_but_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def postgres_ok(*_args: object) -> dict[str, str]:
        return {"status": "ok"}

    async def redis_error(*_args: object) -> dict[str, str]:
        return {"status": "error", "detail": "ConnectionError"}

    monkeypatch.setattr(routes_health, "check_postgres", postgres_ok)
    monkeypatch.setattr(routes_health, "check_redis", redis_error)
    settings = health_settings()
    health_response = Response()
    ready_response = Response()

    health_payload = await routes_health.health(response=health_response, settings=settings)
    ready_payload = await routes_health.ready(response=ready_response, settings=settings)

    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert health_payload["status"] == "degraded"
    assert ready_payload["dependencies"]["redis"] == {
        "status": "error",
        "detail": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_recent_events_times_out_with_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocked_read(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        await asyncio.Event().wait()
        return []

    monkeypatch.setattr(routes_events, "get_redis", lambda: object())
    monkeypatch.setattr(routes_events, "list_recent_events", blocked_read)
    monkeypatch.setattr(routes_events, "EVENT_RECENT_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(HTTPException) as raised:
        await routes_events.recent_events_route(limit=1)

    assert raised.value.status_code == 503
    assert raised.value.detail == "Redis is not available."


@pytest.mark.asyncio
async def test_event_stream_closes_when_redis_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def redis_failure(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise RedisError("Redis is unavailable")

    monkeypatch.setattr(routes_events, "get_redis", lambda: object())
    monkeypatch.setattr(routes_events, "read_event_stream", redis_failure)
    stream = routes_events.stream_events(FakeRequest())

    assert "SSE connected." in await anext(stream)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_event_stream_does_not_swallow_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancelled_read(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
        raise asyncio.CancelledError

    monkeypatch.setattr(routes_events, "get_redis", lambda: object())
    monkeypatch.setattr(routes_events, "read_event_stream", cancelled_read)
    stream = routes_events.stream_events(FakeRequest())

    assert "SSE connected." in await anext(stream)
    with pytest.raises(asyncio.CancelledError):
        await anext(stream)


@pytest.mark.asyncio
async def test_monitor_worker_closes_redis_after_services_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    redis = object()
    settings = SimpleNamespace(
        app_env="test",
        system_mode="paper",
        worker_role="all",
        live_trading_enabled=False,
        log_level="INFO",
        redis_url="redis://redis.example:6379/0",
    )

    class FakeLoop:
        def add_signal_handler(self, *_args: object) -> None:
            return None

    async def run_services(**kwargs: object) -> None:
        assert kwargs["redis"] is redis

    async def close_redis() -> None:
        closed.append(True)

    monkeypatch.setattr(monitor_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(monitor_worker, "configure_logging", lambda *_args: None)
    monkeypatch.setattr(monitor_worker, "get_sessionmaker", lambda *_args: object())
    monkeypatch.setattr(monitor_worker, "get_redis", lambda *_args: redis)
    monkeypatch.setattr(monitor_worker, "run_monitor_services_with_lease_retry", run_services)
    monkeypatch.setattr(monitor_worker, "close_redis_clients", close_redis)
    monkeypatch.setattr(monitor_worker.asyncio, "get_running_loop", lambda: FakeLoop())

    await monitor_worker.run_worker()

    assert closed == [True]


@pytest.mark.asyncio
async def test_api_lifespan_closes_redis_after_embedded_worker_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drained: list[bool] = []
    closed: list[bool] = []
    started = asyncio.Event()
    redis = object()

    async def run_services(**kwargs: object) -> None:
        started.set()
        stop_event = kwargs["stop_event"]
        assert isinstance(stop_event, asyncio.Event)
        await stop_event.wait()
        drained.append(True)

    async def close_redis() -> None:
        assert drained == [True]
        closed.append(True)

    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(worker_run_in_api_process=True, redis_url="redis://redis.example:6379/0"),
    )
    monkeypatch.setattr(main, "get_sessionmaker", lambda *_args: object())
    monkeypatch.setattr(main, "get_redis", lambda *_args: redis)
    monkeypatch.setattr(main, "run_monitor_services", run_services)
    monkeypatch.setattr(main, "close_redis_clients", close_redis)

    async with main.lifespan(main.app):
        await started.wait()

    assert closed == [True]
