from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.services.paper_performance_service import (
    PaperRoundTrip,
    downsample_equity,
    equity_max_drawdown,
    paper_round_trip_read,
    paper_trade_stats,
)

START = datetime(2026, 10, 1, 12, tzinfo=UTC)


def round_trip(index: int, *, realized: str, fee: str = "0", funding: str = "0") -> PaperRoundTrip:
    closed_at = START + timedelta(hours=index + 1)
    return PaperRoundTrip(
        end_fill_id=uuid4(),
        account_key="paper_test",
        source_wallet="0xsource",
        source_fill_id=f"fill-{index}",
        coin="BTC",
        side="long",
        close_type="close",
        opened_at=closed_at - timedelta(minutes=30),
        closed_at=closed_at,
        entry_size=Decimal("2"),
        entry_notional_usd=Decimal("200"),
        exit_size=Decimal("2"),
        exit_notional_usd=Decimal("210"),
        fee_usd=Decimal(fee),
        funding_usd=Decimal(funding),
        realized_pnl_usd=Decimal(realized),
        leverage=Decimal("5"),
        margin_usd=Decimal("40"),
        open_fill_count=2,
        close_fill_count=3,
        created_at=closed_at,
    )


def test_round_trip_net_pnl_counts_all_fees_and_funding() -> None:
    trade = round_trip(0, realized="10", fee="0.4", funding="-0.1")

    read = paper_round_trip_read(trade, source_label="Source", is_source_liquidation=False)

    assert trade.net_pnl_usd == Decimal("9.5")
    assert read["net_pnl_usd"] == Decimal("9.5")
    assert read["entry_price"] == Decimal("100")
    assert read["exit_price"] == Decimal("105")
    assert read["duration_ms"] == 30 * 60 * 1000
    assert read["open_fill_count"] == 2
    assert read["close_fill_count"] == 3


def test_trade_stats_use_close_order_for_streaks() -> None:
    # Newest first, as the round trip query returns them.
    trades = [
        round_trip(4, realized="-2"),
        round_trip(3, realized="-1"),
        round_trip(2, realized="6"),
        round_trip(1, realized="0"),
        round_trip(0, realized="4"),
    ]

    stats = paper_trade_stats(trades)

    assert stats.trade_count == 5
    assert stats.win_count == 2
    assert stats.loss_count == 2
    assert stats.gross_profit_usd == Decimal("10")
    assert stats.gross_loss_usd == Decimal("3")
    assert stats.net_pnl_usd == Decimal("7")
    assert stats.best_trade_usd == Decimal("6")
    assert stats.worst_trade_usd == Decimal("-2")
    assert stats.longest_win_streak == 2
    assert stats.longest_loss_streak == 2
    assert (stats.current_streak_count, stats.current_streak_kind) == (2, "loss")


def test_trade_stats_without_trades_are_empty() -> None:
    stats = paper_trade_stats([])

    assert stats.trade_count == 0
    assert stats.current_streak_kind is None
    assert stats.best_trade_usd == Decimal("0")


def test_equity_drawdown_is_the_largest_fall_from_a_running_peak() -> None:
    values = ["1000", "1100", "990", "1050", "1200", "1150", "1300"]
    points = [
        (START + timedelta(minutes=5 * index), Decimal(value)) for index, value in enumerate(values)
    ]

    drawdown = equity_max_drawdown(points)

    assert drawdown.max_drawdown_usd == Decimal("110")
    assert drawdown.max_drawdown_pct == Decimal("0.1")
    assert drawdown.peak_at == points[1][0]
    assert drawdown.trough_at == points[2][0]


def test_equity_drawdown_is_zero_for_a_rising_curve() -> None:
    points = [(START + timedelta(minutes=index), Decimal(1000 + index)) for index in range(5)]

    drawdown = equity_max_drawdown(points)

    assert drawdown.max_drawdown_usd == Decimal("0")
    assert drawdown.max_drawdown_pct is None


def test_downsample_keeps_ends_and_the_extremes_of_each_bucket() -> None:
    points = [(START + timedelta(minutes=5 * index), Decimal(1000)) for index in range(1000)]
    points[500] = (points[500][0], Decimal("700"))
    points[700] = (points[700][0], Decimal("1400"))

    sampled = downsample_equity(points, max_points=100)

    assert len(sampled) <= 100
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert points[500] in sampled
    assert points[700] in sampled
    assert [recorded_at for recorded_at, _ in sampled] == sorted(
        recorded_at for recorded_at, _ in sampled
    )


def test_downsample_returns_short_curves_unchanged() -> None:
    points = [(START, Decimal("1000")), (START + timedelta(minutes=5), Decimal("1001"))]

    assert downsample_equity(points, max_points=100) == points
