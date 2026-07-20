"""Durable ownership of live-copy source-fill execution work."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, delete, exists, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import LiveCopyWork, WalletFill
from app.services.live_copy_state_service import LiveCopyProcessingOrigin
from app.services.source_fill_ordering import source_fill_order_key

LIVE_COPY_WORK_PENDING = "pending"
LIVE_COPY_WORK_PROCESSING = "processing"
LIVE_COPY_WORK_COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ClaimedLiveCopyWork:
    id: UUID
    wallet_fill_id: UUID
    wallet_address: str
    source_fill_id: str
    origin: LiveCopyProcessingOrigin
    claimed_at: datetime
    attempt_count: int


def live_copy_work_retry_delay_seconds(
    attempt_count: int,
    *,
    base_seconds: int,
) -> int:
    exponent = min(max(attempt_count - 1, 0), 6)
    return min(max(base_seconds, 1) * (2**exponent), 300)


async def enqueue_live_copy_work_for_wallet_fills(
    session: AsyncSession,
    *,
    fills: Iterable[WalletFill],
    origin: LiveCopyProcessingOrigin,
) -> int:
    """Add missing work rows without changing already claimed source history."""

    records = [live_copy_work_record(fill, origin=origin) for fill in fills if fill.id is not None]
    if not records:
        return 0
    result = await session.execute(
        insert(LiveCopyWork)
        .values(records)
        .on_conflict_do_nothing(index_elements=["wallet_fill_id"])
    )
    await session.flush()
    return max(int(result.rowcount or 0), 0)


def live_copy_work_record(
    fill: WalletFill,
    *,
    origin: LiveCopyProcessingOrigin,
) -> dict[str, Any]:
    source_fill_id = str(fill.external_fill_id or "")
    source_key = source_fill_order_key(
        {
            "timestampMs": fill.timestamp_ms,
            "coin": fill.coin,
            "externalFillId": source_fill_id,
            "rawJson": fill.raw_json,
        }
    )
    return {
        "wallet_fill_id": fill.id,
        "wallet_address": str(fill.wallet_address).lower(),
        "source_fill_id": source_fill_id,
        "source_timestamp_ms": int(source_key[0]),
        "coin": str(source_key[1]),
        "source_order_direction_rank": int(source_key[2]),
        "source_order_position": source_key[3],
        "source_order_fill_id_numeric": source_key[5] if source_key[4] == 0 else None,
        "origin": origin,
    }


async def claim_next_live_copy_work(
    sessionmaker: Any,
    *,
    owner: str,
    claim_timeout_seconds: int,
) -> ClaimedLiveCopyWork | None:
    """Claim the earliest executable source fill and commit before any network I/O."""

    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=max(claim_timeout_seconds, 1))
    async with sessionmaker() as session:
        work = await session.scalar(live_copy_work_claim_query(now=now, stale_before=stale_before))
        if work is None:
            return None

        work.status = LIVE_COPY_WORK_PROCESSING
        work.attempt_count = max(int(work.attempt_count or 0), 0) + 1
        work.claimed_at = now
        work.claimed_by = owner
        work.processing_started_at = now
        work.last_error = None
        await session.commit()
        return ClaimedLiveCopyWork(
            id=work.id,
            wallet_fill_id=work.wallet_fill_id,
            wallet_address=work.wallet_address,
            source_fill_id=work.source_fill_id,
            origin=work.origin,
            claimed_at=now,
            attempt_count=work.attempt_count,
        )


def live_copy_work_claim_query(*, now: datetime, stale_before: datetime):
    """Select only a source market lane's earliest unfinished fill.

    A deferred older fill blocks later fills for the same source wallet and
    coin.  Independent markets remain claimable while preserving each market
    lane's durable lifecycle order after a restart or recovery scan.
    """

    earlier = aliased(LiveCopyWork)
    numeric_rank = case(
        (LiveCopyWork.source_order_fill_id_numeric.is_not(None), literal(0)),
        else_=literal(1),
    )
    earlier_numeric_rank = case(
        (earlier.source_order_fill_id_numeric.is_not(None), literal(0)),
        else_=literal(1),
    )
    earlier_order = or_(
        earlier.source_timestamp_ms < LiveCopyWork.source_timestamp_ms,
        and_(
            earlier.source_timestamp_ms == LiveCopyWork.source_timestamp_ms,
            earlier.coin < LiveCopyWork.coin,
        ),
        and_(
            earlier.source_timestamp_ms == LiveCopyWork.source_timestamp_ms,
            earlier.coin == LiveCopyWork.coin,
            earlier.source_order_direction_rank < LiveCopyWork.source_order_direction_rank,
        ),
        and_(
            earlier.source_timestamp_ms == LiveCopyWork.source_timestamp_ms,
            earlier.coin == LiveCopyWork.coin,
            earlier.source_order_direction_rank == LiveCopyWork.source_order_direction_rank,
            earlier.source_order_position < LiveCopyWork.source_order_position,
        ),
        and_(
            earlier.source_timestamp_ms == LiveCopyWork.source_timestamp_ms,
            earlier.coin == LiveCopyWork.coin,
            earlier.source_order_direction_rank == LiveCopyWork.source_order_direction_rank,
            earlier.source_order_position == LiveCopyWork.source_order_position,
            earlier_numeric_rank < numeric_rank,
        ),
        and_(
            earlier.source_timestamp_ms == LiveCopyWork.source_timestamp_ms,
            earlier.coin == LiveCopyWork.coin,
            earlier.source_order_direction_rank == LiveCopyWork.source_order_direction_rank,
            earlier.source_order_position == LiveCopyWork.source_order_position,
            earlier_numeric_rank == numeric_rank,
            or_(
                and_(
                    numeric_rank == 0,
                    earlier.source_order_fill_id_numeric
                    < LiveCopyWork.source_order_fill_id_numeric,
                ),
                and_(
                    numeric_rank == 1,
                    earlier.source_fill_id < LiveCopyWork.source_fill_id,
                ),
                and_(
                    numeric_rank == 0,
                    earlier.source_order_fill_id_numeric
                    == LiveCopyWork.source_order_fill_id_numeric,
                    earlier.source_fill_id < LiveCopyWork.source_fill_id,
                ),
            ),
        ),
    )
    is_claimable = or_(
        and_(
            LiveCopyWork.status == LIVE_COPY_WORK_PENDING,
            LiveCopyWork.available_at <= now,
        ),
        and_(
            LiveCopyWork.status == LIVE_COPY_WORK_PROCESSING,
            or_(
                LiveCopyWork.claimed_at.is_(None),
                LiveCopyWork.claimed_at <= stale_before,
            ),
        ),
    )
    has_earlier_unfinished = exists(
        select(earlier.id).where(
            earlier.wallet_address == LiveCopyWork.wallet_address,
            earlier.coin == LiveCopyWork.coin,
            earlier.status.in_((LIVE_COPY_WORK_PENDING, LIVE_COPY_WORK_PROCESSING)),
            earlier_order,
        )
    )
    return (
        select(LiveCopyWork)
        .where(is_claimable, ~has_earlier_unfinished)
        .order_by(
            LiveCopyWork.source_timestamp_ms.asc(),
            LiveCopyWork.wallet_address.asc(),
            LiveCopyWork.coin.asc(),
            LiveCopyWork.source_order_direction_rank.asc(),
            LiveCopyWork.source_order_position.asc(),
            numeric_rank.asc(),
            LiveCopyWork.source_order_fill_id_numeric.asc().nulls_last(),
            LiveCopyWork.source_fill_id.asc(),
        )
        .with_for_update(skip_locked=True)
        .limit(1)
    )


async def load_claimed_live_copy_wallet_fill(
    session: AsyncSession,
    *,
    work_id: UUID,
    owner: str,
) -> WalletFill | None:
    return await session.scalar(
        select(WalletFill)
        .join(LiveCopyWork, LiveCopyWork.wallet_fill_id == WalletFill.id)
        .where(
            LiveCopyWork.id == work_id,
            LiveCopyWork.status == LIVE_COPY_WORK_PROCESSING,
            LiveCopyWork.claimed_by == owner,
        )
    )


async def complete_live_copy_work(
    sessionmaker: Any,
    *,
    work_id: UUID,
    owner: str,
) -> bool:
    """Remove only work whose lifecycle result has already committed."""

    async with sessionmaker() as session:
        result = await session.execute(
            delete(LiveCopyWork).where(
                LiveCopyWork.id == work_id,
                LiveCopyWork.status == LIVE_COPY_WORK_PROCESSING,
                LiveCopyWork.claimed_by == owner,
            )
        )
        await session.commit()
        return bool(result.rowcount)


async def retry_live_copy_work(
    sessionmaker: Any,
    *,
    work_id: UUID,
    owner: str,
    attempt_count: int,
    error: BaseException | str,
    retry_base_seconds: int,
    immediate: bool = False,
) -> bool:
    delay_seconds = (
        0
        if immediate
        else live_copy_work_retry_delay_seconds(
            attempt_count,
            base_seconds=retry_base_seconds,
        )
    )
    async with sessionmaker() as session:
        result = await session.execute(
            update(LiveCopyWork)
            .where(
                LiveCopyWork.id == work_id,
                LiveCopyWork.status == LIVE_COPY_WORK_PROCESSING,
                LiveCopyWork.claimed_by == owner,
            )
            .values(
                status=LIVE_COPY_WORK_PENDING,
                available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                claimed_at=None,
                claimed_by=None,
                last_error=limited_error(error),
            )
        )
        await session.commit()
        return bool(result.rowcount)


def limited_error(error: BaseException | str) -> str:
    value = str(error) or error.__class__.__name__
    return value[:2000]
