from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.database import IgnoredFillCleanupResponse
from app.services.job_lock_service import job_lock

IGNORED_FILL_CLEANUP_LOCK_KEY = "ignored_fill_cleanup"
IGNORED_FILL_CLEANUP_LOCK_TTL_SECONDS = 900

MATCHING_WALLET_FILL_EXISTS_SQL = """
exists (
  select 1
  from wallet_fills wf
  where wf.wallet_address = sif.wallet_address
    and wf.external_fill_id = sif.external_fill_id
)
"""

POTENTIAL_SOURCE_TRADE_CLOSE_EXISTS_SQL = """
exists (
  select 1
  from source_trades st
  where st.wallet_address = sif.wallet_address
    and st.closed_at_ms = sif.timestamp_ms
)
"""

PREEXISTING_OPEN_CANDIDATE_WHERE_SQL = f"""
sif.reason = 'preexisting_open'
and sif.timestamp_ms < :cutoff_time_ms
and {MATCHING_WALLET_FILL_EXISTS_SQL}
"""

UNMATCHED_CLOSE_CANDIDATE_WHERE_SQL = f"""
sif.reason = 'unmatched_close'
and sif.timestamp_ms < :cutoff_time_ms
and {MATCHING_WALLET_FILL_EXISTS_SQL}
and not {POTENTIAL_SOURCE_TRADE_CLOSE_EXISTS_SQL}
"""

EXCLUDED_POTENTIAL_TRADE_CLOSE_WHERE_SQL = f"""
sif.reason = 'unmatched_close'
and sif.timestamp_ms < :cutoff_time_ms
and {MATCHING_WALLET_FILL_EXISTS_SQL}
and {POTENTIAL_SOURCE_TRADE_CLOSE_EXISTS_SQL}
"""


