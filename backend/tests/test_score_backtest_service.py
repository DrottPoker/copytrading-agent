"""Score backtest evaluation, point-in-time inputs, and report formatting."""

import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.cli.score_backtest import format_report
from app.core.config import Settings
from app.services.ops_monitoring_service import discovery_source_health
from app.services.score_backtest_service import (
    BacktestConfig,
    ScoreSnapshotPoint,
    WalletObservation,
    cutoff_dates,
    cutoff_ms,
    evaluate_cutoff,
    recompute_score_metrics,
    spearman_correlation,
    summarize_backtest,
    wallet_observation,
)
from app.services.source_trade_reconstruction_service import ReconstructedSourceTrade

CUTOFF = date(2026, 9, 1)
DAY_MS = 86_400_000


def observation(address: str, score: str, forward_return: str | None) -> WalletObservation:
    return WalletObservation(
        wallet_address=address,
        score=Decimal(score),
        forward_net_pnl_usd=Decimal("0"),
        forward_return=Decimal(forward_return) if forward_return is not None else None,
        equity_basis="snapshot" if forward_return is not None else "missing",
    )


def trade(trade_id: str, *, closed_at_ms: int, net_pnl: str) -> ReconstructedSourceTrade:
    return ReconstructedSourceTrade(
        id=trade_id,
        wallet_address="0xwallet",
        coin="BTC",
        side="long",
        status="closed",
        opened_at_ms=closed_at_ms - 3_600_000,
        closed_at_ms=closed_at_ms,
        duration_ms=3_600_000,
        entry_size=Decimal("1"),
        closed_size=Decimal("1"),
        remaining_size=Decimal("0"),
        entry_notional_usd=Decimal("1000"),
        close_notional_usd=Decimal("1000") + Decimal(net_pnl),
        average_entry_price=Decimal("1000"),
        average_exit_price=Decimal("1000") + Decimal(net_pnl),
        realized_pnl_usd=Decimal(net_pnl),
        fee_usd=Decimal("0"),
        net_pnl_usd=Decimal(net_pnl),
        entry_fill_count=1,
        close_fill_count=1,
    )


def test_spearman_correlation_handles_ties_and_flat_input() -> None:
    perfect = spearman_correlation(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
        [Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.4")],
    )
    inverse = spearman_correlation(
        [Decimal("1"), Decimal("2"), Decimal("3")],
        [Decimal("3"), Decimal("2"), Decimal("1")],
    )

    assert perfect == Decimal("1")
    assert inverse == Decimal("-1")
    assert (
        spearman_correlation([Decimal("1")] * 3, [Decimal("1"), Decimal("2"), Decimal("3")]) is None
    )


def test_top_wallets_are_compared_with_random_selections() -> None:
    observations = [
        observation("0xa", "90", "0.10"),
        observation("0xb", "80", "0.08"),
        observation("0xc", "70", "-0.02"),
        observation("0xd", "60", "-0.04"),
        observation("0xe", "50", None),
    ]

    result = evaluate_cutoff(
        observations,
        cutoff=CUTOFF,
        source="recompute",
        top_k=2,
        random_samples=500,
        rng=random.Random(1),
    )

    assert result.eligible_wallets == 5
    assert result.evaluated_wallets == 4
    assert result.top_wallets == ["0xa", "0xb"]
    assert result.top_mean_return == Decimal("0.090000")
    assert result.universe_mean_return == Decimal("0.030000")
    assert result.random_percentile is not None
    assert result.random_percentile > Decimal("0.8")
    assert result.top_hit_rate == Decimal("1.000000")
    assert result.rank_correlation == Decimal("1.000000")


def test_cutoffs_leave_room_for_the_window_and_the_horizon() -> None:
    dates = cutoff_dates(
        first_data_date=date(2026, 6, 1),
        last_data_date=date(2026, 9, 30),
        config=BacktestConfig(horizon_days=14, step_days=7),
        window_days=60,
    )

    assert dates[0] == date(2026, 7, 31)
    assert dates[-1] <= date(2026, 9, 16)
    assert all(
        later - earlier == timedelta(days=7)
        for earlier, later in zip(dates, dates[1:], strict=False)
    )


def test_recompute_only_sees_trades_closed_before_the_cutoff() -> None:
    config = Settings(_env_file=None)
    start = cutoff_ms(CUTOFF)
    trades = [
        trade("past", closed_at_ms=start - 2 * DAY_MS, net_pnl="50"),
        trade("future", closed_at_ms=start + DAY_MS, net_pnl="-500"),
    ]

    metrics = recompute_score_metrics("0xwallet", trades, cutoff=CUTOFF, settings=config)

    assert metrics is not None
    assert metrics.trade_count == 1
    assert metrics.net_pnl_usd == Decimal("50")


def test_forward_return_uses_equity_from_the_snapshot_at_the_cutoff() -> None:
    start = cutoff_ms(CUTOFF)
    trades = [
        trade("past", closed_at_ms=start - DAY_MS, net_pnl="999"),
        trade("inside", closed_at_ms=start + DAY_MS, net_pnl="100"),
        trade("after", closed_at_ms=start + 30 * DAY_MS, net_pnl="999"),
    ]
    snapshots = {
        CUTOFF - timedelta(days=1): {
            "0xwallet": ScoreSnapshotPoint(
                score=Decimal("70"),
                payload={"accountValueUsd": "2000"},
            )
        }
    }

    result = wallet_observation(
        "0xwallet",
        Decimal("70"),
        trades,
        snapshots_by_date=snapshots,
        current_equity={},
        cutoff=CUTOFF,
        horizon_days=14,
    )

    assert result.forward_net_pnl_usd == Decimal("100")
    assert result.forward_return == Decimal("0.050000")
    assert result.equity_basis == "snapshot"


def test_report_summarizes_cutoffs_and_explains_missing_history() -> None:
    config = BacktestConfig(top_k=2)
    empty = format_report(summarize_backtest(config, []))
    result = evaluate_cutoff(
        [observation("0xa", "90", "0.10"), observation("0xb", "80", "0.05")],
        cutoff=CUTOFF,
        source="snapshots",
        top_k=2,
        random_samples=10,
        rng=random.Random(1),
    )
    report = format_report(summarize_backtest(config, [result]))

    assert "No cutoff had a full scoring window" in empty
    assert "2026-09-01" in report
    assert "Mean top return" in report


def test_discovery_source_health_flags_repeated_failures_and_empty_results() -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)

    def run(status: str, fetched: int = 0, error: str | None = None, age: int = 0) -> dict:
        return {
            "status": status,
            "fetched_count": fetched,
            "error": error,
            "started_at": now - timedelta(hours=age),
        }

    down = discovery_source_health(
        "hyperdash_tagged",
        [run("failed", error="HTTP 403", age=0), run("failed", age=6), run("failed", age=12)],
    )
    flaky = discovery_source_health(
        "hypertracker_grinder",
        [run("failed", error="timeout"), run("succeeded", fetched=40, age=6)],
    )
    empty = discovery_source_health("hyperliquid_leaderboard_day", [run("succeeded", fetched=0)])
    healthy = discovery_source_health("hyperliquid_leaderboard_week", [run("succeeded", 25)])
    never = discovery_source_health("hyperdash_cohorts", [])

    assert (down.status, down.consecutive_failures, down.last_error) == ("degraded", 3, "HTTP 403")
    assert (flaky.status, flaky.last_fetched_count) == ("warning", 40)
    assert empty.status == "warning"
    assert healthy.status == "ok"
    assert never.status == "unknown"
