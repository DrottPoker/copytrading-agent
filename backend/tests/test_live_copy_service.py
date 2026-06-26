from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount, TradingOrder, TradingPosition
from app.services import live_copy_service
from app.services.live_copy_service import (
    combine_batch_results,
    live_close_below_min_order_notional,
    live_close_size_for_part,
    live_copy_account_snapshot_is_stale,
    live_exchange_position_conflict,
    live_min_order_notional_usd,
    live_pending_close_size_from_orders,
    live_skip,
    live_source_position_is_final_close,
    submit_live_copy_intent,
)
from app.services.live_trading_service import LiveOrderSubmitError
from app.services.paper_trading_service import (
    PaperSourceAccountState,
    PaperSourceCurrentPosition,
    SourceFillPart,
)
from app.services.trading_core import build_copy_trade_intent


def test_live_copy_account_snapshot_without_reconcile_is_stale() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(last_reconciled_at=None)

    assert live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_live_copy_account_snapshot_older_than_interval_is_stale() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(
        last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC) - timedelta(seconds=31)
    )

    assert live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_live_copy_account_snapshot_inside_interval_is_fresh() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(
        last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC) - timedelta(seconds=10)
    )

    assert not live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_live_skip_records_reason_count() -> None:
    result = live_skip("live_account_no_tradable_equity", 3)

    assert result.skipped_fills == 3
    assert result.skip_reasons == {"live_account_no_tradable_equity": 3}


def test_live_copy_combine_batch_results_merges_skip_reasons() -> None:
    combined = combine_batch_results(
        live_skip("live_order_submit_error", 1),
        live_skip("live_order_submit_error", 2),
    )

    assert combined.skipped_fills == 3
    assert combined.skip_reasons == {"live_order_submit_error": 3}


def test_live_exchange_position_conflict_allows_matching_source_position() -> None:
    conflict = live_exchange_position_conflict(
        source_position=live_position(source_wallet="0xsource", side="long"),
        exchange_position=live_position(source_wallet="exchange", side="long"),
        side="long",
    )

    assert conflict is None


def test_live_exchange_position_conflict_blocks_unattributed_exchange_position() -> None:
    conflict = live_exchange_position_conflict(
        source_position=None,
        exchange_position=live_position(source_wallet="exchange", side="long"),
        side="long",
    )

    assert conflict == "live_exchange_position_conflict"


def test_live_exchange_position_conflict_blocks_opposite_exchange_side() -> None:
    conflict = live_exchange_position_conflict(
        source_position=live_position(source_wallet="0xsource", side="long"),
        exchange_position=live_position(source_wallet="exchange", side="short"),
        side="long",
    )

    assert conflict == "live_exchange_position_side_conflict"


def test_live_min_order_notional_uses_stricter_copy_or_exchange_minimum() -> None:
    settings = Settings(
        trading_copy_min_order_notional_usd=Decimal("12"),
        live_trading_min_order_notional_usd=Decimal("10"),
    )

    assert live_min_order_notional_usd(settings) == Decimal("12")


def test_live_close_below_min_order_notional_blocks_sub_min_closes() -> None:
    settings = Settings(
        trading_copy_min_order_notional_usd=Decimal("10"),
        live_trading_min_order_notional_usd=Decimal("10"),
    )

    assert live_close_below_min_order_notional(Decimal("9.99"), settings=settings)
    assert not live_close_below_min_order_notional(Decimal("10"), settings=settings)


def test_live_close_size_uses_ratio_while_source_position_remains_open() -> None:
    position = live_position(source_wallet="0xsource", side="long", size=Decimal("2"))
    part = live_close_part(close_ratio=Decimal("0.25"))
    source_state = live_source_state(position_side="long")

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_state,
        coin="HYPE",
    )

    assert close_size == Decimal("0.50")


def test_live_close_size_closes_remaining_position_when_source_is_flat() -> None:
    position = live_position(source_wallet="0xsource", side="long", size=Decimal("2"))
    part = live_close_part(close_ratio=Decimal("0.05"))
    source_state = live_source_state(position_side=None)

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_state,
        coin="HYPE",
    )

    assert close_size == Decimal("2")


def test_live_close_size_closes_remaining_position_without_ratio_when_source_is_flat() -> None:
    position = live_position(source_wallet="0xsource", side="long", size=Decimal("2"))
    part = live_close_part(close_ratio=None)
    source_state = live_source_state(position_side=None)

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_state,
        coin="HYPE",
    )

    assert close_size == Decimal("2")


def test_live_close_size_uses_unreconciled_available_size() -> None:
    position = live_position(source_wallet="0xsource", side="long", size=Decimal("2"))
    part = live_close_part(close_ratio=Decimal("0.50"))
    source_state = live_source_state(position_side="long")

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_state,
        coin="HYPE",
        available_size=Decimal("1"),
    )

    assert close_size == Decimal("0.50")


