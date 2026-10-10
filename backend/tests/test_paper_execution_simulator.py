"""Order book fills, liquidation, and funding in the paper market simulation."""

from decimal import Decimal

from app.services.paper_execution_simulator import (
    SimulatedPosition,
    cross_liquidation_due,
    funding_payment,
    ioc_limit_price,
    isolated_liquidation_due,
    liquidation_realized_pnl,
    maintenance_margin_rate,
    parse_order_book,
    simulate_ioc_fill,
)

BOOK = parse_order_book(
    {
        "coin": "BTC",
        "levels": [
            [{"px": "99", "sz": "1", "n": 1}, {"px": "98", "sz": "2", "n": 1}],
            [{"px": "101", "sz": "1", "n": 1}, {"px": "102", "sz": "2", "n": 1}],
        ],
    }
)


def position(**overrides: object) -> SimulatedPosition:
    values: dict = {
        "key": "BTC",
        "side": "long",
        "size": Decimal("1"),
        "entry_price": Decimal("100"),
        "margin_usd": Decimal("10"),
        "margin_mode": "isolated",
        "funding_usd": Decimal("0"),
        "max_leverage": 40,
    }
    values.update(overrides)
    return SimulatedPosition(**values)


def test_order_book_levels_are_sorted_by_best_price() -> None:
    assert BOOK is not None
    assert [level.price for level in BOOK.bids] == [Decimal("99"), Decimal("98")]
    assert [level.price for level in BOOK.asks] == [Decimal("101"), Decimal("102")]
    assert parse_order_book({"levels": [[], []]}) is None
    assert parse_order_book("broken") is None


def test_ioc_buy_walks_the_asks_up_to_the_limit() -> None:
    assert BOOK is not None
    fill = simulate_ioc_fill(BOOK, is_buy=True, size=Decimal("2"), limit_price=Decimal("102"))

    assert fill.filled_size == Decimal("2")
    assert fill.average_price == Decimal("101.5")
    assert not fill.partial


def test_ioc_fills_partially_when_the_limit_cuts_off_depth() -> None:
    assert BOOK is not None
    fill = simulate_ioc_fill(BOOK, is_buy=True, size=Decimal("2"), limit_price=Decimal("101.5"))

    assert fill.filled_size == Decimal("1")
    assert fill.partial


def test_ioc_does_not_fill_beyond_the_limit() -> None:
    assert BOOK is not None
    fill = simulate_ioc_fill(BOOK, is_buy=False, size=Decimal("1"), limit_price=Decimal("99.5"))

    assert fill.unfilled
    assert fill.average_price is None


def test_ioc_limit_price_applies_slippage_against_the_trader() -> None:
    assert ioc_limit_price(Decimal("100"), is_buy=True, slippage_bps=Decimal("20")) == Decimal(
        "100.2"
    )
    assert ioc_limit_price(Decimal("100"), is_buy=False, slippage_bps=Decimal("20")) == Decimal(
        "99.8"
    )


def test_maintenance_margin_is_half_the_initial_margin_at_max_leverage() -> None:
    assert maintenance_margin_rate(40) == Decimal("0.0125")
    assert maintenance_margin_rate(None) == Decimal("0.025")


def test_isolated_position_liquidates_when_margin_no_longer_covers_maintenance() -> None:
    # 10x isolated long: 10 USD margin, maintenance 1.25% of notional.
    assert not isolated_liquidation_due(position(), mark_price=Decimal("95"))
    assert isolated_liquidation_due(position(), mark_price=Decimal("91"))


def test_isolated_losses_never_exceed_the_position_margin() -> None:
    assert liquidation_realized_pnl(position(), mark_price=Decimal("80")) == Decimal("-10")
    assert liquidation_realized_pnl(
        position(margin_mode="cross"),
        mark_price=Decimal("80"),
    ) == Decimal("-20")


def test_cross_account_liquidates_when_account_value_no_longer_covers_maintenance() -> None:
    cross = position(margin_mode="cross", size=Decimal("10"), margin_usd=Decimal("100"))
    prices = {"BTC": Decimal("90")}

    # 1000 USD of 10 BTC at 10x leverage: a 10% fall leaves 0 account value.
    assert cross_liquidation_due([cross], cash_balance_usd=Decimal("100"), mark_prices=prices)
    assert not cross_liquidation_due([cross], cash_balance_usd=Decimal("1000"), mark_prices=prices)
    assert not cross_liquidation_due([cross], cash_balance_usd=Decimal("100"), mark_prices={})


def test_isolated_margin_is_not_available_to_cross_positions() -> None:
    cross = position(key="BTC", margin_mode="cross", size=Decimal("10"))
    isolated = position(key="ETH", margin_usd=Decimal("150"))
    prices = {"BTC": Decimal("95"), "ETH": Decimal("100")}

    assert cross_liquidation_due(
        [cross, isolated],
        cash_balance_usd=Decimal("200"),
        mark_prices=prices,
    )


def test_longs_pay_positive_funding_and_shorts_receive_it() -> None:
    long_payment = funding_payment(
        side="long",
        size=Decimal("2"),
        price=Decimal("100"),
        rate=Decimal("0.0001"),
    )
    short_payment = funding_payment(
        side="short",
        size=Decimal("2"),
        price=Decimal("100"),
        rate=Decimal("0.0001"),
    )

    assert long_payment == Decimal("-0.0200")
    assert short_payment == Decimal("0.0200")
