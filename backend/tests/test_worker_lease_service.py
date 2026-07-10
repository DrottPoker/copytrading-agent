import asyncio
from typing import Any

import pytest

from app.services import worker_lease_service


class FakeOwnerTask:
    def __init__(self, runtime_stop_event: asyncio.Event) -> None:
        self.runtime_stop_event = runtime_stop_event
        self.cancelled = False
        self.cancel_message: Any = None

    def done(self) -> bool:
        return False

    def cancel(self, message: Any = None) -> None:
        assert self.runtime_stop_event.is_set()
        self.cancelled = True
        self.cancel_message = message


def test_worker_lease_renewal_timeout_is_shorter_than_ttl() -> None:
    for ttl_seconds in (1, 30, 90, 3600):
        timeout = worker_lease_service.worker_lease_renewal_timeout_seconds(ttl_seconds)

        assert 0 < timeout < ttl_seconds


@pytest.mark.asyncio
async def test_worker_lease_renewal_timeout_stops_runtime_before_owner_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocked_renewal(*_args: object, **_kwargs: object) -> int:
        await asyncio.Event().wait()
        return 1

    monkeypatch.setattr(worker_lease_service, "renew_worker_leases", blocked_renewal)
    monkeypatch.setattr(
        worker_lease_service,
        "worker_lease_renewal_interval_seconds",
        lambda _ttl: 0.001,
    )
    monkeypatch.setattr(
        worker_lease_service,
        "worker_lease_renewal_timeout_seconds",
        lambda _ttl: 0.001,
    )
    runtime_stop_event = asyncio.Event()
    owner_task = FakeOwnerTask(runtime_stop_event)

    await worker_lease_service.renew_worker_leases_loop(
        object(),
        keys=("worker_runtime:trading",),
        owner="worker-1",
        ttl_seconds=30,
        renewal_stop_event=asyncio.Event(),
        runtime_stop_event=runtime_stop_event,
        owner_task=owner_task,  # type: ignore[arg-type]
    )

    assert runtime_stop_event.is_set()
    assert owner_task.cancelled is True
