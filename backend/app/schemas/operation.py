from typing import Any

from app.schemas.base import CamelModel


class OperationStatusRead(CamelModel):
    key: str
    label: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    last_success_at: str | None = None
    duration_ms: int | None = None
    last_error: str | None = None
    payload: dict[str, Any]


class OperationStatusListResponse(CamelModel):
    items: list[OperationStatusRead]
