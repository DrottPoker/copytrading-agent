"""Equity-curve return and drawdown from Hyperliquid portfolio history."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.services.wallet_equity_service import (
    WalletEquityMetrics,
    combined_interval_returns,
    equity_metrics_from_portfolio,
    equity_refresh_due,
    portfolio_period_points,
)

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
START_MS = 1_780_000_000_000


def period(points: list[tuple[int, str, str]]) -> dict:
    """Portfolio period payload from (time_ms, account_value, cumulative_pnl) points."""

    return {
        "accountValueHistory": [[time_ms, value] for time_ms, value, _ in points],
        "pnlHistory": [[time_ms, pnl] for time_ms, _, pnl in points],
        "vlm": "0",
    }


def daily(values: list[tuple[str, str]], *, start_ms: int = START_MS) -> list[tuple[int, str, str]]:
    return [(start_ms + index * DAY_MS, value, pnl) for index, (value, pnl) in enumerate(values)]


def metrics(payload: list, **overrides: object) -> WalletEquityMetrics:
    options: dict = {
        "min_points": 3,
        "min_coverage_days": 2,
        "min_account_value_usd": Decimal("10"),
    }
    options.update(overrides)
    return equity_metrics_from_portfolio(payload, **options)


def test_period_points_pair_account_value_with_cumulative_pnl() -> None:
    payload = [["perpMonth", period(daily([("1000", "0"), ("1100", "100")]))]]

    points = portfolio_period_points(payload, "perpMonth")

    assert [(point.account_value_usd, point.pnl_usd) for point in points] == [
        (Decimal("1000"), Decimal("0")),
        (Decimal("1100"), Decimal("100")),
    ]
    assert portfolio_period_points(payload, "perpWeek") == []


def test_time_weighted_return_compounds_pnl_on_prior_equity() -> None:
    payload = [
        [
            "perpMonth",
            period(daily([("1000", "0"), ("1100", "100"), ("1210", "210"), ("1331", "331")])),
        ]
    ]

    result = metrics(payload)

    assert result.status == "ok"
    assert result.basis == "perp"
    assert result.return_pct == Decimal("0.331000")
    assert result.max_drawdown_pct == Decimal("0")


def test_deposits_and_withdrawals_are_not_returns() -> None:
    # A 9000 USD deposit lifts account value without any PnL change.
    payload = [
        [
            "perpMonth",
            period(daily([("1000", "0"), ("10000", "0"), ("10000", "0"), ("5000", "0")])),
        ]
    ]

    result = metrics(payload)

    assert result.return_pct == Decimal("0")
    assert result.max_drawdown_pct == Decimal("0")


def test_drawdown_includes_unrealized_losses_in_the_curve() -> None:
    payload = [
        [
            "perpMonth",
            period(daily([("1000", "0"), ("1100", "100"), ("880", "-120"), ("990", "-10")])),
        ]
    ]

    result = metrics(payload)

    # 1.10 peak, then -220 / 1100 to 0.88, a 20% fall from the peak.
    assert result.max_drawdown_pct == Decimal("0.200000")
    assert result.return_pct is not None
    assert result.return_pct < Decimal("0")


def test_unified_accounts_fall_back_to_total_equity() -> None:
    zero_perp = daily([("0", "0"), ("0", "50"), ("0", "100"), ("0", "150")])
    total = daily([("1000", "0"), ("1050", "50"), ("1100", "100"), ("1150", "150")])
    payload = [["perpMonth", period(zero_perp)], ["month", period(total)]]

    result = metrics(payload)

    assert result.status == "ok"
    assert result.basis == "total"
    assert result.average_account_value_usd == Decimal("1075.00")


def test_short_or_unfunded_history_is_insufficient() -> None:
    short = [["perpMonth", period(daily([("1000", "0"), ("1010", "10")]))]]
    unfunded = [["perpMonth", period(daily([("1", "0"), ("2", "1"), ("3", "2"), ("4", "3")]))]]

    assert metrics(short).status == "insufficient"
    assert metrics(unfunded).status == "insufficient"


def test_week_history_replaces_the_month_tail_for_finer_drawdown() -> None:
    month_points = daily([("1000", "0"), ("1000", "0"), ("1000", "0"), ("1000", "0")])
    week_start = START_MS + 2 * DAY_MS
    week_points = [
        (week_start, "1000", "0"),
        (week_start + 6 * HOUR_MS, "800", "-200"),
        (week_start + 12 * HOUR_MS, "1000", "0"),
        (week_start + DAY_MS, "1000", "0"),
    ]
    payload = [["perpMonth", period(month_points)], ["perpWeek", period(week_points)]]

    month = portfolio_period_points(payload, "perpMonth")
    week = portfolio_period_points(payload, "perpWeek")
    returns = combined_interval_returns(month, week, min_account_value_usd=Decimal("10"))

    assert all(end <= week_start for _, end, _ in returns[:2])
    assert metrics(payload).max_drawdown_pct == Decimal("0.200000")


@pytest.mark.parametrize(
    ("status", "age_hours", "due"),
    [
        ("ok", 5, False),
        ("ok", 6, True),
        ("insufficient", 5, False),
        ("unavailable", 1, True),
        ("unavailable", 0.5, False),
    ],
)
def test_refresh_is_due_after_the_interval_and_sooner_after_failures(
    status: str,
    age_hours: float,
    due: bool,
) -> None:
    now = datetime(2026, 10, 10, tzinfo=UTC)
    existing = (WalletEquityMetrics(status=status), now - timedelta(hours=age_hours))  # type: ignore[arg-type]

    assert equity_refresh_due(existing, now=now, refresh_hours=6) is due
    assert equity_refresh_due(None, now=now, refresh_hours=6) is True
