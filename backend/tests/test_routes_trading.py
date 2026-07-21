from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api import routes_trading
from app.api.routes_trading import (
    LIVE_EXCHANGE_SOURCE,
    live_entry_delay_ms,
    load_trading_source_metadata,
    matching_live_entry_delay,
    trading_position_read,
)
from app.core.config import Settings
from app.db.models import LiveCopyFillState, TradingPosition
from app.schemas.trading import LiveCopyDecisionRead
from app.services.paper_trading_service import WalletMonitoringSummary


class MappingRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def mappings(self) -> "MappingRows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


class MetadataSession:
    def __init__(self, results: list[MappingRows]) -> None:
        self.results = results
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> MappingRows:
        self.statements.append(statement)
        return self.results.pop(0)


class ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class TradingAccountsRouteSession:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def scalars(self, statement: Any) -> ScalarRows:
        self.statements.append(statement)
        return ScalarRows(self.rows.pop(0))


def test_live_entry_delay_uses_source_timestamp_to_exchange_fill() -> None:
    source_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    filled_at = source_at + timedelta(milliseconds=742)

    assert (
        live_entry_delay_ms(
            source_timestamp_ms=int(source_at.timestamp() * 1000),
            filled_at=filled_at,
        )
        == 742
    )


def test_matching_live_entry_delay_prefers_latest_entry_before_opened_at() -> None:
    opened_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    entries = [
        (opened_at - timedelta(seconds=10), 1200),
        (opened_at - timedelta(seconds=1), 640),
        (opened_at + timedelta(seconds=5), 500),
    ]

    assert matching_live_entry_delay(entries, opened_at=opened_at) == 640


