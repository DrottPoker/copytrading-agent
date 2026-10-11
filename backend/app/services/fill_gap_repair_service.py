"""Find and fill gaps in stored wallet fill history.

Fill imports used to page forward from the oldest fill and stop at a page limit,
so a busy wallet's newest fills were never fetched, and later polls only
continued from the time of that import. This leaves a gap inside the stored
history, or a missing stretch between the newest stored fill and the last poll.
A gap can also be a quiet period, so each one is simply read again; a quiet gap
costs one light request.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.hyperliquid_client import HyperliquidClient
from app.services.fill_import_service import FillPageReader, insert_fill_records


@dataclass(frozen=True)
class FillGap:
    wallet_address: str
    start_ms: int
    end_ms: int
    archive_reason: str | None = None

    @property
    def hours(self) -> float:
        return (self.end_ms - self.start_ms) / 3_600_000


@dataclass(frozen=True)
class FillGapRepair:
    gap: FillGap
    fetched: int
    inserted: int
    complete: bool


@dataclass
class FillGapRepairResult:
    repairs: list[FillGapRepair] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        return sum(repair.inserted for repair in self.repairs)


async def find_fill_gaps(
    session: AsyncSession,
    *,
    since_ms: int,
    min_gap_ms: int,
    gaps_per_wallet: int,
    include_archived: bool = False,
) -> list[FillGap]:
    """The longest gaps in each wallet's stored fills, up to its last poll.

    A gap runs between consecutive stored fills, or from the newest stored fill
    to the last poll, which claims every fill until then is stored.
    """

    result = await session.execute(
        text(
            """
            with target_wallets as (
              select address, last_polled_at, archive_reason
              from watched_wallets
              where :include_archived or (enabled is true and archived_at is null)
            ),
            ordered as (
              select
                wf.wallet_address,
                tw.archive_reason,
                wf.timestamp_ms as gap_start_ms,
                coalesce(
                  lead(wf.timestamp_ms) over (
                    partition by wf.wallet_address
                    order by wf.timestamp_ms
                  ),
                  floor(extract(epoch from tw.last_polled_at) * 1000)::bigint
                ) as gap_end_ms
              from wallet_fills wf
              join target_wallets tw on tw.address = wf.wallet_address
              where wf.timestamp_ms >= :since_ms
            ),
            ranked as (
              select
                wallet_address,
                archive_reason,
                gap_start_ms,
                gap_end_ms,
                row_number() over (
                  partition by wallet_address
                  order by gap_end_ms - gap_start_ms desc
                ) as gap_rank
              from ordered
              where gap_end_ms - gap_start_ms >= :min_gap_ms
            )
            select wallet_address, archive_reason, gap_start_ms, gap_end_ms
            from ranked
            where gap_rank <= :gaps_per_wallet
            order by wallet_address, gap_start_ms
            """
        ),
        {
            "since_ms": since_ms,
            "min_gap_ms": min_gap_ms,
            "gaps_per_wallet": gaps_per_wallet,
            "include_archived": include_archived,
        },
    )
    return [
        FillGap(
            wallet_address=str(row.wallet_address),
            start_ms=int(row.gap_start_ms),
            end_ms=int(row.gap_end_ms),
            archive_reason=row.archive_reason,
        )
        for row in result
    ]


async def repair_fill_gaps(
    session: AsyncSession,
    *,
    gaps: list[FillGap],
    client: HyperliquidClient,
    settings: Settings,
    max_pages_per_gap: int,
    on_repair: Callable[[FillGapRepair], None] | None = None,
) -> FillGapRepairResult:
    """Read every fill inside each gap and store the ones that are missing."""

    result = FillGapRepairResult()
    for gap in gaps:
        reader = FillPageReader(
            client=client,
            user=gap.wallet_address,
            start_time_ms=gap.start_ms,
            end_time_ms=gap.end_ms,
            max_pages=max_pages_per_gap,
            aggregate_by_time=False,
            settings=settings,
        )
        read = await reader.read_forward(gap.start_ms, gap.end_ms)
        # An incomplete read still fills the gap from its start, so a rerun continues.
        records = reader.records_between(gap.start_ms, gap.end_ms)
        inserted = await insert_fill_records(session, records)
        await session.commit()
        repair = FillGapRepair(
            gap=gap,
            fetched=len(records),
            inserted=inserted,
            complete=read.complete,
        )
        result.repairs.append(repair)
        if on_repair is not None:
            on_repair(repair)
    return result


def fill_gap_window_start_ms(settings: Settings, *, now: datetime | None = None) -> int:
    """Gaps are looked for within the pool import window, the history scoring can use."""

    resolved_now = now or datetime.now(UTC)
    return int((resolved_now - timedelta(days=settings.pool_fill_import_days)).timestamp() * 1000)