def test_live_final_close_uses_unreconciled_available_size() -> None:
    position = live_position(source_wallet="0xsource", side="long", size=Decimal("2"))
    part = live_close_part(close_ratio=Decimal("0.05"))
    source_state = live_source_state(position_side=None)

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_state,
        coin="HYPE",
        available_size=Decimal("0.25"),
    )

    assert close_size == Decimal("0.25")


def test_live_pending_close_size_counts_filled_reduce_order() -> None:
    orders = [
        live_order(
            status="filled",
            requested_size=Decimal("0.21"),
            filled_size=Decimal("0.21"),
        )
    ]

    assert live_pending_close_size_from_orders(orders) == Decimal("0.21")


def test_live_pending_close_size_ignores_rejected_reduce_order() -> None:
    orders = [
        live_order(
            status="rejected",
            requested_size=Decimal("0.21"),
            filled_size=Decimal("0"),
        )
    ]

    assert live_pending_close_size_from_orders(orders) == Decimal("0")


def test_live_pending_close_size_counts_active_requested_size() -> None:
    orders = [
        live_order(
            status="accepted",
            requested_size=Decimal("0.21"),
            filled_size=Decimal("0"),
        )
    ]

    assert live_pending_close_size_from_orders(orders) == Decimal("0.21")


def test_live_source_position_is_final_close_when_source_flipped_side() -> None:
    source_state = live_source_state(position_side="short")

    assert live_source_position_is_final_close(source_state, coin="HYPE", side="long")


@pytest.mark.asyncio
async def test_submit_live_copy_intent_reports_submit_error(monkeypatch) -> None:
    async def fake_submit_live_trade_intent(*args, **kwargs):
        raise LiveOrderSubmitError("Rejected by exchange.")

    monkeypatch.setattr(
        live_copy_service,
        "submit_live_trade_intent",
        fake_submit_live_trade_intent,
    )

    result = await submit_live_copy_intent(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        intent=build_copy_trade_intent(
            account_key="live_test",
            account_type="live",
            source_wallet="0xsource",
            source_fill_id="fill-1",
            sequence_index=0,
            coin="HYPE",
            action="open",
            side="long",
            size=Decimal("0.1"),
            notional_usd=Decimal("10"),
            margin_usd=Decimal("1"),
            leverage=Decimal("10"),
            limit_price=Decimal("100"),
            source_price=Decimal("100"),
            observed_price=Decimal("100"),
            price_drift_bps=Decimal("0"),
            price_source="test",
            allocation_pct=Decimal("0.2"),
            allocation_usd=Decimal("40"),
            source_perp_equity_usd=Decimal("1000"),
            source_exposure_pct=Decimal("0.01"),
        ),
        settings=Settings(),
        trading_client=object(),
    )

    assert result.skipped_fills == 1
    assert result.skip_reasons == {"live_order_submit_error": 1}


def live_account(*, last_reconciled_at: datetime | None) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
        last_reconciled_at=last_reconciled_at,
    )


def live_position(
    *,
    source_wallet: str,
    side: str,
    size: Decimal = Decimal("0.1"),
) -> TradingPosition:
    return TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet=source_wallet,
        coin="HYPE",
        side=side,
        size=size,
        entry_price=Decimal("100"),
        notional_usd=Decimal("10"),
        leverage=Decimal("10"),
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def live_order(
    *,
    status: str,
    requested_size: Decimal,
    filled_size: Decimal,
) -> TradingOrder:
    return TradingOrder(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        client_order_id=f"client-{status}",
        coin="HYPE",
        action="close",
        side="long",
        is_buy=False,
        reduce_only=True,
        order_type="ioc",
        status=status,
        requested_size=requested_size,
        requested_notional_usd=Decimal("10"),
        margin_usd=Decimal("1"),
        leverage=Decimal("10"),
        limit_price=Decimal("100"),
        filled_size=filled_size,
        filled_notional_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def live_close_part(close_ratio: Decimal | None) -> SourceFillPart:
    return SourceFillPart(
        action="close",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=0,
        close_ratio=close_ratio,
        start_position=Decimal("10"),
    )


def live_source_state(position_side: str | None) -> PaperSourceAccountState:
    positions_by_coin = {}
    if position_side is not None:
        positions_by_coin["HYPE"] = PaperSourceCurrentPosition(
            coin="HYPE",
            side=position_side,
            size=Decimal("1"),
        )
    return PaperSourceAccountState(
        dex="",
        perp_equity=Decimal("1000"),
        leverage_by_coin={"HYPE": Decimal("10")},
        positions_by_coin=positions_by_coin,
        skip_reason=None,
    )
