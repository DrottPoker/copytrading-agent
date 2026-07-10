from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.realtime_subscription_state_service import (
    load_realtime_subscription_state,
    mark_realtime_subscription_state,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_realtime_subscription_state_round_trip(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    observed_at = datetime(2026, 7, 10, 12, tzinfo=UTC)
    async with integration_sessionmaker() as session:
        await mark_realtime_subscription_state(
            session,
            status="connecting",
            desired_wallets=("0xAAA", "0xBBB"),
            monitored_wallets=("0xAAA",),
            worker_role="trading",
            worker_instance_id="worker-1",
            observed_at=observed_at,
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        snapshot = await load_realtime_subscription_state(
            session,
            stale_after_seconds=45,
            now=observed_at,
        )

    assert snapshot.status == "connecting"
    assert snapshot.desired_wallets == ("0xaaa", "0xbbb")
    assert snapshot.monitored_wallets == frozenset({"0xaaa"})
