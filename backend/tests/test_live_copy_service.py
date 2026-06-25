from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount, TradingPosition
from app.services import live_copy_service
from app.services.live_copy_service import (
    combine_batch_results,
    live_copy_account_snapshot_is_stale,
    live_exchange_position_conflict,
    live_skip,
    submit_live_copy_intent,
)
from app.services.live_trading_service import LiveOrderSubmitError
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


def live_position(*, source_wallet: str, side: str) -> TradingPosition:
    return TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet=source_wallet,
        coin="HYPE",
        side=side,
        size=Decimal("0.1"),
        entry_price=Decimal("100"),
        notional_usd=Decimal("10"),
        leverage=Decimal("10"),
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
