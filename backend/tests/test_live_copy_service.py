from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import (
    LiveCopyFillState,
    LiveCopySourceState,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
)
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
    record_live_skip,
    submit_live_copy_intent,
)
from app.services.live_copy_state_service import LiveCopyPartClaim, LiveCopyProcessingDeferred
from app.services.live_trading_service import LiveOrderSubmitError, LiveReconciliationError
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


@pytest.mark.asyncio
async def test_recovery_sources_prioritize_unresolved_orders_before_positions() -> None:
    class Rows:
        def __init__(self, values: list[str]) -> None:
            self.values = values

        def all(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(source_wallet=value) for value in self.values]

    class Session:
        def __init__(self) -> None:
            self.results = iter(
                [
                    Rows(["0xorder", "0xshared"]),
                    Rows(["0xshared", "0xposition"]),
                    Rows(["0xallocation"]),
                ]
            )

        async def execute(self, _statement):
            return next(self.results)

    sources = await live_copy_service.load_live_copy_recovery_sources(
        Session(),  # type: ignore[arg-type]
        max_sources=4,
    )

    assert sources == ["0xorder", "0xshared", "0xposition", "0xallocation"]


@pytest.mark.asyncio
async def test_legacy_bootstrap_excludes_manual_test_source_candidates() -> None:
    class EmptyRows:
        def all(self) -> list[object]:
            return []

    class Session:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return EmptyRows()

    session = Session()
    await live_copy_service.bootstrap_missing_live_source_attribution(
        session,  # type: ignore[arg-type]
        accounts=[live_account(last_reconciled_at=datetime.now(UTC))],
    )

    assert session.statement is not None
    sql = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "__manual_testnet__" in sql


@pytest.mark.asyncio
async def test_busy_account_reconciliation_is_deferred_without_error_log(
    monkeypatch,
    caplog,
) -> None:
    class FlushOnlySession:
        async def flush(self) -> None:
            pass

    async def busy_reconciliation(*_args, **_kwargs):
        raise LiveReconciliationError(
            "Live execution or reconciliation is already running for this account.",
            status_code=409,
        )

    monkeypatch.setattr(
        live_copy_service,
        "reconcile_live_trading_account",
        busy_reconciliation,
    )
    caplog.set_level("INFO")
    account = live_account(last_reconciled_at=None)

    failed_accounts = await live_copy_service.refresh_stale_live_copy_accounts(
        FlushOnlySession(),
        accounts=[account],
        settings=Settings(live_trading_reconciliation_enabled=True),
        client=object(),
    )

    assert failed_accounts == {account.key}
    assert "reconciliation deferred because account execution is busy" in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_reconciliation_contention_defers_fill_without_trading_order(
    monkeypatch,
) -> None:
    account = live_account(last_reconciled_at=None)
    source_state = live_source_lifecycle_state()
    fill_timestamp_ms = int(datetime.now(UTC).timestamp() * 1000)
    fill_state = LiveCopyFillState(
        account_key=account.key,
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="CASHCAT",
        action="open",
        side="long",
        source_timestamp_ms=fill_timestamp_ms,
        origin="realtime",
        outcome="retryable",
        attempt_count=0,
        fill_complete=False,
    )
    retry_reasons: list[str] = []
    account_loads = 0

    class CommitSession:
        commit_count = 0

        async def commit(self):
            self.commit_count += 1

    async def fake_refresh_allocations(*_args, **_kwargs):
        return {"0xsource": source_allocation()}

    async def fake_load_accounts(*_args, **_kwargs):
        nonlocal account_loads
        account_loads += 1
        return [account]

    async def fake_source_states(**_kwargs):
        return {
            "": PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"CASHCAT": Decimal("3")},
                positions_by_coin={},
                margin_mode_by_coin={"CASHCAT": "cross"},
            )
        }

    async def fake_market_prices(**_kwargs):
        return ExecutionMarketPrices(prices={"CASHCAT": Decimal("0.04")}, sources={})

    async def no_failed_reconciliation(*_args, **_kwargs):
        return set()

    async def fake_failed_reconciliation(*_args, **_kwargs):
        return {account.key}

    async def fake_source_state(*_args, **_kwargs):
        return source_state

    async def fake_fill_plan(*_args, **_kwargs):
        return [fill_state]

    async def fake_claim(*_args, **_kwargs):
        return LiveCopyPartClaim(state=fill_state, claimed=True, reason="claimed")

    async def copyable_lifecycle(*_args, **_kwargs):
        return False

    async def fake_mark_retryable(*_args, **kwargs):
        retry_reasons.append(kwargs["reason"])
        return 5

    async def unexpected_order(*_args, **_kwargs):
        raise AssertionError("Reconciliation contention must not create a TradingOrder.")

    monkeypatch.setattr(
        live_copy_service,
        "refresh_paper_copy_allocations",
        fake_refresh_allocations,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_accounts_for_source_copy",
        fake_load_accounts,
    )
    monkeypatch.setattr(live_copy_service, "load_source_account_states", fake_source_states)
    monkeypatch.setattr(live_copy_service, "load_execution_market_prices", fake_market_prices)
    monkeypatch.setattr(
        live_copy_service,
        "refresh_stale_live_copy_accounts",
        fake_failed_reconciliation,
    )
    monkeypatch.setattr(
        live_copy_service,
        "ensure_live_copy_source_state",
        fake_source_state,
    )
    monkeypatch.setattr(
        live_copy_service,
        "ensure_live_copy_fill_plan_states",
        fake_fill_plan,
    )
    monkeypatch.setattr(live_copy_service, "claim_live_copy_fill_part", fake_claim)
    monkeypatch.setattr(
        live_copy_service,
        "live_copy_part_is_unowned_source_lifecycle",
        copyable_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_retryable",
        fake_mark_retryable,
    )
    monkeypatch.setattr(live_copy_service, "record_live_skip", unexpected_order)

    session = CommitSession()
    live_settings = Settings().model_copy(update={"live_trading_enabled": True})
    with pytest.raises(LiveCopyProcessingDeferred):
        await live_copy_service.process_live_copy_fills(
            session,
            source_wallet="0xsource",
            fills=[
                {
                    "externalFillId": "fill-1",
                    "coin": "CASHCAT",
                    "price": "0.04",
                    "size": "100",
                    "notionalUsd": "4",
                    "timestampMs": fill_timestamp_ms,
                    "rawJson": {"dir": "Open Long", "startPosition": "0"},
                }
            ],
            settings=live_settings,
            client=object(),
            trading_client=object(),
        )

    assert retry_reasons == ["live_reconciliation_deferred"]
    assert account_loads == 2
    assert session.commit_count == 5


