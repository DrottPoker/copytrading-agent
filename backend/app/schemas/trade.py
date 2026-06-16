from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelModel


class CopyTradeRead(CamelModel):
    id: UUID
    mode: str
    source_wallet: str
    coin: str
    side: str
    status: str
    source_entry_price: Decimal | None
    our_entry_price: Decimal | None
    source_exit_price: Decimal | None
    our_exit_price: Decimal | None
    size_usd: Decimal
    risk_usd: Decimal | None
    pnl_usd: Decimal | None
    pnl_pct: Decimal | None
    entry_signal_id: UUID | None
    exit_signal_id: UUID | None
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CopyTradeListResponse(CamelModel):
    items: list[CopyTradeRead]
    total: int
    limit: int
    offset: int
