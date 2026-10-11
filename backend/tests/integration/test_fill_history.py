"""Fill history on a real database: newest-first imports and gap repair."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import WalletFill, WatchedWallet
from app.schemas.fill import WalletFillImportRequest
from app.services.fill_gap_repair_service import find_fill_gaps, repair_fill_gaps
from app.services.fill_import_service import build_fill_record, import_wallet_fills

pytestmark = pytest.mark.integration

WALLET = "0x" + "a" * 40
ARCHIVED_WALLET = "0x" + "b" * 40
PAGE = 2000
HOUR_MS = 3_600_000


def fill(tid: int, time_ms: int) -> dict[str, Any]:
    return {
        "coin": "BTC",
        "px": "100",
        "sz": "1",
        "side": "B",
        "time": time_ms,
        "dir": "Open Long",
        "startPosition": "0",
        "closedPnl": "0",
        "fee": "0.01",
        "tid": tid,
    }


class FakeHyperliquid:
    """Answers like Hyperliquid: by time gives the oldest page of a range, userFills the newest."""

    def __init__(self, fills: list[dict[str, Any]]) -> None:
        self.fills = sorted(fills, key=lambda item: (item["time"], item["tid"]))
        self.requests = 0

    async def user_fills_by_time(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
        aggregate_by_time: bool = False,
    ) -> list[dict[str, Any]]:
        self.requests += 1
        end = end_time_ms if end_time_ms is not None else 2**63
        return [item for item in self.fills if start_time_ms <= item["time"] <= end][:PAGE]

    async def user_fills(self, *, user: str, aggregate_by_time: bool = False) -> list[dict]:
        self.requests += 1
        return list(reversed(self.fills[-PAGE:]))


def settings() -> Settings:
    return Settings(_env_file=None, fill_import_storage_guard_enabled=False)


@pytest.mark.asyncio
async def test_a_busy_wallet_import_stores_its_newest_fills_and_polls_until_now(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    # 25,000 fills over the last 80 days, far more than five pages.
    fills = [
        fill(index + 1, now_ms - 80 * 24 * HOUR_MS + index * 276_000) for index in range(25_000)
    ]
    client = FakeHyperliquid(fills)
    async with integration_sessionmaker() as session:
        session.add(WatchedWallet(address=WALLET, enabled=True))
        await session.commit()

        result = await import_wallet_fills(
            session,
            address=WALLET,
            payload=WalletFillImportRequest(days=90, max_pages=5, target_fills=10_000),
            client=client,  # type: ignore[arg-type]
        )
        newest_stored = await session.scalar(select(func.max(WalletFill.timestamp_ms)))
        stored = await session.scalar(select(func.count()).select_from(WalletFill))
        wallet = await session.scalar(select(WatchedWallet))

    assert result.history_complete is False
    assert result.history_end_ms == result.end_time_ms
    assert newest_stored == fills[-1]["time"]
    assert stored == result.fetched
    assert wallet is not None and wallet.last_polled_at is not None
    assert wallet.last_polled_at > datetime.now(UTC) - timedelta(minutes=1)


@pytest.mark.asyncio
async def test_gap_repair_reads_each_gap_again_and_stores_the_missing_fills(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = now_ms - 20 * 24 * HOUR_MS
    # One fill an hour for 20 days; the stored copy lacks days 8 to 13.
    truth = [fill(index + 1, start_ms + index * HOUR_MS) for index in range(20 * 24)]
    gap_start_ms = start_ms + 8 * 24 * HOUR_MS
    gap_end_ms = start_ms + 13 * 24 * HOUR_MS
    stored = [item for item in truth if not gap_start_ms < item["time"] < gap_end_ms]
    config = settings()
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                WatchedWallet(address=WALLET, enabled=True),
                WatchedWallet(
                    address=ARCHIVED_WALLET,
                    enabled=False,
                    archived_at=datetime.now(UTC),
                ),
            ]
        )
        await session.flush()
        records = [build_fill_record(WALLET, item, settings=config) for item in stored]
        archived = [
            build_fill_record(ARCHIVED_WALLET, item, settings=config)
            for item in (truth[0], truth[-1])
        ]
        session.add_all(WalletFill(**record) for record in [*records, *archived] if record)
        await session.commit()

        gaps = await find_fill_gaps(
            session,
            since_ms=start_ms - HOUR_MS,
            min_gap_ms=24 * HOUR_MS,
            gaps_per_wallet=1,
        )
        result = await repair_fill_gaps(
            session,
            gaps=gaps,
            client=FakeHyperliquid(truth),  # type: ignore[arg-type]
            settings=config,
            max_pages_per_gap=5,
        )
        total = await session.scalar(
            select(func.count()).select_from(WalletFill).where(WalletFill.wallet_address == WALLET)
        )
        later_gaps = await find_fill_gaps(
            session,
            since_ms=start_ms - HOUR_MS,
            min_gap_ms=24 * HOUR_MS,
            gaps_per_wallet=1,
        )

    # The fills on both edges are stored; the archived wallet's 20 day gap is left alone.
    assert [(gap.wallet_address, gap.start_ms, gap.end_ms) for gap in gaps] == [
        (WALLET, gap_start_ms, gap_end_ms)
    ]
    assert result.inserted == len(truth) - len(stored)
    assert result.repairs[0].complete is True
    assert total == len(truth)
    assert later_gaps == []


@pytest.mark.asyncio
async def test_gap_repair_reads_the_missing_stretch_up_to_the_last_poll(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    now_ms = int(now.timestamp() * 1000)
    start_ms = now_ms - 20 * 24 * HOUR_MS
    truth = [fill(index + 1, start_ms + index * HOUR_MS) for index in range(20 * 24)]
    # The old reader stopped at a page limit eight days before the import.
    cut_ms = now_ms - 8 * 24 * HOUR_MS
    stored = [item for item in truth if item["time"] <= cut_ms]
    config = settings()
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                WatchedWallet(address=WALLET, enabled=True, last_polled_at=now),
                WatchedWallet(
                    address=ARCHIVED_WALLET,
                    enabled=False,
                    archived_at=now,
                    archive_reason="stale_fill_prune",
                    last_polled_at=now,
                ),
            ]
        )
        await session.flush()
        session.add_all(
            WalletFill(**record)
            for address in (WALLET, ARCHIVED_WALLET)
            for record in (build_fill_record(address, item, settings=config) for item in stored)
            if record
        )
        await session.commit()

        active_only = await find_fill_gaps(
            session,
            since_ms=start_ms - HOUR_MS,
            min_gap_ms=24 * HOUR_MS,
            gaps_per_wallet=1,
        )
        with_archived = await find_fill_gaps(
            session,
            since_ms=start_ms - HOUR_MS,
            min_gap_ms=24 * HOUR_MS,
            gaps_per_wallet=1,
            include_archived=True,
        )
        result = await repair_fill_gaps(
            session,
            gaps=with_archived,
            client=FakeHyperliquid(truth),  # type: ignore[arg-type]
            settings=config,
            max_pages_per_gap=5,
        )

    assert [gap.wallet_address for gap in active_only] == [WALLET]
    assert {gap.wallet_address: gap.archive_reason for gap in with_archived} == {
        WALLET: None,
        ARCHIVED_WALLET: "stale_fill_prune",
    }
    missing = len([item for item in truth if cut_ms < item["time"] <= now_ms])
    assert [repair.inserted for repair in result.repairs] == [missing, missing]