def test_trading_position_read_exposes_position_pnl_and_fill_counts() -> None:
    opened_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    position = TradingPosition(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet=LIVE_EXCHANGE_SOURCE,
        coin="HYPE",
        side="long",
        size=Decimal("2"),
        entry_price=Decimal("10"),
        notional_usd=Decimal("20"),
        leverage=Decimal("5"),
        margin_usd=Decimal("4"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0.02"),
        raw_payload={
            "position": {
                "szi": "2",
                "entryPx": "10",
                "positionValue": "22",
                "unrealizedPnl": "2.50",
                "returnOnEquity": "0.625",
            }
        },
        opened_at=opened_at,
        last_reconciled_at=opened_at + timedelta(seconds=30),
        created_at=opened_at,
        updated_at=opened_at,
    )

    read = trading_position_read(
        position,
        entry_execution_delay_ms=640,
        fill_metrics=(3, 2, Decimal("1.23")),
    )

    assert read.realized_pnl_usd == Decimal("1.23")
    assert read.unrealized_pnl_usd == Decimal("2.50")
    assert read.add_fill_count == 3
    assert read.close_fill_count == 2
    assert read.entry_execution_delay_ms == 640


def test_live_copy_decision_read_serializes_the_planned_action_and_lifecycle_fields() -> None:
    observed_at = datetime(2026, 1, 1, 12, tzinfo=UTC)

    payload = LiveCopyDecisionRead(
        account_key="live_test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=2,
        coin="HYPE",
        planned_action="flip_open",
        side="long",
        outcome="retryable",
        reason="price_unavailable",
        attempt_count=3,
        origin="realtime",
        source_timestamp_ms=int(observed_at.timestamp() * 1000),
        observed_at=observed_at,
        first_observed_at=observed_at,
        execution_claimed_at=observed_at + timedelta(milliseconds=100),
        processing_started_at=observed_at + timedelta(milliseconds=200),
        decision_at=observed_at + timedelta(seconds=1),
        last_attempt_at=observed_at,
        next_attempt_at=observed_at + timedelta(seconds=30),
        trading_order_id=None,
        updated_at=observed_at,
    ).model_dump(mode="json", by_alias=True)

    assert payload["plannedAction"] == "flip_open"
    assert payload["outcome"] == "retryable"
    assert payload["tradingOrderId"] is None
    assert payload["origin"] == "realtime"
    assert payload["sourceTimestampMs"] == 1767268800000
    assert payload["observedAt"] == "2026-01-01T12:00:00Z"
    assert payload["executionClaimedAt"] == "2026-01-01T12:00:00.100000Z"
    assert payload["processingStartedAt"] == "2026-01-01T12:00:00.200000Z"
    assert payload["decisionAt"] == "2026-01-01T12:00:01Z"
    assert payload["nextAttemptAt"] == "2026-01-01T12:00:30Z"


@pytest.mark.asyncio
async def test_trading_accounts_route_exposes_recent_live_copy_decisions_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    decision = LiveCopyFillState(
        id=uuid4(),
        account_key="live_test",
        account_type="live",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=1,
        expected_part_count=1,
        plan_version=1,
        coin="BTC",
        action="open",
        side="long",
        source_timestamp_ms=int(observed_at.timestamp() * 1000),
        source_order_direction_rank=1,
        source_order_position=Decimal("1"),
        observed_at=observed_at,
        first_observed_at=observed_at,
        execution_claimed_at=observed_at + timedelta(milliseconds=100),
        processing_started_at=observed_at + timedelta(milliseconds=200),
        decision_at=None,
        origin="realtime",
        outcome="retryable",
        reason="price_unavailable",
        attempt_count=2,
        first_seen_at=observed_at,
        last_attempt_at=observed_at,
        next_attempt_at=observed_at + timedelta(seconds=15),
        fill_complete=False,
        trading_order_id=None,
        created_at=observed_at,
        updated_at=observed_at + timedelta(seconds=1),
    )
    session = TradingAccountsRouteSession([[], [], [], [decision], [], []])

    async def empty_list_trading_accounts(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def empty_closed_trades(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    async def empty_entry_delays(*_args: Any, **_kwargs: Any) -> dict[Any, int]:
        return {}

    async def empty_fill_metrics(
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[Any, tuple[int, int, Decimal]]:
        return {}

    async def empty_source_metadata(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(routes_trading, "list_trading_accounts", empty_list_trading_accounts)
    monkeypatch.setattr(routes_trading, "load_live_closed_trades", empty_closed_trades)
    monkeypatch.setattr(
        routes_trading,
        "load_live_position_entry_execution_delays",
        empty_entry_delays,
    )
    monkeypatch.setattr(routes_trading, "load_live_position_fill_metrics", empty_fill_metrics)
    monkeypatch.setattr(routes_trading, "load_trading_source_metadata", empty_source_metadata)

    response = await routes_trading.list_trading_accounts_route(session, Settings())  # type: ignore[arg-type]

    assert response.recent_fills == []
    assert response.recent_orders == []
    assert len(response.recent_live_copy_decisions) == 1
    read = response.recent_live_copy_decisions[0]
    assert read.account_key == "live_test"
    assert read.planned_action == "open"
    assert read.outcome == "retryable"
    assert read.trading_order_id is None
    assert read.origin == "realtime"
    assert read.source_timestamp_ms == int(observed_at.timestamp() * 1000)
    assert read.observed_at == observed_at
    assert read.execution_claimed_at == observed_at + timedelta(milliseconds=100)
    assert read.processing_started_at == observed_at + timedelta(milliseconds=200)
    assert read.decision_at is None

    decision_sql = str(
        session.statements[3].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "FROM live_copy_fill_states" in decision_sql
    assert "live_copy_fill_states.account_type = 'live'" in decision_sql
    assert "ORDER BY coalesce(live_copy_fill_states.decision_at" in decision_sql
    assert "LIMIT 50" in decision_sql


@pytest.mark.asyncio
async def test_live_source_metadata_uses_all_live_fills_and_monitoring_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "0xsource"
    monitored_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    session = MetadataSession(
        [
            MappingRows(
                [
                    {
                        "source_wallet": source,
                        "source_label": "Source wallet",
                        "score": Decimal("91.5"),
                        "pool_rank": 2,
                    }
                ]
            ),
            MappingRows(
                [
                    {
                        "source_wallet": source,
                        "live_realized_pnl_usd": Decimal("0.79"),
                        "live_fill_count": 12,
                    }
                ]
            ),
        ]
    )

    async def fake_monitoring_stats(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            source: WalletMonitoringSummary(
                first_monitored_at=monitored_at,
                current_monitoring_started_at=monitored_at,
                last_monitored_at=monitored_at,
                monitored_seconds=150 * 3600,
            )
        }

    monkeypatch.setattr(routes_trading, "load_wallet_monitoring_stats", fake_monitoring_stats)

    rows = await load_trading_source_metadata(  # type: ignore[arg-type]
        session,
        source_wallets=[source],
        settings=Settings(),
    )

    assert len(rows) == 1
    assert rows[0].live_realized_pnl_usd == Decimal("0.79")
    assert rows[0].live_fill_count == 12
    assert rows[0].monitored_seconds == 150 * 3600
    assert rows[0].first_monitored_at == monitored_at
    performance_sql = str(
        session.statements[1].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "sum(trading_fills.realized_pnl_usd)" in performance_sql
    assert "trading_fills.account_type = 'live'" in performance_sql
    assert "trading_fills.source_wallet IN ('0xsource')" in performance_sql
    assert "GROUP BY trading_fills.source_wallet" in performance_sql
