from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.base import CamelModel


class PaperTradingAccountRead(CamelModel):
    key: str
    label: str
    starting_balance_usd: Decimal
    cash_balance_usd: Decimal
    equity_usd: Decimal
    realized_pnl_usd: Decimal
    unrealized_pnl_usd: Decimal = Decimal("0")
    total_pnl_usd: Decimal = Decimal("0")
    total_pnl_pct: Decimal | None = None
    open_position_count: int = 0
    open_notional_usd: Decimal = Decimal("0")
    open_margin_usd: Decimal = Decimal("0")
    fee_usd: Decimal
    enabled: bool
    created_at: datetime
    updated_at: datetime


class PaperCopyAllocationRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    rank: int
    score: Decimal | None
    allocation_pct: Decimal
    allocation_usd: Decimal
    open_margin_usd: Decimal = Decimal("0")
    remaining_allocation_usd: Decimal = Decimal("0")
    pocket_used_pct: Decimal | None = None
    max_total_allocation_pct: Decimal
    active: bool
    updated_at: datetime


class PaperPositionRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    coin: str
    side: str
    size: Decimal
    entry_price: Decimal
    notional_usd: Decimal
    leverage: Decimal
    margin_usd: Decimal
    realized_pnl_usd: Decimal
    mark_price: Decimal | None = None
    current_notional_usd: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None
    price_updated_at: datetime | None = None
    fee_usd: Decimal
    opened_at: datetime
    created_at: datetime
    updated_at: datetime


class PaperCopyFillRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    source_fill_id: str
    sequence_index: int
    coin: str
    action: str
    side: str | None
    price: Decimal | None
    size: Decimal | None
    notional_usd: Decimal | None
    leverage: Decimal | None
    margin_usd: Decimal | None
    fee_usd: Decimal
    realized_pnl_usd: Decimal
    source_price: Decimal | None
    source_size: Decimal | None
    source_notional_usd: Decimal | None
    source_perp_equity_usd: Decimal | None
    source_account_value_usd: Decimal | None
    source_exposure_pct: Decimal | None
    allocation_pct: Decimal | None
    allocation_usd: Decimal | None
    skipped_reason: str | None
    filled_at: datetime
    created_at: datetime


class PaperTradingPolicyRead(CamelModel):
    enabled: bool
    top_wallet_count: int
    top_tier_wallet_count: int
    top_tier_allocation_pct: Decimal
    standard_allocation_pct: Decimal
    max_total_allocation_pct: Decimal
    min_order_notional_usd: Decimal
    fee_rate: Decimal
    slippage_bps: Decimal
    latency_ms: int
    max_price_drift_bps: Decimal
    use_live_mid_price: bool


class PaperWalletPerformanceRead(CamelModel):
    source_wallet: str
    rank: int | None = None
    score: Decimal | None = None
    allocation_pct: Decimal | None = None
    active: bool
    account_count: int
    open_position_count: int
    copied_fill_count: int
    skipped_fill_count: int
    realized_pnl_usd: Decimal
    unrealized_pnl_usd: Decimal
    total_pnl_usd: Decimal
    fee_usd: Decimal
    open_notional_usd: Decimal
    open_margin_usd: Decimal
    last_fill_at: datetime | None = None


class PaperTradingSummaryResponse(CamelModel):
    policy: PaperTradingPolicyRead
    accounts: list[PaperTradingAccountRead]
    allocations: list[PaperCopyAllocationRead]
    positions: list[PaperPositionRead]
    wallet_performance: list[PaperWalletPerformanceRead]
    recent_fills: list[PaperCopyFillRead]
    updated_at: datetime
    market_data_status: str
