import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.realtime_event_service import publish_event, read_event_stream
from app.services.worker_lease_service import (
    WorkerCapabilityLeaseUnavailableError,
    worker_capability_leases,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_phase_6_database_constraints_are_installed(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        constraints = set(
            (
                await session.scalars(
                    text(
                        """
                        select conname
                        from pg_constraint
                        where conname in (
                          'fk_trading_positions_account_key_type_trading_accounts',
                          'fk_trading_orders_account_key_type_trading_accounts',
                          'fk_trading_fills_account_key_type_trading_accounts'
                        )
                        """
                    )
                )
            ).all()
        )
        route_index = await session.scalar(
            text(
                """
                select indexname
                from pg_indexes
                where indexname = 'ux_trading_accounts_live_active_route'
                """
            )
        )
        removed_gate = await session.scalar(
            text("select to_regclass('public.live_entry_safety_controls')")
        )

    assert constraints == {
        "fk_trading_positions_account_key_type_trading_accounts",
        "fk_trading_orders_account_key_type_trading_accounts",
        "fk_trading_fills_account_key_type_trading_accounts",
    }
    assert route_index == "ux_trading_accounts_live_active_route"
    assert removed_gate is None


@pytest.mark.asyncio
async def test_wallet_fill_ingest_latency_supports_historical_snapshots(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    latency_ms = 3_029_584_669
    async with integration_sessionmaker() as session:
        stored_latency = await session.scalar(
            text(
                """
                insert into wallet_fills (
                  wallet_address,
                  external_fill_id,
                  coin,
                  side,
                  price,
                  size,
                  timestamp_ms,
                  ingest_latency_ms,
                  raw_json
                )
                values (
                  '0xintegration-latency',
                  'integration-latency-fill',
                  'BTC',
                  'buy',
                  1,
                  1,
                  1,
                  :latency_ms,
                  '{}'::jsonb
                )
                returning ingest_latency_ms
                """
            ),
            {"latency_ms": latency_ms},
        )
        await session.rollback()

    assert stored_latency == latency_ms


@pytest.mark.asyncio
async def test_worker_capability_lease_rejects_duplicate_owner_and_releases(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    first_stop_event = asyncio.Event()
    async with worker_capability_leases(
        integration_sessionmaker,
        capabilities=("trading",),
        ttl_seconds=30,
        runtime_stop_event=first_stop_event,
    ):
        with pytest.raises(WorkerCapabilityLeaseUnavailableError):
            async with worker_capability_leases(
                integration_sessionmaker,
                capabilities=("trading",),
                ttl_seconds=30,
                runtime_stop_event=asyncio.Event(),
            ):
                pytest.fail("A duplicate trading capability lease was acquired.")

    async with worker_capability_leases(
        integration_sessionmaker,
        capabilities=("trading",),
        ttl_seconds=30,
        runtime_stop_event=asyncio.Event(),
    ):
        pass


@pytest.mark.asyncio
async def test_redis_event_stream_replays_from_cursor(integration_redis) -> None:
    first_event = await publish_event(
        integration_redis,
        event_type="worker_started",
        channel="events:system",
        message="Worker started.",
        payload={"instanceId": "worker-1"},
        producer="integration-test",
    )
    second_event = await publish_event(
        integration_redis,
        event_type="worker_progress",
        channel="events:system",
        message="Worker progressed.",
        payload={"instanceId": "worker-1"},
        producer="integration-test",
    )

    replay = await read_event_stream(
        integration_redis,
        last_event_id=first_event["id"],
        block_ms=1,
        count=10,
    )

    assert [event["id"] for event in replay] == [second_event["id"]]
    assert replay[0]["schemaVersion"] == 1
    assert replay[0]["producer"] == "integration-test"
