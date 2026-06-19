from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.database import IgnoredFillCleanupResponse
from app.services.job_lock_service import job_lock

IGNORED_FILL_CLEANUP_LOCK_KEY = "ignored_fill_cleanup"
IGNORED_FILL_CLEANUP_LOCK_TTL_SECONDS = 900

IGNORED_FILL_CANDIDATES_CTE = """
ignored_matches as (
  select
    wf.id as wallet_fill_id,
    wf.wallet_address,
    wf.external_fill_id,
    wf.coin,
    wf.timestamp_ms,
    sif.id as ignored_fill_id,
    sif.reason,
    case
      when wf.raw_json->>'dir' in ('Close Long', 'Long > Short') then 'long'
      when wf.raw_json->>'dir' in ('Close Short', 'Short > Long') then 'short'
      else null
    end as close_side
  from source_trade_ignored_fills sif
  join wallet_fills wf
    on wf.wallet_address = sif.wallet_address
   and wf.external_fill_id = sif.external_fill_id
  where wf.timestamp_ms < :cutoff_time_ms
),
candidate_ignored_fills as (
  select im.*
  from ignored_matches im
  where im.reason = 'preexisting_open'
     or (
       im.reason = 'unmatched_close'
       and im.close_side is not null
       and not exists (
         select 1
         from source_trades st
         where st.wallet_address = im.wallet_address
           and st.coin = im.coin
           and st.side = im.close_side
           and st.closed_at_ms = im.timestamp_ms
       )
     )
),
excluded_potential_trade_closes as (
  select im.*
  from ignored_matches im
  where im.reason = 'unmatched_close'
    and exists (
      select 1
      from source_trades st
      where st.wallet_address = im.wallet_address
        and st.coin = im.coin
        and st.side = im.close_side
        and st.closed_at_ms = im.timestamp_ms
    )
)
"""


async def cleanup_ignored_wallet_fills(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    min_age_days: int = 7,
    max_rows: int = 50000,
    use_lock: bool = True,
) -> IgnoredFillCleanupResponse:
    if use_lock:
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
    result = await session.execute(
        text(
            f"""
            with {IGNORED_FILL_CANDIDATES_CTE}
            select
              count(distinct wallet_fill_id)::int as candidate_fills,
              count(distinct wallet_address)::int as candidate_wallets,
              count(distinct wallet_fill_id)
                filter (where reason = 'preexisting_open')::int
                as candidate_preexisting_open_fills,
              count(distinct wallet_fill_id)
                filter (where reason = 'unmatched_close')::int
                as candidate_unmatched_close_fills,
              (
                select count(distinct wallet_fill_id)::int
                from excluded_potential_trade_closes
              ) as excluded_potential_trade_close_fills
            from candidate_ignored_fills
            """
        ),
        params,
    )
    row = result.mappings().one()
    return {
        "candidate_fills": int(row["candidate_fills"] or 0),
        "candidate_wallets": int(row["candidate_wallets"] or 0),
        "candidate_preexisting_open_fills": int(
            row["candidate_preexisting_open_fills"] or 0
        ),
        "candidate_unmatched_close_fills": int(
            row["candidate_unmatched_close_fills"] or 0
        ),
        "excluded_potential_trade_close_fills": int(
            row["excluded_potential_trade_close_fills"] or 0
        ),
    }


async def delete_ignored_wallet_fill_batch(
    session: AsyncSession,
    *,
    params: dict[str, int],
    max_rows: int,
) -> dict[str, Any]:
    result = await session.execute(
        text(
            f"""
            with {IGNORED_FILL_CANDIDATES_CTE},
            target_fills as (
              select distinct wallet_fill_id, wallet_address, external_fill_id
              from candidate_ignored_fills
              order by wallet_fill_id
              limit :max_rows
            ),
            deleted_fills as (
              delete from wallet_fills wf
              using target_fills target
              where wf.id = target.wallet_fill_id
              returning wf.wallet_address
            ),
            deleted_ignored as (
              delete from source_trade_ignored_fills sif
              using target_fills target
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
              coalesce(array_agg(distinct wallet_address)::text[], array[]::text[])
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
        "affected_wallets": string_list(row["affected_wallets"]),
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
