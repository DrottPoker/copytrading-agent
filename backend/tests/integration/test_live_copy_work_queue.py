import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LiveCopyWork, WalletFill
from app.services.fill_retention_service import cleanup_wallet_fill_retention
from app.services.live_copy_state_service import (
    LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
    LIVE_COPY_ORIGIN_REALTIME,
    LIVE_COPY_ORIGIN_SNAPSHOT_RECOVERY,
)
from app.services.live_copy_work_service import (
    LIVE_COPY_WORK_PENDING,
    LIVE_COPY_WORK_PROCESSING,
    claim_next_live_copy_work,
    complete_live_copy_work,
    enqueue_live_copy_work_for_wallet_fills,
    retry_live_copy_work,
)
from app.services.realtime_fill_service import store_realtime_fills

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_realtime_duplicates_and_recovery_converge_on_one_durable_work_row(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    realtime_wallet = "0x" + "1" * 40
    snapshot_wallet = "0x" + "2" * 40
    fill_time_ms = int(datetime.now(UTC).timestamp() * 1000)
    realtime_fill = hyperliquid_fill("shared-fill", fill_time_ms=fill_time_ms)
    snapshot_fill = hyperliquid_fill("snapshot-fill", fill_time_ms=fill_time_ms + 1)

    async with integration_sessionmaker() as session:
        first = await store_realtime_fills(
            session,
            wallet_address=realtime_wallet,
            fills=[realtime_fill],
            is_snapshot=False,
        )
    async with integration_sessionmaker() as session:
        duplicate = await store_realtime_fills(
            session,
            wallet_address=realtime_wallet,
            fills=[realtime_fill],
            is_snapshot=False,
        )
    async with integration_sessionmaker() as session:
        snapshot = await store_realtime_fills(
            session,
            wallet_address=snapshot_wallet,
            fills=[snapshot_fill],
            is_snapshot=True,
        )
        realtime_stored_fill = await session.scalar(
            select(WalletFill).where(
                WalletFill.wallet_address == realtime_wallet,
                WalletFill.external_fill_id == "shared-fill",
            )
        )
        snapshot_stored_fill = await session.scalar(
            select(WalletFill).where(
                WalletFill.wallet_address == snapshot_wallet,
                WalletFill.external_fill_id == "snapshot-fill",
            )
        )
        assert realtime_stored_fill is not None
        assert snapshot_stored_fill is not None

        recovery_duplicate_count = await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=[realtime_stored_fill],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        snapshot_recovery_count = await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=[snapshot_stored_fill],
            origin=LIVE_COPY_ORIGIN_SNAPSHOT_RECOVERY,
        )
        await session.commit()

    assert first.live_copy_work_enqueued == 1
    assert duplicate.live_copy_work_enqueued == 0
    assert snapshot.live_copy_work_enqueued == 0
    assert recovery_duplicate_count == 0
    assert snapshot_recovery_count == 1

    async with integration_sessionmaker() as session:
        work_rows = list(
            (
                await session.scalars(
                    select(LiveCopyWork).order_by(LiveCopyWork.wallet_address.asc())
                )
            ).all()
        )
    assert [(row.source_fill_id, row.origin) for row in work_rows] == [
        ("shared-fill", LIVE_COPY_ORIGIN_REALTIME),
        ("snapshot-fill", LIVE_COPY_ORIGIN_SNAPSHOT_RECOVERY),
    ]


@pytest.mark.asyncio
async def test_claiming_respects_per_source_order_and_commits_before_processing(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_a = "0xsource-a"
    source_b = "0xsource-b"
    older_a = wallet_fill(source_a, "a-old", timestamp_ms=1_000)
    newer_a = wallet_fill(source_a, "a-new", timestamp_ms=2_000)
    only_b = wallet_fill(source_b, "b-only", timestamp_ms=1_500)
    async with integration_sessionmaker() as session:
        session.add_all([older_a, newer_a, only_b])
        await session.flush()
        assert (
            await enqueue_live_copy_work_for_wallet_fills(
                session,
                fills=[newer_a, only_b, older_a],
                origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            )
            == 3
        )
        await session.commit()

    first = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-a",
        claim_timeout_seconds=300,
    )
    assert first is not None
    assert first.source_fill_id == "a-old"
    async with integration_sessionmaker() as session:
        committed = await session.get(LiveCopyWork, first.id)
    assert committed is not None
    assert committed.status == LIVE_COPY_WORK_PROCESSING
    assert committed.claimed_by == "worker-a"
    assert committed.attempt_count == 1

    second = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-b",
        claim_timeout_seconds=300,
    )
    assert second is not None
    assert second.source_fill_id == "b-only"
    assert await complete_live_copy_work(
        integration_sessionmaker,
        work_id=second.id,
        owner="worker-b",
    )
    assert await complete_live_copy_work(
        integration_sessionmaker,
        work_id=first.id,
        owner="worker-a",
    )

    third = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-c",
        claim_timeout_seconds=300,
    )
    assert third is not None
    assert third.source_fill_id == "a-new"