@pytest.mark.asyncio
async def test_ambiguous_attribution_defers_fill_without_trading_order(monkeypatch) -> None:
    account = live_account(last_reconciled_at=datetime.now(UTC))
    source_state = live_source_lifecycle_state()
    fill_state = LiveCopyFillState(
        account_key=account.key,
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="CASHCAT",
        action="add",
        side="long",
        source_timestamp_ms=1_000,
        origin="realtime",
        outcome="retryable",
        attempt_count=0,
        fill_complete=False,
    )
    retry_reasons: list[str] = []

    class CommitSession:
        async def commit(self) -> None:
            pass

    async def fake_refresh_allocations(*_args, **_kwargs):
        return {"0xsource": source_allocation()}

    async def fake_load_accounts(*_args, **_kwargs):
        return [account]

    async def fake_source_states(**_kwargs):
        return {
            "": PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"CASHCAT": Decimal("3")},
                positions_by_coin={},
                margin_mode_by_coin={"CASHCAT": "cross"},
            )
        }

    async def fake_market_prices(**_kwargs):
        return ExecutionMarketPrices(prices={"CASHCAT": Decimal("0.04")}, sources={})

    async def no_failed_reconciliation(*_args, **_kwargs):
        return set()

    async def fake_source_state(*_args, **_kwargs):
        return source_state

    async def fake_fill_plan(*_args, **_kwargs):
        return [fill_state]

    async def fake_claim(*_args, **_kwargs):
        return LiveCopyPartClaim(state=fill_state, claimed=True, reason="claimed")

    async def ambiguous_lifecycle(*_args, **_kwargs):
        raise live_copy_service.LiveCopyPartDeferred("live_source_attribution_ambiguous")

    async def fake_mark_retryable(*_args, **kwargs):
        retry_reasons.append(kwargs["reason"])
        return 5

    async def unexpected_order(*_args, **_kwargs):
        raise AssertionError("Ambiguous ownership must not enter the order pipeline.")

    monkeypatch.setattr(
        live_copy_service,
        "refresh_paper_copy_allocations",
        fake_refresh_allocations,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_accounts_for_source_copy",
        fake_load_accounts,
    )
    monkeypatch.setattr(live_copy_service, "load_source_account_states", fake_source_states)
    monkeypatch.setattr(live_copy_service, "load_execution_market_prices", fake_market_prices)
    monkeypatch.setattr(
        live_copy_service,
        "refresh_stale_live_copy_accounts",
        no_failed_reconciliation,
    )
    monkeypatch.setattr(live_copy_service, "ensure_live_copy_source_state", fake_source_state)
    monkeypatch.setattr(live_copy_service, "ensure_live_copy_fill_plan_states", fake_fill_plan)
    monkeypatch.setattr(live_copy_service, "claim_live_copy_fill_part", fake_claim)
    monkeypatch.setattr(
        live_copy_service,
        "live_copy_part_is_unowned_source_lifecycle",
        ambiguous_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_retryable",
        fake_mark_retryable,
    )
    monkeypatch.setattr(live_copy_service, "apply_live_copy_part", unexpected_order)

    with pytest.raises(LiveCopyProcessingDeferred):
        await live_copy_service.process_live_copy_fills(
            CommitSession(),
            source_wallet="0xsource",
            fills=[
                {
                    "externalFillId": "fill-1",
                    "coin": "CASHCAT",
                    "price": "0.04",
                    "size": "100",
                    "notionalUsd": "4",
                    "timestampMs": int(datetime.now(UTC).timestamp() * 1000),
                    "rawJson": {"dir": "Open Long", "startPosition": "100"},
                }
            ],
            settings=Settings().model_copy(update={"live_trading_enabled": True}),
            client=object(),
            trading_client=object(),
        )

    assert retry_reasons == ["live_source_attribution_ambiguous"]


