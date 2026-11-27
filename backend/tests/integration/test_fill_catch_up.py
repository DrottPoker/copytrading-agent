"""Catching up on the fills a reconnect snapshot leaves out, on a real database."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import WalletFill, WatchedWallet
from app.services.fill_catch_up_service import catch_up_fills_after_snapshot
from app.services.realtime_fill_service import StoredRealtimeFills, store_realtime_fills

pytestmark = pytest.mark.integration

WALLET = "0x" + "c" * 40
MINUTE_MS = 60_000


def fill(tid: int, time_ms: int, *, direction: str = "Close Long") -> dict[str, Any]:
    return {
        "coin": "BTC",
        "px": "100",
        "sz": "1",
        "side": "A",
        "time": time_ms,
        "dir": direction,
        "startPosition": "2",
        "closedPnl": "1",
        "fee": "0.01",
        "tid": tid,
    }


class FakeHyperliquid:
    def __init__(self, fills: list[dict[str, Any]]) -> None:
        self.fills = sorted(fills, key=lambda item: (item["time"], item["tid"]))
        self.start_times: list[int] = []

    async def user_fills_by_time(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
        aggregate_by_time: bool = False,
    ) -> list[dict[str, Any]]:
        self.start_times.append(start_time_ms)
        end = end_time_ms if end_time_ms is not None else 2**63
        return [item for item in self.fills if start_time_ms <= item["time"] <= end][:2000]

    async def user_fills(self, *, user: str, aggregate_by_time: bool = False) -> list[dict]:
        self.start_times.append(-1)
        return list(reversed(self.fills[-2000:]))


def settings() -> Settings:
    return Settings(_env_file=None, pool_fill_import_overlap_seconds=300)


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


async def add_wallet(session: AsyncSession, *, last_polled_at: datetime | None) -> None:
    session.add(WatchedWallet(address=WALLET, enabled=True, last_polled_at=last_polled_at))
    await session.commit()


@pytest.mark.asyncio
async def test_a_snapshot_after_downtime_fetches_the_fills_it_left_out(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    last_poll = now - timedelta(hours=2)
    downtime_exit = fill(1, ms(now) - 90 * MINUTE_MS)
    snapshot_fill = fill(2, ms(now) - 5 * MINUTE_MS)
    client = FakeHyperliquid([downtime_exit, snapshot_fill])
    async with integration_sessionmaker() as session:
        await add_wallet(session, last_polled_at=last_poll)
        stored = await store_realtime_fills(
            session,
            wallet_address=WALLET,
            fills=[snapshot_fill],
            is_snapshot=True,
            received_at=now,
        )
        result = await catch_up_fills_after_snapshot(
            session,
            wallet_address=WALLET,
            oldest_snapshot_fill_ms=stored.oldest_fill_time_ms,
            observed_at=now,
            settings=settings(),
            client=client,  # type: ignore[arg-type]
        )
        stored_ids = set((await session.scalars(select(WalletFill.external_fill_id))).all())
        wallet = await session.scalar(select(WatchedWallet))

    assert result.status == "imported"
    assert result.inserted == 1
    assert result.history_complete is True
    assert result.start_time_ms == ms(last_poll) - 300_000
    assert stored_ids == {"1", "2"}
    assert wallet is not None and wallet.last_polled_at is not None
    assert wallet.last_polled_at >= now


@pytest.mark.asyncio
async def test_a_snapshot_reaching_into_stored_history_needs_no_request(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    client = FakeHyperliquid([])
    async with integration_sessionmaker() as session:
        await add_wallet(session, last_polled_at=now - timedelta(minutes=10))
        result = await catch_up_fills_after_snapshot(
            session,
            wallet_address=WALLET,
            oldest_snapshot_fill_ms=ms(now) - 30 * MINUTE_MS,
            observed_at=now,
            settings=settings(),
            client=client,  # type: ignore[arg-type]
        )
        wallet = await session.scalar(select(WatchedWallet))

    assert result.status == "covered"
    assert client.start_times == []
    assert wallet is not None and wallet.last_polled_at == now


@pytest.mark.asyncio
async def test_unknown_and_never_polled_wallets_are_left_to_the_pool_import(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    client = FakeHyperliquid([fill(1, ms(now))])
    async with integration_sessionmaker() as session:
        unknown = await catch_up_fills_after_snapshot(
            session,
            wallet_address=WALLET,
            oldest_snapshot_fill_ms=ms(now),
            observed_at=now,
            settings=settings(),
            client=client,  # type: ignore[arg-type]
        )
        await add_wallet(session, last_polled_at=None)
        never_polled = await catch_up_fills_after_snapshot(
            session,
            wallet_address=WALLET,
            oldest_snapshot_fill_ms=ms(now),
            observed_at=now,
            settings=settings(),
            client=client,  # type: ignore[arg-type]
        )

    assert unknown.status == "not_watched"
    assert never_polled.status == "never_polled"
    assert client.start_times == []


@pytest.mark.asyncio
async def test_realtime_fills_never_claim_a_complete_history(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    last_poll = now - timedelta(hours=1)
    spot_fill = {**fill(1, ms(now) - 20 * MINUTE_MS), "coin": "@107", "dir": "Sell"}
    perp_fill = fill(2, ms(now) - MINUTE_MS)
    async with integration_sessionmaker() as session:
        await add_wallet(session, last_polled_at=last_poll)
        snapshot = await store_realtime_fills(
            session,
            wallet_address=WALLET,
            fills=[spot_fill, perp_fill],
            is_snapshot=True,
            received_at=now,
        )
        await store_realtime_fills(
            session,
            wallet_address=WALLET,
            fills=[fill(3, ms(now))],
            is_snapshot=False,
            received_at=now,
        )
        wallet = await session.scalar(select(WatchedWallet))

    assert wallet is not None
    assert wallet.last_polled_at == last_poll
    assert wallet.last_seen_fill_at == datetime.fromtimestamp(ms(now) / 1000, tz=UTC)
    # Coverage counts every market, although only perp fills are stored.
    assert snapshot.oldest_fill_time_ms == spot_fill["time"]
    replayed = StoredRealtimeFills.from_execution_payload(
        snapshot.execution_payload(),
        inbox_id="inbox",
    )
    assert replayed.oldest_fill_time_ms == spot_fill["time"]
