from decimal import Decimal

from app.services.wallet_stats_service import wallet_window_stats_from_values


def test_wallet_window_stats_from_values_calculates_net_roi() -> None:
    stats = wallet_window_stats_from_values(
        label="All time",
        fill_count=3,
        notional_usd=Decimal("1000"),
        pnl_usd=Decimal("120"),
        fee_usd=Decimal("20"),
    )

    assert stats.label == "All time"
    assert stats.fill_count == 3
    assert stats.pnl_usd == Decimal("120")
    assert stats.roi_pct == Decimal("0.1")


def test_wallet_window_stats_from_values_handles_zero_notional() -> None:
    stats = wallet_window_stats_from_values(
        label="All time",
        fill_count=0,
        notional_usd=Decimal("0"),
        pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )

    assert stats.roi_pct is None