@pytest.mark.asyncio
async def test_unowned_close_becomes_lifecycle_state_without_trading_order(
    monkeypatch,
) -> None:
    account = live_account(last_reconciled_at=datetime.now(UTC))
    source_state = live_source_lifecycle_state()
    fill_state = LiveCopyFillState(
        account_key=account.key,
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="close-1",
        sequence_index=0,
        coin="CASHCAT",
        action="close",
        side="long",
        source_timestamp_ms=1_000,
        origin="realtime",
        outcome="retryable",
        attempt_count=0,
        fill_complete=False,
    )
    ignored_reasons: list[str] = []
    completed_fill_ids: list[str] = []

    class CommitSession:
        async def commit(self):
            pass

    async def fake_refresh_allocations(*_args, **_kwargs):
        return {"0xsource": source_allocation()}

    async def fake_load_accounts(*_args, **_kwargs):
        return [account]

    async def fake_source_states(**_kwargs):
        return {
            "": PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("0"),
                leverage_by_coin={},
                positions_by_coin={},
            )
        }

    async def fake_market_prices(**_kwargs):
        return ExecutionMarketPrices(prices={}, sources={})

    async def no_failed_reconciliation(*_args, **_kwargs):
        return set()

    async def fake_source_state(*_args, **_kwargs):
        return source_state

    async def fake_fill_plan(*_args, **_kwargs):
        return [fill_state]

    async def fake_claim(*_args, **_kwargs):
        return LiveCopyPartClaim(state=fill_state, claimed=True, reason="claimed")

    async def unowned_lifecycle(*_args, **_kwargs):
        return True

    async def fake_mark_ignored(*_args, **kwargs):
        ignored_reasons.append(kwargs["reason"])
        fill_state.outcome = "baseline_ignored"

    async def fake_complete(*_args, **kwargs):
        completed_fill_ids.append(kwargs["source_fill_id"])
        return True

    async def unexpected_execution(*_args, **_kwargs):
        raise AssertionError("Unowned source history must not enter the order pipeline.")

    monkeypatch.setattr(
        live_copy_service,
        "refresh_paper_copy_allocations",
        fake_refresh_allocations,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_accounts_for_source_copy",
        fake_load_accounts,
    )
    monkeypatch.setattr(live_copy_service, "load_source_account_states", fake_source_states)
    monkeypatch.setattr(live_copy_service, "load_execution_market_prices", fake_market_prices)
    monkeypatch.setattr(
        live_copy_service,
        "refresh_stale_live_copy_accounts",
        no_failed_reconciliation,
    )
    monkeypatch.setattr(
        live_copy_service,
        "ensure_live_copy_source_state",
        fake_source_state,
    )
    monkeypatch.setattr(
        live_copy_service,
        "ensure_live_copy_fill_plan_states",
        fake_fill_plan,
    )
    monkeypatch.setattr(live_copy_service, "claim_live_copy_fill_part", fake_claim)
    monkeypatch.setattr(
        live_copy_service,
        "live_copy_part_is_unowned_source_lifecycle",
        unowned_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_baseline_ignored",
        fake_mark_ignored,
    )
    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_complete_if_durable",
        fake_complete,
    )
    monkeypatch.setattr(live_copy_service, "apply_live_copy_part", unexpected_execution)
    monkeypatch.setattr(live_copy_service, "record_live_skip", unexpected_execution)

    result = await live_copy_service.process_live_copy_fills(
        CommitSession(),
        source_wallet="0xsource",
        fills=[
            {
                "externalFillId": "close-1",
                "coin": "CASHCAT",
                "price": "0.04",
                "size": "100",
                "notionalUsd": "4",
                "timestampMs": 1_000,
                "rawJson": {"dir": "Close Long", "startPosition": "200"},
            }
        ],
        settings=Settings().model_copy(update={"live_trading_enabled": True}),
        client=object(),
        trading_client=object(),
    )

    assert result == PaperCopyBatchResult()
    assert ignored_reasons == ["unowned_preexisting_lifecycle"]
    assert completed_fill_ids == ["close-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "pre_barrier_reason",
        "normal_reason",
        "stale_checks",
        "apply_terminal",
        "expected_completion_count",
    ),
    [
        ("claimed", "complete", (True, True), False, 2),
        ("not_due", "claimed", (True, True), False, 1),
        ("claimed", "claimed", (False, False), True, 1),
    ],
)
async def test_stale_live_entry_becomes_terminal_state_without_trading_order(
    monkeypatch: pytest.MonkeyPatch,
    pre_barrier_reason: str,
    normal_reason: str,
    stale_checks: tuple[bool, bool],
    apply_terminal: bool,
    expected_completion_count: int,
) -> None:
    account = live_account(last_reconciled_at=datetime.now(UTC))
    source_state = live_source_lifecycle_state()
    fill_state = LiveCopyFillState(
        account_key=account.key,
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="stale-open",
        sequence_index=0,
        coin="CASHCAT",
        action="open",
        side="long",
        source_timestamp_ms=1_000,
        origin="realtime",
        outcome="retryable",
        attempt_count=0,
        fill_complete=False,
    )
    terminal_reasons: list[str] = []
    completed_fill_ids: list[str] = []
    claims = iter((pre_barrier_reason, normal_reason))
    stale_results = iter(stale_checks)

    class CommitSession:
        async def commit(self) -> None:
            return None

    async def fake_refresh_allocations(*_args: object, **_kwargs: object):
        return {"0xsource": source_allocation()}

    async def fake_load_accounts(*_args: object, **_kwargs: object):
        return [account]

    async def fake_source_states(*_args: object, **_kwargs: object):
        return {
            "": PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"CASHCAT": Decimal("3")},
                positions_by_coin={},
                margin_mode_by_coin={"CASHCAT": "cross"},
            )
        }

    async def fake_market_prices(*_args: object, **_kwargs: object):
        return ExecutionMarketPrices(prices={"CASHCAT": Decimal("0.04")}, sources={})

    async def no_failed_reconciliation(*_args: object, **_kwargs: object):
        return set()

    async def fake_source_state(*_args: object, **_kwargs: object):
        return source_state

    async def fake_fill_plan(*_args: object, **_kwargs: object):
        return [fill_state]

    async def fake_claim(*_args: object, **_kwargs: object):
        reason = next(claims)
        return LiveCopyPartClaim(
            state=fill_state,
            claimed=reason == "claimed",
            reason=reason,
        )

    async def fake_mark_terminal(*_args: object, **kwargs: object) -> None:
        terminal_reasons.append(str(kwargs["reason"]))
        fill_state.outcome = "terminal_skip"
        fill_state.reason = str(kwargs["reason"])
        fill_state.trading_order_id = None

    async def copyable_lifecycle(*_args: object, **_kwargs: object) -> bool:
        return False

    async def fake_finalize(*_args: object, **_kwargs: object) -> bool:
        return True

    async def fake_complete(*_args: object, **kwargs: object) -> bool:
        completed_fill_ids.append(str(kwargs["source_fill_id"]))
        return True

    async def unexpected_order(*_args: object, **_kwargs: object):
        raise AssertionError("Stale entries must not create or mutate TradingOrder rows.")

    async def apply_part(*_args: object, **_kwargs: object):
        if apply_terminal:
            raise live_copy_service.LiveCopyPartTerminal("live_source_fill_too_old")
        return await unexpected_order()

    monkeypatch.setattr(
        live_copy_service,
        "refresh_paper_copy_allocations",
        fake_refresh_allocations,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_accounts_for_source_copy",
        fake_load_accounts,
    )
    monkeypatch.setattr(live_copy_service, "load_source_account_states", fake_source_states)
    monkeypatch.setattr(live_copy_service, "load_execution_market_prices", fake_market_prices)
    monkeypatch.setattr(
        live_copy_service,
        "refresh_stale_live_copy_accounts",
        no_failed_reconciliation,
    )
    monkeypatch.setattr(live_copy_service, "ensure_live_copy_source_state", fake_source_state)
    monkeypatch.setattr(live_copy_service, "ensure_live_copy_fill_plan_states", fake_fill_plan)
    monkeypatch.setattr(live_copy_service, "claim_live_copy_fill_part", fake_claim)
    monkeypatch.setattr(
        live_copy_service,
        "live_copy_part_is_unowned_source_lifecycle",
        copyable_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "source_fill_age_exceeds_entry_limit",
        lambda *_args, **_kwargs: next(stale_results),
    )
    monkeypatch.setattr(live_copy_service, "mark_live_copy_fill_terminal_skip", fake_mark_terminal)
    monkeypatch.setattr(live_copy_service, "finalize_live_copy_fill_disposition", fake_finalize)
    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_complete_if_durable",
        fake_complete,
    )
    monkeypatch.setattr(live_copy_service, "apply_live_copy_part", apply_part)
    monkeypatch.setattr(live_copy_service, "record_live_skip", unexpected_order)

    result = await live_copy_service.process_live_copy_fills(
        CommitSession(),
        source_wallet="0xsource",
        fills=[
            {
                "externalFillId": "stale-open",
                "coin": "CASHCAT",
                "price": "0.04",
                "size": "100",
                "notionalUsd": "4",
                "timestampMs": int((datetime.now(UTC) - timedelta(minutes=2)).timestamp() * 1000),
                "rawJson": {"dir": "Open Long", "startPosition": "0"},
            }
        ],
        settings=Settings().model_copy(
            update={
                "live_trading_enabled": True,
                "trading_copy_max_entry_age_seconds": 15,
            }
        ),
        client=object(),
        trading_client=object(),
    )

    assert terminal_reasons == ["live_source_fill_too_old"]
    assert completed_fill_ids == ["stale-open"] * expected_completion_count
    assert fill_state.outcome == "terminal_skip"
    assert fill_state.reason == "live_source_fill_too_old"
    assert fill_state.trading_order_id is None
    assert result.skip_reasons == {"live_source_fill_too_old": 1}


