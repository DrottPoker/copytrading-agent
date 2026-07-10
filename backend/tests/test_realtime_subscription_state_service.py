from datetime import UTC, datetime, timedelta

from app.services.realtime_subscription_state_service import (
    parse_realtime_subscription_state,
    realtime_subscription_stale_after_seconds,
)


def test_subscription_state_staleness_tracks_realtime_refresh_cadence() -> None:
    assert realtime_subscription_stale_after_seconds(15) == 45
    assert realtime_subscription_stale_after_seconds(5) == 30


def test_fresh_subscription_state_exposes_only_acknowledged_wallets() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    snapshot = parse_realtime_subscription_state(
        {
            "status": "connecting",
            "desiredWallets": ["0xAAA", "0xBBB"],
            "monitoredWallets": ["0xAAA"],
            "workerRole": "trading",
            "workerInstanceId": "worker-1",
            "updatedAt": now.isoformat(),
        },
        stale_after_seconds=180,
        now=now,
    )

    assert snapshot.status == "connecting"
    assert snapshot.desired_wallets == ("0xaaa", "0xbbb")
    assert snapshot.monitored_wallets == frozenset({"0xaaa"})
    assert snapshot.worker_instance_id == "worker-1"


def test_stale_subscription_state_never_reports_monitored_wallets() -> None:
    now = datetime(2026, 7, 10, 12, tzinfo=UTC)
    snapshot = parse_realtime_subscription_state(
        {
            "status": "connected",
            "desiredWallets": ["0xAAA"],
            "monitoredWallets": ["0xAAA"],
            "updatedAt": (now - timedelta(seconds=181)).isoformat(),
        },
        stale_after_seconds=180,
        now=now,
    )

    assert snapshot.status == "disconnected"
    assert snapshot.desired_wallets == ()
    assert snapshot.monitored_wallets == frozenset()
