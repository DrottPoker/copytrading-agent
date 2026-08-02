from typing import Any

import pytest

from app.services.job_lock_service import (
    JOB_LOCK_STALE_AFTER_SECONDS,
    job_lock_is_active,
    try_acquire_job_lock,
)


@pytest.mark.asyncio
async def test_try_acquire_job_lock_can_take_over_stale_renewal() -> None:
    session = CaptureSession(execute_value="wallet_scoring")

    acquired = await try_acquire_job_lock(
        session,  # type: ignore[arg-type]
        key="wallet_scoring",
        owner="new-owner",
        ttl_seconds=1800,
    )

    assert acquired
    assert session.commits == 1
    assert "job_locks.updated_at <=" in session.execute_sql
    assert ":stale_after_seconds" in session.execute_sql
    assert session.execute_params == {
        "key": "wallet_scoring",
        "owner": "new-owner",
        "ttl_seconds": 1800,
        "stale_after_seconds": JOB_LOCK_STALE_AFTER_SECONDS,
    }


@pytest.mark.asyncio
async def test_job_lock_is_active_requires_recent_renewal() -> None:
    session = CaptureSession(scalar_value=True)

    active = await job_lock_is_active(
        session,  # type: ignore[arg-type]
        key="discovery_import",
    )

    assert active
    assert "updated_at >" in session.scalar_sql
    assert ":stale_after_seconds" in session.scalar_sql
    assert session.scalar_params == {
        "key": "discovery_import",
        "stale_after_seconds": JOB_LOCK_STALE_AFTER_SECONDS,
    }


class CaptureResult:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> str | None:
        return self.value


class CaptureSession:
    def __init__(
        self,
        *,
        execute_value: str | None = None,
        scalar_value: bool = False,
    ) -> None:
        self.execute_value = execute_value
        self.scalar_value = scalar_value
        self.execute_sql = ""
        self.execute_params: dict[str, Any] = {}
        self.scalar_sql = ""
        self.scalar_params: dict[str, Any] = {}
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any]) -> CaptureResult:
        self.execute_sql = str(statement)
        self.execute_params = params
        return CaptureResult(self.execute_value)

    async def scalar(self, statement: Any, params: dict[str, Any]) -> bool:
        self.scalar_sql = str(statement)
        self.scalar_params = params
        return self.scalar_value

    async def commit(self) -> None:
        self.commits += 1
