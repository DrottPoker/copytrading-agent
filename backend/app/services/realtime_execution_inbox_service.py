import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update

from app.db.models import RealtimeExecutionInbox
from app.services.realtime_fill_service import StoredRealtimeFills

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedRealtimeExecution:
    inbox_id: UUID
    stored: StoredRealtimeFills
    attempt_count: int


async def claim_next_realtime_execution(
    sessionmaker: Any,
    *,
    owner: str,
    claim_timeout_seconds: int,
    retry_base_seconds: int,
) -> ClaimedRealtimeExecution | None:
    now = datetime.now(UTC)
    stale_before = now - timedelta(seconds=claim_timeout_seconds)
    async with sessionmaker() as session:
        inbox = await session.scalar(
            select(RealtimeExecutionInbox)
            .where(
                or_(
                    and_(
                        RealtimeExecutionInbox.status == "pending",
                        RealtimeExecutionInbox.available_at <= now,
                    ),
                    and_(
                        RealtimeExecutionInbox.status == "processing",
                        or_(
                            RealtimeExecutionInbox.claimed_at.is_(None),
                            RealtimeExecutionInbox.claimed_at <= stale_before,
                        ),
                    ),
                )
            )
            .order_by(
                RealtimeExecutionInbox.available_at.asc(),
                RealtimeExecutionInbox.created_at.asc(),
                RealtimeExecutionInbox.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if inbox is None:
            return None

        inbox.attempt_count += 1
        try:
            stored = StoredRealtimeFills.from_execution_payload(
                inbox.payload,
                inbox_id=str(inbox.id),
                fallback_observed_at=inbox.created_at,
            )
        except ValueError as exc:
            inbox.status = "pending"
            inbox.available_at = now + timedelta(
                seconds=realtime_execution_retry_delay_seconds(
                    inbox.attempt_count,
                    base_seconds=retry_base_seconds,
                )
            )
            inbox.claimed_at = None
            inbox.claimed_by = None
            inbox.last_error = limited_error(exc)
            await session.commit()
            logger.error("Invalid realtime execution inbox payload id=%s: %s", inbox.id, exc)
            return None

        inbox.status = "processing"
        inbox.claimed_at = now
        inbox.claimed_by = owner
        inbox.last_error = None
        await session.commit()
        return ClaimedRealtimeExecution(
            inbox_id=inbox.id,
            stored=stored,
            attempt_count=inbox.attempt_count,
        )


async def complete_realtime_execution(
    sessionmaker: Any,
    *,
    inbox_id: UUID,
    owner: str,
) -> bool:
    async with sessionmaker() as session:
        result = await session.execute(
            delete(RealtimeExecutionInbox).where(
                RealtimeExecutionInbox.id == inbox_id,
                RealtimeExecutionInbox.status == "processing",
                RealtimeExecutionInbox.claimed_by == owner,
            )
        )
        await session.commit()
        return bool(result.rowcount)


async def retry_realtime_execution(
    sessionmaker: Any,
    *,
    inbox_id: UUID,
    owner: str,
    attempt_count: int,
    error: BaseException | str,
    retry_base_seconds: int,
    immediate: bool = False,
) -> bool:
    delay_seconds = (
        0
        if immediate
        else realtime_execution_retry_delay_seconds(
            attempt_count,
            base_seconds=retry_base_seconds,
        )
    )
    async with sessionmaker() as session:
        result = await session.execute(
            update(RealtimeExecutionInbox)
            .where(
                RealtimeExecutionInbox.id == inbox_id,
                RealtimeExecutionInbox.status == "processing",
                RealtimeExecutionInbox.claimed_by == owner,
            )
            .values(
                status="pending",
                available_at=datetime.now(UTC) + timedelta(seconds=delay_seconds),
                claimed_at=None,
                claimed_by=None,
                last_error=limited_error(error),
            )
        )
        await session.commit()
        return bool(result.rowcount)


def realtime_execution_retry_delay_seconds(
    attempt_count: int,
    *,
    base_seconds: int,
) -> int:
    exponent = min(max(attempt_count - 1, 0), 6)
    return min(max(base_seconds, 1) * (2**exponent), 300)


def limited_error(error: BaseException | str) -> str:
    value = str(error) or error.__class__.__name__
    return value[:2000]
