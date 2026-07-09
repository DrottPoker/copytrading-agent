from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import TradingAccount, TradingOrder, TradingPosition
from app.services import live_copy_service
from app.services.live_copy_service import (
    combine_batch_results,
    live_aggregated_below_min_close_size,
    live_close_below_min_order_notional,
    live_close_size_for_part,
    live_copy_account_snapshot_is_stale,
    live_copy_allocation_equity_usd,
    live_exchange_position_conflict,
    live_min_order_notional_usd,
    live_order_exists,
    live_pending_close_size_from_orders,
    live_skip,
    live_source_position_is_final_close,
    live_stale_entry_skip_hidden_from_activity,
    record_live_skip,
    submit_live_copy_intent,
)
from app.services.live_trading_service import LiveOrderSubmitError
from app.services.paper_trading_service import (
    ExecutionMarketPrices,
    PaperCopyBatchResult,
    PaperSourceAccountState,
    PaperSourceAllocation,
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


def test_old_live_stale_entry_skip_is_hidden_from_activity() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    fill = {
        "timestampMs": int((now - timedelta(minutes=10)).timestamp() * 1000),
    }

    assert live_stale_entry_skip_hidden_from_activity(
        fill,
        settings=Settings(trading_copy_stale_entry_skip_activity_seconds=300),
        now=now,
    )


def test_recent_live_stale_entry_skip_stays_visible_for_diagnostics() -> None:
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    fill = {
        "timestampMs": int((now - timedelta(seconds=30)).timestamp() * 1000),
    }

    assert not live_stale_entry_skip_hidden_from_activity(
        fill,
        settings=Settings(trading_copy_stale_entry_skip_activity_seconds=300),
        now=now,
    )


@pytest.mark.asyncio
async def test_recovery_stale_live_entry_skip_is_hidden_from_activity(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_record_live_skip(*_args, **kwargs):
        captured.update(kwargs)
        return PaperCopyBatchResult(skipped_fills=1)

    monkeypatch.setattr(live_copy_service, "record_live_skip", fake_record_live_skip)

    result = await live_copy_service.apply_live_open_part(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=PaperSourceAllocation(
            source_wallet="0xsource",
            source_label="Source",
            rank=1,
            pool_rank=1,
            score=Decimal("90"),
            allocation_pct=Decimal("0.2"),
            active=True,
            has_realtime_slot=True,
            status_reason="trading",
        ),
        fill={
            "externalFillId": "fill-1",
            "coin": "HYPE",
            "price": "100",
            "timestampMs": int((datetime.now(UTC) - timedelta(seconds=20)).timestamp() * 1000),
        },
        part=SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("0.1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            close_ratio=None,
            start_position=Decimal("0"),
        ),
        source_perp_equity=Decimal("1000"),
        source_leverages={},
        market_prices=ExecutionMarketPrices(prices={}, sources={}),
        settings=Settings(
            trading_copy_max_entry_age_seconds=15,
            trading_copy_stale_entry_skip_activity_seconds=300,
        ),
        trading_client=object(),
        hide_stale_entry_skips=True,
    )

    assert result.skipped_fills == 1
    assert captured["reason"] == "live_source_fill_too_old"
    assert captured["hidden_from_activity"] is True


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


def test_live_copy_allocation_equity_uses_unified_equity_not_available() -> None:
    settings = Settings(live_trading_capital_mode="unified")
    account = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    account.equity_usd = Decimal("200")
    account.cash_balance_usd = Decimal("50")
    account.config_payload = {
        "lastReconciliation": {
            "unifiedAvailableUsd": "50",
            "unifiedEquityUsd": "200",
        }
    }

    assert (
        live_copy_allocation_equity_usd(account, settings=settings)
        == Decimal("200")
    )


def test_live_copy_allocation_equity_uses_standard_dex_equity() -> None:
    settings = Settings(live_trading_capital_mode="standard_per_dex")
    account = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    account.equity_usd = Decimal("500")
    account.config_payload = {
        "lastReconciliation": {
            "perpStates": [
                {"dex": "default", "accountValue": "120"},
                {"dex": "xyz", "accountValue": "80"},
            ],
        }
    }

    assert (
        live_copy_allocation_equity_usd(account, dex="xyz", settings=settings)
        == Decimal("80")
    )


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


def test_live_aggregated_below_min_close_size_caps_available_size() -> None:
    previous = live_order(
        status="failed",
        requested_size=Decimal("0.45"),
        filled_size=Decimal("0"),
    )

    assert (
        live_aggregated_below_min_close_size(
            close_size=Decimal("0.35"),
            previous_skip_orders=[previous],
            available_size=Decimal("0.70"),
        )
        == Decimal("0.70")
    )


@pytest.mark.asyncio
async def test_live_partial_close_aggregates_previous_below_min_skips(
    monkeypatch,
) -> None:
    class CaptureSession:
        async def flush(self):
            return None

    previous_skip = live_order(
        status="failed",
        requested_size=Decimal("0.30"),
        filled_size=Decimal("0"),
    )
    previous_skip.order_type = "skip"
    previous_skip.error = "skip:live_close_below_min_order_notional"
    previous_skip.raw_payload = {"skipReason": "live_close_below_min_order_notional"}
    submitted_intents = []

    async def fake_load_live_source_position(*_args, **_kwargs):
        return live_position(source_wallet="0xsource", side="long", size=Decimal("1"))

    async def fake_pending_close_size(*_args, **_kwargs):
        return Decimal("0")

    async def fake_load_previous_skips(*_args, **_kwargs):
        return [previous_skip]

    async def fake_submit_live_copy_intent(_session, *, account, intent, settings, trading_client):
        submitted_intents.append(intent)
        return PaperCopyBatchResult(processed_fills=1)

    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_position",
        fake_load_live_source_position,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_pending_close_size_for_position",
        fake_pending_close_size,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_below_min_close_skip_orders",
        fake_load_previous_skips,
    )
    monkeypatch.setattr(
        live_copy_service,
        "submit_live_copy_intent",
        fake_submit_live_copy_intent,
    )

    result = await live_copy_service.apply_live_close_part(
        CaptureSession(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=PaperSourceAllocation(
            source_wallet="0xsource",
            source_label="Source",
            rank=1,
            pool_rank=1,
            score=Decimal("90"),
            allocation_pct=Decimal("0.2"),
            active=True,
            has_realtime_slot=True,
            status_reason="trading",
        ),
        fill={
            "externalFillId": "fill-2",
            "coin": "HYPE",
            "price": "20",
            "timestampMs": 1_725_000_000_000,
        },
        part=SourceFillPart(
            action="close",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("20"),
            sequence_index=0,
            close_ratio=Decimal("0.25"),
            start_position=Decimal("4"),
        ),
        source_account_state=live_source_state(position_side="long"),
        source_perp_equity=Decimal("1000"),
        source_leverages={"HYPE": Decimal("10")},
        market_prices=ExecutionMarketPrices(
            prices={"HYPE": Decimal("20")},
            sources={"HYPE": "test"},
        ),
        settings=Settings(
            trading_copy_min_order_notional_usd=Decimal("10"),
            live_trading_min_order_notional_usd=Decimal("10"),
        ),
        trading_client=object(),
    )

    assert result.processed_fills == 1
    assert result.skipped_fills == 0
    assert len(submitted_intents) == 1
    intent = submitted_intents[0]
    assert intent.reduce_only is True
    assert intent.size == Decimal("0.55")
    assert intent.notional_usd >= Decimal("10")
    assert previous_skip.error == "skip:live_close_aggregated_into_later_order"
    assert previous_skip.raw_payload["hiddenFromActivity"] is True
    assert previous_skip.raw_payload["aggregatedInto"]["sourceFillId"] == "fill-2"


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


def test_live_pending_close_size_counts_uncertain_requested_size() -> None:
    orders = [
        live_order(
            status="uncertain",
            requested_size=Decimal("0.21"),
            filled_size=Decimal("0"),
        )
    ]

    assert live_pending_close_size_from_orders(orders) == Decimal("0.21")


def test_live_source_position_is_final_close_when_source_flipped_side() -> None:
    source_state = live_source_state(position_side="short")

    assert live_source_position_is_final_close(source_state, coin="HYPE", side="long")


@pytest.mark.asyncio
async def test_final_live_dust_close_submits_min_notional_order(monkeypatch) -> None:
    submitted_intents = []

    async def fake_load_live_source_position(*args, **kwargs):
        return live_position(
            source_wallet="0xsource",
            side="short",
            size=Decimal("303"),
        )

    async def fake_pending_close_size(*args, **kwargs):
        return Decimal("0")

    async def fake_submit_live_copy_intent(_session, *, account, intent, settings, trading_client):
        submitted_intents.append(intent)
        return PaperCopyBatchResult(processed_fills=1)

    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_position",
        fake_load_live_source_position,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_pending_close_size_for_position",
        fake_pending_close_size,
    )
    monkeypatch.setattr(
        live_copy_service,
        "submit_live_copy_intent",
        fake_submit_live_copy_intent,
    )

    result = await live_copy_service.apply_live_close_part(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=PaperSourceAllocation(
            source_wallet="0xsource",
            source_label="Source",
            rank=1,
            pool_rank=1,
            score=Decimal("90"),
            allocation_pct=Decimal("0.2"),
            active=True,
            has_realtime_slot=True,
            status_reason="trading",
        ),
        fill={
            "externalFillId": "bio-final-close",
            "coin": "BIO",
            "price": "0.031077",
            "timestampMs": 1_725_000_000_000,
        },
        part=SourceFillPart(
            action="close",
            side="short",
            source_size=Decimal("801"),
            source_notional_usd=Decimal("26.4"),
            sequence_index=0,
            close_ratio=Decimal("1"),
            start_position=Decimal("-801"),
        ),
        source_account_state=PaperSourceAccountState(
            dex="",
            perp_equity=Decimal("1000"),
            leverage_by_coin={"BIO": Decimal("3")},
            positions_by_coin={},
            skip_reason=None,
        ),
        source_perp_equity=Decimal("1000"),
        source_leverages={"BIO": Decimal("3")},
        market_prices=ExecutionMarketPrices(
            prices={"BIO": Decimal("0.031077")},
            sources={"BIO": "test"},
        ),
        settings=Settings(
            trading_copy_min_order_notional_usd=Decimal("10"),
            live_trading_min_order_notional_usd=Decimal("10"),
        ),
        trading_client=object(),
    )

    assert result.processed_fills == 1
    assert result.skipped_fills == 0
    assert len(submitted_intents) == 1
    intent = submitted_intents[0]
    assert intent.reduce_only is True
    assert intent.size == Decimal("303")
    assert intent.notional_usd == Decimal("10")


@pytest.mark.asyncio
async def test_orphan_exchange_close_submits_when_source_position_is_missing(
    monkeypatch,
) -> None:
    submitted_intents = []

    async def fake_load_live_source_position(*_args, **kwargs):
        if kwargs["source_wallet"] == "__exchange__":
            return live_position(
                source_wallet="__exchange__",
                side="short",
                size=Decimal("303"),
            )
        return None

    async def fake_source_has_open_fill_history(*_args, **_kwargs):
        return True

    async def fake_any_source_position_exists(*_args, **_kwargs):
        return False

    async def fake_submit_live_copy_intent(_session, *, account, intent, settings, trading_client):
        submitted_intents.append(intent)
        return PaperCopyBatchResult(processed_fills=1)

    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_position",
        fake_load_live_source_position,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_source_has_open_fill_history",
        fake_source_has_open_fill_history,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_any_source_position_exists_for_market",
        fake_any_source_position_exists,
    )
    monkeypatch.setattr(
        live_copy_service,
        "submit_live_copy_intent",
        fake_submit_live_copy_intent,
    )

    result = await live_copy_service.apply_live_close_part(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=PaperSourceAllocation(
            source_wallet="0xsource",
            source_label="Source",
            rank=1,
            pool_rank=1,
            score=Decimal("90"),
            allocation_pct=Decimal("0.2"),
            active=True,
            has_realtime_slot=True,
            status_reason="trading",
        ),
        fill={
            "externalFillId": "bio-final-close",
            "coin": "BIO",
            "price": "0.031077",
            "timestampMs": 1_725_000_000_000,
        },
        part=SourceFillPart(
            action="close",
            side="short",
            source_size=Decimal("801"),
            source_notional_usd=Decimal("26.4"),
            sequence_index=0,
            close_ratio=Decimal("1"),
            start_position=Decimal("-801"),
        ),
        source_account_state=PaperSourceAccountState(
            dex="",
            perp_equity=Decimal("1000"),
            leverage_by_coin={"BIO": Decimal("3")},
            positions_by_coin={},
            skip_reason=None,
        ),
        source_perp_equity=Decimal("1000"),
        source_leverages={"BIO": Decimal("3")},
        market_prices=ExecutionMarketPrices(
            prices={"BIO": Decimal("0.031077")},
            sources={"BIO": "test"},
        ),
        settings=Settings(
            trading_copy_min_order_notional_usd=Decimal("10"),
            live_trading_min_order_notional_usd=Decimal("10"),
        ),
        trading_client=object(),
    )

    assert result.processed_fills == 1
    assert result.skipped_fills == 0
    assert len(submitted_intents) == 1
    intent = submitted_intents[0]
    assert intent.source_wallet == "0xsource"
    assert intent.reduce_only is True
    assert intent.size == Decimal("303")
    assert intent.notional_usd == Decimal("10")
    assert intent.price_source == "orphan_exchange_close"


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