def test_live_skip_records_reason_count() -> None:
    result = live_skip("live_account_no_tradable_equity", 3)

    assert result.skipped_fills == 3
    assert result.skip_reasons == {"live_account_no_tradable_equity": 3}


@pytest.mark.asyncio
async def test_exit_only_account_requires_owned_source_exposure() -> None:
    enabled = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    exit_only = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    exit_only.key = "live_exit_only"
    exit_only.status = "exit_only"

    class ScalarRows:
        def __init__(self, account_keys: list[str]) -> None:
            self.account_keys = account_keys

        def all(self) -> list[str]:
            return self.account_keys

    class ExposureSession:
        def __init__(self, account_keys: list[str]) -> None:
            self.account_keys = account_keys

        async def scalars(self, _query):
            return ScalarRows(self.account_keys)

    without_exposure = await live_copy_service.filter_live_accounts_for_source_allocation(
        ExposureSession([]),
        accounts=[enabled, exit_only],
        source_wallet="0xsource",
        allocation=source_allocation(),
    )
    with_exposure = await live_copy_service.filter_live_accounts_for_source_allocation(
        ExposureSession([exit_only.key]),
        accounts=[enabled, exit_only],
        source_wallet="0xsource",
        allocation=source_allocation(),
    )

    assert [account.key for account in without_exposure] == [enabled.key]
    assert [account.key for account in with_exposure] == [enabled.key, exit_only.key]


@pytest.mark.asyncio
async def test_exit_only_account_retains_unresolved_source_order_without_position() -> None:
    enabled = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    exit_only = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    exit_only.key = "live_exit_only"
    exit_only.status = "exit_only"

    class Rows:
        def __init__(self, account_keys: list[str]) -> None:
            self.account_keys = account_keys

        def all(self) -> list[str]:
            return self.account_keys

    class RetainedOrderSession:
        def __init__(self) -> None:
            self.rows = iter([Rows([]), Rows([exit_only.key])])

        async def scalars(self, _query):
            return next(self.rows)

    accounts = await live_copy_service.filter_live_accounts_for_source_allocation(
        RetainedOrderSession(),
        accounts=[enabled, exit_only],
        source_wallet="0xsource",
        allocation=source_allocation(),
    )

    assert [account.key for account in accounts] == [enabled.key, exit_only.key]


@pytest.mark.asyncio
async def test_pending_other_source_entry_reserves_live_market() -> None:
    class ReservationSession:
        def __init__(self) -> None:
            self.results = iter([None, "order-id"])

        async def scalar(self, _query):
            return next(self.results)

    reserved = await live_copy_service.live_market_is_reserved_by_other_source(
        ReservationSession(),
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
    )

    assert reserved is True


@pytest.mark.asyncio
async def test_zero_size_other_source_position_does_not_reserve_live_market() -> None:
    class ReservationSession:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def scalar(self, query):
            self.statements.append(query)
            return None

    session = ReservationSession()
    reserved = await live_copy_service.live_market_is_reserved_by_other_source(
        session,  # type: ignore[arg-type]
        account_key="live_test",
        source_wallet="0xsource",
        coin="HYPE",
    )

    sql = str(
        session.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert reserved is False
    assert "trading_positions.size > 1E-12" in sql


@pytest.mark.asyncio
async def test_close_without_owned_or_exchange_position_is_unowned_lifecycle(
    monkeypatch,
) -> None:
    async def no_position(*_args, **_kwargs):
        return None

    monkeypatch.setattr(live_copy_service, "load_live_source_position", no_position)
    monkeypatch.setattr(
        live_copy_service,
        "recover_live_source_position_attribution",
        no_position,
    )

    is_unowned = await live_copy_service.live_copy_part_is_unowned_source_lifecycle(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        source_state=live_source_lifecycle_state(),
        fill={"externalFillId": "fill-1", "coin": "CASHCAT", "timestampMs": 1_000},
        part=SourceFillPart(
            action="close",
            side="long",
            source_size=Decimal("100"),
            source_notional_usd=Decimal("4"),
            sequence_index=0,
            close_ratio=Decimal("0.5"),
            start_position=Decimal("200"),
        ),
        baseline_part=False,
    )

    assert is_unowned is True


@pytest.mark.asyncio
async def test_old_exit_cannot_close_a_newer_owned_source_lifecycle(monkeypatch) -> None:
    position = live_position(source_wallet="0xsource", side="long")
    position.source_lifecycle_timestamp_ms = int(
        datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000
    )
    position.source_lifecycle_direction_rank = 0
    position.source_lifecycle_position = Decimal("-1")
    position.source_lifecycle_fill_id_numeric = Decimal("10")
    position.source_lifecycle_fill_id = "10"

    async def owned_position(*_args, **_kwargs):
        return position

    monkeypatch.setattr(live_copy_service, "load_live_source_position", owned_position)

    is_unowned = await live_copy_service.live_copy_part_is_unowned_source_lifecycle(
        object(),
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        source_state=live_source_lifecycle_state(),
        fill={
            "externalFillId": "old-close",
            "coin": "HYPE",
            "timestampMs": int(datetime(2025, 12, 31, tzinfo=UTC).timestamp() * 1000),
        },
        part=SourceFillPart(
            action="close",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("100"),
            sequence_index=0,
            close_ratio=Decimal("1"),
            start_position=Decimal("1"),
        ),
        baseline_part=False,
    )

    assert is_unowned is True


@pytest.mark.asyncio
async def test_preexisting_add_is_unowned_but_fresh_flip_open_is_copyable(
    monkeypatch,
) -> None:
    async def no_position(*_args, **_kwargs):
        return None

    monkeypatch.setattr(live_copy_service, "load_live_source_position", no_position)
    monkeypatch.setattr(
        live_copy_service,
        "recover_live_source_position_attribution",
        no_position,
    )
    account = live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC))
    source_state = live_source_lifecycle_state()
    fill = {"externalFillId": "fill-1", "coin": "CASHCAT", "timestampMs": 1_000}

    preexisting_add = await live_copy_service.live_copy_part_is_unowned_source_lifecycle(
        object(),
        account=account,
        source_state=source_state,
        fill=fill,
        part=SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("100"),
            source_notional_usd=Decimal("4"),
            sequence_index=0,
            close_ratio=None,
            start_position=Decimal("500"),
        ),
        baseline_part=False,
    )
    flip_open = await live_copy_service.live_copy_part_is_unowned_source_lifecycle(
        object(),
        account=account,
        source_state=source_state,
        fill=fill,
        part=SourceFillPart(
            action="flip_open",
            side="short",
            source_size=Decimal("100"),
            source_notional_usd=Decimal("4"),
            sequence_index=1,
            close_ratio=None,
            start_position=Decimal("0"),
        ),
        baseline_part=False,
    )

    assert preexisting_add is True
    assert flip_open is False


