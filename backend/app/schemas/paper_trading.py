from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel


class PaperTradingAccountCreateRequest(CamelModel):
    account_type: Literal["paper", "live"] = "paper"
    starting_balance_usd: Decimal = Field(gt=0, le=Decimal("1000000000"))


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
    source_label: str | None = None
    rank: int
    pool_rank: int | None = None
    score: Decimal | None
    allocation_pct: Decimal
    allocation_usd: Decimal
    open_margin_usd: Decimal = Decimal("0")
    remaining_allocation_usd: Decimal = Decimal("0")
    pocket_used_pct: Decimal | None = None
    max_total_allocation_pct: Decimal
    active: bool
    has_realtime_slot: bool = False
    is_realtime_monitored: bool = False
    can_open_new_positions: bool = False
    monitor_status: str = "waiting"
    source_status: str = "waiting_for_slot"
    source_status_reason: str = "unknown"
    updated_at: datetime


class PaperPositionRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    source_label: str | None = None
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
    add_fill_count: int = 0
    close_fill_count: int = 0
    opened_at: datetime
    entry_execution_delay_ms: int
    created_at: datetime
    updated_at: datetime


class PaperCopyFillRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    source_label: str | None = None
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
    observed_price: Decimal | None
    execution_price: Decimal | None
    price_drift_bps: Decimal | None
    price_source: str | None
    max_price_drift_bps: Decimal | None
    source_size: Decimal | None
    source_notional_usd: Decimal | None
    source_perp_equity_usd: Decimal | None
    source_account_value_usd: Decimal | None
    source_exposure_pct: Decimal | None
    allocation_pct: Decimal | None
    allocation_usd: Decimal | None
    skipped_reason: str | None
    min_order_adjusted: bool = False
    original_notional_usd: Decimal | None = None
    adjusted_notional_usd: Decimal | None = None
    min_order_notional_usd: Decimal | None = None
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
    adjust_small_orders_to_min_order: bool
    fee_rate: Decimal
    slippage_bps: Decimal
    latency_ms: int
    max_price_drift_bps: Decimal
    use_live_mid_price: bool
    market_price_cache_enabled: bool
    market_price_cache_stale_seconds: float


class RealtimeMonitoringRead(CamelModel):
    status: str = "disconnected"
    desired_wallets: list[str] = Field(default_factory=list)
    monitored_wallets: list[str] = Field(default_factory=list)
    worker_role: str | None = None
    worker_instance_id: str | None = None
    updated_at: datetime | None = None


class PaperWalletPerformanceRead(CamelModel):
    source_wallet: str
    source_label: str | None = None
    rank: int | None = None
    pool_rank: int | None = None
    score: Decimal | None = None
    allocation_pct: Decimal | None = None
    active: bool
    monitor_status: str = "history"
    account_count: int
    open_position_count: int
    copied_fill_count: int
    skipped_fill_count: int
    realized_pnl_usd: Decimal
    unrealized_pnl_usd: Decimal
    total_pnl_usd: Decimal
    monitored_seconds: int = 0
    monitored_hours: Decimal = Decimal("0")
    realized_pnl_per_monitored_hour_usd: Decimal | None = None
    total_pnl_per_monitored_hour_usd: Decimal | None = None
    first_monitored_at: datetime | None = None
    current_monitoring_started_at: datetime | None = None
    last_monitored_at: datetime | None = None
    fee_usd: Decimal
    open_notional_usd: Decimal
    open_margin_usd: Decimal
    last_fill_at: datetime | None = None


class PaperClosedTradeRead(CamelModel):
    id: UUID
    account_key: str
    source_wallet: str
    source_label: str | None = None
    source_fill_id: str
    coin: str
    close_type: str
    side: str | None
    exit_price: Decimal | None
    size: Decimal | None
    notional_usd: Decimal | None
    leverage: Decimal | None
    margin_usd: Decimal | None
    fee_usd: Decimal
    realized_pnl_usd: Decimal
    net_pnl_usd: Decimal
    is_source_liquidation: bool = False
    opened_at: datetime | None = None
    closed_at: datetime
    duration_ms: int | None = None
    created_at: datetime


class PaperTradingSummaryResponse(CamelModel):
    policy: PaperTradingPolicyRead
    accounts: list[PaperTradingAccountRead]
    allocations: list[PaperCopyAllocationRead]
    positions: list[PaperPositionRead]
    wallet_performance: list[PaperWalletPerformanceRead]
    closed_trades: list[PaperClosedTradeRead]
    recent_fills: list[PaperCopyFillRead]
    realtime_monitoring: RealtimeMonitoringRead
    updated_at: datetime
    market_data_status: str
