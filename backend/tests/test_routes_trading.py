from datetime import UTC, datetime, timedelta

from app.api.routes_trading import live_entry_delay_ms, matching_live_entry_delay


def test_live_entry_delay_uses_source_timestamp_to_exchange_fill() -> None:
    source_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    filled_at = source_at + timedelta(milliseconds=742)

    assert live_entry_delay_ms(
        source_timestamp_ms=int(source_at.timestamp() * 1000),
        filled_at=filled_at,
    ) == 742


def test_matching_live_entry_delay_prefers_latest_entry_before_opened_at() -> None:
    opened_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    entries = [
        (opened_at - timedelta(seconds=10), 1200),
        (opened_at - timedelta(seconds=1), 640),
        (opened_at + timedelta(seconds=5), 500),
    ]

    assert matching_live_entry_delay(entries, opened_at=opened_at) == 640
