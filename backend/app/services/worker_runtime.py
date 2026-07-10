import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


@dataclass
class WorkerLoopState:
    status: str = "starting"
    restart_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_started_at: datetime | None = None
    last_progress_at: datetime | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "restartCount": self.restart_count,
            "consecutiveFailures": self.consecutive_failures,
            "lastError": self.last_error,
            "lastStartedAt": isoformat(self.last_started_at),
            "lastProgressAt": isoformat(self.last_progress_at),
            "updatedAt": self.updated_at.isoformat(),
        }


@dataclass
class WorkerRuntimeState:
    role: str
    capabilities: tuple[str, ...]
    instance_id: str = field(default_factory=lambda: str(uuid4()))
    loops: dict[str, WorkerLoopState] = field(default_factory=dict)
    realtime_queue_depth: int = 0
    realtime_queue_capacity: int = 0
    realtime_queue_dropped: int = 0

    def loop(self, name: str) -> WorkerLoopState:
        return self.loops.setdefault(name, WorkerLoopState())

    def mark_starting(self, name: str) -> None:
        state = self.loop(name)
        state.status = "starting"
        state.last_started_at = datetime.now(UTC)
        state.updated_at = state.last_started_at

    def mark_running(self, name: str) -> None:
        state = self.loop(name)
        now = datetime.now(UTC)
        state.status = "running"
        state.last_error = None
        state.last_progress_at = now
        state.updated_at = now

    def mark_progress(self, name: str) -> None:
        state = self.loop(name)
        now = datetime.now(UTC)
        state.last_progress_at = now
        state.updated_at = now

    def mark_restarting(self, name: str, error: BaseException | str) -> None:
        state = self.loop(name)
        state.status = "restarting"
        state.restart_count += 1
        state.consecutive_failures += 1
        state.last_error = str(error) or error.__class__.__name__
        state.updated_at = datetime.now(UTC)

    def mark_stopped(self, name: str) -> None:
        state = self.loop(name)
        state.status = "stopped"
        state.updated_at = datetime.now(UTC)

    def mark_queue_state(self, *, depth: int, capacity: int, dropped: bool = False) -> None:
        self.realtime_queue_depth = max(depth, 0)
        self.realtime_queue_capacity = max(capacity, 0)
        if dropped:
            self.realtime_queue_dropped += 1

    def payload(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "capabilities": list(self.capabilities),
            "loops": {name: state.payload() for name, state in sorted(self.loops.items())},
            "realtimeQueue": {
                "depth": self.realtime_queue_depth,
                "capacity": self.realtime_queue_capacity,
                "dropped": self.realtime_queue_dropped,
            },
        }


async def run_supervised_worker_loop(
    *,
    name: str,
    loop_factory: Callable[[], Awaitable[None]],
    stop_event: asyncio.Event,
    runtime: WorkerRuntimeState,
    restart_delay_seconds: int,
    on_error: Callable[[str, BaseException], Awaitable[None]] | None = None,
) -> None:
    while not stop_event.is_set():
        runtime.mark_starting(name)
        try:
            runtime.mark_running(name)
            await loop_factory()
            if stop_event.is_set():
                break
            error = RuntimeError(f"Worker loop exited unexpectedly: {name}.")
        except asyncio.CancelledError:
            runtime.mark_stopped(name)
            raise
        except Exception as exc:
            error = exc

        runtime.mark_restarting(name, error)
        if on_error is not None:
            await on_error(name, error)
        await sleep_until_stop(stop_event, restart_delay_seconds)

    runtime.mark_stopped(name)


async def sleep_until_stop(stop_event: asyncio.Event, seconds: int) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(seconds, 0))
    except TimeoutError:
        pass


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
