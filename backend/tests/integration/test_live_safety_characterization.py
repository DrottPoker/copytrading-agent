import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
    TradingReconciliationRun,
)
from app.integrations.hyperliquid_live_client import LiveOrderResult
from app.services.live_execution_state import build_dispatch_client_order_id
from app.services.live_trading_service import (
    LIVE_EXCHANGE_SOURCE,
    LiveOrderSubmitError,
    LivePerpState,
    build_testnet_live_trade_intent,
    fetch_live_perp_states,
    prune_live_reconciliation_runs,
    recompute_live_account_fill_totals,
    reconcile_live_fills,
    reconcile_live_positions,
    reconcile_live_trading_account,
    recover_live_order_dispatches,
    submit_live_trade_intent,
    update_live_orders_from_reconciled_fills,
)
from app.services.trading_core import TradeIntent

from ..fakes.live_exchange import FaultInjectingTradingClient, SimulatedProcessCrash

pytestmark = pytest.mark.integration


def live_account() -> TradingAccount:
    return TradingAccount(
        key="live_integration",
        account_type="live",
        label="Live Integration",
        status="enabled",
        network="testnet",
        wallet_address="0x" + "2" * 40,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_settings() -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    settings.hyperliquid_private_key = "0x" + "1" * 64
    settings.hyperliquid_wallet_address = "0x" + "2" * 40
    settings.live_trading_enabled = True
    return settings


def reduce_only_intent(account: TradingAccount):
    return build_testnet_live_trade_intent(
        account=account,
        coin="BTC",
        side="long",
        notional_usd=Decimal("100"),
        limit_price=Decimal("100"),
        leverage=Decimal("1"),
        reduce_only=True,
        source_fill_id="source-fill-1",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_accepted_order_timeout_is_persisted_as_uncertain(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with integration_sessionmaker() as session:
        account = live_account()
        session.add(account)
        await session.commit()
        client = FaultInjectingTradingClient("accepted_then_timeout")

        with pytest.raises(LiveOrderSubmitError):
            await submit_live_trade_intent(
                session,
                account=account,
                intent=reduce_only_intent(account),
                settings=live_settings(),
                client=client,  # type: ignore[arg-type]
            )
        await session.commit()

    async with integration_sessionmaker() as session:
        order = await session.scalar(select(TradingOrder))
        dispatch = await session.scalar(select(TradingOrderDispatch))

    assert order is not None
    assert order.status == "uncertain"
    assert dispatch is not None
    assert dispatch.status == "uncertain"


@pytest.mark.asyncio
async def test_process_restart_recovers_durable_intent_after_exchange_acceptance(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    async with integration_sessionmaker() as session:
        session.add(account)
        await session.commit()

    with pytest.raises(SimulatedProcessCrash):
        async with integration_sessionmaker() as session:
            persisted_account = await session.get(TradingAccount, account.key)
            assert persisted_account is not None
            await submit_live_trade_intent(
                session,
                account=persisted_account,
                intent=reduce_only_intent(persisted_account),
                settings=live_settings(),
                client=FaultInjectingTradingClient(  # type: ignore[arg-type]
                    "accepted_then_process_crash"
                ),
            )

    async with integration_sessionmaker() as session:
        recovered_order = await session.scalar(select(TradingOrder))
        dispatch = await session.scalar(select(TradingOrderDispatch))

    assert recovered_order is not None
    assert recovered_order.status in {"submitting", "uncertain"}
    assert dispatch is not None
    assert dispatch.status == "dispatching"


@pytest.mark.asyncio
async def test_live_dispatch_is_serialized_per_account(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    async with integration_sessionmaker() as session:
        session.add(account)
        await session.commit()

    class BlockingTradingClient(FaultInjectingTradingClient):
        def __init__(self) -> None:
            super().__init__("filled")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def submit_order(self, *, account, intent):  # type: ignore[no-untyped-def]
            self.started.set()
            await self.release.wait()
            return await super().submit_order(account=account, intent=intent)

    client = BlockingTradingClient()
    async with (
        integration_sessionmaker() as first_session,
        integration_sessionmaker() as second_session,
    ):
        first_account = await first_session.get(TradingAccount, account.key)
        second_account = await second_session.get(TradingAccount, account.key)
        assert first_account is not None
        assert second_account is not None
        first_task = asyncio.create_task(
            submit_live_trade_intent(
                first_session,
                account=first_account,
                intent=reduce_only_intent(first_account),
                settings=live_settings(),
                client=client,  # type: ignore[arg-type]
            )
        )
        await client.started.wait()
        second_intent = build_testnet_live_trade_intent(
            account=second_account,
            coin="ETH",
            side="long",
            notional_usd=Decimal("100"),
            limit_price=Decimal("100"),
            leverage=Decimal("1"),
            reduce_only=True,
            source_fill_id="source-fill-2",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(
            LiveOrderSubmitError,
            match="already being dispatched",
        ):
            await submit_live_trade_intent(
                second_session,
                account=second_account,
                intent=second_intent,
                settings=live_settings(),
                client=FaultInjectingTradingClient("filled"),  # type: ignore[arg-type]
            )
        client.release.set()
        first_result = await first_task

    assert first_result.order.status == "filled"
    async with integration_sessionmaker() as session:
        order_count = await session.scalar(select(func.count(TradingOrder.id)))
    assert order_count == 1


@pytest.mark.asyncio
async def test_uncertain_dispatch_is_recovered_by_cloid_before_any_retry(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    intent = reduce_only_intent(account)

    async with integration_sessionmaker() as session:
        session.add(account)
        await session.commit()
        with pytest.raises(LiveOrderSubmitError):
            await submit_live_trade_intent(
                session,
                account=account,
                intent=intent,
                settings=live_settings(),
                client=FaultInjectingTradingClient(  # type: ignore[arg-type]
                    "accepted_then_timeout"
                ),
            )

    class OrderStatusClient:
        requested_oids: list[int | str]

        def __init__(self) -> None:
            self.requested_oids = []

        async def order_status(self, *, user: str, oid: int | str) -> dict[str, object]:
            assert user == account.wallet_address
            self.requested_oids.append(oid)
            return {
                "status": "order",
                "order": {
                    "order": {"oid": 123},
                    "status": "filled",
                    "statusTimestamp": 1_725_000_000_000,
                },
            }

    info_client = OrderStatusClient()
    async with integration_sessionmaker() as session:
        result = await recover_live_order_dispatches(
            session,
            settings=live_settings(),
            info_client=info_client,  # type: ignore[arg-type]
        )

    async with integration_sessionmaker() as session:
        order = await session.scalar(select(TradingOrder))
        dispatch = await session.scalar(select(TradingOrderDispatch))

    assert info_client.requested_oids == [
        build_dispatch_client_order_id(intent.client_order_id, attempt_number=1)
    ]
    assert result.recovered == 1
    assert result.dispatched == 0
    assert order is not None
    assert order.status == "filled"
    assert dispatch is not None
    assert dispatch.status == "completed"


@pytest.mark.asyncio
async def test_definitive_ioc_reject_uses_a_new_cloid_for_the_bounded_retry(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    intent = reduce_only_intent(account)

    class RejectThenFillClient:
        def __init__(self) -> None:
            self.submitted_client_order_ids: list[str] = []

        def validate_account_order(self, *, account: TradingAccount, intent: TradeIntent) -> None:
            return None

        async def submit_order(
            self,
            *,
            account: TradingAccount,
            intent: TradeIntent,
        ) -> LiveOrderResult:
            client_order_id = str(intent.client_order_id)
            self.submitted_client_order_ids.append(client_order_id)
            if len(self.submitted_client_order_ids) == 1:
                return LiveOrderResult(
                    status="rejected",
                    client_order_id=client_order_id,
                    exchange_order_id=None,
                    filled_size=None,
                    average_fill_price=None,
                    raw_response={"status": "ok", "attempt": 1},
                    error="Order could not immediately match against any resting orders.",
                )
            return LiveOrderResult(
                status="filled",
                client_order_id=client_order_id,
                exchange_order_id="456",
                filled_size=intent.size,
                average_fill_price=intent.limit_price,
                raw_response={"status": "ok", "attempt": 2},
            )

    client = RejectThenFillClient()
    async with integration_sessionmaker() as session:
        session.add(account)
        await session.commit()
        first = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=live_settings(),
            client=client,  # type: ignore[arg-type]
        )
        assert first.order.status == "rejected"

        second = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=live_settings(),
            client=client,  # type: ignore[arg-type]
        )
        dispatches = list(
            (
                await session.scalars(
                    select(TradingOrderDispatch).order_by(
                        TradingOrderDispatch.attempt_number.asc()
                    )
                )
            ).all()
        )

    assert second.order.status == "filled"
    assert [dispatch.attempt_number for dispatch in dispatches] == [1, 2]
    assert client.submitted_client_order_ids == [
        build_dispatch_client_order_id(intent.client_order_id, attempt_number=1),
        build_dispatch_client_order_id(intent.client_order_id, attempt_number=2),
    ]


@pytest.mark.asyncio
async def test_duplicate_and_partial_exchange_fills_are_idempotent(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    intent = reduce_only_intent(account)
    order = TradingOrder(
        account_key=account.key,
        account_type="live",
        source_wallet=intent.source_wallet,
        source_fill_id=intent.source_fill_id,
        sequence_index=intent.sequence_index,
        client_order_id=intent.client_order_id,
        exchange_order_id="123",
        coin=intent.coin,
        action=intent.action,
        side=intent.side,
        is_buy=intent.is_buy,
        reduce_only=intent.reduce_only,
        order_type="ioc",
        status="accepted",
        requested_size=Decimal("2"),
        requested_notional_usd=Decimal("200"),
        margin_usd=Decimal("200"),
        leverage=Decimal("1"),
        limit_price=Decimal("100"),
        filled_size=Decimal("0"),
        filled_notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )
    fill = {
        "coin": "BTC",
        "px": "100",
        "sz": "1",
        "time": 1_725_000_000_000,
        "side": "A",
        "dir": "Close Long",
        "closedPnl": "1",
        "fee": "0.01",
        "tid": 99,
        "oid": 123,
        "cloid": build_dispatch_client_order_id(intent.client_order_id, attempt_number=1),
    }

    async with integration_sessionmaker() as session:
        session.add_all([account, order])
        await session.flush()
        session.add(
            TradingOrderDispatch(
                order_id=order.id,
                account_key=account.key,
                client_order_id=build_dispatch_client_order_id(
                    intent.client_order_id,
                    attempt_number=1,
                ),
                attempt_number=1,
                status="completed",
                attempt_count=1,
                available_at=datetime.now(UTC),
            )
        )
        await session.commit()
        inserted_first = await reconcile_live_fills(session, account=account, fills=[fill])
        inserted_second = await reconcile_live_fills(session, account=account, fills=[fill])
        updated = await update_live_orders_from_reconciled_fills(
            session,
            account_key=account.key,
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        fill_count = await session.scalar(select(func.count(TradingFill.id)))
        stored_order = await session.scalar(select(TradingOrder))

    assert inserted_first == 1
    assert inserted_second == 0
    assert fill_count == 1
    assert updated == 1
    assert stored_order is not None
    assert stored_order.status == "partially_filled"
    assert stored_order.filled_size == Decimal("1")


@pytest.mark.asyncio
async def test_reconciliation_repairs_account_totals_from_fill_ledger(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    account.realized_pnl_usd = Decimal("999")
    account.fee_usd = Decimal("999")
    fill = TradingFill(
        account_key=account.key,
        account_type="live",
        source_wallet=LIVE_EXCHANGE_SOURCE,
        source_fill_id="fill-ledger-1",
        exchange_fill_id="exchange-fill-ledger-1",
        coin="BTC",
        action="close",
        side="long",
        price=Decimal("100"),
        size=Decimal("1"),
        notional_usd=Decimal("100"),
        fee_usd=Decimal("0.25"),
        realized_pnl_usd=Decimal("5"),
        raw_payload={},
        filled_at=datetime.now(UTC),
    )

    async with integration_sessionmaker() as session:
        session.add_all([account, fill])
        await session.commit()
        await recompute_live_account_fill_totals(session, account=account)
        await session.commit()

    async with integration_sessionmaker() as session:
        stored_account = await session.get(TradingAccount, account.key)

    assert stored_account is not None
    assert stored_account.realized_pnl_usd == Decimal("5")
    assert stored_account.fee_usd == Decimal("0.25")


class PartialPerpStateClient:
    async def perp_dexs(self) -> list[dict[str, str]]:
        return [{"name": "xyz"}]

    async def clearinghouse_state(
        self,
        *,
        user: str,
        dex: str | None = None,
    ) -> dict:
        if dex == "xyz":
            raise TimeoutError("HIP-3 dex snapshot timed out.")
        return {"assetPositions": [], "marginSummary": {"accountValue": "0"}}


@pytest.mark.asyncio
async def test_partial_hip3_snapshot_preserves_existing_position(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    existing_position = TradingPosition(
        account_key=account.key,
        account_type="live",
        source_wallet=LIVE_EXCHANGE_SOURCE,
        coin="xyz:SNDK",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("1"),
        notional_usd=Decimal("1"),
        leverage=Decimal("1"),
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    source_position = TradingPosition(
        account_key=account.key,
        account_type="live",
        source_wallet="0xsource",
        coin="xyz:SNDK",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("1"),
        notional_usd=Decimal("1"),
        leverage=Decimal("1"),
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    client = PartialPerpStateClient()

    async with integration_sessionmaker() as session:
        session.add_all([account, existing_position, source_position])
        await session.commit()
        perp_states = await fetch_live_perp_states(  # type: ignore[arg-type]
            client,
            user_address=account.wallet_address or "",
        )
        result = await reconcile_live_positions(
            session,
            account=account,
            perp_states=perp_states,
            reconciled_at=datetime.now(UTC),
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        preserved = list(
            (
                await session.scalars(
                    select(TradingPosition).where(TradingPosition.coin == "xyz:SNDK")
                )
            ).all()
        )

    assert result.complete is False
    assert result.removed_positions == 0
    assert len(preserved) == 2


@pytest.mark.asyncio
async def test_complete_empty_dex_snapshot_removes_stale_exchange_and_source_positions(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    positions = [
        TradingPosition(
            account_key=account.key,
            account_type="live",
            source_wallet=source_wallet,
            coin="BTC",
            side="long",
            size=Decimal("1"),
            entry_price=Decimal("100"),
            notional_usd=Decimal("100"),
            leverage=Decimal("1"),
            margin_usd=Decimal("100"),
            realized_pnl_usd=Decimal("0"),
            fee_usd=Decimal("0"),
            opened_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for source_wallet in (LIVE_EXCHANGE_SOURCE, "0xsource")
    ]

    async with integration_sessionmaker() as session:
        session.add_all([account, *positions])
        await session.commit()
        result = await reconcile_live_positions(
            session,
            account=account,
            perp_states=[
                LivePerpState(
                    dex="",
                    payload={"assetPositions": [], "marginSummary": {"accountValue": "0"}},
                )
            ],
            reconciled_at=datetime.now(UTC),
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        position_count = await session.scalar(select(func.count(TradingPosition.id)))

    assert result.complete is True
    assert result.removed_positions == 2
    assert position_count == 0


@pytest.mark.asyncio
async def test_partial_reconciliation_is_audited_and_does_not_advance_complete_timestamp(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    existing_position = TradingPosition(
        account_key=account.key,
        account_type="live",
        source_wallet=LIVE_EXCHANGE_SOURCE,
        coin="xyz:SNDK",
        side="long",
        size=Decimal("1"),
        entry_price=Decimal("1"),
        notional_usd=Decimal("1"),
        leverage=Decimal("1"),
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    class PartialReconciliationClient(PartialPerpStateClient):
        async def user_fills_by_time(self, **_kwargs: object) -> list[dict[str, object]]:
            return []

        async def spot_clearinghouse_state(self, *, user: str) -> dict[str, object]:
            assert user == account.wallet_address
            return {
                "balances": [{"coin": "USDC", "total": "100", "hold": "0"}],
                "tokenToAvailableAfterMaintenance": [[0, "100"]],
            }

        async def user_abstraction(self, *, user: str) -> str:
            assert user == account.wallet_address
            return "unifiedAccount"

    settings = live_settings()
    settings.live_trading_capital_mode = "unified"
    async with integration_sessionmaker() as session:
        session.add_all([account, existing_position])
        await session.commit()
        result = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=PartialReconciliationClient(),  # type: ignore[arg-type]
        )

    async with integration_sessionmaker() as session:
        stored_account = await session.get(TradingAccount, account.key)
        run = await session.scalar(select(TradingReconciliationRun))
        preserved_position = await session.scalar(
            select(TradingPosition).where(TradingPosition.coin == "xyz:SNDK")
        )

    assert result.status == "partial"
    assert "perp:xyz" in result.incomplete_components
    assert stored_account is not None
    assert stored_account.last_reconciled_at is None
    assert stored_account.config_payload["lastReconciliationAttempt"]["status"] == "partial"
    assert run is not None
    assert run.status == "partial"
    assert run.components["perpStates"]["xyz"]["status"] == "partial"
    assert preserved_position is not None


@pytest.mark.asyncio
async def test_reconciliation_run_history_prunes_rows_older_than_thirty_days(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account = live_account()
    now = datetime(2026, 7, 9, tzinfo=UTC)
    old_run = TradingReconciliationRun(
        account_key=account.key,
        status="complete",
        started_at=now - timedelta(days=31),
        completed_at=now - timedelta(days=31),
        components={},
    )
    current_run = TradingReconciliationRun(
        account_key=account.key,
        status="complete",
        started_at=now,
        completed_at=now,
        components={},
    )

    async with integration_sessionmaker() as session:
        session.add_all([account, old_run, current_run])
        await session.commit()
        await prune_live_reconciliation_runs(
            session,
            account_key=account.key,
            now=now,
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        runs = list((await session.scalars(select(TradingReconciliationRun))).all())

    assert [run.id for run in runs] == [current_run.id]
