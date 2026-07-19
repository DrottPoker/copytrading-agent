from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.db.models import LiveCopyFillState, LiveCopySourceState, TradingOrder
from app.services.live_copy_state_service import (
    LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
    LIVE_COPY_ORIGIN_REALTIME,
    LIVE_COPY_OUTCOME_RETRYABLE,
    LIVE_COPY_OUTCOME_TERMINAL_SKIP,
    build_live_copy_recovery_candidate_query,
    ensure_live_copy_source_state,
    is_live_copy_fill_post_baseline,
    live_copy_retry_delay_seconds,
    live_copy_unresolved_order_predicate,
    mark_live_copy_fill_retryable,
    mark_live_copy_fill_terminal_skip,
    normalize_live_copy_fill_plan_parts,
    preexisting_market_matches_part,
    update_preexisting_markets_for_part,
)
from app.services.paper_trading_service import SourceFillPart
from app.services.source_fill_ordering import source_fill_order_key


def test_normal_entry_requires_observation_and_source_timestamp_after_start() -> None:
    source_state = live_copy_source_state(
        baseline_source_timestamp_ms=1_000,
        baseline_fill_ids=["existing-a", "existing-b"],
    )

    assert not is_live_copy_fill_post_baseline(
        source_state,
        fill={"externalFillId": "existing-a", "timestampMs": 1_000},
        origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        first_observed_at=datetime(2026, 7, 17, 23, 59, tzinfo=UTC),
    )
    activation_timestamp_ms = int(source_state.activated_at.timestamp() * 1000)
    assert not is_live_copy_fill_post_baseline(
        source_state,
        fill={"externalFillId": "late-snapshot", "timestampMs": 1_000},
        origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        first_observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    assert is_live_copy_fill_post_baseline(
        source_state,
        fill={"externalFillId": "post-start", "timestampMs": activation_timestamp_ms},
        origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        first_observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


def test_realtime_old_exchange_timestamp_is_not_eligible_when_first_seen_after_start() -> None:
    source_state = live_copy_source_state(
        baseline_source_timestamp_ms=1_000,
        baseline_fill_ids=["existing"],
    )

    assert not is_live_copy_fill_post_baseline(
        source_state,
        fill={"externalFillId": "existing", "timestampMs": 999},
        origin=LIVE_COPY_ORIGIN_REALTIME,
        first_observed_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_baseline_captures_every_external_id_at_latest_timestamp() -> None:
    session = BaselineSession(
        scalar_values=[None, 1_000],
        scalar_list_values=["z-fill", "a-fill", "z-fill"],
    )
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)

    source_state = await ensure_live_copy_source_state(
        session,
        account_key="live_main",
        source_wallet="0xSOURCE",
        now=observed_at,
    )

    assert source_state.source_wallet == "0xsource"
    assert source_state.status == "active"
    assert source_state.baseline_source_timestamp_ms == 1_000
    assert source_state.baseline_fill_ids == ["a-fill", "z-fill"]
    assert source_state.scan_high_water_fill_id is None
    assert source_state.baseline_completed_at == observed_at
    assert session.added == [source_state]
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_new_selected_epoch_rebaselines_an_active_retained_lane() -> None:
    original_epoch = datetime(2026, 7, 18, 10, tzinfo=UTC)
    reselected_epoch = original_epoch + timedelta(hours=1)
    source_state = LiveCopySourceState(
        account_key="live_main",
        account_type="live",
        source_wallet="0xsource",
        status="active",
        entry_eligible=False,
        activated_at=original_epoch,
        baseline_source_timestamp_ms=1_000,
        baseline_fill_ids=["old-baseline"],
        scan_high_water_timestamp_ms=1_000,
        scan_high_water_coin="HYPE",
        scan_high_water_direction_rank=1,
        scan_high_water_position=Decimal("0"),
        scan_high_water_fill_id="old-baseline",
        preexisting_markets={"BTC": {"side": "long"}},
    )
    session = ExistingStateBaselineSession(
        scalar_values=[source_state, 2_000],
        scalar_lists=[["new-baseline"], []],
    )

    reactivated = await ensure_live_copy_source_state(
        session,  # type: ignore[arg-type]
        account_key="live_main",
        source_wallet="0xsource",
        now=reselected_epoch + timedelta(minutes=1),
        eligibility_started_at=reselected_epoch,
    )

    assert reactivated is source_state
    assert source_state.entry_eligible is True
    assert source_state.activated_at == reselected_epoch
    assert source_state.baseline_source_timestamp_ms == 2_000
    assert source_state.baseline_fill_ids == ["new-baseline"]
    assert source_state.scan_high_water_timestamp_ms is None
    assert source_state.preexisting_markets == {}


def test_preexisting_lifecycle_tracks_flip_without_history_growth() -> None:
    source_state = live_copy_source_state()
    initial_open = source_part(action="open", side="long", start_position=Decimal("0"))
    flip_close = source_part(
        action="flip_close",
        side="long",
        start_position=Decimal("5"),
        close_ratio=Decimal("1"),
    )
    flip_open = source_part(action="flip_open", side="short", start_position=Decimal("0"))

    update_preexisting_markets_for_part(
        source_state,
        coin="HYPE",
        part=initial_open,
        source_fill_id="open",
        source_timestamp_ms=100,
    )
    assert preexisting_market_matches_part(source_state, coin="HYPE", part=flip_close)

    update_preexisting_markets_for_part(
        source_state,
        coin="HYPE",
        part=flip_close,
        source_fill_id="flip-close",
        source_timestamp_ms=200,
    )
    assert source_state.preexisting_markets == {}

    update_preexisting_markets_for_part(
        source_state,
        coin="HYPE",
        part=flip_open,
        source_fill_id="flip-open",
        source_timestamp_ms=200,
    )
    assert source_state.preexisting_markets == {
        "HYPE": {
            "side": "short",
            "openedAtTimestampMs": 200,
            "lastSourceTimestampMs": 200,
            "lastSourceFillId": "flip-open",
            "ignoredFillCount": 1,
        }
    }


def test_preexisting_add_and_partial_close_keep_one_compact_market_marker() -> None:
    source_state = live_copy_source_state(
        preexisting_markets={
            "HYPE": {
                "side": "long",
                "openedAtTimestampMs": 10,
                "lastSourceTimestampMs": 10,
                "lastSourceFillId": "seed",
                "ignoredFillCount": 1,
            }
        }
    )
    add = source_part(action="open", side="long", start_position=Decimal("2"))
    partial_close = source_part(
        action="close",
        side="long",
        start_position=Decimal("3"),
        close_ratio=Decimal("0.5"),
    )

    assert preexisting_market_matches_part(source_state, coin="HYPE", part=add)
    assert preexisting_market_matches_part(source_state, coin="HYPE", part=partial_close)

    update_preexisting_markets_for_part(
        source_state,
        coin="HYPE",
        part=partial_close,
        source_fill_id="partial-close",
        source_timestamp_ms=20,
    )

    assert source_state.preexisting_markets["HYPE"] == {
        "side": "long",
        "openedAtTimestampMs": 10,
        "lastSourceTimestampMs": 20,
        "lastSourceFillId": "partial-close",
        "ignoredFillCount": 2,
    }


@pytest.mark.asyncio
async def test_retryable_state_uses_bounded_backoff_without_an_order() -> None:
    session = FlushSession()
    state = LiveCopyFillState(
        account_key="live_main",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="HYPE",
        action="open",
        side="long",
        source_timestamp_ms=1_000,
        origin=LIVE_COPY_ORIGIN_REALTIME,
        outcome="pending",
        attempt_count=0,
        fill_complete=False,
    )
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)

    delay = await mark_live_copy_fill_retryable(
        session,
        fill_state=state,
        reason="live_reconciliation_deferred",
        now=observed_at,
        base_seconds=5,
        max_seconds=60,
    )

    assert delay == 5
    assert state.outcome == LIVE_COPY_OUTCOME_RETRYABLE
    assert state.attempt_count == 1
    assert state.next_attempt_at == observed_at + timedelta(seconds=5)
    assert state.trading_order_id is None
    assert session.flush_count == 1
    assert live_copy_retry_delay_seconds(5, base_seconds=5, max_seconds=60) == 60
    assert live_copy_retry_delay_seconds(999, base_seconds=5, max_seconds=60) == 60


@pytest.mark.asyncio
async def test_terminal_skip_state_does_not_require_a_trading_order() -> None:
    session = FlushSession()
    state = LiveCopyFillState(
        account_key="live_main",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        coin="HYPE",
        action="open",
        side="long",
        source_timestamp_ms=1_000,
        origin=LIVE_COPY_ORIGIN_REALTIME,
        outcome="retryable",
        reason="processing",
        attempt_count=1,
        next_attempt_at=datetime(2026, 7, 18, tzinfo=UTC) + timedelta(minutes=1),
        fill_complete=False,
    )

    await mark_live_copy_fill_terminal_skip(
        session,
        fill_state=state,
        reason="live_source_fill_too_old",
    )

    assert state.outcome == LIVE_COPY_OUTCOME_TERMINAL_SKIP
    assert state.reason == "live_source_fill_too_old"
    assert state.next_attempt_at is None
    assert state.fill_complete is False
    assert state.trading_order_id is None
    assert session.flush_count == 1


def test_recovery_query_excludes_completed_before_limit_and_keeps_due_retry_paths() -> None:
    source_state = live_copy_source_state(
        baseline_source_timestamp_ms=1_000,
        baseline_fill_ids=["baseline-fill"],
    )
    query = build_live_copy_recovery_candidate_query(
        account_key="live_main",
        source_wallet="0xsource",
        source_state=source_state,
        limit=37,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )
    sql = str(
        query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "NOT (EXISTS" in sql
    assert "fill_complete IS true" in sql
    assert "outcome = 'retryable'" in sql
    assert "wallet_fills.received_at >=" in sql
    assert "wallet_fills.timestamp_ms >= 1784332800000" in sql
    assert "external_fill_id NOT IN ('baseline-fill')" not in sql
    assert sql.index("NOT (EXISTS") < sql.index("LIMIT 37")
    assert "trading_positions" in sql


def test_filled_order_stays_unresolved_until_aggregate_fills_cover_order_size() -> None:
    sql = str(
        select(TradingOrder.id)
        .where(live_copy_unresolved_order_predicate())
        .compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "sum(trading_fills.size)" in sql
    assert "trading_orders.filled_size" in sql
    assert "1E-12" in sql
    assert "NOT (EXISTS (SELECT trading_fills.id" in sql


def test_multipart_plan_requires_contiguous_unique_sequences() -> None:
    first = source_part(action="flip_close", side="long", start_position=Decimal("1"))
    second = SourceFillPart(
        action="flip_open",
        side="short",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=2,
        start_position=Decimal("0"),
    )

    with pytest.raises(ValueError, match="contiguous"):
        normalize_live_copy_fill_plan_parts((first, second))


def test_source_fill_order_uses_close_priority_and_numeric_fill_ids() -> None:
    close_with_lexically_later_id = {
        "externalFillId": "10",
        "coin": "HYPE",
        "timestampMs": 1_000,
        "rawJson": {"dir": "Close Long", "startPosition": "1"},
    }
    open_with_lexically_earlier_id = {
        "externalFillId": "2",
        "coin": "HYPE",
        "timestampMs": 1_000,
        "rawJson": {"dir": "Open Long", "startPosition": "0"},
    }
    numeric_two = {
        **open_with_lexically_earlier_id,
        "externalFillId": "2",
    }
    numeric_ten = {
        **open_with_lexically_earlier_id,
        "externalFillId": "10",
    }

    assert source_fill_order_key(close_with_lexically_later_id) < source_fill_order_key(
        open_with_lexically_earlier_id
    )
    assert source_fill_order_key(numeric_two) < source_fill_order_key(numeric_ten)


def live_copy_source_state(
    *,
    baseline_source_timestamp_ms: int | None = None,
    baseline_fill_ids: list[str] | None = None,
    preexisting_markets: dict[str, object] | None = None,
) -> LiveCopySourceState:
    return LiveCopySourceState(
        account_key="live_main",
        source_wallet="0xsource",
        account_type="live",
        status="active",
        entry_eligible=True,
        activated_at=datetime(2026, 7, 18, tzinfo=UTC),
        baseline_source_timestamp_ms=baseline_source_timestamp_ms,
        baseline_fill_ids=baseline_fill_ids or [],
        preexisting_markets=preexisting_markets or {},
    )


def source_part(
    *,
    action: str,
    side: str,
    start_position: Decimal,
    close_ratio: Decimal | None = None,
) -> SourceFillPart:
    return SourceFillPart(
        action=action,
        side=side,
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=0,
        start_position=start_position,
        close_ratio=close_ratio,
    )


class BaselineSession:
    def __init__(self, *, scalar_values: list[object], scalar_list_values: list[str]) -> None:
        self.scalar_values = scalar_values
        self.scalar_list_values = scalar_list_values
        self.added: list[object] = []
        self.flush_count = 0

    async def scalar(self, _query):
        return self.scalar_values.pop(0)

    async def scalars(self, _query):
        return SimpleNamespace(all=lambda: list(self.scalar_list_values))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


class ExistingStateBaselineSession:
    def __init__(
        self,
        *,
        scalar_values: list[object],
        scalar_lists: list[list[object]],
    ) -> None:
        self.scalar_values = scalar_values
        self.scalar_lists = scalar_lists
        self.flush_count = 0

    async def scalar(self, _query):
        return self.scalar_values.pop(0)

    async def scalars(self, _query):
        values = self.scalar_lists.pop(0)
        return SimpleNamespace(all=lambda: list(values))

    async def flush(self) -> None:
        self.flush_count += 1


class FlushSession:
    def __init__(self) -> None:
        self.flush_count = 0

    async def flush(self) -> None:
        self.flush_count += 1
