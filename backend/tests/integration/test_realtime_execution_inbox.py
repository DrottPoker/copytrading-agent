from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import RealtimeExecutionInbox, WalletFill
from app.services.realtime_execution_inbox_service import (
    claim_next_realtime_execution,
    complete_realtime_execution,
    retry_realtime_execution,
)
from app.services.realtime_fill_service import StoredRealtimeFills, store_realtime_fills

pytestmark = pytest.mark.integration


def inbox_payload(external_fill_id: str) -> dict[str, object]:
    return StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=1,
        duplicate=0,
        is_snapshot=False,
        latest_fill_time_ms=1,
        inserted_rows=[{"externalFillId": external_fill_id}],
    ).execution_payload()


@pytest.mark.asyncio
async def test_realtime_fill_and_inbox_are_committed_together(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "1" * 40
    fill_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    async with integration_sessionmaker() as session:
        stored = await store_realtime_fills(
            session,
            wallet_address=address,
            fills=[
                {
                    "tid": "atomic-first-fill",
                    "coin": "HYPE",
                    "side": "B",
                    "dir": "Open Long",
                    "px": "40",
                    "sz": "1",
                    "time": fill_time_ms,
                }
            ],
            is_snapshot=False,
        )

    assert stored.inbox_id is not None
    async with integration_sessionmaker() as session:
        wallet_fill_count = await session.scalar(select(func.count()).select_from(WalletFill))
        inbox = await session.get(RealtimeExecutionInbox, UUID(stored.inbox_id))
    assert wallet_fill_count == 1
    assert inbox is not None
    assert inbox.payload["insertedRows"][0]["externalFillId"] == "atomic-first-fill"


@pytest.mark.asyncio
async def test_poll_inserted_fill_still_creates_realtime_execution_inbox(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    address = "0x" + "2" * 40
    fill_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    fill = {
        "tid": "poll-before-websocket",
        "coin": "HYPE",
        "side": "B",
        "dir": "Open Long",
        "px": "40",
        "sz": "1",
        "time": fill_time_ms,
    }
    async with integration_sessionmaker() as session:
        first = await store_realtime_fills(
            session,
            wallet_address=address,
            fills=[fill],
            is_snapshot=False,
        )
    async with integration_sessionmaker() as session:
        duplicate = await store_realtime_fills(
            session,
            wallet_address=address,
            fills=[fill],
            is_snapshot=False,
        )

    assert first.inserted == 1
    assert duplicate.inserted == 0
    assert duplicate.duplicate == 1
    assert duplicate.inbox_id is not None
    assert duplicate.rows_for_execution[0]["externalFillId"] == "poll-before-websocket"
    async with integration_sessionmaker() as session:
        inbox = await session.get(RealtimeExecutionInbox, UUID(duplicate.inbox_id))
    assert inbox is not None
    assert inbox.payload["insertedRows"] == []
    assert inbox.payload["executionRows"][0]["externalFillId"] == "poll-before-websocket"


@pytest.mark.asyncio
async def test_realtime_inbox_claims_first_fill_without_copy_history(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        session.add(
            RealtimeExecutionInbox(
                wallet_address="0xsource",
                payload=inbox_payload("first-fill"),
            )
        )
        await session.commit()

    claimed = await claim_next_realtime_execution(
        integration_sessionmaker,
        owner="worker-1",
        claim_timeout_seconds=300,
        retry_base_seconds=1,
    )

    assert claimed is not None
    assert claimed.stored.inserted_rows[0]["externalFillId"] == "first-fill"
    assert await complete_realtime_execution(
        integration_sessionmaker,
        inbox_id=claimed.inbox_id,
        owner="worker-1",
    )
    async with integration_sessionmaker() as session:
        remaining = await session.scalar(select(func.count()).select_from(RealtimeExecutionInbox))
    assert remaining == 0


@pytest.mark.asyncio
async def test_realtime_inbox_retry_does_not_starve_newer_work_and_stale_claim_recovers(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                RealtimeExecutionInbox(
                    wallet_address="0xsource",
                    payload=inbox_payload("first-fill"),
                    created_at=now,
                ),
                RealtimeExecutionInbox(
                    wallet_address="0xsource",
                    payload=inbox_payload("second-fill"),
                    created_at=now + timedelta(microseconds=1),
                ),
            ]
        )
        await session.commit()

    first = await claim_next_realtime_execution(
        integration_sessionmaker,
        owner="worker-1",
        claim_timeout_seconds=300,
        retry_base_seconds=30,
    )
    assert first is not None
    assert first.stored.inserted_rows[0]["externalFillId"] == "first-fill"
    assert await retry_realtime_execution(
        integration_sessionmaker,
        inbox_id=first.inbox_id,
        owner="worker-1",
        attempt_count=first.attempt_count,
        error="temporary failure",
        retry_base_seconds=30,
    )

    second = await claim_next_realtime_execution(
        integration_sessionmaker,
        owner="worker-1",
        claim_timeout_seconds=300,
        retry_base_seconds=30,
    )
    assert second is not None
    assert second.stored.inserted_rows[0]["externalFillId"] == "second-fill"
    assert await complete_realtime_execution(
        integration_sessionmaker,
        inbox_id=second.inbox_id,
        owner="worker-1",
    )

    async with integration_sessionmaker() as session:
        pending = await session.get(RealtimeExecutionInbox, first.inbox_id)
        assert pending is not None
        pending.status = "processing"
        pending.claimed_by = "dead-worker"
        pending.claimed_at = now - timedelta(seconds=301)
        await session.commit()

    reclaimed = await claim_next_realtime_execution(
        integration_sessionmaker,
        owner="worker-2",
        claim_timeout_seconds=300,
        retry_base_seconds=30,
    )
    assert reclaimed is not None
    assert reclaimed.inbox_id == first.inbox_id
    assert reclaimed.attempt_count == 2
