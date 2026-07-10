import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from sqlalchemy import text

from app.services.job_lock_service import job_lock_owner

logger = logging.getLogger(__name__)


class WorkerCapabilityLeaseUnavailableError(RuntimeError):
    pass


class WorkerCapabilityLeaseLostError(RuntimeError):
    pass


@asynccontextmanager
async def worker_capability_leases(
    sessionmaker: Any,
    *,
    capabilities: tuple[str, ...],
    ttl_seconds: int,
    runtime_stop_event: asyncio.Event,
):
    owner = job_lock_owner()
    keys = tuple(f"worker_runtime:{capability}" for capability in capabilities)
    acquired: list[str] = []
    try:
        for key in keys:
            if not await acquire_worker_lease(
                sessionmaker,
                key=key,
                owner=owner,
                ttl_seconds=ttl_seconds,
            ):
                raise WorkerCapabilityLeaseUnavailableError(
                    f"Worker capability is already owned: {key}."
                )
            acquired.append(key)

        renewal_stop_event = asyncio.Event()
        owner_task = asyncio.current_task()
        renewal_task = asyncio.create_task(
            renew_worker_leases_loop(
                sessionmaker,
                keys=tuple(acquired),
                owner=owner,
                ttl_seconds=ttl_seconds,
                renewal_stop_event=renewal_stop_event,
                runtime_stop_event=runtime_stop_event,
                owner_task=owner_task,
            ),
            name="worker-capability-lease-renewal",
        )
        try:
            yield owner
        finally:
            renewal_stop_event.set()
            renewal_task.cancel()
            with suppress(asyncio.CancelledError):
                await renewal_task
    finally:
        for key in reversed(acquired):
            await release_worker_lease_safely(sessionmaker, key=key, owner=owner)


async def acquire_worker_lease(
    sessionmaker: Any,
    *,
    key: str,
    owner: str,
    ttl_seconds: int,
) -> bool:
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                """
                insert into job_locks (key, owner, locked_until, updated_at)
                values (:key, :owner, now() + (:ttl_seconds * interval '1 second'), now())
                on conflict (key) do update
                set
                  owner = excluded.owner,
                  locked_until = excluded.locked_until,
                  updated_at = now()
                where job_locks.locked_until <= now()
                  or job_locks.owner = excluded.owner
                returning key
                """
            ),
            {"key": key, "owner": owner, "ttl_seconds": ttl_seconds},
        )
        acquired = result.scalar_one_or_none() is not None
        await session.commit()
        return acquired


async def renew_worker_leases_loop(
    sessionmaker: Any,
    *,
    keys: tuple[str, ...],
    owner: str,
    ttl_seconds: int,
    renewal_stop_event: asyncio.Event,
    runtime_stop_event: asyncio.Event,
    owner_task: asyncio.Task[Any] | None,
) -> None:
    interval_seconds = worker_lease_renewal_interval_seconds(ttl_seconds)
    timeout_seconds = worker_lease_renewal_timeout_seconds(ttl_seconds)
    while not renewal_stop_event.is_set():
        try:
            await asyncio.wait_for(renewal_stop_event.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            pass

        try:
            renewed = await asyncio.wait_for(
                renew_worker_leases(
                    sessionmaker,
                    keys=keys,
                    owner=owner,
                    ttl_seconds=ttl_seconds,
                ),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            logger.exception("Worker capability lease renewal failed.")
            runtime_stop_event.set()
            if owner_task is not None and not owner_task.done():
                owner_task.cancel(
                    WorkerCapabilityLeaseLostError(
                        f"Worker capability lease renewal failed: {exc.__class__.__name__}."
                    )
                )
            return

        if renewed != len(keys):
            logger.error("Worker capability lease ownership was lost.")
            runtime_stop_event.set()
            if owner_task is not None and not owner_task.done():
                owner_task.cancel(
                    WorkerCapabilityLeaseLostError("Worker capability lease ownership was lost.")
                )
            return


def worker_lease_renewal_interval_seconds(ttl_seconds: int) -> float:
    return max(5.0, ttl_seconds / 3)


def worker_lease_renewal_timeout_seconds(ttl_seconds: int) -> float:
    if ttl_seconds <= 1:
        return max(ttl_seconds / 2, 0.01)
    return max(1.0, min(ttl_seconds / 3, ttl_seconds - 1))


async def renew_worker_leases(
    sessionmaker: Any,
    *,
    keys: tuple[str, ...],
    owner: str,
    ttl_seconds: int,
) -> int:
    if not keys:
        return 0
    async with sessionmaker() as session:
        result = await session.execute(
            text(
                """
                update job_locks
                set
                  locked_until = now() + (:ttl_seconds * interval '1 second'),
                  updated_at = now()
                where key = any(:keys)
                  and owner = :owner
                returning key
                """
            ),
            {"keys": list(keys), "owner": owner, "ttl_seconds": ttl_seconds},
        )
        renewed = len(result.scalars().all())
        await session.commit()
        return renewed


async def release_worker_lease_safely(
    sessionmaker: Any,
    *,
    key: str,
    owner: str,
) -> None:
    try:
        async with sessionmaker() as session:
            await session.execute(
                text("delete from job_locks where key = :key and owner = :owner"),
                {"key": key, "owner": owner},
            )
            await session.commit()
    except Exception:
        logger.exception("Failed to release worker capability lease %s.", key)