async def cleanup_ignored_wallet_fills(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    min_age_days: int = 7,
    max_rows: int = 50000,
    use_lock: bool = True,
) -> IgnoredFillCleanupResponse:
    if use_lock and not dry_run:
        async with job_lock(
            session,
            key=IGNORED_FILL_CLEANUP_LOCK_KEY,
            ttl_seconds=IGNORED_FILL_CLEANUP_LOCK_TTL_SECONDS,
        ):
            return await cleanup_ignored_wallet_fills(
                session,
                dry_run=dry_run,
                min_age_days=min_age_days,
                max_rows=max_rows,
                use_lock=False,
            )

    min_age_days = max(0, min_age_days)
    max_rows = max(100, max_rows)
    cutoff_time_ms = int(
        (datetime.now(UTC) - timedelta(days=min_age_days)).timestamp() * 1000
    )
    params = {"cutoff_time_ms": cutoff_time_ms}
    candidates = await count_ignored_wallet_fill_candidates(session, params=params)

    if dry_run:
        return IgnoredFillCleanupResponse(
            dry_run=True,
            min_age_days=min_age_days,
            cutoff_time_ms=cutoff_time_ms,
            candidate_fills=candidates["candidate_fills"],
            candidate_wallets=candidates["candidate_wallets"],
            candidate_preexisting_open_fills=candidates["candidate_preexisting_open_fills"],
            candidate_unmatched_close_fills=candidates["candidate_unmatched_close_fills"],
            excluded_potential_trade_close_fills=candidates[
                "excluded_potential_trade_close_fills"
            ],
            deleted_fills=0,
            deleted_ignored_fill_markers=0,
            affected_wallets=0,
            remaining_candidate_fills=candidates["candidate_fills"],
            max_rows=max_rows,
            note=(
                "Dry run only. Cleanup targets raw wallet_fills that were classified "
                "as close-only or pre-existing-position fills and are not needed for "
                "a reconstructed source trade."
            ),
        )

    try:
        delete_result = await delete_ignored_wallet_fill_batch(
            session,
            params=params,
            max_rows=max_rows,
        )
        await delete_source_trade_sync_states(
            session,
            addresses=delete_result["affected_wallets"],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    remaining = await count_ignored_wallet_fill_candidates(session, params=params)
    return IgnoredFillCleanupResponse(
        dry_run=False,
        min_age_days=min_age_days,
        cutoff_time_ms=cutoff_time_ms,
        candidate_fills=candidates["candidate_fills"],
        candidate_wallets=candidates["candidate_wallets"],
        candidate_preexisting_open_fills=candidates["candidate_preexisting_open_fills"],
        candidate_unmatched_close_fills=candidates["candidate_unmatched_close_fills"],
        excluded_potential_trade_close_fills=candidates[
            "excluded_potential_trade_close_fills"
        ],
        deleted_fills=delete_result["deleted_fills"],
        deleted_ignored_fill_markers=delete_result["deleted_ignored_fill_markers"],
        affected_wallets=len(delete_result["affected_wallets"]),
        remaining_candidate_fills=remaining["candidate_fills"],
        max_rows=max_rows,
        note=(
            "Cleanup completed. Source-trade sync state was cleared for affected "
            "wallets so materialized trade diagnostics can rebuild from remaining "
            "fills."
        ),
    )


async def count_ignored_wallet_fill_candidates(
    session: AsyncSession,
    *,
    params: dict[str, int],
) -> dict[str, int]:
    preexisting_open_count = await count_candidate_rows(
        session,
        where_sql=PREEXISTING_OPEN_CANDIDATE_WHERE_SQL,
        params=params,
    )
    unmatched_close_count = await count_candidate_rows(
        session,
        where_sql=UNMATCHED_CLOSE_CANDIDATE_WHERE_SQL,
        params=params,
    )
    excluded_potential_trade_close_count = await count_candidate_rows(
        session,
        where_sql=EXCLUDED_POTENTIAL_TRADE_CLOSE_WHERE_SQL,
        params=params,
    )
    candidate_wallet_count = await count_candidate_wallets(session, params=params)
    return {
        "candidate_fills": preexisting_open_count + unmatched_close_count,
        "candidate_wallets": candidate_wallet_count,
        "candidate_preexisting_open_fills": preexisting_open_count,
        "candidate_unmatched_close_fills": unmatched_close_count,
        "excluded_potential_trade_close_fills": excluded_potential_trade_close_count,
    }


async def count_candidate_rows(
    session: AsyncSession,
    *,
    where_sql: str,
    params: dict[str, int],
) -> int:
    result = await session.execute(
        text(
            f"""
            select count(*)::int as rows
            from source_trade_ignored_fills sif
            where {where_sql}
            """
        ),
        params,
    )
    return int(result.scalar_one() or 0)


async def count_candidate_wallets(
    session: AsyncSession,
    *,
    params: dict[str, int],
) -> int:
    result = await session.execute(
        text(
            f"""
            select sif.wallet_address
            from source_trade_ignored_fills sif
            where {PREEXISTING_OPEN_CANDIDATE_WHERE_SQL}
            union all
            select sif.wallet_address
            from source_trade_ignored_fills sif
            where {UNMATCHED_CLOSE_CANDIDATE_WHERE_SQL}
            """
        ),
        params,
    )
    return len({str(row[0]) for row in result.all() if row[0]})


async def delete_ignored_wallet_fill_batch(
    session: AsyncSession,
    *,
    params: dict[str, int],
    max_rows: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"""
            with target_markers as (
              select id, wallet_address, external_fill_id
              from (
                select sif.id, sif.wallet_address, sif.external_fill_id
                from source_trade_ignored_fills sif
                where {PREEXISTING_OPEN_CANDIDATE_WHERE_SQL}
                union all
                select sif.id, sif.wallet_address, sif.external_fill_id
                from source_trade_ignored_fills sif
                where {UNMATCHED_CLOSE_CANDIDATE_WHERE_SQL}
              ) candidates
              limit :max_rows
            ),
            deleted_fills as (
              delete from wallet_fills wf
              using target_markers target
              where wf.wallet_address = target.wallet_address
                and wf.external_fill_id = target.external_fill_id
              returning wf.wallet_address
            ),
            deleted_ignored as (
              delete from source_trade_ignored_fills sif
              using target_markers target
              where sif.wallet_address = target.wallet_address
                and sif.external_fill_id = target.external_fill_id
              returning sif.wallet_address
            ),
            affected as (
              select wallet_address from deleted_fills
              union
              select wallet_address from deleted_ignored
            )
            select
              (select count(*)::int from deleted_fills) as deleted_fills,
              (select count(*)::int from deleted_ignored)
                as deleted_ignored_fill_markers,
              coalesce(array_agg(wallet_address)::text[], array[]::text[])
                as affected_wallets
            from affected
            """
        ),
        {**params, "max_rows": max_rows},
    )
    row = result.mappings().one()
    return {
        "deleted_fills": int(row["deleted_fills"] or 0),
        "deleted_ignored_fill_markers": int(row["deleted_ignored_fill_markers"] or 0),
        "affected_wallets": sorted(set(string_list(row["affected_wallets"]))),
    }


async def delete_source_trade_sync_states(
    session: AsyncSession,
    *,
    addresses: list[str],
) -> None:
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
