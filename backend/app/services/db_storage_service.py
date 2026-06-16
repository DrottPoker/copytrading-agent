from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class DatabaseStorageBudget:
    used_bytes: int
    limit_bytes: int | None
    free_bytes: int | None
    has_budget: bool


async def check_database_storage_budget(
    session: AsyncSession,
    *,
    min_free_mb: int,
) -> DatabaseStorageBudget:
    try:
        result = await session.execute(
            text(
                """
                select
                  pg_database_size(current_database()) as used_bytes,
                  pg_size_bytes(current_setting('neon.max_cluster_size', true)) as limit_bytes
                """
            )
        )
        row = result.mappings().one()
    except Exception:
        return DatabaseStorageBudget(
            used_bytes=0,
            limit_bytes=None,
            free_bytes=None,
            has_budget=True,
        )

    used_bytes = int(row["used_bytes"])
    limit_bytes = int(row["limit_bytes"]) if row["limit_bytes"] is not None else None
    if limit_bytes is None:
        return DatabaseStorageBudget(
            used_bytes=used_bytes,
            limit_bytes=None,
            free_bytes=None,
            has_budget=True,
        )

    free_bytes = limit_bytes - used_bytes
    min_free_bytes = min_free_mb * 1024 * 1024
    return DatabaseStorageBudget(
        used_bytes=used_bytes,
        limit_bytes=limit_bytes,
        free_bytes=free_bytes,
        has_budget=free_bytes >= min_free_bytes,
    )
