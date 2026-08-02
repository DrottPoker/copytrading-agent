import asyncio
import logging
import os
import socket
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_sessionmaker

logger = logging.getLogger(__name__)
JOB_LOCK_RENEWAL_MAX_INTERVAL_SECONDS = 30
JOB_LOCK_STALE_AFTER_SECONDS = 90


class JobLockAlreadyHeldError(RuntimeError):
    pass


@asynccontextmanager
async def job_lock(
    session: AsyncSession,
    *,
    key: str,
    ttl_seconds: int,
):
    owner = job_lock_owner()
    acquired = await try_acquire_job_lock(
        session,
        key=key,
        owner=owner,
        ttl_seconds=ttl_seconds,
    )
    if not acquired:
        raise JobLockAlreadyHeldError(f"Job lock is already held: {key}.")

    stop_event = asyncio.Event()
    owner_task = asyncio.current_task()
    renewal_task = asyncio.create_task(
        renew_job_lock_loop(
            key=key,
            owner=owner,
            ttl_seconds=ttl_seconds,
            stop_event=stop_event,
            owner_task=owner_task,
        )
    )

    try:
        yield
    except BaseException:
        await rollback_session(session)
        raise
    finally:
        stop_event.set()
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
        await release_job_lock_safely(session, key=key, owner=owner)


async def try_acquire_job_lock(
    session: AsyncSession,
    *,
    key: str,
    owner: str,
    ttl_seconds: int,
) -> bool:
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
              or job_locks.updated_at <=
                now() - (:stale_after_seconds * interval '1 second')
            returning key
            """
        ),
        {
            "key": key,
            "owner": owner,
            "ttl_seconds": ttl_seconds,
            "stale_after_seconds": JOB_LOCK_STALE_AFTER_SECONDS,
        },
    )
    acquired = result.scalar_one_or_none() is not None
    await session.commit()
    return acquired


async def job_lock_is_active(session: AsyncSession, *, key: str) -> bool:
    result = await session.scalar(
        text(
            """
            select exists(
              select 1
              from job_locks
              where key = :key
                and locked_until > now()
                and updated_at >
                  now() - (:stale_after_seconds * interval '1 second')
            )
            """
        ),
        {
            "key": key,
            "stale_after_seconds": JOB_LOCK_STALE_AFTER_SECONDS,
        },
    )
    return bool(result)


async def release_job_lock(
    session: AsyncSession,
    *,
    key: str,
    owner: str,
) -> None:
    await session.execute(
        text("delete from job_locks where key = :key and owner = :owner"),
        {"key": key, "owner": owner},
    )
    await session.commit()


async def release_job_lock_safely(
    session: AsyncSession,
    *,
    key: str,
    owner: str,
) -> None:
    try:
        await release_job_lock(session, key=key, owner=owner)
    except Exception:
        await rollback_session(session)
        try:
            await release_job_lock(session, key=key, owner=owner)
        except Exception:
            await rollback_session(session)


async def renew_job_lock_loop(
    *,
    key: str,
    owner: str,
    ttl_seconds: int,
    stop_event: asyncio.Event,
    owner_task: asyncio.Task | None,
) -> None:
    interval_seconds = job_lock_renewal_interval_seconds(ttl_seconds)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            pass

        try:
            renewed = await renew_job_lock(key=key, owner=owner, ttl_seconds=ttl_seconds)
        except Exception:
            logger.exception("Failed to renew job lock %s.", key)
            if not stop_event.is_set() and owner_task is not None:
                owner_task.cancel()
            return

        if not renewed:
            logger.warning("Job lock %s is no longer owned by this worker.", key)
            if not stop_event.is_set() and owner_task is not None:
                owner_task.cancel()
            return


async def renew_job_lock(
    *,
    key: str,
    owner: str,
    ttl_seconds: int,
) -> bool:
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        return True

    async with sessionmaker() as session:
        result = await session.execute(
            text(
                """
                update job_locks
                set
                  locked_until = now() + (:ttl_seconds * interval '1 second'),
                  updated_at = now()
                where key = :key
                  and owner = :owner
                returning key
                """
            ),
            {"key": key, "owner": owner, "ttl_seconds": ttl_seconds},
        )
        renewed = result.scalar_one_or_none() is not None
        await session.commit()
        return renewed


def job_lock_renewal_interval_seconds(ttl_seconds: int) -> int:
    return max(5, min(JOB_LOCK_RENEWAL_MAX_INTERVAL_SECONDS, ttl_seconds // 3))


async def rollback_session(session: AsyncSession) -> None:
    try:
        await session.rollback()
    except Exception:
        pass


def job_lock_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