@pytest.mark.asyncio
async def test_owned_add_recovers_missing_source_attribution(monkeypatch) -> None:
    exchange_position = live_position(
        source_wallet="__exchange__",
        side="long",
        size=Decimal("3"),
    )

    class AttributionSession:
        added: list[TradingPosition]

        def __init__(self) -> None:
            self.added = []

        def add(self, value: TradingPosition) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            pass

    async def load_position(*_args, **kwargs):
        if kwargs["source_wallet"] == "__exchange__":
            return exchange_position
        return None

    async def market_is_free(*_args, **_kwargs):
        return False

    async def owned_lifecycle(*_args, **_kwargs):
        opened_at = datetime(2026, 1, 1, tzinfo=UTC)
        return live_copy_service.LiveSourceLifecycleProof(
            aggregate_signed_size=Decimal("3"),
            contributions=(("0xsource", Decimal("3")),),
            lifecycle_opened_at=opened_at,
            source_first_fill_at=opened_at,
            last_fill_at=opened_at,
            history_incomplete=False,
            source_opening_fill_id="owned-open",
            source_opening_sequence_index=0,
        )

    async def lifecycle_order(*_args, **_kwargs):
        return source_lifecycle_order()

    monkeypatch.setattr(live_copy_service, "load_live_source_position", load_position)
    monkeypatch.setattr(
        live_copy_service,
        "live_market_is_reserved_by_other_source",
        market_is_free,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_lifecycle_proof",
        owned_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_recovered_live_copy_lifecycle_order",
        lifecycle_order,
    )

    session = AttributionSession()
    is_unowned = await live_copy_service.live_copy_part_is_unowned_source_lifecycle(
        session,
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        source_state=live_source_lifecycle_state(),
        fill={"externalFillId": "owned-add", "coin": "HYPE", "timestampMs": 2_000},
        part=SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("100"),
            sequence_index=0,
            start_position=Decimal("2"),
        ),
        baseline_part=False,
    )

    assert is_unowned is False
    assert len(session.added) == 1
    recovered = session.added[0]
    assert recovered.source_wallet == "0xsource"
    assert recovered.side == "long"
    assert recovered.size == exchange_position.size
    assert recovered.margin_mode == exchange_position.margin_mode
    assert recovered.raw_payload["sourceAttributionRecovery"]["sourceWallet"] == "0xsource"


def test_lifecycle_reconstruction_does_not_reuse_closed_source_history() -> None:
    opened_at = datetime(2026, 1, 1, tzinfo=UTC)
    proof = live_copy_service.reconstruct_live_source_lifecycle(
        [
            live_trading_fill(
                source_wallet="0xsource",
                action="open",
                side="long",
                size=Decimal("3"),
                filled_at=opened_at,
            ),
            live_trading_fill(
                source_wallet="0xsource",
                action="close",
                side="long",
                size=Decimal("3"),
                filled_at=opened_at + timedelta(minutes=1),
            ),
            live_trading_fill(
                source_wallet="__exchange__",
                action="open",
                side="long",
                size=Decimal("2"),
                filled_at=opened_at + timedelta(hours=1),
            ),
        ],
        source_wallet="0xsource",
    )

    assert proof.aggregate_signed_size == Decimal("2")
    assert dict(proof.contributions) == {"__exchange__": Decimal("2")}
    assert proof.source_first_fill_at is None
    assert proof.history_incomplete is False


