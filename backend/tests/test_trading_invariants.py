"""Property-based tests for pure trading helpers that every copy order passes through."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.db.models import TradingPosition
from app.integrations.hyperliquid_live_client import (
    FALLBACK_SIZE_DECIMALS,
    PRICE_SIGNIFICANT_DIGITS,
    HyperliquidLiveTradingConfigurationError,
    live_order_wire_price,
    live_order_wire_values,
)
from app.services.live_copy_service import live_close_size_for_part
from app.services.paper_trading_service import (
    SourceFillPart,
    adverse_price_drift_bps,
    apply_adverse_slippage,
    is_buy_execution,
    plan_source_fill,
)
from app.services.trading_core import (
    MARGIN_MODES,
    TRADE_ACTIONS,
    TRADE_SIDES,
    build_client_order_id,
    build_copy_trade_intent,
    parse_margin_mode,
    parse_trade_action,
    parse_trade_side,
    trade_is_buy,
)

prices = st.decimals(
    min_value=Decimal("0.000001"),
    max_value=Decimal("1000000"),
    places=8,
    allow_nan=False,
    allow_infinity=False,
)
sizes = st.decimals(
    min_value=Decimal("0.00000001"),
    max_value=Decimal("100000"),
    places=8,
    allow_nan=False,
    allow_infinity=False,
)
sides = st.sampled_from(sorted(TRADE_SIDES))
actions = st.sampled_from(sorted(TRADE_ACTIONS))
bps = st.decimals(min_value=Decimal("0"), max_value=Decimal("500"), places=2)


def significant_digits(value: Decimal) -> int:
    return len(value.normalize().as_tuple().digits)


def decimal_places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(-int(exponent), 0)


@given(price=prices, is_buy=st.booleans(), max_decimal_places=st.integers(0, 8))
def test_wire_price_never_crosses_the_requested_limit(
    price: Decimal,
    is_buy: bool,
    max_decimal_places: int,
) -> None:
    wire_price = live_order_wire_price(
        price,
        is_buy=is_buy,
        max_decimal_places=max_decimal_places,
    )

    if is_buy:
        assert Decimal("0") <= wire_price <= price
    else:
        assert wire_price >= price
    assert decimal_places(wire_price) <= max_decimal_places
    is_integer = wire_price == wire_price.to_integral_value()
    assert is_integer or significant_digits(wire_price) <= PRICE_SIGNIFICANT_DIGITS


def intent_for(
    *,
    size: Decimal,
    limit_price: Decimal,
    side: str,
    reduce_only: bool,
):
    return build_copy_trade_intent(
        account_key="live-test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="BTC",
        action="close" if reduce_only else "open",
        side=parse_trade_side(side),
        size=size,
        notional_usd=size * limit_price,
        margin_usd=size * limit_price,
        leverage=Decimal("1"),
        limit_price=limit_price,
        source_price=limit_price,
        observed_price=limit_price,
        price_drift_bps=Decimal("0"),
        price_source="test",
        allocation_pct=None,
        allocation_usd=None,
        source_perp_equity_usd=None,
        source_exposure_pct=None,
    )


@given(size=sizes, limit_price=prices, side=sides, reduce_only=st.booleans())
def test_wire_size_rounds_down_to_lot_precision_without_min_order_adjustment(
    size: Decimal,
    limit_price: Decimal,
    side: str,
    reduce_only: bool,
) -> None:
    intent = intent_for(size=size, limit_price=limit_price, side=side, reduce_only=reduce_only)
    try:
        values = live_order_wire_values(intent)
    except HyperliquidLiveTradingConfigurationError:
        return

    assert Decimal("0") < values.size <= size
    assert decimal_places(values.size) <= FALLBACK_SIZE_DECIMALS
    assert values.notional_usd == values.size * values.limit_price


@given(
    size=sizes,
    limit_price=prices,
    side=sides,
    min_order=st.decimals(min_value=Decimal("1"), max_value=Decimal("50"), places=2),
    buffer=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2),
)
def test_adjusted_entry_orders_meet_the_buffered_minimum(
    size: Decimal,
    limit_price: Decimal,
    side: str,
    min_order: Decimal,
    buffer: Decimal,
) -> None:
    intent = intent_for(size=size, limit_price=limit_price, side=side, reduce_only=False)
    try:
        values = live_order_wire_values(
            intent,
            min_order_notional_usd=min_order,
            min_order_notional_buffer_usd=buffer,
            adjust_to_min_order=True,
        )
    except HyperliquidLiveTradingConfigurationError:
        return

    assert values.notional_usd >= min_order + buffer


@given(price=prices, side=sides, action=actions, slippage_bps=bps)
def test_adverse_slippage_always_moves_the_price_against_the_copier(
    price: Decimal,
    side: str,
    action: str,
    slippage_bps: Decimal,
) -> None:
    adjusted = apply_adverse_slippage(
        price=price,
        side=side,
        action=action,
        slippage_bps=slippage_bps,
    )

    if is_buy_execution(side=side, action=action):
        assert adjusted >= price
    else:
        assert Decimal("0") <= adjusted <= price


@given(source_price=prices, observed_price=prices, side=sides, action=actions)
def test_adverse_drift_is_never_negative_and_zero_for_favorable_moves(
    source_price: Decimal,
    observed_price: Decimal,
    side: str,
    action: str,
) -> None:
    drift = adverse_price_drift_bps(
        source_price=source_price,
        observed_price=observed_price,
        side=side,
        action=action,
    )

    assert drift >= Decimal("0")
    buy = is_buy_execution(side=side, action=action)
    favorable = observed_price <= source_price if buy else observed_price >= source_price
    if favorable:
        assert drift == Decimal("0")


@given(side=sides, action=actions)
def test_order_direction_matches_between_paper_and_live_helpers(side: str, action: str) -> None:
    reduce_only = action in {"reduce", "close", "flip_close"}
    assert trade_is_buy(side=parse_trade_side(side), reduce_only=reduce_only) == (
        is_buy_execution(side=side, action=action)
    )


@given(
    position_size=sizes,
    close_ratio=st.one_of(
        st.none(),
        st.decimals(min_value=Decimal("0"), max_value=Decimal("2"), places=6),
    ),
    available_size=st.one_of(st.none(), sizes),
    side=sides,
)
def test_close_size_never_exceeds_the_owned_position(
    position_size: Decimal,
    close_ratio: Decimal | None,
    available_size: Decimal | None,
    side: str,
) -> None:
    position = TradingPosition(size=position_size, side=side)
    part = SourceFillPart(
        action="close",
        side=parse_trade_side(side),
        source_size=Decimal("1"),
        source_notional_usd=Decimal("1"),
        sequence_index=0,
        close_ratio=close_ratio,
    )

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=None,
        coin="BTC",
        available_size=available_size,
    )

    if close_ratio is None or close_ratio <= Decimal("0"):
        assert close_size is None
        return
    assert close_size is not None
    limit = position_size if available_size is None else min(position_size, available_size)
    assert Decimal("0") <= close_size <= limit


@given(
    source_size=sizes,
    price=prices,
    start_position=st.decimals(
        min_value=Decimal("-100000"),
        max_value=Decimal("100000"),
        places=8,
    ),
    direction=st.sampled_from(["Long > Short", "Short > Long"]),
)
def test_flip_parts_conserve_source_size(
    source_size: Decimal,
    price: Decimal,
    start_position: Decimal,
    direction: str,
) -> None:
    parts = plan_source_fill(
        {
            "size": str(source_size),
            "price": str(price),
            "rawJson": {"dir": direction, "startPosition": str(start_position)},
        }
    )

    assert parts
    assert sum((part.source_size for part in parts), Decimal("0")) == source_size
    close_parts = [part for part in parts if part.action == "flip_close"]
    open_parts = [part for part in parts if part.action == "flip_open"]
    if start_position == 0:
        # Nothing to close, so the whole fill opens the new side.
        assert not close_parts
        assert [part.source_size for part in open_parts] == [source_size]
        return
    assert len(close_parts) == 1 and len(open_parts) <= 1
    assert close_parts[0].source_size <= abs(start_position)
    assert close_parts[0].close_ratio is not None
    assert Decimal("0") < close_parts[0].close_ratio <= Decimal("1")
    if open_parts:
        assert open_parts[0].side != close_parts[0].side
        assert open_parts[0].sequence_index > close_parts[0].sequence_index


@given(
    account_key=st.text(min_size=1, max_size=20),
    source_wallet=st.text(min_size=1, max_size=42),
    source_fill_id=st.text(max_size=40),
    sequence_index=st.integers(0, 10),
    action=actions,
)
def test_client_order_id_is_stable_and_exchange_compatible(
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
    action: str,
) -> None:
    client_order_id = build_client_order_id(
        account_key=account_key,
        source_wallet=source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=sequence_index,
        action=action,
    )

    # Account and wallet casing must not change the idempotency key.
    assert client_order_id == build_client_order_id(
        account_key=account_key.lower(),
        source_wallet=source_wallet.lower(),
        source_fill_id=source_fill_id,
        sequence_index=sequence_index,
        action=action,
    )
    assert len(client_order_id) == 34
    assert client_order_id.startswith("0x")
    int(client_order_id[2:], 16)
    assert client_order_id != build_client_order_id(
        account_key=account_key,
        source_wallet=source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=sequence_index + 1,
        action=action,
    )


@pytest.mark.parametrize(
    ("parser", "valid_values"),
    [
        (parse_trade_action, TRADE_ACTIONS),
        (parse_trade_side, TRADE_SIDES),
        (parse_margin_mode, MARGIN_MODES),
    ],
)
def test_literal_parsers_accept_every_declared_value(parser, valid_values) -> None:
    for value in valid_values:
        assert parser(value) == value


@given(value=st.text(max_size=20))
def test_literal_parsers_reject_undeclared_values(value: str) -> None:
    for parser, valid_values in (
        (parse_trade_action, TRADE_ACTIONS),
        (parse_trade_side, TRADE_SIDES),
        (parse_margin_mode, MARGIN_MODES),
    ):
        if value in valid_values:
            continue
        with pytest.raises(ValueError):
            parser(value)
