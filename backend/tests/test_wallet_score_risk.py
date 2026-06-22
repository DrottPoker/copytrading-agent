from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import Settings
from app.services.wallet_score_service import (
    WalletScoreMetrics,
    calculate_wallet_score,
    current_drawdown_risk_penalty,
    current_drawdown_score_cap,
    forced_exit_fill_ratio_score,
    forced_exit_severity_penalty,
)


def test_large_current_drawdown_is_penalized_without_zeroing_score_too_early() -> None:
    settings = risk_settings()
    metrics = wallet_metrics(current_drawdown_pct=Decimal("0.685"))

    breakdown = calculate_wallet_score(
        metrics,
        settings=settings,
        now=datetime.fromtimestamp(1_781_000_000, UTC),
    )

    assert breakdown.risk_score == Decimal("9.29")
    assert breakdown.live_risk_score_cap == Decimal("42.00")
    assert breakdown.score == Decimal("42.00")


def test_current_drawdown_penalty_is_inactive_until_start_ratio() -> None:
    settings = risk_settings()

    assert current_drawdown_risk_penalty(Decimal("0.04"), settings=settings) == Decimal("0")
    assert current_drawdown_risk_penalty(Decimal("0.40"), settings=settings) == Decimal("50.0")
    assert current_drawdown_risk_penalty(Decimal("0.80"), settings=settings) == Decimal("100")


def test_current_drawdown_score_cap_is_inactive_until_start_ratio() -> None:
    settings = risk_settings()

    assert current_drawdown_score_cap(Decimal("0.24"), settings=settings) is None
    assert current_drawdown_score_cap(Decimal("0.625"), settings=settings) == Decimal("50.00")


def test_forced_exit_severity_scales_with_notional_even_when_profitable() -> None:
    settings = risk_settings()

    small = wallet_metrics(current_drawdown_pct=Decimal("0"))
    small = replace_forced_exit_metrics(small, notional_usd=Decimal("1000"))
    large = wallet_metrics(current_drawdown_pct=Decimal("0"))
    large = replace_forced_exit_metrics(large, notional_usd=Decimal("25000"))

    assert forced_exit_severity_penalty(small, settings=settings) == Decimal("0.60")
    assert forced_exit_severity_penalty(large, settings=settings) == Decimal("15")


def test_forced_exit_fill_ratio_reduces_copyability() -> None:
    settings = risk_settings()

    rare = replace_forced_exit_metrics(
        wallet_metrics(current_drawdown_pct=Decimal("0")),
        close_fill_count=100,
        liquidation_close_fill_count=2,
        notional_usd=Decimal("1000"),
    )
    frequent = replace_forced_exit_metrics(
        wallet_metrics(current_drawdown_pct=Decimal("0")),
        close_fill_count=100,
        liquidation_close_fill_count=20,
        notional_usd=Decimal("1000"),
    )

    assert forced_exit_fill_ratio_score(rare, settings=settings) == Decimal("90.00")
    assert forced_exit_fill_ratio_score(frequent, settings=settings) == Decimal("0")


def risk_settings() -> Settings:
    return Settings(
        scoring_forced_exit_notional_full_ratio=Decimal("0.25"),
        scoring_forced_exit_penalty_max=Decimal("15"),
        scoring_copyability_forced_exit_fill_ratio_zero_score_ratio=Decimal("0.20"),
        scoring_current_drawdown_penalty_start_ratio=Decimal("0.05"),
        scoring_current_drawdown_full_penalty_ratio=Decimal("0.75"),
        scoring_current_drawdown_penalty_max=Decimal("100"),
        scoring_current_drawdown_score_cap_start_ratio=Decimal("0.25"),
        scoring_current_drawdown_score_cap_zero_ratio=Decimal("1"),
        scoring_confidence_target_trades=50,
    )


def wallet_metrics(*, current_drawdown_pct: Decimal) -> WalletScoreMetrics:
    return WalletScoreMetrics(
        wallet_address="0x1111111111111111111111111111111111111111",
        fill_count=180,
        trade_count=50,
        ignored_fill_count=0,
        open_trade_count=1,
        close_fill_count=100,
        unique_coin_count=5,
        active_days=20,
        total_notional_usd=Decimal("100000"),
        average_trade_notional_usd=Decimal("2000"),
        median_trade_notional_usd=Decimal("2000"),
        p25_trade_notional_usd=Decimal("1500"),
        copyable_trade_ratio=Decimal("1"),
        average_fills_per_trade=Decimal("2"),
        average_trade_roi=Decimal("0.02"),
        median_trade_roi=Decimal("0.02"),
        total_pnl_usd=Decimal("2000"),
        total_fee_usd=Decimal("0"),
        net_pnl_usd=Decimal("2000"),
        gross_profit_usd=Decimal("2000"),
        gross_loss_usd=Decimal("0"),
        profitable_trade_count=50,
        losing_trade_count=0,
        effective_winning_trade_count=Decimal("50"),
        largest_win_profit_share=Decimal("0.02"),
        trade_roi_stddev=Decimal("0.01"),
        downside_trade_roi_stddev=Decimal("0"),
        max_inactive_gap_days=2,
        liquidation_fill_count=0,
        liquidation_event_count=0,
        liquidation_trade_count=0,
        liquidation_close_fill_count=0,
        liquidation_notional_usd=Decimal("0"),
        max_coin_notional_usd=Decimal("25000"),
        max_drawdown_usd=Decimal("0"),
        current_perp_equity_usd=Decimal("1000"),
        current_unrealized_pnl_usd=-current_drawdown_pct * Decimal("1000"),
        current_drawdown_pct=current_drawdown_pct,
        current_margin_usage_pct=Decimal("0.10"),
        current_notional_exposure_pct=Decimal("1"),
        open_position_stress_pct=current_drawdown_pct,
        current_drawdown_status="ok",
        first_trade_time_ms=1_780_000_000_000,
        last_trade_time_ms=1_781_000_000_000,
        trades_24h=1,
        notional_24h=Decimal("2000"),
        net_pnl_24h=Decimal("40"),
        trades_7d=10,
        notional_7d=Decimal("20000"),
        net_pnl_7d=Decimal("400"),
    )


def replace_forced_exit_metrics(
    metrics: WalletScoreMetrics,
    *,
    close_fill_count: int = 100,
    liquidation_close_fill_count: int = 1,
    notional_usd: Decimal,
) -> WalletScoreMetrics:
    return WalletScoreMetrics(
        **{
            **metrics.__dict__,
            "close_fill_count": close_fill_count,
            "liquidation_fill_count": liquidation_close_fill_count,
            "liquidation_event_count": liquidation_close_fill_count,
            "liquidation_trade_count": 1,
            "liquidation_close_fill_count": liquidation_close_fill_count,
            "liquidation_notional_usd": notional_usd,
        }
    )
