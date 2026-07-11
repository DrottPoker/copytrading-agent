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
from app.db.models import TradingPosition
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
