"""Target-position entry sizing shared by paper and live copy trading."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.config import Settings
from app.services.trading_core import (
    EntrySizingLimits,
    copy_allocation_pct,
    plan_entry_sizing,
    source_position_after_fill,
)

LIMITS = EntrySizingLimits(
    min_order_notional_usd=Decimal("10"),
    min_order_round_up_ratio=Decimal("0.5"),
    max_source_exposure_multiple=Decimal("3"),
    max_total_exposure_multiple=Decimal("3"),
)


def size(
    *,
    opening: bool = False,
    allocation_usd: str = "200",
    account_equity_usd: str = "1000",
    source_equity_usd: str = "1000000",
    source_fill_size: str = "1",
    source_position_after: str = "1",
    current_position_size: str = "0",
    price: str = "100",
    source_exposure_usd: str = "0",
    account_exposure_usd: str = "0",
    limits: EntrySizingLimits = LIMITS,
):
    return plan_entry_sizing(
        opening=opening,
        allocation_usd=Decimal(allocation_usd),
        account_equity_usd=Decimal(account_equity_usd),
        source_equity_usd=Decimal(source_equity_usd),
        source_fill_size=Decimal(source_fill_size),
        source_position_after=Decimal(source_position_after),
        current_position_size=Decimal(current_position_size),
        price=Decimal(price),
        source_exposure_usd=Decimal(source_exposure_usd),
        account_exposure_usd=Decimal(account_exposure_usd),
        limits=limits,
    )


# Copy ratio in these examples: 200 / 1,000,000 = 0.0002. At price 100 one
# source coin therefore maps to $0.02 of target notional.


def test_opening_below_the_minimum_is_rounded_up_to_one_minimum_order() -> None:
    decision = size(opening=True, source_fill_size="10", source_position_after="10")

    assert decision.skip_reason is None
    assert decision.notional_usd == Decimal("10")
    assert decision.rounded_up
    assert decision.desired_notional_usd == Decimal("0.2")


def test_opening_above_the_minimum_copies_the_proportional_size() -> None:
    decision = size(opening=True, source_fill_size="2500", source_position_after="2500")

    assert decision.notional_usd == Decimal("50")
    assert not decision.rounded_up


def test_opening_is_trimmed_to_the_remaining_exposure_room() -> None:
    decision = size(
        opening=True,
        source_fill_size="5000",
        source_position_after="5000",
        source_exposure_usd="550",
    )

    assert decision.notional_usd == Decimal("50")
    assert decision.capped


def test_opening_is_skipped_when_the_room_cannot_fit_a_minimum_order() -> None:
    decision = size(opening=True, source_exposure_usd="595")

    assert decision.skip_reason == "source_exposure_cap_reached"
    assert decision.notional_usd == Decimal("0")


def test_add_copies_the_gap_to_the_target_position() -> None:
    decision = size(
        source_fill_size="1000",
        source_position_after="2000",
        current_position_size="0.1",
    )

    assert decision.target_notional_usd == Decimal("40")
    assert decision.notional_usd == Decimal("30")


def test_add_rounds_up_when_the_gap_is_at_least_half_a_minimum_order() -> None:
    decision = size(
        source_fill_size="500",
        source_position_after="750",
        current_position_size="0.1",
    )

    assert decision.notional_usd == Decimal("10")
    assert decision.rounded_up
    assert decision.desired_notional_usd == Decimal("5")


def test_add_is_skipped_when_the_gap_is_below_half_a_minimum_order() -> None:
    decision = size(
        source_fill_size="200",
        source_position_after="700",
        current_position_size="0.1",
    )

    assert decision.skip_reason == "below_min_order_notional"


def test_add_is_skipped_once_the_target_position_is_reached() -> None:
    decision = size(source_fill_size="10", source_position_after="400", current_position_size="0.1")

    assert decision.skip_reason == "target_position_reached"


def test_add_never_chases_more_than_its_own_fill_plus_one_minimum_order() -> None:
    decision = size(
        source_fill_size="1000",
        source_position_after="100000",
        current_position_size="0",
    )

    assert decision.target_notional_usd == Decimal("2000")
    assert decision.notional_usd == Decimal("30")


def test_total_exposure_cap_reason_when_the_account_is_the_binding_limit() -> None:
    decision = size(
        source_fill_size="5000",
        source_position_after="5000",
        current_position_size="0.1",
        account_exposure_usd="2998",
    )

    assert decision.skip_reason == "total_exposure_cap_reached"


@pytest.mark.parametrize(
    "overrides",
    [{"allocation_usd": "0"}, {"source_equity_usd": "0"}, {"price": "0"}],
)
def test_sizing_requires_positive_inputs(overrides: dict[str, str]) -> None:
    assert size(opening=True, **overrides).skip_reason == "sizing_inputs_unavailable"


@pytest.mark.parametrize(
    ("start_position", "fill_size", "expected"),
    [
        (None, "2", "2"),
        ("0", "2", "2"),
        ("3", "2", "5"),
        ("-3", "2", "5"),
    ],
)
def test_source_position_after_fill_uses_the_absolute_start_position(
    start_position: str | None,
    fill_size: str,
    expected: str,
) -> None:
    assert source_position_after_fill(
        start_position=Decimal(start_position) if start_position is not None else None,
        fill_size=Decimal(fill_size),
    ) == Decimal(expected)


def test_copy_allocation_is_an_equal_share_of_the_budget() -> None:
    settings = Settings(
        _env_file=None,
        trading_copy_top_wallet_count=5,
        trading_copy_max_total_allocation_pct=Decimal("1"),
    )

    assert copy_allocation_pct(settings) == Decimal("0.2")


def test_whale_burst_stays_within_one_minimum_order_of_the_target() -> None:
    """Replay the incident: a large wallet scales in with many tiny fills while price drops."""

    allocation = Decimal("200")
    source_equity = Decimal("2000000")
    fills = 200
    fill_coins = Decimal("50")
    position = Decimal("0")
    our_coins = Decimal("0")
    our_cost = Decimal("0")
    source_cost = Decimal("0")
    orders = 0
    for index in range(fills):
        price = Decimal("100") - Decimal("20") * Decimal(index) / Decimal(fills - 1)
        decision = plan_entry_sizing(
            opening=our_coins == 0,
            allocation_usd=allocation,
            account_equity_usd=Decimal("1000"),
            source_equity_usd=source_equity,
            source_fill_size=fill_coins,
            source_position_after=position + fill_coins,
            current_position_size=our_coins,
            price=price,
            source_exposure_usd=our_coins * price,
            account_exposure_usd=our_coins * price,
            limits=LIMITS,
        )
        position += fill_coins
        source_cost += fill_coins * price
        if decision.skip_reason is None:
            orders += 1
            our_coins += decision.notional_usd / price
            our_cost += decision.notional_usd
        target = allocation / source_equity * position * price
        assert our_coins * price <= target + LIMITS.min_order_notional_usd

    final_price = Decimal("80")
    final_target = allocation / source_equity * position * final_price
    assert abs(our_coins * final_price - final_target) <= LIMITS.min_order_notional_usd
    # The previous fill-by-fill rounding placed one $10 order per fill.
    assert orders < 20
    our_average = our_cost / our_coins
    source_average = source_cost / position
    assert abs(our_average - source_average) / source_average < Decimal("0.03")


positive = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=4)
coins = st.decimals(min_value=Decimal("0"), max_value=Decimal("100000"), places=4)
exposure = st.decimals(min_value=Decimal("0"), max_value=Decimal("10000"), places=2)


@given(
    opening=st.booleans(),
    allocation=st.decimals(min_value=Decimal("1"), max_value=Decimal("5000"), places=2),
    equity=st.decimals(min_value=Decimal("1"), max_value=Decimal("10000"), places=2),
    source_equity=positive,
    fill_size=coins,
    extra_position=coins,
    current=st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=4),
    price=positive,
    source_exposure=exposure,
    account_exposure=exposure,
)
def test_sizing_invariants(
    opening: bool,
    allocation: Decimal,
    equity: Decimal,
    source_equity: Decimal,
    fill_size: Decimal,
    extra_position: Decimal,
    current: Decimal,
    price: Decimal,
    source_exposure: Decimal,
    account_exposure: Decimal,
) -> None:
    decision = plan_entry_sizing(
        opening=opening,
        allocation_usd=allocation,
        account_equity_usd=equity,
        source_equity_usd=source_equity,
        source_fill_size=fill_size,
        source_position_after=fill_size + extra_position,
        current_position_size=current,
        price=price,
        source_exposure_usd=source_exposure,
        account_exposure_usd=account_exposure,
        limits=LIMITS,
    )
    room = min(
        LIMITS.max_source_exposure_multiple * allocation - source_exposure,
        LIMITS.max_total_exposure_multiple * equity - account_exposure,
    )
    epsilon = Decimal("0.000001")

    assert (decision.skip_reason is None) == (decision.notional_usd > 0)
    if decision.skip_reason is not None:
        return
    assert decision.notional_usd >= LIMITS.min_order_notional_usd
    assert decision.notional_usd <= room + epsilon
    if opening:
        assert (
            decision.notional_usd
            <= max(
                decision.target_notional_usd - current * price,
                LIMITS.min_order_notional_usd,
            )
            + epsilon
        )
    else:
        assert decision.notional_usd <= (
            decision.fill_target_notional_usd + LIMITS.min_order_notional_usd + epsilon
        )
        overshoot = (1 - LIMITS.min_order_round_up_ratio) * LIMITS.min_order_notional_usd
        assert current * price + decision.notional_usd <= (
            decision.target_notional_usd + overshoot + epsilon
        )
