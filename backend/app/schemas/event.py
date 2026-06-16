from typing import Any

from app.schemas.base import CamelModel


class LiveEvent(CamelModel):
    id: str
    type: str
    channel: str
    message: str
    payload: dict[str, Any]
    created_at: str


class LiveEventListResponse(CamelModel):
    items: list[LiveEvent]
    total: int
