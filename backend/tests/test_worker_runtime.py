import asyncio

import pytest

from app.services.worker_runtime import WorkerRuntimeState, run_supervised_worker_loop


def test_worker_runtime_payload_tracks_loop_and_queue_state() -> None:
    runtime = WorkerRuntimeState(
        role="trading",
        capabilities=("realtime", "reconciliation"),
        instance_id="worker-1",
    )

    runtime.mark_starting("realtime")
    starting_at = runtime.loop("realtime").last_started_at
    runtime.mark_running("realtime")
    runtime.mark_progress("realtime")
    runtime.mark_restarting("reconciliation", RuntimeError("exchange unavailable"))
    runtime.mark_queue_state(depth=-1, capacity=-5)
    runtime.mark_queue_state(depth=4, capacity=20, dropped=True)

    payload = runtime.payload()

    assert starting_at is not None
    assert payload["instanceId"] == "worker-1"
    assert payload["capabilities"] == ["realtime", "reconciliation"]
    assert payload["loops"]["realtime"]["status"] == "running"
    assert payload["loops"]["realtime"]["lastStartedAt"] == starting_at.isoformat()
    assert payload["loops"]["realtime"]["lastProgressAt"] is not None
    assert payload["loops"]["reconciliation"] == {
        "status": "restarting",
        "restartCount": 1,
        "consecutiveFailures": 1,
        "lastError": "exchange unavailable",
        "lastStartedAt": None,
        "lastProgressAt": None,
        "updatedAt": runtime.loop("reconciliation").updated_at.isoformat(),
    }
    assert payload["realtimeQueue"] == {
        "depth": 4,
        "capacity": 20,
        "dropped": 1,
    }


@pytest.mark.asyncio
async def test_supervised_loop_restarts_after_failure_and_reports_error() -> None:
    stop_event = asyncio.Event()
    runtime = WorkerRuntimeState(role="trading", capabilities=("realtime",))
    calls = 0
    errors: list[tuple[str, str]] = []

    async def loop_factory() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("websocket disconnected")
        stop_event.set()

    async def on_error(name: str, error: BaseException) -> None:
        errors.append((name, str(error)))

    await run_supervised_worker_loop(
        name="realtime",
        loop_factory=loop_factory,
        stop_event=stop_event,
        runtime=runtime,
        restart_delay_seconds=0,
        on_error=on_error,
    )

    state = runtime.loop("realtime")
    assert calls == 2
    assert errors == [("realtime", "websocket disconnected")]
    assert state.status == "stopped"
    assert state.restart_count == 1
    assert state.consecutive_failures == 1
    assert state.last_error is None


@pytest.mark.asyncio
async def test_supervised_loop_restarts_after_unexpected_clean_exit() -> None:
    stop_event = asyncio.Event()
    runtime = WorkerRuntimeState(role="maintenance", capabilities=("scoring",))
    errors: list[str] = []
    calls = 0

    async def loop_factory() -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            stop_event.set()

    async def on_error(_name: str, error: BaseException) -> None:
        errors.append(str(error))

    await run_supervised_worker_loop(
        name="scoring",
        loop_factory=loop_factory,
        stop_event=stop_event,
        runtime=runtime,
        restart_delay_seconds=0,
        on_error=on_error,
    )

    assert calls == 2
    assert errors == ["Worker loop exited unexpectedly: scoring."]
    assert runtime.loop("scoring").status == "stopped"
    assert runtime.loop("scoring").restart_count == 1


@pytest.mark.asyncio
async def test_supervised_loop_marks_state_stopped_when_cancelled() -> None:
    stop_event = asyncio.Event()
    loop_entered = asyncio.Event()
    runtime = WorkerRuntimeState(role="trading", capabilities=("realtime",))

    async def loop_factory() -> None:
        loop_entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        run_supervised_worker_loop(
            name="realtime",
            loop_factory=loop_factory,
            stop_event=stop_event,
            runtime=runtime,
            restart_delay_seconds=0,
        )
    )
    await loop_entered.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.loop("realtime").status == "stopped"
