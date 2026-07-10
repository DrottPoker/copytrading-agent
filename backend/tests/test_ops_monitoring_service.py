from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.job_lock_service import job_lock_renewal_interval_seconds
from app.services.ops_monitoring_service import (
    aggregate_status,
    get_backup_status,
    parse_worker_heartbeat,
)
from app.services.worker_heartbeat_service import (
    build_worker_heartbeat_value,
    worker_heartbeat_key,
)


def test_disabled_backup_status_is_neutral() -> None:
    status = get_backup_status(
        enabled=False,
        directory="/app/backups/postgres",
        stale_after_seconds=129600,
        now=datetime(2026, 6, 23, tzinfo=UTC),
    )

    assert status.status == "disabled"
    assert status.backup_count == 0
    assert status.latest_file is None
    assert status.note == "Backup status monitoring is disabled."


def test_job_lock_renewal_interval_is_bounded() -> None:
    assert job_lock_renewal_interval_seconds(9) == 5
    assert job_lock_renewal_interval_seconds(1800) == 300
    assert job_lock_renewal_interval_seconds(90) == 30


def test_worker_heartbeat_value_includes_runtime_identity_and_health_payload() -> None:
    observed_at = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    value = build_worker_heartbeat_value(
        role="trading",
        trading_loops=True,
        maintenance_loops=False,
        started_at=observed_at - timedelta(minutes=5),
        observed_at=observed_at,
        hostname="worker-host",
        pid=42,
        runtime_payload={
            "instanceId": "instance-a",
            "capabilities": ["trading", "trading", ""],
            "loops": {
                "realtime_monitor": {
                    "status": "running",
                    "restartCount": 1,
                }
            },
            "realtimeQueue": {"depth": 2, "capacity": 10, "dropped": 0},
            "role": "maintenance",
        },
    )

    assert value["key"] == "worker_heartbeat:trading:instance-a"
    assert value["role"] == "trading"
    assert value["instanceId"] == "instance-a"
    assert value["capabilities"] == ["trading"]
    assert value["loops"] == {"realtime_monitor": {"status": "running", "restartCount": 1}}
    assert value["realtimeQueue"] == {"depth": 2, "capacity": 10, "dropped": 0}
    assert worker_heartbeat_key("trading") == "worker_heartbeat:trading"


def test_parse_worker_heartbeat_exposes_loop_and_queue_health() -> None:
    now = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    heartbeat = parse_worker_heartbeat(
        {
            "key": "worker_heartbeat:trading:instance-a",
            "role": "trading",
            "instanceId": "instance-a",
            "hostname": "worker-host",
            "pid": 42,
            "tradingLoops": True,
            "maintenanceLoops": False,
            "capabilities": ["trading"],
            "startedAt": (now - timedelta(minutes=5)).isoformat(),
            "updatedAt": (now - timedelta(seconds=10)).isoformat(),
            "loops": {
                "live_reconciliation": {
                    "status": "restarting",
                    "restartCount": 2,
                    "consecutiveFailures": 1,
                    "lastError": "temporary failure",
                    "lastStartedAt": (now - timedelta(minutes=1)).isoformat(),
                    "lastProgressAt": (now - timedelta(seconds=30)).isoformat(),
                    "updatedAt": (now - timedelta(seconds=20)).isoformat(),
                },
                "realtime_monitor": {
                    "status": "running",
                    "restartCount": 0,
                    "consecutiveFailures": 0,
                    "lastError": None,
                    "lastStartedAt": (now - timedelta(minutes=5)).isoformat(),
                    "lastProgressAt": (now - timedelta(seconds=5)).isoformat(),
                    "updatedAt": (now - timedelta(seconds=5)).isoformat(),
                },
            },
            "realtimeQueue": {"depth": 3, "capacity": 4, "dropped": 2},
        },
        now=now,
        stale_after_seconds=180,
    )

    assert heartbeat.status == "warning"
    assert heartbeat.instance_id == "instance-a"
    assert heartbeat.capabilities == ["trading"]
    assert [loop.name for loop in heartbeat.loops] == [
        "live_reconciliation",
        "realtime_monitor",
    ]
    assert heartbeat.loops[0].health == "warning"
    assert heartbeat.loops[0].last_error == "temporary failure"
    assert heartbeat.loops[1].health == "ok"
    assert heartbeat.realtime_queue is not None
    assert heartbeat.realtime_queue.status == "warning"
    assert heartbeat.realtime_queue.utilization_pct == Decimal("0.75")


def test_failed_worker_loop_degrades_fresh_heartbeat() -> None:
    now = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    heartbeat = parse_worker_heartbeat(
        {
            "key": "worker_heartbeat:maintenance:instance-b",
            "updatedAt": now.isoformat(),
            "loops": {"wallet_maintenance": {"status": "failed"}},
        },
        now=now,
        stale_after_seconds=180,
    )

    assert heartbeat.role == "maintenance"
    assert heartbeat.instance_id == "instance-b"
    assert heartbeat.status == "degraded"


def test_legacy_worker_heartbeat_remains_compatible() -> None:
    now = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    heartbeat = parse_worker_heartbeat(
        {
            "key": "worker_heartbeat:all",
            "tradingLoops": True,
            "maintenanceLoops": True,
            "updatedAt": now.isoformat(),
        },
        now=now,
        stale_after_seconds=180,
    )

    assert heartbeat.status == "ok"
    assert heartbeat.instance_id is None
    assert heartbeat.capabilities == ["trading", "maintenance"]
    assert heartbeat.loops == []
    assert heartbeat.realtime_queue is None


def test_degraded_worker_degrades_aggregate_ops_status() -> None:
    ok = SimpleNamespace(status="ok")
    status = aggregate_status(
        postgres_status={"status": "ok"},
        redis_status={"status": "ok"},
        disk=ok,
        memory=ok,
        load=ok,
        backup=SimpleNamespace(status="disabled"),
        database=ok,
        workers=[SimpleNamespace(status="degraded")],
    )

    assert status == "degraded"
