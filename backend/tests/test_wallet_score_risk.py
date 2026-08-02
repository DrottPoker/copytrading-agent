import asyncio
import inspect
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services import wallet_score_service
from app.services.source_trade_reconstruction_service import (
    OpenSourceTrade,
    ReconstructedWalletTrades,
)
from app.services.wallet_score_service import (
    WalletScoreMetrics,
    calculate_profitability_score,
    calculate_wallet_score,
    current_drawdown_risk_penalty,
    current_drawdown_score_cap,
    forced_exit_fill_ratio_score,
    forced_exit_severity_penalty,
    metric_with_current_drawdown,
    metrics_with_current_drawdowns,
    metrics_with_reconstructed_trades,
)

ZERO = Decimal("0")
HUNDRED = Decimal("100")


def test_raw_fill_summary_defers_trade_metrics_to_materialized_trades() -> None:
    source = inspect.getsource(wallet_score_service.load_wallet_score_metrics)

    assert "ordered_fills" not in source
    assert "running_peaks" not in source
    assert "coin_notional" not in source
    assert "max_drawdown_usd" not in source

    metrics = wallet_score_service.base_metrics_from_row(
        {
            "wallet_address": "0x1111111111111111111111111111111111111111",
            "fill_count": 250,
            "first_fill_time_ms": 1_700_000_000_000,
            "last_activity_time_ms": 1_800_000_000_000,
            "liquidation_fill_count": 2,
            "liquidation_event_count": 1,
            "liquidation_notional_usd": Decimal("1250"),
        }
    )

    assert metrics.fill_count == 250
    assert metrics.liquidation_fill_count == 2
    assert metrics.liquidation_event_count == 1
    assert metrics.liquidation_notional_usd == Decimal("1250")
    assert metrics.total_notional_usd == ZERO
    assert metrics.net_pnl_usd == ZERO
    assert metrics.max_coin_notional_usd == ZERO
    assert metrics.max_drawdown_usd == ZERO
    assert metrics.trades_24h == 0
    assert metrics.trades_7d == 0


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


@pytest.mark.asyncio
async def test_current_drawdown_uses_unified_account_value_when_perp_equity_is_zero() -> None:
    metric = wallet_metrics(current_drawdown_pct=Decimal("0"))

    updated = await metric_with_current_drawdown(
        metric,
        client=FakeUnifiedWalletClient(),
        known_dexes=(),
        settings=risk_settings(),
    )

    assert updated.current_perp_equity_usd == Decimal("0")
    assert updated.current_account_value_usd == Decimal("200")
    assert updated.current_drawdown_pct == Decimal("0.1000")
    assert updated.current_margin_usage_pct == Decimal("0.0500")
    assert updated.current_notional_exposure_pct == Decimal("0.5000")
    assert updated.current_drawdown_status == "ok"


@pytest.mark.asyncio
async def test_current_drawdown_runtime_budget_keeps_scoring_completable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = risk_settings()
    settings.scoring_current_drawdown_concurrency = 1
    settings.scoring_current_drawdown_run_timeout_seconds = 1
    first = wallet_metrics(current_drawdown_pct=Decimal("0"))
    pending = replace(
        wallet_metrics(current_drawdown_pct=Decimal("0")),
        wallet_address="0x2222222222222222222222222222222222222222",
    )

    async def fake_known_dexes(*_args: object, **_kwargs: object) -> dict[str, tuple[str, ...]]:
        return {}

    async def fake_live_metric(
        metric: WalletScoreMetrics,
        **_kwargs: object,
    ) -> WalletScoreMetrics:
        if metric.wallet_address == first.wallet_address:
            return replace(metric, current_drawdown_status="ok")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def fake_progress(*_args: object, **_kwargs: object) -> None:
        return None

    class FakeClient:
        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        wallet_score_service,
        "load_known_wallet_perp_dexes_for_addresses",
        fake_known_dexes,
    )
    monkeypatch.setattr(wallet_score_service, "metric_with_current_drawdown", fake_live_metric)
    monkeypatch.setattr(wallet_score_service, "mark_operation_progress", fake_progress)
    monkeypatch.setattr(wallet_score_service, "HyperliquidClient", FakeClient)

    result = await metrics_with_current_drawdowns(
        object(),  # type: ignore[arg-type]
        metrics=[first, pending],
        settings=settings,
    )

    assert result[0].current_drawdown_status == "ok"
    assert result[1].current_drawdown_status == "unavailable"


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


def test_open_trade_realized_loss_reduces_profitability() -> None:
    settings = risk_settings()
    wallet_address = "0x1111111111111111111111111111111111111111"
    trades = ReconstructedWalletTrades(wallet_address=wallet_address)
    closed_trade = OpenSourceTrade(
        wallet_address=wallet_address,
        coin="HYPE",
        side="long",
        opened_at_ms=1_780_000_000_000,
    )
    closed_trade.add_entry(
        size=Decimal("10"),
        notional_usd=Decimal("100"),
        fee_usd=ZERO,
        timestamp_ms=1_780_000_000_000,
    )
    closed_trade.add_close(
        size=Decimal("10"),
        notional_usd=Decimal("110"),
        pnl_usd=Decimal("10"),
        fee_usd=ZERO,
        is_liquidation=False,
    )
    trades.record_closed_trade(
        closed_trade,
        closed_at_ms=1_780_010_000_000,
        start_24h_ms=0,
        start_7d_ms=0,
    )
    open_trade = OpenSourceTrade(
        wallet_address=wallet_address,
        coin="HYPE",
        side="short",
        opened_at_ms=1_780_020_000_000,
    )
    open_trade.add_entry(
        size=Decimal("100"),
        notional_usd=Decimal("1000"),
        fee_usd=ZERO,
        timestamp_ms=1_780_020_000_000,
    )
    open_trade.add_close(
        size=Decimal("50"),
        notional_usd=Decimal("450"),
        pnl_usd=Decimal("-200"),
        fee_usd=ZERO,
        is_liquidation=True,
    )
    trades.record_open_trade(open_trade)

    metrics = metrics_with_reconstructed_trades(
        wallet_metrics(current_drawdown_pct=ZERO),
        trades,
        settings=settings,
    )

    assert metrics.trade_count == 1
    assert metrics.open_trade_count == 1
    assert metrics.net_pnl_usd == Decimal("-190")
    assert metrics.total_notional_usd == Decimal("600")
    assert metrics.gross_loss_usd == Decimal("200")
    assert metrics.liquidation_trade_count == 1
    assert calculate_profitability_score(metrics, settings=settings) < HUNDRED


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
        current_account_value_usd=Decimal("1000"),
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


class FakeUnifiedWalletClient:
    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict:
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": "HYPE",
                        "entryPx": "100",
                        "leverage": {"type": "cross", "value": "5"},
                        "marginUsed": "10",
                        "positionValue": "100",
                        "szi": "1",
                        "unrealizedPnl": "-20",
                    }
                }
            ],
            "marginSummary": {
                "accountValue": "0",
                "totalMarginUsed": "10",
                "totalNtlPos": "100",
            },
            "withdrawable": "0",
        }

    async def user_abstraction(self, *, user: str) -> str:
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        return {
            "balances": [{"coin": "USDC", "hold": "0", "total": "200"}],
            "tokenToAvailableAfterMaintenance": [[0, "200"]],
        }