def test_lifecycle_reconstruction_rejects_add_without_current_open() -> None:
    proof = live_copy_service.reconstruct_live_source_lifecycle(
        [
            live_trading_fill(
                source_wallet="0xsource",
                action="add",
                side="long",
                size=Decimal("2"),
                filled_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
        source_wallet="0xsource",
    )

    assert proof.aggregate_signed_size == Decimal("2")
    assert proof.history_incomplete is True


def test_lifecycle_order_uses_source_canonical_key_and_part_sequence() -> None:
    filled_at = datetime(2026, 1, 1, tzinfo=UTC)
    flip_open = live_trading_fill(
        source_wallet="0xsource",
        action="flip_open",
        side="short",
        size=Decimal("1"),
        filled_at=filled_at,
    )
    flip_open.source_fill_id = "flip-10"
    flip_open.sequence_index = 1
    numeric_ten = live_trading_fill(
        source_wallet="0xsource",
        action="open",
        side="long",
        size=Decimal("1"),
        filled_at=filled_at,
    )
    numeric_ten.source_fill_id = "10"
    numeric_ten.sequence_index = 0
    numeric_two = live_trading_fill(
        source_wallet="0xsource",
        action="open",
        side="long",
        size=Decimal("1"),
        filled_at=filled_at,
    )
    numeric_two.source_fill_id = "2"
    numeric_two.sequence_index = 0
    flip_close = live_trading_fill(
        source_wallet="0xsource",
        action="flip_close",
        side="long",
        size=Decimal("1"),
        filled_at=filled_at,
    )
    flip_close.source_fill_id = "flip-10"
    flip_close.sequence_index = 0
    states = {
        ("flip-10", 0): lifecycle_fill_state(
            source_fill_id="flip-10",
            sequence_index=0,
            action="flip_close",
            side="long",
            direction_rank=0,
            numeric_fill_id=None,
        ),
        ("flip-10", 1): lifecycle_fill_state(
            source_fill_id="flip-10",
            sequence_index=1,
            action="flip_open",
            side="short",
            direction_rank=0,
            numeric_fill_id=None,
        ),
        ("10", 0): lifecycle_fill_state(
            source_fill_id="10",
            sequence_index=0,
            action="open",
            side="long",
            direction_rank=1,
            numeric_fill_id=Decimal("10"),
        ),
        ("2", 0): lifecycle_fill_state(
            source_fill_id="2",
            sequence_index=0,
            action="open",
            side="long",
            direction_rank=1,
            numeric_fill_id=Decimal("2"),
        ),
    }

    ordered = live_copy_service.order_live_source_lifecycle_fills(
        [flip_open, numeric_ten, numeric_two, flip_close],
        source_wallet="0xsource",
        source_states_by_key=states,
        wallet_fills_by_id={},
    )

    assert [(fill.source_fill_id, fill.sequence_index) for fill in ordered] == [
        ("flip-10", 0),
        ("flip-10", 1),
        ("2", 0),
        ("10", 0),
    ]


def test_lifecycle_order_fails_closed_without_source_order_proof() -> None:
    source_fill = live_trading_fill(
        source_wallet="0xsource",
        action="open",
        side="long",
        size=Decimal("1"),
        filled_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    source_fill.source_fill_id = "unproven"
    source_fill.sequence_index = 0

    with pytest.raises(live_copy_service.LiveCopyPartDeferred) as exc_info:
        live_copy_service.order_live_source_lifecycle_fills(
            [source_fill],
            source_wallet="0xsource",
            source_states_by_key={},
            wallet_fills_by_id={},
        )

    assert exc_info.value.reason == "live_source_attribution_ambiguous"


@pytest.mark.asyncio
async def test_attribution_recovery_defers_unexplained_manual_exposure(monkeypatch) -> None:
    exchange_position = live_position(
        source_wallet="__exchange__",
        side="long",
        size=Decimal("3"),
    )

    class AttributionSession:
        def __init__(self) -> None:
            self.added: list[TradingPosition] = []

        def add(self, value: TradingPosition) -> None:
            self.added.append(value)

    async def load_position(*_args, **kwargs):
        if kwargs["source_wallet"] == "__exchange__":
            return exchange_position
        return None

    async def market_is_free(*_args, **_kwargs):
        return False

    async def ambiguous_lifecycle(*_args, **_kwargs):
        opened_at = datetime(2026, 1, 1, tzinfo=UTC)
        return live_copy_service.LiveSourceLifecycleProof(
            aggregate_signed_size=Decimal("3"),
            contributions=(
                ("0xsource", Decimal("2")),
                ("__exchange__", Decimal("1")),
            ),
            lifecycle_opened_at=opened_at,
            source_first_fill_at=opened_at,
            last_fill_at=opened_at,
            history_incomplete=False,
        )

    monkeypatch.setattr(live_copy_service, "load_live_source_position", load_position)
    monkeypatch.setattr(
        live_copy_service,
        "live_market_is_reserved_by_other_source",
        market_is_free,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_lifecycle_proof",
        ambiguous_lifecycle,
    )

    session = AttributionSession()
    with pytest.raises(live_copy_service.LiveCopyPartDeferred) as exc_info:
        await live_copy_service.recover_live_source_position_attribution(
            session,
            account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
            source_wallet="0xsource",
            coin="HYPE",
            side="long",
        )

    assert exc_info.value.reason == "live_source_attribution_ambiguous"
    assert session.added == []


@pytest.mark.asyncio
async def test_attribution_recovery_caps_source_size_after_manual_reduction(
    monkeypatch,
) -> None:
    exchange_position = live_position(
        source_wallet="__exchange__",
        side="long",
        size=Decimal("3"),
    )

    class AttributionSession:
        def __init__(self) -> None:
            self.added: list[TradingPosition] = []

        def add(self, value: TradingPosition) -> None:
            self.added.append(value)

        async def flush(self) -> None:
            pass

    async def load_position(*_args, **kwargs):
        if kwargs["source_wallet"] == "__exchange__":
            return exchange_position
        return None

    async def market_is_free(*_args, **_kwargs):
        return False

    async def reduced_lifecycle(*_args, **_kwargs):
        opened_at = datetime(2026, 1, 1, tzinfo=UTC)
        return live_copy_service.LiveSourceLifecycleProof(
            aggregate_signed_size=Decimal("3"),
            contributions=(
                ("0xsource", Decimal("5")),
                ("__exchange__", Decimal("-2")),
            ),
            lifecycle_opened_at=opened_at,
            source_first_fill_at=opened_at,
            last_fill_at=opened_at + timedelta(minutes=1),
            history_incomplete=False,
            source_opening_fill_id="owned-open",
            source_opening_sequence_index=0,
        )

    async def lifecycle_order(*_args, **_kwargs):
        return source_lifecycle_order()

    monkeypatch.setattr(live_copy_service, "load_live_source_position", load_position)
    monkeypatch.setattr(
        live_copy_service,
        "live_market_is_reserved_by_other_source",
        market_is_free,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_lifecycle_proof",
        reduced_lifecycle,
    )
    monkeypatch.setattr(
        live_copy_service,
        "load_recovered_live_copy_lifecycle_order",
        lifecycle_order,
    )

    session = AttributionSession()
    recovered = await live_copy_service.recover_live_source_position_attribution(
        session,
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        source_wallet="0xsource",
        coin="HYPE",
        side="long",
    )

    assert recovered is session.added[0]
    assert recovered.size == Decimal("3")
    assert recovered.notional_usd == exchange_position.notional_usd


@pytest.mark.asyncio
async def test_retryable_skip_disposition_remains_retryable(monkeypatch) -> None:
    order = live_order(
        status="failed",
        requested_size=Decimal("1"),
        filled_size=Decimal("0"),
    )
    order.order_type = "skip"
    order.error = "skip:live_execution_busy"
    fill_state = LiveCopyFillState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="HYPE",
        action="open",
        side="long",
        source_timestamp_ms=1_000,
        origin="realtime",
        outcome="pending",
        attempt_count=0,
        fill_complete=False,
    )
    retry_reasons: list[str] = []

    class OrderSession:
        def __init__(self) -> None:
            self.values = iter((fill_state, order))

        async def scalar(self, _query):
            return next(self.values)

    async def mark_retryable(*_args, **kwargs):
        retry_reasons.append(kwargs["reason"])
        return 5

    async def unexpected_terminal(*_args, **_kwargs):
        raise AssertionError("A retryable skip must not become terminal.")

    monkeypatch.setattr(
        live_copy_service,
        "mark_live_copy_fill_retryable",
        mark_retryable,
    )
    monkeypatch.setattr(
        live_copy_service,
        "link_live_copy_fill_state_to_order",
        unexpected_terminal,
    )

    durable = await live_copy_service.finalize_live_copy_fill_disposition(
        OrderSession(),
        fill_state=fill_state,
    )

    assert durable is False
    assert retry_reasons == ["skip:live_execution_busy"]


@pytest.mark.asyncio
async def test_flip_open_waits_until_old_side_is_reconciled(monkeypatch) -> None:
    async def old_side_position(*_args, **_kwargs):
        return live_position(source_wallet="0xsource", side="long")

    monkeypatch.setattr(
        live_copy_service,
        "load_live_source_position",
        old_side_position,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_tradable_equity_usd",
        lambda *_args, **_kwargs: Decimal("1000"),
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_copy_allocation_equity_usd",
        lambda *_args, **_kwargs: Decimal("1000"),
    )

    with pytest.raises(live_copy_service.LiveCopyPartDeferred) as exc_info:
        await live_copy_service.apply_live_open_part(
            object(),
            account=live_account(last_reconciled_at=datetime.now(UTC)),
            allocation=source_allocation(),
            fill={
                "externalFillId": "flip-1",
                "coin": "HYPE",
                "price": "100",
                "timestampMs": int(datetime.now(UTC).timestamp() * 1000),
            },
            part=SourceFillPart(
                action="flip_open",
                side="short",
                source_size=Decimal("1"),
                source_notional_usd=Decimal("100"),
                sequence_index=1,
                start_position=Decimal("0"),
            ),
            source_account_state=PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"HYPE": Decimal("3")},
                positions_by_coin={},
                margin_mode_by_coin={"HYPE": "cross"},
            ),
            source_perp_equity=Decimal("1000"),
            source_leverages={"HYPE": Decimal("3")},
            market_prices=ExecutionMarketPrices(
                prices={"HYPE": Decimal("100")},
                sources={"HYPE": "test"},
            ),
            settings=Settings().model_copy(update={"live_trading_enabled": True}),
            trading_client=object(),
        )

    assert exc_info.value.reason == "live_flip_close_pending"


