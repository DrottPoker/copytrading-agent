from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.db.models import LiveCopyWork, WalletFill
from app.services.live_copy_state_service import LIVE_COPY_ORIGIN_REALTIME
from app.services.live_copy_work_service import (
    LIVE_COPY_WORK_PENDING,
    limited_error,
    live_copy_work_claim_query,
    live_copy_work_record,
    live_copy_work_retry_delay_seconds,
)


def test_live_copy_work_record_preserves_canonical_source_fill_order() -> None:
    source_wallet = "0xsource"
    fills = [
        wallet_fill(
            source_wallet,
            "10",
            direction="Close Long",
            start_position="1",
        ),
        wallet_fill(
            source_wallet,
            "9",
            direction="Close Long",
            start_position="3",
        ),
        wallet_fill(
            source_wallet,
            "2",
            direction="Open Long",
            start_position="0",
        ),
    ]

    records = [live_copy_work_record(fill, origin=LIVE_COPY_ORIGIN_REALTIME) for fill in fills]
    ordered = sorted(
        records,
        key=lambda record: (
            record["source_timestamp_ms"],
            record["coin"],
            record["source_order_direction_rank"],
            record["source_order_position"],
            0 if record["source_order_fill_id_numeric"] is not None else 1,
            record["source_order_fill_id_numeric"] or Decimal("0"),
            record["source_fill_id"],
        ),
    )

    assert [record["source_fill_id"] for record in ordered] == ["9", "10", "2"]
    assert ordered[0]["source_order_direction_rank"] == 0
    assert ordered[0]["source_order_position"] == Decimal("-3")
    assert ordered[2]["source_order_direction_rank"] == 1


def test_claim_query_uses_postgres_skip_locked_and_earlier_source_barrier() -> None:
    sql = str(
        live_copy_work_claim_query(
            now=datetime(2026, 7, 19, 12, tzinfo=UTC),
            stale_before=datetime(2026, 7, 19, 12, tzinfo=UTC) - timedelta(minutes=1),
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "live_copy_work_1.wallet_address = live_copy_work.wallet_address" in sql
    assert "live_copy_work_1.status IN ('pending', 'processing')" in sql
    assert (
        "live_copy_work_1.source_order_fill_id_numeric "
        "< live_copy_work.source_order_fill_id_numeric"
    ) in sql
    assert "live_copy_work.status = 'pending'" in sql
    assert "live_copy_work.status = 'processing'" in sql


def test_live_copy_work_model_matches_postgres_enqueue_contract() -> None:
    table = LiveCopyWork.__table__
    assert {"wallet_fill_id", "wallet_address", "source_fill_id"} <= set(table.c.keys())
    assert table.c.status.server_default is not None
    assert table.c.available_at.server_default is not None
    assert table.c.wallet_fill_id.foreign_keys
    assert next(iter(table.c.wallet_fill_id.foreign_keys)).ondelete == "CASCADE"

    unique_constraints = {constraint.name for constraint in table.constraints}
    assert "ux_live_copy_work_wallet_fill" in unique_constraints
    assert "ux_live_copy_work_wallet_source_fill" in unique_constraints
    assert {index.name for index in table.indexes} >= {
        "ix_live_copy_work_claim",
        "ix_live_copy_work_wallet_order",
    }


def test_live_copy_work_retry_delay_is_bounded_and_errors_are_limited() -> None:
    assert live_copy_work_retry_delay_seconds(1, base_seconds=5) == 5
    assert live_copy_work_retry_delay_seconds(2, base_seconds=5) == 10
    assert live_copy_work_retry_delay_seconds(100, base_seconds=5) == 300
    assert live_copy_work_retry_delay_seconds(1, base_seconds=0) == 1
    assert limited_error("x" * 2_001) == "x" * 2_000
    assert limited_error(ValueError()) == "ValueError"
    assert LIVE_COPY_WORK_PENDING == "pending"


def wallet_fill(
    wallet_address: str,
    external_fill_id: str,
    *,
    direction: str,
    start_position: str,
) -> WalletFill:
    return WalletFill(
        id=uuid4(),
        wallet_address=wallet_address,
        external_fill_id=external_fill_id,
        coin="HYPE",
        side="buy",
        price=Decimal("10"),
        size=Decimal("1"),
        timestamp_ms=1_000,
        raw_json={"dir": direction, "startPosition": start_position},
    )
