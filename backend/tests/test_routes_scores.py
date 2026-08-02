from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks

from app.api import routes_scores
from app.core.config import Settings
from app.schemas.operation import OperationStatusRead


def scoring_operation(
    *,
    status: str = "running",
    stage: str = "historical_metrics",
    updated_at: datetime | None = None,
) -> OperationStatusRead:
    timestamp = (updated_at or datetime.now(UTC)).isoformat()
    return OperationStatusRead(
        key="wallet_scoring",
        label="Wallet pool scoring",
        status=status,
        started_at=timestamp,
        updated_at=timestamp,
        payload={"stage": stage},
    )


@pytest.mark.asyncio
async def test_scoring_start_keeps_active_locked_run(monkeypatch: pytest.MonkeyPatch) -> None:
    current = scoring_operation(updated_at=datetime.now(UTC) - timedelta(hours=1))

    async def fake_get_operation_status(*_args: object, **_kwargs: object) -> OperationStatusRead:
        return current

    async def fake_job_lock_is_active(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(routes_scores, "get_operation_status", fake_get_operation_status)
    monkeypatch.setattr(routes_scores, "job_lock_is_active", fake_job_lock_is_active)

    background_tasks = BackgroundTasks()
    result = await routes_scores.start_recalculate_scores_route(
        background_tasks,
        object(),  # type: ignore[arg-type]
        Settings(_env_file=None),
        False,
    )

    assert result is current
    assert background_tasks.tasks == []


@pytest.mark.asyncio
async def test_scoring_start_recovers_stale_running_status_without_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "operation": scoring_operation(updated_at=datetime.now(UTC) - timedelta(hours=1)),
    }

    async def fake_get_operation_status(*_args: object, **_kwargs: object) -> OperationStatusRead:
        return state["operation"]

    async def fake_job_lock_is_active(*_args: object, **_kwargs: object) -> bool:
        return False

    async def fake_mark_operation_started(
        *_args: object,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> None:
        state["operation"] = scoring_operation(stage=str(payload["stage"]))

    monkeypatch.setattr(routes_scores, "get_operation_status", fake_get_operation_status)
    monkeypatch.setattr(routes_scores, "job_lock_is_active", fake_job_lock_is_active)
    monkeypatch.setattr(routes_scores, "mark_operation_started", fake_mark_operation_started)

    background_tasks = BackgroundTasks()
    result = await routes_scores.start_recalculate_scores_route(
        background_tasks,
        object(),  # type: ignore[arg-type]
        Settings(_env_file=None),
        False,
    )

    assert result.payload["stage"] == "queued"
    assert len(background_tasks.tasks) == 1


def test_recently_queued_scoring_start_uses_short_race_grace() -> None:
    now = datetime.now(UTC)

    assert routes_scores.scoring_start_is_recently_queued(
        scoring_operation(stage="queued", updated_at=now - timedelta(seconds=10)),
        now=now,
    )
    assert not routes_scores.scoring_start_is_recently_queued(
        scoring_operation(stage="queued", updated_at=now - timedelta(minutes=2)),
        now=now,
    )