@pytest.mark.asyncio
async def test_defensive_stale_live_entry_guard_raises_terminal_decision() -> None:
    with pytest.raises(live_copy_service.LiveCopyPartTerminal) as exc_info:
        await live_copy_service.apply_live_open_part(
            object(),
            account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
            allocation=source_allocation(),
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
            source_account_state=PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"HYPE": Decimal("25")},
                positions_by_coin={},
                margin_mode_by_coin={"HYPE": "cross"},
            ),
            source_perp_equity=Decimal("1000"),
            source_leverages={"HYPE": Decimal("25")},
            market_prices=ExecutionMarketPrices(prices={}, sources={}),
            settings=Settings(trading_copy_max_entry_age_seconds=15),
            trading_client=object(),
        )

    assert exc_info.value.reason == "live_source_fill_too_old"


@pytest.mark.asyncio
async def test_stale_live_entry_is_classified_before_missing_source_leverage() -> None:
    with pytest.raises(live_copy_service.LiveCopyPartTerminal) as exc_info:
        await live_copy_service.apply_live_open_part(
            object(),
            account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
            allocation=source_allocation(),
            fill={
                "externalFillId": "fill-old",
                "coin": "CASHCAT",
                "price": "0.04",
                "timestampMs": int((datetime.now(UTC) - timedelta(minutes=2)).timestamp() * 1000),
            },
            part=SourceFillPart(
                action="open",
                side="long",
                source_size=Decimal("100"),
                source_notional_usd=Decimal("4"),
                sequence_index=0,
                close_ratio=None,
                start_position=Decimal("0"),
            ),
            source_account_state=PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={},
                positions_by_coin={},
                margin_mode_by_coin={},
            ),
            source_perp_equity=Decimal("1000"),
            source_leverages={},
            market_prices=ExecutionMarketPrices(prices={}, sources={}),
            settings=Settings(trading_copy_max_entry_age_seconds=15),
            trading_client=object(),
        )

    assert exc_info.value.reason == "live_source_fill_too_old"


@pytest.mark.asyncio
async def test_live_entry_defers_when_source_margin_mode_is_missing() -> None:
    with pytest.raises(live_copy_service.LiveCopyPartDeferred) as exc_info:
        await live_copy_service.apply_live_open_part(
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
            fill={"externalFillId": "fill-1", "coin": "HYPE", "price": "100"},
            part=SourceFillPart(
                action="open",
                side="long",
                source_size=Decimal("0.1"),
                source_notional_usd=Decimal("10"),
                sequence_index=0,
                close_ratio=None,
                start_position=Decimal("0"),
            ),
            source_account_state=PaperSourceAccountState(
                dex="",
                perp_equity=Decimal("1000"),
                leverage_by_coin={"HYPE": Decimal("1")},
                positions_by_coin={},
            ),
            source_perp_equity=Decimal("1000"),
            source_leverages={"HYPE": Decimal("1")},
            market_prices=ExecutionMarketPrices(prices={}, sources={}),
            settings=Settings(),
            trading_client=object(),
        )

    assert exc_info.value.reason == "live_source_margin_mode_missing"


@pytest.mark.asyncio
async def test_recovery_syncs_current_source_leverage_and_margin_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}
    position = live_position(source_wallet="0xsource", side="long")

    class ScalarResult:
        def all(self):
            return [position]

    class FakeSession:
        async def scalars(self, _query):
            return ScalarResult()

        async def commit(self) -> None:
            return None

    async def fake_load_source_account_state(**_kwargs):
        return PaperSourceAccountState(
            dex="",
            perp_equity=Decimal("1000"),
            leverage_by_coin={"HYPE": Decimal("1")},
            positions_by_coin={},
            margin_mode_by_coin={"HYPE": "isolated"},
        )

    async def fake_sync_live_position_margin_setting(*_args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        live_copy_service,
        "load_source_account_state",
        fake_load_source_account_state,
    )
    monkeypatch.setattr(
        live_copy_service,
        "sync_live_position_margin_setting",
        fake_sync_live_position_margin_setting,
    )

    updated = await live_copy_service.sync_live_source_margin_settings(
        FakeSession(),
        source_wallet="0xsource",
        settings=Settings(),
        info_client=object(),
        trading_client=object(),
    )

    assert updated == 1
    assert captured["leverage"] == Decimal("1")
    assert captured["margin_mode"] == "isolated"
    assert captured["coin"] == "HYPE"


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

    assert live_copy_allocation_equity_usd(account, settings=settings) == Decimal("200")


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

    assert live_copy_allocation_equity_usd(account, dex="xyz", settings=settings) == Decimal("80")


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

    assert live_aggregated_below_min_close_size(
        close_size=Decimal("0.35"),
        previous_skip_orders=[previous],
        available_size=Decimal("0.70"),
    ) == Decimal("0.70")


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
    assert "hiddenFromActivity" not in previous_skip.raw_payload
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
async def test_close_submits_after_safe_source_attribution_recovery(
    monkeypatch,
) -> None:
    submitted_intents = []

    async def fake_load_live_source_position(*_args, **_kwargs):
        return None

    async def fake_recover_source_position(*_args, **_kwargs):
        return live_position(
            source_wallet="0xsource",
            side="short",
            size=Decimal("303"),
        )

    async def no_pending_close(*_args, **_kwargs):
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
        "recover_live_source_position_attribution",
        fake_recover_source_position,
    )
    monkeypatch.setattr(
        live_copy_service,
        "live_pending_close_size_for_position",
        no_pending_close,
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
    assert intent.price_source == "test"


@pytest.mark.asyncio
async def test_submit_live_copy_intent_reports_submit_error(monkeypatch) -> None:
    class CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement

    async def fake_submit_live_trade_intent(*args, **kwargs):
        raise LiveOrderSubmitError("Rejected by exchange.")

    monkeypatch.setattr(
        live_copy_service,
        "submit_live_trade_intent",
        fake_submit_live_trade_intent,
    )

    session = CaptureSession()
    result = await submit_live_copy_intent(
        session,
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
    assert session.statement is not None
    params = session.statement.compile(dialect=postgresql.dialect()).params
    assert params["order_type"] == "skip"
    assert params["status"] == "failed"
    assert params["error"] == "skip:live_order_submit_error"
    assert params["raw_payload"]["decisionAt"]
    assert params["raw_payload"]["submitError"]["message"] == "Rejected by exchange."


@pytest.mark.asyncio
async def test_concurrent_start_reclassification_keeps_baseline_state_without_order(
    monkeypatch,
) -> None:
    class NoOrderSession:
        def __init__(self) -> None:
            self.executed: list[object] = []

        async def execute(self, statement):
            self.executed.append(statement)

        async def scalar(self, _statement):
            return None

    async def reclassified_submit(*_args, **_kwargs):
        raise live_copy_service.LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )

    monkeypatch.setattr(
        live_copy_service,
        "submit_live_trade_intent",
        reclassified_submit,
    )
    session = NoOrderSession()
    intent = build_copy_trade_intent(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="old-entry",
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
    )
    result = await submit_live_copy_intent(
        session,  # type: ignore[arg-type]
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        intent=intent,
        settings=Settings(),
        trading_client=object(),
    )
    baseline_state = LiveCopyFillState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="old-entry",
        sequence_index=0,
        coin="HYPE",
        action="open",
        side="long",
        source_timestamp_ms=1,
        origin="realtime",
        outcome="baseline_ignored",
        attempt_count=0,
        fill_complete=False,
    )

    finalized = await live_copy_service.finalize_live_copy_fill_disposition(
        session,  # type: ignore[arg-type]
        fill_state=baseline_state,
    )

    assert result.skip_reasons == {"live_source_lifecycle_reclassified": 1}
    assert finalized is True
    assert baseline_state.outcome == "baseline_ignored"
    assert session.executed == []


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
    class InsertedResult:
        @staticmethod
        def scalar_one_or_none():
            return "order-id"

    class CaptureSession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return InsertedResult()

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
        margin_mode="cross",
        source_fill_age_seconds=600.1234,
        requested_notional_usd=Decimal("0"),
        requested_size=Decimal("0"),
        decision_context={"sourceRemainingMarginUsd": "0"},
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
    assert "hiddenFromActivity" not in params["raw_payload"]
    assert params["raw_payload"]["sourceFillAgeSeconds"] == 600.123
    assert params["raw_payload"]["decisionAt"]
    assert params["raw_payload"]["decisionContext"] == {"sourceRemainingMarginUsd": "0"}
    assert "submitted_at" not in params
    assert params["requested_notional_usd"] == Decimal("0")
    assert params["requested_size"] == Decimal("0")
    assert params["filled_size"] == Decimal("0")


