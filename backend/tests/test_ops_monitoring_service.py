from datetime import UTC, datetime

from app.services.job_lock_service import job_lock_renewal_interval_seconds
from app.services.ops_monitoring_service import get_backup_status


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
