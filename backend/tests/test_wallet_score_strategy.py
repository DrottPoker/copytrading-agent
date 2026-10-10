"""Equity-based return and drawdown, averaging down, copy size, and sample shrinkage."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from app.core.config import Settings
from app.services.source_trade_reconstruction_service import OpenSourceTrade
from app.services.wallet_score_service import (
    WalletScoreMetrics,
    calculate_penalty_items,
    calculate_profitability_score,
    calculate_risk_score,
    calculate_wallet_score,
    copy_size_metrics,
    metric_with_copy_scale,
    penalty_from_items,
    profitability_score_items,
    risk_score_items,
    sample_credibility,
    score_from_items,
    small_copy_score_cap,
)
from tests.test_wallet_score_risk import wallet_metrics

NOW = datetime.fromtimestamp(1_781_000_000, UTC)


def settings() -> Settings:
    return Settings(_env_file=None)


def healthy_metrics(**overrides: object) -> WalletScoreMetrics:
    return replace(wallet_metrics(current_drawdown_pct=Decimal("0")), **overrides)


def item_by_key(items: list, key: str):
    return next(item for item in items if item.key == key)


def test_equity_drawdown_replaces_realized_drawdown_when_available() -> None:
    config = settings()
    deep = healthy_metrics(equity_max_drawdown_pct=Decimal("0.40"))
    missing = healthy_metrics(
        equity_status="unavailable",
        equity_max_drawdown_pct=None,
        max_drawdown_usd=Decimal("1000"),
    )

    deep_items = risk_score_items(deep, settings=config)
    missing_items = risk_score_items(missing, settings=config)

    assert item_by_key(deep_items, "equity_drawdown").contribution == Decimal("35.00")
    assert item_by_key(deep_items, "realized_drawdown").effect == "reference"
    assert all(item.key != "equity_drawdown" for item in missing_items)
    assert item_by_key(missing_items, "realized_drawdown").effect == "subtract"


def test_adds_into_losing_positions_are_penalized() -> None:
    config = settings()
    metrics = healthy_metrics(underwater_add_ratio=Decimal("0.20"))

    penalty = item_by_key(risk_score_items(metrics, settings=config), "underwater_adds")

    assert penalty.contribution == Decimal("10.00")


def test_equity_return_drives_profitability() -> None:
    config = settings()
    strong = healthy_metrics(equity_return_pct=Decimal("0.10"))
    unknown = healthy_metrics(equity_status="missing", equity_return_pct=None)

    assert item_by_key(
        profitability_score_items(strong, settings=config), "equity_return"
    ).score == (Decimal("100.00"))
    assert item_by_key(
        profitability_score_items(unknown, settings=config), "equity_return"
    ).score == Decimal("0.00")
    assert calculate_profitability_score(strong, settings=config) > calculate_profitability_score(
        unknown, settings=config
    )


def test_component_scores_match_their_explanation_items() -> None:
    config = settings()
    metrics = healthy_metrics(underwater_add_ratio=Decimal("0.1"))

    assert calculate_profitability_score(metrics, settings=config) == score_from_items(
        profitability_score_items(metrics, settings=config)
    )
    assert calculate_risk_score(metrics=metrics, settings=config) == Decimal("100") - (
        penalty_from_items(risk_score_items(metrics, settings=config))
    )


def test_whale_trades_below_our_minimum_order_cap_the_score() -> None:
    """The incident: a huge wallet whose trades map to cents at our allocation."""

    config = settings()
    whale = healthy_metrics(
        equity_average_account_value_usd=Decimal("10000000"),
        copy_scale=Decimal("200") / Decimal("10000000"),
        trade_notional_values=(Decimal("50000"),) * 50,
    )

    copy_size = copy_size_metrics(whale, settings=config)
    breakdown = calculate_wallet_score(whale, settings=config, now=NOW)

    assert copy_size.ratio == Decimal("0")
    assert copy_size.median_copy_notional_usd == Decimal("1.00")
    assert breakdown.small_copy_score_cap == Decimal("0.00")
    assert breakdown.score == Decimal("0.00")


def test_small_copy_cap_scales_below_the_minimum_ratio() -> None:
    config = settings()

    assert small_copy_score_cap(Decimal("0.5"), settings=config) is None
    assert small_copy_score_cap(Decimal("0.25"), settings=config) == Decimal("22.50")
    assert small_copy_score_cap(None, settings=config) is None


def test_copy_scale_uses_average_equity_then_current_account_value() -> None:
    config = settings()
    base = healthy_metrics(equity_average_account_value_usd=Decimal("2000"))
    no_equity_history = healthy_metrics(
        equity_average_account_value_usd=None,
        current_account_value_usd=Decimal("4000"),
    )

    scaled = metric_with_copy_scale(base, reference_equity_usd=Decimal("1000"), settings=config)
    fallback = metric_with_copy_scale(
        no_equity_history,
        reference_equity_usd=Decimal("1000"),
        settings=config,
    )

    # 1000 USD of capital split over five sources is 200 USD per source.
    assert scaled.copy_allocation_usd == Decimal("200.0")
    assert scaled.copy_scale == Decimal("0.1")
    assert fallback.copy_scale == Decimal("0.05")


def test_small_samples_are_pulled_toward_the_neutral_score() -> None:
    config = settings()
    established = healthy_metrics(trade_count=40)
    thin = healthy_metrics(trade_count=10)

    established_score = calculate_wallet_score(established, settings=config, now=NOW)
    thin_score = calculate_wallet_score(thin, settings=config, now=NOW)

    assert sample_credibility(10, settings=config) == Decimal("0.25")
    assert established_score.sample_credibility == Decimal("1")
    neutral = config.scoring_shrinkage_neutral_score
    assert abs(thin_score.risk_score - neutral) < abs(established_score.risk_score - neutral)


def test_confidence_penalty_is_replaced_by_shrinkage() -> None:
    config = settings()
    keys = {
        item.key
        for item in calculate_penalty_items(
            metrics=healthy_metrics(trade_count=25),
            min_trades=config.scoring_min_trades,
            recency_score=Decimal("100"),
            settings=config,
        )
    }

    assert "confidence" not in keys


def test_adds_below_the_average_entry_count_as_averaging_down() -> None:
    long_trade = OpenSourceTrade(wallet_address="0xw", coin="BTC", side="long", opened_at_ms=1)
    long_trade.add_entry(
        size=Decimal("1"), notional_usd=Decimal("100"), fee_usd=Decimal("0"), timestamp_ms=1
    )
    long_trade.add_entry(
        size=Decimal("1"), notional_usd=Decimal("90"), fee_usd=Decimal("0"), timestamp_ms=2
    )
    long_trade.add_entry(
        size=Decimal("1"), notional_usd=Decimal("120"), fee_usd=Decimal("0"), timestamp_ms=3
    )

    short_trade = OpenSourceTrade(wallet_address="0xw", coin="ETH", side="short", opened_at_ms=1)
    short_trade.add_entry(
        size=Decimal("1"), notional_usd=Decimal("100"), fee_usd=Decimal("0"), timestamp_ms=1
    )
    short_trade.add_entry(
        size=Decimal("1"), notional_usd=Decimal("110"), fee_usd=Decimal("0"), timestamp_ms=2
    )

    assert long_trade.underwater_add_notional_usd == Decimal("90")
    assert short_trade.underwater_add_notional_usd == Decimal("110")