@pytest.mark.asyncio
async def test_record_live_skip_does_not_recount_existing_decision() -> None:
    class ConflictResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    class ConflictSession:
        async def execute(self, _statement):
            return ConflictResult()

        async def scalar(self, _statement):
            order = live_order(
                status="failed",
                requested_size=Decimal("0.1"),
                filled_size=Decimal("0"),
            )
            order.error = "Insufficient margin."
            return order

    result = await record_live_skip(
        ConflictSession(),
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
    )

    assert result == PaperCopyBatchResult()


@pytest.mark.asyncio
async def test_record_live_skip_promotes_old_transient_decision_to_terminal() -> None:
    class ConflictResult:
        @staticmethod
        def scalar_one_or_none():
            return None

    existing = live_order(
        status="failed",
        requested_size=Decimal("0.1"),
        filled_size=Decimal("0"),
    )
    existing.order_type = "skip"
    existing.error = "skip:live_source_leverage_missing"
    existing.raw_payload = {"skipReason": "live_source_leverage_missing"}

    class PromotionSession:
        flush_count = 0

        async def execute(self, _statement):
            return ConflictResult()

        async def scalar(self, _statement):
            return existing

        async def flush(self):
            self.flush_count += 1

    session = PromotionSession()
    result = await record_live_skip(
        session,
        account=live_account(last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC)),
        allocation=source_allocation(),
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
            start_position=Decimal("0"),
        ),
        reason="live_source_fill_too_old",
    )

    assert result.skip_reasons == {"live_source_fill_too_old": 1}
    assert existing.error == "skip:live_source_fill_too_old"
    assert existing.raw_payload["skipReason"] == "live_source_fill_too_old"
    assert session.flush_count == 1


def live_account(*, last_reconciled_at: datetime | None) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
        last_reconciled_at=last_reconciled_at,
    )


def source_allocation() -> PaperSourceAllocation:
    return PaperSourceAllocation(
        source_wallet="0xsource",
        source_label="Source",
        rank=1,
        pool_rank=1,
        score=Decimal("90"),
        allocation_pct=Decimal("0.2"),
        active=True,
        has_realtime_slot=True,
        status_reason="trading",
    )


def live_source_lifecycle_state() -> LiveCopySourceState:
    return LiveCopySourceState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        status="active",
        entry_eligible=True,
        activated_at=datetime(2026, 1, 1, tzinfo=UTC),
        baseline_source_timestamp_ms=1_000,
        baseline_fill_ids=[],
        preexisting_markets={},
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
        margin_mode="cross",
        margin_usd=Decimal("1"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        opened_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def source_lifecycle_order() -> dict[str, object]:
    return {
        "raw": {
            "timestampMs": 1_000,
            "coin": "HYPE",
            "directionRank": 1,
            "positionRank": "0",
            "fillIdNumeric": "1",
            "fillId": "owned-open",
        },
        "columns": {
            "source_lifecycle_timestamp_ms": 1_000,
            "source_lifecycle_direction_rank": 1,
            "source_lifecycle_position": Decimal("0"),
            "source_lifecycle_fill_id_numeric": Decimal("1"),
            "source_lifecycle_fill_id": "owned-open",
        },
    }


def live_trading_fill(
    *,
    source_wallet: str,
    action: str,
    side: str,
    size: Decimal,
    filled_at: datetime,
) -> TradingFill:
    return TradingFill(
        account_key="live_test",
        account_type="live",
        source_wallet=source_wallet,
        source_fill_id=f"{source_wallet}-{action}-{filled_at.timestamp()}",
        sequence_index=0,
        exchange_fill_id=f"exchange-{source_wallet}-{action}-{filled_at.timestamp()}",
        coin="HYPE",
        action=action,
        side=side,
        price=Decimal("100"),
        size=size,
        notional_usd=size * Decimal("100"),
        fee_usd=Decimal("0"),
        realized_pnl_usd=Decimal("0"),
        filled_at=filled_at,
    )


def lifecycle_fill_state(
    *,
    source_fill_id: str,
    sequence_index: int,
    action: str,
    side: str,
    direction_rank: int,
    numeric_fill_id: Decimal | None,
) -> LiveCopyFillState:
    return LiveCopyFillState(
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id=source_fill_id,
        sequence_index=sequence_index,
        expected_part_count=2 if source_fill_id == "flip-10" else 1,
        plan_version=1,
        coin="HYPE",
        action=action,
        side=side,
        source_timestamp_ms=1_000,
        source_order_direction_rank=direction_rank,
        source_order_position=Decimal("0"),
        source_order_fill_id_numeric=numeric_fill_id,
        origin="periodic_recovery",
        outcome="pending",
        fill_complete=False,
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
