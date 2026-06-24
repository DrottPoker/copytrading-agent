from decimal import Decimal

import pytest

from app.schemas.wallet_stats import WalletPerpPositionStats
from app.services.wallet_current_state_service import (
    OpenPositionTradeStats,
    WalletPerpStateSummary,
    annotate_open_position_source_stats,
    load_wallet_account_value_summary,
)


def test_annotate_open_position_source_stats_adds_reconstructed_trade_metrics() -> None:
    position = wallet_position(coin="HYPE", side="short")

    annotate_open_position_source_stats(
        [position],
        {
            ("HYPE", "short"): OpenPositionTradeStats(
                opened_at_ms=1_781_000_000_000,
                realized_pnl_usd=Decimal("-185.52"),
                net_pnl_usd=Decimal("-186.00"),
                add_fill_count=12,
                reduce_fill_count=4,
                liquidation_fill_count=2,
            )
        },
    )

    assert position.opened_at_ms == 1_781_000_000_000
    assert position.realized_pnl_usd == Decimal("-185.52")
    assert position.net_pnl_usd == Decimal("-186.00")
    assert position.add_fill_count == 12
    assert position.reduce_fill_count == 4
    assert position.liquidation_fill_count == 2


def test_annotate_open_position_source_stats_matches_dex_prefixed_coin() -> None:
    position = wallet_position(coin="xyz:HYPE", side="long")

    annotate_open_position_source_stats(
        [position],
        {
            ("HYPE", "long"): OpenPositionTradeStats(
                opened_at_ms=1_781_000_000_000,
                realized_pnl_usd=Decimal("10"),
                net_pnl_usd=Decimal("9.5"),
                add_fill_count=1,
                reduce_fill_count=2,
            )
        },
    )

    assert position.opened_at_ms == 1_781_000_000_000
    assert position.realized_pnl_usd == Decimal("10")
    assert position.add_fill_count == 1
    assert position.reduce_fill_count == 2


@pytest.mark.asyncio
async def test_load_wallet_account_value_summary_uses_unified_spot_balance() -> None:
    summary = WalletPerpStateSummary(
        state_time_ms=1,
        account_value_usd=Decimal("0"),
        withdrawable_usd=Decimal("0"),
        total_position_notional_usd=Decimal("100"),
        total_margin_used_usd=Decimal("10"),
        total_unrealized_pnl_usd=Decimal("-20"),
        positions=[],
        raw_positions=[],
    )

    result = await load_wallet_account_value_summary(
        client=FakeUnifiedWalletClient(),
        address="0xwallet",
        perp_summary=summary,
    )

    assert result.uses_unified_account is True
    assert result.perp_equity_usd == Decimal("0")
    assert result.account_value_usd == Decimal("200")
    assert result.withdrawable_usd == Decimal("190")
    assert result.spot_usdc_total == Decimal("200")
    assert result.spot_usdc_available == Decimal("190")
    assert result.user_abstraction == "unifiedAccount"
    assert result.error is None


def wallet_position(*, coin: str, side: str) -> WalletPerpPositionStats:
    return WalletPerpPositionStats(
        coin=coin,
        side=side,
        opened_at_ms=None,
        size=Decimal("1"),
        entry_price=Decimal("100"),
        position_value_usd=Decimal("100"),
        unrealized_pnl_usd=Decimal("0"),
        return_on_equity=None,
        margin_used_usd=Decimal("10"),
        liquidation_price=None,
        leverage_type="cross",
        leverage_value=10,
    )


class FakeUnifiedWalletClient:
    async def user_abstraction(self, *, user: str) -> str:
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        return {
            "balances": [{"coin": "USDC", "hold": "0", "total": "200"}],
            "tokenToAvailableAfterMaintenance": [[0, "190"]],
        }
