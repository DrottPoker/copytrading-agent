from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.schemas.database import FillRawJsonCompactResponse

DEFAULT_COMPACT_BATCH_SIZE = 5000
DEFAULT_COMPACT_MAX_ROWS = 50000


async def compact_wallet_fill_raw_json(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    batch_size: int = DEFAULT_COMPACT_BATCH_SIZE,
    max_rows: int = DEFAULT_COMPACT_MAX_ROWS,
    settings: Settings | None = None,
) -> FillRawJsonCompactResponse:
    resolved_settings = settings or get_settings()
    kept_fields = list(dict.fromkeys(resolved_settings.fill_import_raw_json_fields))
    compact_expr, field_params = compact_expression(kept_fields)
    batch_size = max(1, batch_size)
    max_rows = max(1, max_rows)

    candidate_fills = await count_candidates(session, compact_expr, field_params)
    if dry_run:
        before_bytes, after_bytes, sample_count = await sample_candidate_bytes(
            session,
            compact_expr,
            field_params,
            sample_size=min(batch_size, max_rows),
        )
        estimated_saved = estimate_total_saved(
            before_bytes=before_bytes,
            after_bytes=after_bytes,
            sample_count=sample_count,
            candidate_count=candidate_fills,
        )
        return FillRawJsonCompactResponse(
            dry_run=True,
            candidate_fills=candidate_fills,
            processed_fills=0,
            remaining_candidates=candidate_fills,
            before_raw_json_bytes=before_bytes,
            after_raw_json_bytes=after_bytes,
            saved_raw_json_bytes=estimated_saved,
            kept_fields=kept_fields,
            batch_size=batch_size,
            max_rows=max_rows,
            note=(
                "Dry run only. Saved bytes are estimated from the sampled candidate rows. "
                "Run compact to rewrite old raw_json payloads in batches."
            ),
        )

    processed_fills = 0
    before_bytes = 0
    after_bytes = 0
    while processed_fills < max_rows:
        rows_to_update = min(batch_size, max_rows - processed_fills)
        batch = await compact_candidate_batch(
            session,
            compact_expr,
            field_params,
            batch_size=rows_to_update,
        )
        if batch.processed == 0:
            break
        processed_fills += batch.processed
        before_bytes += batch.before_bytes
        after_bytes += batch.after_bytes
        await session.commit()

    remaining_candidates = await count_candidates(session, compact_expr, field_params)
    return FillRawJsonCompactResponse(
        dry_run=False,
        candidate_fills=candidate_fills,
        processed_fills=processed_fills,
        remaining_candidates=remaining_candidates,
        before_raw_json_bytes=before_bytes,
        after_raw_json_bytes=after_bytes,
        saved_raw_json_bytes=max(0, before_bytes - after_bytes),
        kept_fields=kept_fields,
        batch_size=batch_size,
        max_rows=max_rows,
        note=(
            "Compacted old raw_json payloads. Postgres may keep old tuple versions until vacuum, "
            "so total database size can lag behind the saved raw_json bytes."
        ),
    )


class CompactBatchResult:
    def __init__(self, *, processed: int, before_bytes: int, after_bytes: int) -> None:
        self.processed = processed
        self.before_bytes = before_bytes
        self.after_bytes = after_bytes


async def compact_candidate_batch(
    session: AsyncSession,
    compact_expr: str,
    field_params: dict[str, str],
    *,
    batch_size: int,
) -> CompactBatchResult:
    statement = text(
        f"""
        with candidates as (
          select
            wallet_address,
            external_fill_id,
            pg_column_size(raw_json)::bigint as before_bytes,
            pg_column_size({compact_expr})::bigint as after_bytes,
            {compact_expr} as compact_raw_json
          from wallet_fills
          where external_fill_id is not null
            and raw_json <> {compact_expr}
          order by created_at asc
          limit :batch_size
        ),
        updated as (
          update wallet_fills wf
          set raw_json = candidates.compact_raw_json
          from candidates
          where wf.wallet_address = candidates.wallet_address
            and wf.external_fill_id = candidates.external_fill_id
          returning candidates.before_bytes, candidates.after_bytes
        )
        select
          count(*)::int as processed,
          coalesce(sum(before_bytes), 0)::bigint as before_bytes,
          coalesce(sum(after_bytes), 0)::bigint as after_bytes
        from updated
        """
    )
    row = (
        await session.execute(
            statement,
            {
                **field_params,
                "batch_size": batch_size,
            },
        )
    ).mappings().one()
    return CompactBatchResult(
        processed=int(row["processed"] or 0),
        before_bytes=int(row["before_bytes"] or 0),
        after_bytes=int(row["after_bytes"] or 0),
    )


async def count_candidates(
    session: AsyncSession,
    compact_expr: str,
    field_params: dict[str, str],
) -> int:
    statement = text(
        f"""
        select count(*)::int as candidate_fills
        from wallet_fills
        where external_fill_id is not null
          and raw_json <> {compact_expr}
        """
    )
    return int((await session.execute(statement, field_params)).scalar_one() or 0)


async def sample_candidate_bytes(
    session: AsyncSession,
    compact_expr: str,
    field_params: dict[str, str],
    *,
    sample_size: int,
) -> tuple[int, int, int]:
    statement = text(
        f"""
        with candidates as (
          select
            pg_column_size(raw_json)::bigint as before_bytes,
            pg_column_size({compact_expr})::bigint as after_bytes
          from wallet_fills
          where external_fill_id is not null
            and raw_json <> {compact_expr}
          order by created_at asc
          limit :sample_size
        )
        select
          count(*)::int as sampled,
          coalesce(sum(before_bytes), 0)::bigint as before_bytes,
          coalesce(sum(after_bytes), 0)::bigint as after_bytes
        from candidates
        """
    )
    row = (
        await session.execute(
            statement,
            {
                **field_params,
                "sample_size": sample_size,
            },
        )
    ).mappings().one()
    return (
        int(row["before_bytes"] or 0),
        int(row["after_bytes"] or 0),
        int(row["sampled"] or 0),
    )


def compact_expression(fields: list[str]) -> tuple[str, dict[str, str]]:
    if not fields:
        return "'{}'::jsonb", {}

    params: dict[str, str] = {}
    parts: list[str] = []
    for index, field in enumerate(fields):
        key = f"field_{index}"
        params[key] = field
        parts.extend([f"cast(:{key} as text)", f"raw_json -> cast(:{key} as text)"])
    return f"jsonb_strip_nulls(jsonb_build_object({', '.join(parts)}))", params


def estimate_total_saved(
    *,
    before_bytes: int,
    after_bytes: int,
    sample_count: int,
    candidate_count: int,
) -> int:
    if sample_count <= 0 or candidate_count <= 0:
        return 0
    saved_per_row = max(0, before_bytes - after_bytes) / sample_count
    return int(saved_per_row * candidate_count)
