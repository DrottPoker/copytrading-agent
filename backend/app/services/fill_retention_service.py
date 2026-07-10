from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.database import FillRetentionCleanupResponse
from app.services.job_lock_service import job_lock
from app.services.wallet_data_policy import protected_wallets_cte

RETENTION_LOCK_KEY = "fill_retention_cleanup"
RETENTION_LOCK_TTL_SECONDS = 900

PROTECTED_WALLETS_CTE = protected_wallets_cte(include_top_scores=True)


async def cleanup_wallet_fill_retention(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    retention_days: int = 90,
    batch_size: int = 5000,
    max_rows: int = 50000,
    protect_top_score_wallets: int = 50,
    use_lock: bool = True,
) -> FillRetentionCleanupResponse:
    if use_lock:
        async with job_lock(
            session,
            key=RETENTION_LOCK_KEY,
            ttl_seconds=RETENTION_LOCK_TTL_SECONDS,
        ):
            return await cleanup_wallet_fill_retention(
                session,
                dry_run=dry_run,
                retention_days=retention_days,
                batch_size=batch_size,
                max_rows=max_rows,
                protect_top_score_wallets=protect_top_score_wallets,
                use_lock=False,
            )

    retention_days = max(61, retention_days)
    batch_size = max(100, batch_size)
    max_rows = max(100, max_rows)
    cutoff_time_ms = int((datetime.now(UTC) - timedelta(days=retention_days)).timestamp() * 1000)
    params = {
        "cutoff_time_ms": cutoff_time_ms,
        "protect_top_score_wallets": protect_top_score_wallets,
    }

    protected_wallets = await count_protected_wallets(session, params=params)
    fill_candidates = await count_fill_candidates(session, params=params)
    source_trade_candidates = await count_source_trade_candidates(session, params=params)
    ignored_fill_candidates = await count_ignored_fill_candidates(session, params=params)

    if dry_run:
        return FillRetentionCleanupResponse(
            dry_run=True,
            retention_days=retention_days,
            cutoff_time_ms=cutoff_time_ms,
            protected_wallets=protected_wallets,
            candidate_fills=int(fill_candidates["rows"]),
            candidate_wallets=int(fill_candidates["wallets"]),
            candidate_source_trades=source_trade_candidates,
            candidate_ignored_fills=ignored_fill_candidates,
            deleted_fills=0,
            deleted_source_trades=0,
            deleted_ignored_fills=0,
            affected_wallets=0,
            remaining_candidate_fills=int(fill_candidates["rows"]),
            batch_size=batch_size,
            max_rows=max_rows,
            protect_top_score_wallets=protect_top_score_wallets,
            note=(
                "Dry run only. Cleanup deletes old unprotected wallet_fills and "
                "old derived source-trade rows in bounded batches."
            ),
        )

    deleted_fills = 0
    deleted_source_trades = 0
    deleted_ignored_fills = 0
    affected_wallets: set[str] = set()

    while deleted_fills < max_rows:
        delete_limit = min(batch_size, max_rows - deleted_fills)
        batch = await delete_old_fill_batch(
            session,
            params=params,
            batch_size=delete_limit,
        )
        if batch["deleted_rows"] == 0:
            break
        deleted_fills += batch["deleted_rows"]
        batch_wallets = set(batch["affected_wallets"])
        affected_wallets.update(batch_wallets)
        await delete_sync_states(session, addresses=sorted(batch_wallets))
        await session.commit()

    source_trade_result = await delete_old_source_trade_batch(
        session,
        params=params,
        max_rows=max_rows,
    )
    deleted_source_trades = source_trade_result["deleted_rows"]
    affected_wallets.update(source_trade_result["affected_wallets"])
    await delete_sync_states(session, addresses=source_trade_result["affected_wallets"])
    await session.commit()

    ignored_fill_result = await delete_old_ignored_fill_batch(
        session,
        params=params,
        max_rows=max_rows,
    )
    deleted_ignored_fills = ignored_fill_result["deleted_rows"]
    affected_wallets.update(ignored_fill_result["affected_wallets"])
    await delete_sync_states(session, addresses=ignored_fill_result["affected_wallets"])
    await session.commit()

    remaining = await count_fill_candidates(session, params=params)
    return FillRetentionCleanupResponse(
        dry_run=False,
        retention_days=retention_days,
        cutoff_time_ms=cutoff_time_ms,
        protected_wallets=protected_wallets,
        candidate_fills=int(fill_candidates["rows"]),
        candidate_wallets=int(fill_candidates["wallets"]),
        candidate_source_trades=source_trade_candidates,
        candidate_ignored_fills=ignored_fill_candidates,
        deleted_fills=deleted_fills,
        deleted_source_trades=deleted_source_trades,
        deleted_ignored_fills=deleted_ignored_fills,
        affected_wallets=len(affected_wallets),
        remaining_candidate_fills=int(remaining["rows"]),
        batch_size=batch_size,
        max_rows=max_rows,
        protect_top_score_wallets=protect_top_score_wallets,
        note=(
            "Cleanup completed. Postgres can reuse deleted space after vacuum, "
            "but total database file size may not shrink immediately."
        ),
    )


