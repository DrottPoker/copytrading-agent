from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelModel


class WalletScoreRead(CamelModel):
    id: UUID
    wallet_address: str
    score: Decimal
    pnl_score: Decimal
    copyability_score: Decimal
    risk_score: Decimal
    consistency_score: Decimal
    recency_score: Decimal
    penalty_score: Decimal
    copyable_pnl_usd: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    max_drawdown_pct: Decimal | None
    current_drawdown_pct: Decimal | None
    trade_count: int
    last_24h_score: Decimal | None
    last_7d_score: Decimal | None
    last_30d_score: Decimal | None
    updated_at: datetime


class WalletScoreListResponse(CamelModel):
    items: list[WalletScoreRead]
    total: int
    limit: int
    offset: int


class WalletScoreRunResponse(CamelModel):
    total_wallets: int
    scored_wallets: int
    skipped_wallets: int
    window_days: int
    min_fills: int
    min_trades: int
    updated_at: datetime


class WalletScorePenaltyItem(CamelModel):
    key: str
    label: str
    value: Decimal
    max_value: Decimal
    active: bool
    detail: str


class WalletScoreDetailResponse(CamelModel):
    wallet_address: str
    window_days: int
    min_trades: int
    fill_count: int
    trade_count: int
    ignored_fill_count: int
    open_trade_count: int
    liquidation_count: int
    liquidation_fill_count: int
    liquidation_event_count: int
    recency_score: Decimal
    net_pnl_usd: Decimal
    gross_profit_usd: Decimal
    current_account_value_usd: Decimal | None
    current_unrealized_pnl_usd: Decimal | None
    current_drawdown_pct: Decimal | None
    penalty_score: Decimal
    penalty_items: list[WalletScorePenaltyItem]
