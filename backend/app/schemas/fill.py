from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel


class WalletFillImportRequest(CamelModel):
    start_time_ms: int | None = Field(default=None, ge=0)
    end_time_ms: int | None = Field(default=None, ge=0)
    days: int = Field(default=30, ge=1, le=365)
    max_pages: int = Field(default=1, ge=1, le=50)
    target_fills: int = Field(default=2000, ge=1, le=10000)
    aggregate_by_time: bool = False


class WalletFillImportResponse(CamelModel):
    wallet_address: str
    fetched: int
    raw_fetched: int
    pages_fetched: int
    inserted: int
    duplicate: int
    target_fills: int
    start_time_ms: int
    end_time_ms: int
    latest_fill_time_ms: int | None


class WalletFillRead(CamelModel):
    id: UUID
    wallet_address: str
    external_fill_id: str
    coin: str
    side: str
    price: Decimal
    size: Decimal
    notional_usd: Decimal | None
    fee_usd: Decimal | None
    pnl_usd: Decimal | None
    timestamp_ms: int
    source_timestamp_ms: int | None
    received_at: datetime
    processed_at: datetime | None
    ingest_latency_ms: int | None
    is_snapshot: bool
    raw_json: dict[str, Any]
    created_at: datetime


class WalletFillListResponse(CamelModel):
    items: list[WalletFillRead]
    total: int
    limit: int
    offset: int
