from typing import Any

import pytest

from app.schemas.operation import OperationStatusRead
from app.services import operation_status_service


@pytest.mark.asyncio
async def test_cancel_request_survives_progress_and_finishes_canceled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {
        "key": "wallet_scoring",
        "label": "Wallet pool scoring",
        "status": "running",
        "startedAt": "2026-08-02T04:21:00+00:00",
        "updatedAt": "2026-08-02T04:22:00+00:00",
        "payload": {
            "runId": "current-run",
            "progressPercent": 42,
            "stage": "current_drawdown",
        },
    }

    async def fake_write_operation_value(
        _session: object,
        _key: str,
        build_value: operation_status_service.OperationValueBuilder,
    ) -> None:
        nonlocal state
        state = build_value(state)

    async def fake_get_operation_status(
        _session: object,
        key: str,
    ) -> OperationStatusRead:
        return OperationStatusRead(
            key=key,
            label=str(state["label"]),
            status=str(state["status"]),
            started_at=state.get("startedAt"),
            completed_at=state.get("completedAt"),
            updated_at=state.get("updatedAt"),
            duration_ms=state.get("durationMs"),
            payload=dict(state["payload"]),
        )

    monkeypatch.setattr(
        operation_status_service,
        "write_operation_value",
        fake_write_operation_value,
    )
    monkeypatch.setattr(
        operation_status_service,
        "get_operation_status",
        fake_get_operation_status,
    )

    requested = await operation_status_service.request_operation_cancellation(
        object(),  # type: ignore[arg-type]
        key="wallet_scoring",
    )

    assert requested.status == "running"
    assert requested.payload["cancelRequested"] is True
    assert requested.payload["progressPercent"] == 42
    assert requested.payload["stage"] == "cancel_requested"

    await operation_status_service.mark_operation_progress(
        object(),  # type: ignore[arg-type]
        key="wallet_scoring",
        payload={
            "runId": "current-run",
            "progressPercent": 55,
            "stage": "current_drawdown",
        },
    )

    assert state["payload"]["progressPercent"] == 55
    assert state["payload"]["stage"] == "cancel_requested"
    assert state["payload"]["stageLabel"] == "Stopping"

    await operation_status_service.mark_operation_canceled(
        object(),  # type: ignore[arg-type]
        key="wallet_scoring",
        run_id="current-run",
    )

    assert state["status"] == "canceled"
    assert state["payload"]["progressPercent"] == 55
    assert state["payload"]["stage"] == "canceled"
    assert state["lastError"] is None


@pytest.mark.asyncio
async def test_cancellation_request_only_targets_matching_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_load_current_operation_value(
        _session: object,
        _key: str,
    ) -> dict[str, Any]:
        return {
            "status": "running",
            "payload": {
                "runId": "new-run",
                "cancelRequested": True,
            },
        }

    monkeypatch.setattr(
        operation_status_service,
        "load_current_operation_value",
        fake_load_current_operation_value,
    )

    assert not await operation_status_service.operation_cancellation_requested(
        object(),  # type: ignore[arg-type]
        key="wallet_scoring",
        run_id="old-run",
    )
    with pytest.raises(operation_status_service.OperationCanceledError):
        await operation_status_service.raise_if_operation_cancellation_requested(
            object(),  # type: ignore[arg-type]
            key="wallet_scoring",
            run_id="new-run",
        )


@pytest.mark.asyncio
async def test_cancel_rejects_non_running_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_write_operation_value(
        _session: object,
        _key: str,
        build_value: operation_status_service.OperationValueBuilder,
    ) -> None:
        build_value({"status": "succeeded", "payload": {}})

    monkeypatch.setattr(
        operation_status_service,
        "write_operation_value",
        fake_write_operation_value,
    )

    with pytest.raises(operation_status_service.OperationNotRunningError):
        await operation_status_service.request_operation_cancellation(
            object(),  # type: ignore[arg-type]
            key="discovery_import",
        )