async def count_protected_wallets(session: AsyncSession, *, params: dict[str, int]) -> int:
    row = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE}
            select count(*)::int as protected_wallets
            from protected_wallets
            """
        ),
        params,
    )
    return int(row.scalar_one() or 0)


async def count_fill_candidates(
    session: AsyncSession,
    *,
    params: dict[str, int],
) -> dict[str, int]:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE}
            select
              count(*)::int as rows,
              count(distinct wf.wallet_address)::int as wallets
            from wallet_fills wf
            left join protected_wallets pw on pw.wallet_address = wf.wallet_address
            where wf.timestamp_ms < :cutoff_time_ms
              and pw.wallet_address is null
            """
        ),
        params,
    )
    row = result.mappings().one()
    return {"rows": int(row["rows"] or 0), "wallets": int(row["wallets"] or 0)}


async def count_source_trade_candidates(
    session: AsyncSession,
    *,
    params: dict[str, int],
) -> int:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE}
            select count(*)::int as rows
            from source_trades st
            left join protected_wallets pw on pw.wallet_address = st.wallet_address
            where st.status = 'closed'
              and coalesce(st.closed_at_ms, st.opened_at_ms) < :cutoff_time_ms
              and pw.wallet_address is null
            """
        ),
        params,
    )
    return int(result.scalar_one() or 0)


async def count_ignored_fill_candidates(
    session: AsyncSession,
    *,
    params: dict[str, int],
) -> int:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE}
            select count(*)::int as rows
            from source_trade_ignored_fills sif
            left join protected_wallets pw on pw.wallet_address = sif.wallet_address
            where sif.timestamp_ms < :cutoff_time_ms
              and pw.wallet_address is null
            """
        ),
        params,
    )
    return int(result.scalar_one() or 0)


async def delete_old_fill_batch(
    session: AsyncSession,
    *,
    params: dict[str, int],
    batch_size: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE},
            target as (
              select wf.id, wf.wallet_address
              from wallet_fills wf
              left join protected_wallets pw on pw.wallet_address = wf.wallet_address
              where wf.timestamp_ms < :cutoff_time_ms
                and pw.wallet_address is null
              order by wf.timestamp_ms asc, wf.id asc
              limit :batch_size
            ),
            deleted as (
              delete from wallet_fills wf
              using target
              where wf.id = target.id
              returning wf.wallet_address
            )
            select
              count(*)::int as deleted_rows,
              coalesce(array_agg(distinct wallet_address), array[]::text[]) as affected_wallets
            from deleted
            """
        ),
        {**params, "batch_size": batch_size},
    )
    row = result.mappings().one()
    return {
        "deleted_rows": int(row["deleted_rows"] or 0),
        "affected_wallets": string_list(row["affected_wallets"]),
    }


async def delete_old_source_trade_batch(
    session: AsyncSession,
    *,
    params: dict[str, int],
    max_rows: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE},
            target as (
              select st.id, st.wallet_address
              from source_trades st
              left join protected_wallets pw on pw.wallet_address = st.wallet_address
              where st.status = 'closed'
                and coalesce(st.closed_at_ms, st.opened_at_ms) < :cutoff_time_ms
                and pw.wallet_address is null
              order by coalesce(st.closed_at_ms, st.opened_at_ms) asc, st.id asc
              limit :max_rows
            ),
            deleted as (
              delete from source_trades st
              using target
              where st.id = target.id
              returning st.wallet_address
            )
            select
              count(*)::int as deleted_rows,
              coalesce(array_agg(distinct wallet_address), array[]::text[]) as affected_wallets
            from deleted
            """
        ),
        {**params, "max_rows": max_rows},
    )
    row = result.mappings().one()
    return {
        "deleted_rows": int(row["deleted_rows"] or 0),
        "affected_wallets": string_list(row["affected_wallets"]),
    }


async def delete_old_ignored_fill_batch(
    session: AsyncSession,
    *,
    params: dict[str, int],
    max_rows: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"""
            with {PROTECTED_WALLETS_CTE},
            target as (
              select sif.id, sif.wallet_address
              from source_trade_ignored_fills sif
              left join protected_wallets pw on pw.wallet_address = sif.wallet_address
              where sif.timestamp_ms < :cutoff_time_ms
                and pw.wallet_address is null
              order by sif.timestamp_ms asc, sif.id asc
              limit :max_rows
            ),
            deleted as (
              delete from source_trade_ignored_fills sif
              using target
              where sif.id = target.id
              returning sif.wallet_address
            )
            select
              count(*)::int as deleted_rows,
              coalesce(array_agg(distinct wallet_address), array[]::text[]) as affected_wallets
            from deleted
            """
        ),
        {**params, "max_rows": max_rows},
    )
    row = result.mappings().one()
    return {
        "deleted_rows": int(row["deleted_rows"] or 0),
        "affected_wallets": string_list(row["affected_wallets"]),
    }


async def delete_sync_states(session: AsyncSession, *, addresses: list[str]) -> None:
    if not addresses:
        return
    statement = text(
        "delete from source_trade_sync_states where wallet_address in :addresses"
    ).bindparams(bindparam("addresses", expanding=True))
    await session.execute(statement, {"addresses": addresses})


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, tuple):
        return [str(item) for item in value if item]
    return []