@pytest.mark.asyncio
async def test_retry_and_dead_worker_claim_are_durable_and_reclaimable(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_fill = wallet_fill("0xretry", "retry-fill", timestamp_ms=1_000)
    async with integration_sessionmaker() as session:
        session.add(source_fill)
        await session.flush()
        assert (
            await enqueue_live_copy_work_for_wallet_fills(
                session,
                fills=[source_fill],
                origin=LIVE_COPY_ORIGIN_REALTIME,
            )
            == 1
        )
        await session.commit()

    first = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-1",
        claim_timeout_seconds=300,
    )
    assert first is not None
    assert await retry_live_copy_work(
        integration_sessionmaker,
        work_id=first.id,
        owner="worker-1",
        attempt_count=first.attempt_count,
        error="temporary exchange error",
        retry_base_seconds=30,
        immediate=True,
    )
    async with integration_sessionmaker() as session:
        retried = await session.get(LiveCopyWork, first.id)
    assert retried is not None
    assert retried.status == LIVE_COPY_WORK_PENDING
    assert retried.claimed_at is None
    assert retried.claimed_by is None
    assert retried.last_error == "temporary exchange error"

    second = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-1",
        claim_timeout_seconds=300,
    )
    assert second is not None
    assert second.attempt_count == 2
    async with integration_sessionmaker() as session:
        claimed = await session.get(LiveCopyWork, second.id)
        assert claimed is not None
        claimed.claimed_at = datetime.now(UTC) - timedelta(seconds=2)
        claimed.claimed_by = "dead-worker"
        await session.commit()

    reclaimed = await claim_next_live_copy_work(
        integration_sessionmaker,
        owner="worker-2",
        claim_timeout_seconds=1,
    )
    assert reclaimed is not None
    assert reclaimed.id == second.id
    assert reclaimed.attempt_count == 3
    assert reclaimed.origin == LIVE_COPY_ORIGIN_REALTIME


@pytest.mark.asyncio
async def test_skip_locked_allows_only_one_concurrent_claimant_for_a_work_row(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_fill = wallet_fill("0xconcurrent", "claim-once", timestamp_ms=1_000)
    async with integration_sessionmaker() as session:
        session.add(source_fill)
        await session.flush()
        await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=[source_fill],
            origin=LIVE_COPY_ORIGIN_REALTIME,
        )
        await session.commit()

    first, second = await asyncio.gather(
        claim_next_live_copy_work(
            integration_sessionmaker,
            owner="concurrent-worker-1",
            claim_timeout_seconds=300,
        ),
        claim_next_live_copy_work(
            integration_sessionmaker,
            owner="concurrent-worker-2",
            claim_timeout_seconds=300,
        ),
    )
    claims = [claim for claim in (first, second) if claim is not None]

    assert len(claims) == 1
    assert claims[0].source_fill_id == "claim-once"
    async with integration_sessionmaker() as session:
        work_count = await session.scalar(select(func.count()).select_from(LiveCopyWork))
        work = await session.get(LiveCopyWork, claims[0].id)
    assert work_count == 1
    assert work is not None
    assert work.status == LIVE_COPY_WORK_PROCESSING
    assert work.claimed_by in {"concurrent-worker-1", "concurrent-worker-2"}


@pytest.mark.asyncio
async def test_retention_cleanup_protects_pending_live_copy_work(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_fill = wallet_fill(
        "0xretention",
        "retention-fill",
        timestamp_ms=int((datetime.now(UTC) - timedelta(days=90)).timestamp() * 1000),
    )
    async with integration_sessionmaker() as session:
        session.add(source_fill)
        await session.flush()
        await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=[source_fill],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        await session.commit()
        result = await cleanup_wallet_fill_retention(
            session,
            dry_run=True,
            retention_days=61,
            protect_top_score_wallets=0,
            use_lock=False,
        )

    assert result.candidate_fills == 0


@pytest.mark.asyncio
async def test_postgres_live_copy_work_defaults_and_foreign_key_match_the_migration(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_fill = wallet_fill("0xmigration", "migration-fill", timestamp_ms=1_000)
    async with integration_sessionmaker() as session:
        session.add(source_fill)
        await session.flush()
        await enqueue_live_copy_work_for_wallet_fills(
            session,
            fills=[source_fill],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        work = await session.scalar(select(LiveCopyWork))
        assert work is not None
        assert work.status == LIVE_COPY_WORK_PENDING
        assert work.attempt_count == 0
        assert work.available_at is not None
        assert work.created_at is not None
        assert work.updated_at is not None
        stored_fill = await session.get(WalletFill, source_fill.id)
        assert stored_fill is not None
        await session.delete(stored_fill)
        await session.commit()

    async with integration_sessionmaker() as session:
        remaining = await session.scalar(select(func.count()).select_from(LiveCopyWork))
    assert remaining == 0


def hyperliquid_fill(external_fill_id: str, *, fill_time_ms: int) -> dict[str, object]:
    return {
        "tid": external_fill_id,
        "coin": "HYPE",
        "side": "B",
        "dir": "Open Long",
        "px": "40",
        "sz": "1",
        "time": fill_time_ms,
    }


def wallet_fill(wallet_address: str, external_fill_id: str, *, timestamp_ms: int) -> WalletFill:
    return WalletFill(
        wallet_address=wallet_address,
        external_fill_id=external_fill_id,
        coin="HYPE",
        side="buy",
        price=Decimal("10"),
        size=Decimal("1"),
        timestamp_ms=timestamp_ms,
        raw_json={"dir": "Open Long", "startPosition": "0"},
    )
