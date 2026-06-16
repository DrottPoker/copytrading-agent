import os
import socket
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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

    try:
        yield
    finally:
        await release_job_lock(session, key=key, owner=owner)


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
            returning key
            """
        ),
        {"key": key, "owner": owner, "ttl_seconds": ttl_seconds},
    )
    acquired = result.scalar_one_or_none() is not None
    await session.commit()
    return acquired


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


def job_lock_owner() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"
