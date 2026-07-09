from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import TradingAccount, TradingFill, TradingOrder, TradingPosition
from app.services.live_trading_service import (
    LIVE_EXCHANGE_SOURCE,
    LiveOrderSubmitError,
    build_testnet_live_trade_intent,
    fetch_live_perp_states,
    reconcile_live_fills,
    reconcile_live_positions,
    submit_live_trade_intent,
    update_live_orders_from_reconciled_fills,
)

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
    settings.live_trading_acknowledged = True
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
@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 must persist an uncertain order when exchange acceptance loses its response.",
)
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

    assert order is not None
    assert order.status == "uncertain"


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="Phase 2 must commit the durable order intent before exchange submission.",
)
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

    assert recovered_order is not None
    assert recovered_order.status in {"submitting", "uncertain"}


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
        "cloid": intent.client_order_id,
    }

    async with integration_sessionmaker() as session:
        session.add_all([account, order])
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
@pytest.mark.xfail(
    strict=True,
    reason="Phase 3 must not delete positions when a HIP-3 snapshot is incomplete.",
)
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
    client = PartialPerpStateClient()

    async with integration_sessionmaker() as session:
        session.add_all([account, existing_position])
        await session.commit()
        perp_states = await fetch_live_perp_states(  # type: ignore[arg-type]
            client,
            user_address=account.wallet_address or "",
        )
        await reconcile_live_positions(
            session,
            account=account,
            perp_states=perp_states,
            reconciled_at=datetime.now(UTC),
        )
        await session.commit()

    async with integration_sessionmaker() as session:
        preserved = await session.scalar(
            select(TradingPosition).where(TradingPosition.coin == "xyz:SNDK")
        )

    assert preserved is not None