@pytest.mark.asyncio
async def test_live_order_exists_ignores_retryable_market_metadata_failure() -> None:
    class ExistingOrderSession:
        async def scalar(self, statement):
            return retryable_market_metadata_order()

    exists = await live_order_exists(
        ExistingOrderSession(),
        account_key="live_test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
    )

    assert exists is False


@pytest.mark.asyncio
async def test_live_order_exists_ignores_retryable_below_min_close_skip() -> None:
    class ExistingOrderSession:
        async def scalar(self, statement):
            return retryable_below_min_close_skip_order()

    exists = await live_order_exists(
        ExistingOrderSession(),
        account_key="live_test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
    )

    assert exists is False


@pytest.mark.asyncio
async def test_live_order_exists_blocks_non_retryable_failed_order() -> None:
    class ExistingOrderSession:
        async def scalar(self, statement):
            order = retryable_market_metadata_order()
            order.raw_payload["submitError"]["message"] = "Insufficient margin."
            order.error = "Insufficient margin."
            return order

    exists = await live_order_exists(
        ExistingOrderSession(),
        account_key="live_test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
    )

    assert exists is True


@pytest.mark.asyncio
async def test_record_live_skip_persists_diagnostic_order() -> None:
    class CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement

    session = CaptureSession()

    result = await record_live_skip(
        session,
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=PaperSourceAllocation(
            source_wallet="0xsource",
            source_label="Source",
            rank=1,
            pool_rank=1,
            score=Decimal("90"),
            allocation_pct=Decimal("0.2"),
            active=True,
            has_realtime_slot=True,
            status_reason="trading",
        ),
        fill={
            "externalFillId": "fill-1",
            "coin": "HYPE",
            "price": "100",
            "time": 1_725_000_000_000,
        },
        part=SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("0.1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            close_ratio=None,
            start_position=Decimal("0"),
        ),
        reason="live_price_drift_too_high",
        leverage=Decimal("10"),
        hidden_from_activity=True,
        source_fill_age_seconds=600.1234,
    )

    assert result.skipped_fills == 1
    assert result.skip_reasons == {"live_price_drift_too_high": 1}
    assert session.statement is not None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    params = compiled.params
    assert params["order_type"] == "skip"
    assert params["status"] == "failed"
    assert params["source_fill_id"] == "fill-1"
    assert params["error"] == "skip:live_price_drift_too_high"
    assert params["raw_payload"]["hiddenFromActivity"] is True
    assert params["raw_payload"]["sourceFillAgeSeconds"] == 600.123
    assert "submitted_at" not in params
    assert params["filled_size"] == Decimal("0")


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


def retryable_market_metadata_order() -> TradingOrder:
    order = live_order(
        status="failed",
        requested_size=Decimal("0.01"),
        filled_size=Decimal("0"),
    )
    order.error = "Live order market is not available for exchange submission: xyz:MU."
    order.raw_payload = {
        "submitError": {
            "type": "HyperliquidLiveOrderRejectedError",
            "message": order.error,
        }
    }
    return order


def retryable_below_min_close_skip_order() -> TradingOrder:
    order = live_order(
        status="failed",
        requested_size=Decimal("303"),
        filled_size=Decimal("0"),
    )
    order.coin = "BIO"
    order.side = "short"
    order.is_buy = True
    order.order_type = "skip"
    order.error = "skip:live_close_below_min_order_notional"
    order.raw_payload = {"skipReason": "live_close_below_min_order_notional"}
    return order


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
