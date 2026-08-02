import pytest
from fastapi import HTTPException

from app.api import routes_operations
from app.schemas.operation import OperationStatusRead
from app.services.operation_status_service import OperationNotRunningError


@pytest.mark.asyncio
async def test_cancel_operation_route_returns_updated_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation = OperationStatusRead(
        key="wallet_scoring",
        label="Wallet pool scoring",
        status="running",
        payload={"cancelRequested": True, "stage": "cancel_requested"},
    )

    async def fake_request_operation_cancellation(
        _session: object,
        *,
        key: str,
    ) -> OperationStatusRead:
        assert key == "wallet_scoring"
        return operation

    monkeypatch.setattr(
        routes_operations,
        "request_operation_cancellation",
        fake_request_operation_cancellation,
    )

    result = await routes_operations.cancel_operation_route(
        "wallet_scoring",
        object(),  # type: ignore[arg-type]
    )

    assert result is operation


@pytest.mark.asyncio
async def test_cancel_operation_route_rejects_finished_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request_operation_cancellation(
        _session: object,
        *,
        key: str,
    ) -> OperationStatusRead:
        raise OperationNotRunningError(f"{key} is not currently running.")

    monkeypatch.setattr(
        routes_operations,
        "request_operation_cancellation",
        fake_request_operation_cancellation,
    )

    with pytest.raises(HTTPException) as error:
        await routes_operations.cancel_operation_route(
            "pool_fill_import",
            object(),  # type: ignore[arg-type]
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_operation_route_rejects_unsupported_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_request_operation_cancellation(
        _session: object,
        *,
        key: str,
    ) -> OperationStatusRead:
        raise ValueError(f"Operation {key} cannot be canceled.")

    monkeypatch.setattr(
        routes_operations,
        "request_operation_cancellation",
        fake_request_operation_cancellation,
    )

    with pytest.raises(HTTPException) as error:
        await routes_operations.cancel_operation_route(
            "wallet_prune",
            object(),  # type: ignore[arg-type]
        )

    assert error.value.status_code == 400
