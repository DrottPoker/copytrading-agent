from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services.paper_trading_service import (
    load_source_account_state,
    source_fill_age_exceeds_entry_limit,
)
from app.services.trading_core import (
    adjust_open_sizing_to_min_order,
    build_client_order_id,
    open_notional_skip_reason,
    trade_is_buy,
)


def test_adjust_open_sizing_to_min_order_when_enabled() -> None:
    settings = Settings(
        trading_copy_min_order_notional_usd=Decimal("10"),
        trading_copy_adjust_small_orders_to_min_order=True,
    )

    margin_usd, notional_usd, adjustment = adjust_open_sizing_to_min_order(
        target_notional=Decimal("5"),
        margin_usd=Decimal("1"),
        notional_usd=Decimal("5"),
        source_remaining=Decimal("20"),
        global_remaining=Decimal("20"),
        source_leverage=Decimal("5"),
        settings=settings,
    )

    assert margin_usd == Decimal("2")
    assert notional_usd == Decimal("10")
    assert adjustment is not None
    assert adjustment.original_notional_usd == Decimal("5")
    assert adjustment.adjusted_notional_usd == Decimal("10")
    assert adjustment.min_order_notional_usd == Decimal("10")


def test_adjust_open_sizing_to_min_order_when_disabled() -> None:
    settings = Settings(
        trading_copy_min_order_notional_usd=Decimal("10"),
        trading_copy_adjust_small_orders_to_min_order=False,
    )

    margin_usd, notional_usd, adjustment = adjust_open_sizing_to_min_order(
        target_notional=Decimal("5"),
        margin_usd=Decimal("1"),
        notional_usd=Decimal("5"),
        source_remaining=Decimal("20"),
        global_remaining=Decimal("20"),
        source_leverage=Decimal("5"),
        settings=settings,
    )

    assert margin_usd == Decimal("1")
    assert notional_usd == Decimal("5")
    assert adjustment is None


def test_adjust_open_sizing_to_min_order_respects_caps() -> None:
    settings = Settings(
        trading_copy_min_order_notional_usd=Decimal("10"),
        trading_copy_adjust_small_orders_to_min_order=True,
    )

    margin_usd, notional_usd, adjustment = adjust_open_sizing_to_min_order(
        target_notional=Decimal("5"),
        margin_usd=Decimal("1"),
        notional_usd=Decimal("5"),
        source_remaining=Decimal("1.5"),
        global_remaining=Decimal("20"),
        source_leverage=Decimal("5"),
        settings=settings,
    )

    assert margin_usd == Decimal("1")
    assert notional_usd == Decimal("5")
    assert adjustment is None
    assert (
        open_notional_skip_reason(
            target_notional=Decimal("5"),
            source_remaining=Decimal("7.5"),
            global_remaining=Decimal("100"),
            min_order_notional=Decimal("10"),
        )
        == "source_allocation_cap_reached"
    )


def test_source_fill_age_limit_allows_fresh_entries() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(seconds=5)).timestamp() * 1000)}

    assert not source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=15),
        now=now,
    )


def test_source_fill_age_limit_blocks_stale_entries() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(seconds=16)).timestamp() * 1000)}

    assert source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=15),
        now=now,
    )


def test_source_fill_age_limit_can_be_disabled() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(minutes=5)).timestamp() * 1000)}

    assert not source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=0),
        now=now,
    )


def test_build_client_order_id_is_deterministic_hyperliquid_cloid() -> None:
    first = build_client_order_id(
        account_key="live_1",
        source_wallet="0xABC",
        source_fill_id="123",
        sequence_index=0,
        action="open",
    )
    second = build_client_order_id(
        account_key="LIVE_1",
        source_wallet="0xabc",
        source_fill_id="123",
        sequence_index=0,
        action="open",
    )

    assert first == second
    assert first.startswith("0x")
    assert len(first) == 34


def test_trade_is_buy_matches_side_and_reduce_only() -> None:
    assert trade_is_buy(side="long", reduce_only=False) is True
    assert trade_is_buy(side="long", reduce_only=True) is False
    assert trade_is_buy(side="short", reduce_only=False) is False
    assert trade_is_buy(side="short", reduce_only=True) is True


@pytest.mark.asyncio
async def test_load_source_account_state_uses_unified_spot_equity_when_perp_zero() -> None:
    client = FakeUnifiedSourceClient()

    state = await load_source_account_state(
        client=client,
        source_wallet="0xsource",
        dex="xyz",
        unified_equity_cache={},
    )

    assert state.perp_equity == Decimal("200")
    assert state.skip_reason is None
    assert client.user_abstraction_calls == 1
    assert client.spot_state_calls == 1


class FakeUnifiedSourceClient:
    def __init__(self) -> None:
        self.user_abstraction_calls = 0
        self.spot_state_calls = 0

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict:
        return {
            "assetPositions": [],
            "marginSummary": {"accountValue": "0"},
        }

    async def user_abstraction(self, *, user: str) -> str:
        self.user_abstraction_calls += 1
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        self.spot_state_calls += 1
        return {
            "balances": [{"coin": "USDC", "total": "200", "hold": "0"}],
            "tokenToAvailableAfterMaintenance": [[0, "200"]],
        }
