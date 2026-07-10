import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AuditLog,
    RiskEvent,
    TradingAccount,
    TradingOrder,
    TradingOrderDispatch,
)
from app.services.realtime_event_service import publish_event, read_event_stream
from app.services.trading_safety_service import set_live_entry_safety_state
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

    assert constraints == {
        "fk_trading_positions_account_key_type_trading_accounts",
        "fk_trading_orders_account_key_type_trading_accounts",
        "fk_trading_fills_account_key_type_trading_accounts",
    }
    assert route_index == "ux_trading_accounts_live_active_route"


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


@pytest.mark.asyncio
async def test_global_pause_demotes_accounts_cancels_unsent_entries_and_audits(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        await set_live_entry_safety_state(
            session,
            entry_state="enabled",
            reason="Prepare integration test.",
            actor="integration-test",
        )
        account = TradingAccount(
            key="live_phase_6",
            account_type="live",
            label="Phase 6",
            status="enabled",
            network="testnet",
            wallet_address="0x" + "1" * 40,
            realized_pnl_usd=Decimal("0"),
            fee_usd=Decimal("0"),
            lifecycle_version=0,
            status_changed_at=datetime.now(UTC),
        )
        session.add(account)
        order = TradingOrder(
            account_key=account.key,
            account_type="live",
            source_wallet="0x" + "2" * 40,
            source_fill_id="phase-6-fill",
            sequence_index=0,
            client_order_id="0x" + "a" * 32,
            coin="HYPE",
            action="open",
            side="long",
            is_buy=True,
            reduce_only=False,
            order_type="ioc",
            status="ready",
            requested_size=Decimal("0.1"),
            requested_notional_usd=Decimal("10"),
            margin_usd=Decimal("10"),
            leverage=Decimal("1"),
            limit_price=Decimal("100"),
            filled_size=Decimal("0"),
            filled_notional_usd=Decimal("0"),
            fee_usd=Decimal("0"),
        )
        session.add(order)
        await session.flush()
        dispatch = TradingOrderDispatch(
            order_id=order.id,
            account_key=account.key,
            client_order_id=order.client_order_id,
            status="pending",
            attempt_count=0,
            available_at=datetime.now(UTC),
        )
        session.add(dispatch)
        await session.commit()

        control = await set_live_entry_safety_state(
            session,
            entry_state="paused",
            reason="Integration safety pause.",
            actor="integration-test",
        )
        await session.commit()

        await session.refresh(account)
        await session.refresh(order)
        await session.refresh(dispatch)
        audit_count = await session.scalar(
            select(func.count(AuditLog.id)).where(AuditLog.action == "live_entries.paused")
        )
        risk_count = await session.scalar(
            select(func.count(RiskEvent.id)).where(RiskEvent.event_type == "live_entries_paused")
        )

    assert control.entry_state == "paused"
    assert account.status == "exit_only"
    assert account.status_reason == "global_entry_paused:Integration safety pause."
    assert order.status == "canceled"
    assert dispatch.status == "canceled"
    assert audit_count == 1
    assert risk_count == 1
