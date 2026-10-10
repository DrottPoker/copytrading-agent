"""The shared entry and exit rules both copy engines apply."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services import copy_decision
from app.services.copy_decision import (
    CopyPosition,
    aggregate_below_min_close,
    decide_close,
    decide_entry,
    entry_precheck,
)
from app.services.copy_risk_rules import (
    AccountLossState,
    account_loss_breach,
    source_loss_limit_usd,
)
from app.services.copy_signal_service import (
    ExecutionMarketPrices,
    SourceAccountState,
    SourceCurrentPosition,
    SourceFillPart,
)
from app.services.live_copy_service import LIVE_DECISION_REASONS
from app.services.paper_trading_service import PAPER_DECISION_REASONS

SETTINGS = Settings(
    _env_file=None,
    trading_copy_max_price_drift_bps=Decimal("30"),
    trading_copy_max_entry_age_seconds=0,
)
PRICES = ExecutionMarketPrices(prices={"BTC": Decimal("100")}, sources={"BTC": "test"})


def fill(price: str = "100") -> dict:
    return {
        "externalFillId": "fill-1",
        "coin": "BTC",
        "price": price,
        "timestampMs": int(datetime.now(UTC).timestamp() * 1000),
    }


def entry_part(*, start_position: str | None = "0", size: str = "10") -> SourceFillPart:
    return SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal(size),
        source_notional_usd=Decimal(size) * Decimal("100"),
        sequence_index=0,
        start_position=Decimal(start_position) if start_position is not None else None,
    )


def close_part(close_ratio: str | None) -> SourceFillPart:
    return SourceFillPart(
        action="close",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=0,
        close_ratio=Decimal(close_ratio) if close_ratio is not None else None,
    )


def source_state(*, holds: bool) -> SourceAccountState:
    return SourceAccountState(
        dex="",
        perp_equity=Decimal("1000000"),
        leverage_by_coin={"BTC": Decimal("5")},
        positions_by_coin=(
            {"BTC": SourceCurrentPosition(coin="BTC", side="long", size=Decimal("5"))}
            if holds
            else {}
        ),
    )


def entry(**overrides: object):
    options: dict = {
        "fill": fill(),
        "part": entry_part(),
        "position": None,
        "allocation_active": True,
        "allocation_pct": Decimal("0.2"),
        "account_equity_usd": Decimal("1000"),
        "source_equity_usd": Decimal("10000"),
        "source_leverage": Decimal("5"),
        "market_prices": PRICES,
        "source_exposure_usd": Decimal("0"),
        "account_exposure_usd": Decimal("0"),
        "settings": SETTINGS,
        "min_order_notional_usd": Decimal("10"),
        "slippage_bps": Decimal("0"),
        "latency_ms": 0,
    }
    options.update(overrides)
    return decide_entry(**options)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"fill": fill(price="0")}, copy_decision.INVALID_SOURCE_PRICE),
        ({"part": entry_part(start_position="3")}, copy_decision.PREEXISTING_SOURCE_POSITION),
        (
            {"position": CopyPosition(side="short", size=Decimal("1"))},
            copy_decision.OPPOSITE_POSITION,
        ),
        ({"allocation_active": False}, copy_decision.ALLOCATION_INACTIVE),
        ({"source_equity_usd": Decimal("0")}, copy_decision.SOURCE_EQUITY_MISSING),
        (
            {"market_prices": ExecutionMarketPrices(prices={}, sources={})},
            copy_decision.EXECUTION_PRICE_UNAVAILABLE,
        ),
        (
            {"market_prices": ExecutionMarketPrices(prices={"BTC": Decimal("101")}, sources={})},
            copy_decision.PRICE_DRIFT_TOO_HIGH,
        ),
        (
            {
                "position": CopyPosition(side="long", size=Decimal("1")),
                "part": entry_part(start_position=None),
            },
            copy_decision.SOURCE_POSITION_UNKNOWN,
        ),
    ],
)
def test_entry_rules_report_the_first_failing_reason(overrides: dict, reason: str) -> None:
    assert entry(**overrides).reason == reason


def test_an_approved_entry_carries_the_sized_order() -> None:
    decision = entry(part=entry_part(size="100"))

    # Target: 200 USD allocation / 10,000 source equity * 100 coins * 100 USD.
    assert decision.approved
    assert decision.action == "open"
    assert decision.notional_usd == Decimal("200")
    assert decision.size == Decimal("2")
    assert decision.margin_usd == Decimal("40")


def test_an_add_is_named_add_and_sized_against_the_target() -> None:
    decision = entry(
        position=CopyPosition(side="long", size=Decimal("1")),
        part=entry_part(start_position="100", size="100"),
    )

    assert decision.approved
    assert decision.action == "add"


def test_precheck_needs_no_prices() -> None:
    assert (
        entry_precheck(
            part=entry_part(),
            position=None,
            allocation_active=True,
            source_price=Decimal("100"),
        )
        is None
    )


def close(**overrides: object):
    options: dict = {
        "fill": fill(),
        "part": close_part("0.5"),
        "position": CopyPosition(side="long", size=Decimal("1")),
        "available_size": Decimal("1"),
        "source_account_state": source_state(holds=True),
        "market_prices": PRICES,
        "settings": SETTINGS,
        "min_order_notional_usd": Decimal("10"),
        "slippage_bps": Decimal("0"),
        "latency_ms": 0,
    }
    options.update(overrides)
    return decide_close(**options)


def test_exits_follow_the_source_close_ratio() -> None:
    decision = close()

    assert decision.approved
    assert decision.close_size == Decimal("0.5")
    assert not decision.final_close


def test_a_final_source_close_closes_everything_without_a_ratio() -> None:
    decision = close(part=close_part(None), source_account_state=source_state(holds=False))

    assert decision.approved
    assert decision.final_close
    assert decision.close_size == Decimal("1")


def test_exits_are_never_blocked_by_price_drift() -> None:
    crashed = ExecutionMarketPrices(prices={"BTC": Decimal("50")}, sources={})

    assert close(market_prices=crashed).approved


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"position": None}, copy_decision.MATCHING_POSITION_MISSING),
        ({"available_size": Decimal("0")}, copy_decision.CLOSE_ALREADY_PENDING),
        ({"part": close_part(None)}, copy_decision.CLOSE_RATIO_MISSING),
    ],
)
def test_exit_rules_report_why_nothing_closes(overrides: dict, reason: str) -> None:
    assert close(**overrides).reason == reason


def test_small_partial_closes_are_combined_before_they_are_sent() -> None:
    small = close(part=close_part("0.05"))
    assert small.needs_aggregation
    assert not small.approved

    still_small = aggregate_below_min_close(
        small,
        previous_skipped_size=Decimal("0.02"),
        available_size=Decimal("1"),
        min_order_notional_usd=Decimal("10"),
    )
    combined = aggregate_below_min_close(
        small,
        previous_skipped_size=Decimal("0.06"),
        available_size=Decimal("1"),
        min_order_notional_usd=Decimal("10"),
    )

    assert still_small.reason == copy_decision.CLOSE_BELOW_MIN_ORDER
    assert combined.approved
    assert combined.aggregated
    assert combined.close_size == Decimal("0.11")


def test_both_engines_name_every_shared_reason() -> None:
    shared_entry = {
        copy_decision.INVALID_SOURCE_PRICE,
        copy_decision.OPPOSITE_POSITION,
        copy_decision.ALLOCATION_INACTIVE,
        copy_decision.MATCHING_POSITION_MISSING,
        copy_decision.CLOSE_RATIO_MISSING,
        copy_decision.CLOSE_SIZE_ZERO,
    }

    assert shared_entry <= set(PAPER_DECISION_REASONS)
    assert shared_entry <= set(LIVE_DECISION_REASONS)


def test_account_loss_breach_reports_the_first_limit_reached() -> None:
    settings = Settings(
        _env_file=None,
        live_trading_max_daily_loss_pct=Decimal("0.10"),
        live_trading_max_weekly_loss_pct=Decimal("0.20"),
        live_trading_max_drawdown_pct=Decimal("0.25"),
    )

    assert account_loss_breach(None, settings=settings) is None
    assert (
        account_loss_breach(
            AccountLossState(Decimal("0.05"), Decimal("0.05"), Decimal("0.05")),
            settings=settings,
        )
        is None
    )
    breach = account_loss_breach(
        AccountLossState(Decimal("0.05"), Decimal("0.21"), Decimal("0.30")),
        settings=settings,
    )
    assert breach is not None
    assert breach.rule == "max_weekly_loss"


def test_source_loss_limit_scales_with_equity_and_can_be_disabled() -> None:
    enabled = Settings(_env_file=None, live_trading_max_source_loss_pct=Decimal("0.05"))
    disabled = Settings(_env_file=None, live_trading_max_source_loss_pct=Decimal("0"))

    assert source_loss_limit_usd(equity_usd=Decimal("1000"), settings=enabled) == Decimal("50")
    assert source_loss_limit_usd(equity_usd=Decimal("1000"), settings=disabled) is None
