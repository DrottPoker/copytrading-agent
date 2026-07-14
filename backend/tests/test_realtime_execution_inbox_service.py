from app.services.realtime_execution_inbox_service import (
    realtime_execution_retry_delay_seconds,
)
from app.services.realtime_fill_service import StoredRealtimeFills


def test_stored_realtime_fills_execution_payload_round_trip() -> None:
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=2,
        inserted=1,
        duplicate=1,
        is_snapshot=False,
        latest_fill_time_ms=123,
        inserted_rows=[{"externalFillId": "fill-1", "coin": "HYPE"}],
    )

    restored = StoredRealtimeFills.from_execution_payload(
        stored.execution_payload(),
        inbox_id="inbox-1",
    )

    assert restored == StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=2,
        inserted=1,
        duplicate=1,
        is_snapshot=False,
        latest_fill_time_ms=123,
        inserted_rows=[{"externalFillId": "fill-1", "coin": "HYPE"}],
        execution_rows=[{"externalFillId": "fill-1", "coin": "HYPE"}],
        inbox_id="inbox-1",
    )


def test_execution_payload_preserves_duplicate_rows_for_idempotent_execution() -> None:
    execution_row = {"externalFillId": "duplicate-fill", "coin": "HYPE"}
    stored = StoredRealtimeFills(
        wallet_address="0xsource",
        fetched=1,
        inserted=0,
        duplicate=1,
        is_snapshot=False,
        latest_fill_time_ms=456,
        inserted_rows=[],
        execution_rows=[execution_row],
    )

    restored = StoredRealtimeFills.from_execution_payload(
        stored.execution_payload(),
        inbox_id="inbox-2",
    )

    assert restored.inserted_rows == []
    assert restored.rows_for_execution == [execution_row]
    assert restored.inserted == 0
    assert restored.duplicate == 1


def test_legacy_execution_payload_falls_back_to_inserted_rows() -> None:
    restored = StoredRealtimeFills.from_execution_payload(
        {
            "walletAddress": "0xsource",
            "fetched": 1,
            "inserted": 1,
            "duplicate": 0,
            "isSnapshot": False,
            "latestFillTimeMs": 789,
            "insertedRows": [{"externalFillId": "legacy-fill", "coin": "HYPE"}],
        },
        inbox_id="legacy-inbox",
    )

    assert restored.rows_for_execution == [{"externalFillId": "legacy-fill", "coin": "HYPE"}]


def test_realtime_execution_retry_delay_is_bounded() -> None:
    assert realtime_execution_retry_delay_seconds(1, base_seconds=5) == 5
    assert realtime_execution_retry_delay_seconds(2, base_seconds=5) == 10
    assert realtime_execution_retry_delay_seconds(100, base_seconds=5) == 300
