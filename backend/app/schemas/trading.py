from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class LiveTradingAccountCreateRequest(CamelModel):
    key: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    label: str = Field(min_length=1, max_length=120)
    wallet_address: str | None = Field(default=None, max_length=128)
    vault_address: str | None = Field(default=None, max_length=128)
    status: Literal["disabled"] = "disabled"

    @field_validator("key", "wallet_address", "vault_address", mode="before")
    @classmethod
    def blank_string_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class TradingAccountStatusRequest(CamelModel):
    status: Literal["disabled", "enabled", "exit_only"]


class TradingCapitalBalanceRead(CamelModel):
    key: str
    label: str
    equity_usd: Decimal
    available_usd: Decimal | None = None
    tradable: bool = False
    stale: bool = False
    error: str | None = None


class TradingAccountRead(CamelModel):
    key: str
    account_type: Literal["paper", "live"]
    label: str
    status: Literal["disabled", "enabled", "exit_only"]
    network: Literal["mainnet", "testnet"]
    wallet_address: str | None
    vault_address: str | None
    starting_balance_usd: Decimal | None
    cash_balance_usd: Decimal | None
    equity_usd: Decimal | None
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    funding_usd: Decimal = Decimal("0")
    last_reconciled_at: datetime | None
    lifecycle_version: int = 0
    status_changed_at: datetime | None = None
    status_reason: str | None = None
    archived_at: datetime | None = None
    capital_mode: Literal["unified", "standard_per_dex"] | None = None
    user_abstraction: str | None = None
    tradable_equity_usd: Decimal | None = None
    perp_equity_usd: Decimal | None = None
    spot_usdc_balance_usd: Decimal | None = None
    spot_usdc_available_usd: Decimal | None = None
    capital_balances: list[TradingCapitalBalanceRead] = Field(default_factory=list)
    reconciliation_status: Literal["never", "complete", "partial", "failed"] = "never"
    reconciliation_attempted_at: datetime | None = None
    incomplete_reconciliation_components: list[str] = Field(default_factory=list)
    reconciliation_errors: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TradingPositionRead(CamelModel):
    id: UUID
    account_key: str
    account_type: Literal["paper", "live"]
    source_wallet: str
    coin: str
    side: Literal["long", "short"]
    size: Decimal
    entry_price: Decimal
    notional_usd: Decimal
    leverage: Decimal
    margin_mode: Literal["cross", "isolated"]
    margin_usd: Decimal
    current_notional_usd: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl_usd: Decimal | None = None
    unrealized_pnl_pct: Decimal | None = None
    price_updated_at: datetime | None = None
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    funding_usd: Decimal = Decimal("0")
    add_fill_count: int = 0
    close_fill_count: int = 0
    opened_at: datetime
    entry_execution_delay_ms: int | None = None
    last_reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("margin_mode", mode="before")
    @classmethod
    def legacy_position_margin_mode_defaults_to_cross(cls, value: object) -> object:
        return "cross" if value is None else value


class TradingFillRead(CamelModel):
    id: UUID
    order_id: UUID | None
    account_key: str
    account_type: Literal["paper", "live"]
    source_wallet: str
    source_fill_id: str | None
    sequence_index: int | None
    exchange_fill_id: str | None
    coin: str
    action: str
    side: Literal["long", "short"]
    price: Decimal
    size: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    realized_pnl_usd: Decimal
    filled_at: datetime
    created_at: datetime


class TradingOrderRead(CamelModel):
    id: UUID
    account_key: str
    account_type: Literal["paper", "live"]
    source_wallet: str
    source_fill_id: str
    sequence_index: int
    client_order_id: str
    exchange_order_id: str | None
    coin: str
    action: str
    side: str
    is_buy: bool
    reduce_only: bool
    order_type: str
    status: str
    requested_size: Decimal
    requested_notional_usd: Decimal
    margin_usd: Decimal | None
    leverage: Decimal | None
    margin_mode: Literal["cross", "isolated"]
    limit_price: Decimal | None
    average_fill_price: Decimal | None
    filled_size: Decimal
    filled_notional_usd: Decimal
    fee_usd: Decimal
    error: str | None
    submitted_at: datetime | None
    accepted_at: datetime | None
    filled_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator("margin_mode", mode="before")
    @classmethod
    def legacy_order_margin_mode_defaults_to_cross(cls, value: object) -> object:
        return "cross" if value is None else value


class LiveCopyDecisionRead(CamelModel):
    account_key: str
    source_wallet: str
    source_fill_id: str
    sequence_index: int
    coin: str
    planned_action: Literal["open", "add", "reduce", "close", "flip_close", "flip_open"]
    side: Literal["long", "short"]
    outcome: Literal["pending", "retryable", "order", "terminal_skip", "baseline_ignored"]
    reason: str | None = None
    attempt_count: int
    origin: Literal["realtime", "snapshot_recovery", "startup_recovery", "periodic_recovery"]
    source_timestamp_ms: int
    observed_at: datetime | None = None
    first_observed_at: datetime | None = None
    execution_claimed_at: datetime | None = None
    processing_started_at: datetime | None = None
    decision_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    trading_order_id: UUID | None = None
    order_record_id: UUID | None = None
    logical_order_status: str | None = None
    logical_order_error: str | None = None
    latest_dispatch_attempt_number: int | None = None
    latest_dispatch_client_order_id: str | None = None
    latest_dispatch_status: str | None = None
    latest_exchange_status: str | None = None
    latest_exchange_error_code: str | None = None
    latest_exchange_error_message: str | None = None
    latest_exchange_response: dict[str, Any] | None = None
    submit_attempt_count: int = 0
    status_lookup_count: int = 0
    last_status_lookup_at: datetime | None = None
    last_status_lookup_error: str | None = None
    updated_at: datetime


class TradingClosedTradeRead(CamelModel):
    id: str
    account_key: str
    source_wallet: str
    source_label: str | None = None
    coin: str
    side: Literal["long", "short"]
    entry_price: Decimal | None = None
    exit_price: Decimal | None = None
    size: Decimal
    entry_notional_usd: Decimal
    exit_notional_usd: Decimal
    fee_usd: Decimal
    funding_usd: Decimal
    realized_pnl_usd: Decimal
    net_pnl_usd: Decimal
    opened_at: datetime
    closed_at: datetime
    duration_ms: int | None = None
    open_fill_count: int
    close_fill_count: int


class TradingSourceMetadataRead(CamelModel):
    source_wallet: str
    source_label: str | None = None
    rank: int | None = None
    pool_rank: int | None = None
    score: Decimal | None = None
    allocation_pct: Decimal | None = None
    live_realized_pnl_usd: Decimal = Decimal("0")
    live_fill_count: int = 0
    monitored_seconds: int = 0
    first_monitored_at: datetime | None = None
    current_monitoring_started_at: datetime | None = None
    last_monitored_at: datetime | None = None


class LiveRiskLimitsRead(CamelModel):
    max_weekly_loss_pct: Decimal
    max_orders_per_minute: int
    reconciliation_max_snapshot_age_seconds: int
    entry_intent_ttl_seconds: int
    reduce_only_when_stopped: bool


class TradingAccountsResponse(CamelModel):
    accounts: list[TradingAccountRead]
    live_trading_enabled: bool = False
    risk_limits: LiveRiskLimitsRead
    positions: list[TradingPositionRead] = Field(default_factory=list)
    recent_fills: list[TradingFillRead] = Field(default_factory=list)
    recent_orders: list[TradingOrderRead] = Field(default_factory=list)
    recent_live_copy_decisions: list[LiveCopyDecisionRead] = Field(default_factory=list)
    closed_trades: list[TradingClosedTradeRead] = Field(default_factory=list)
    source_metadata: list[TradingSourceMetadataRead] = Field(default_factory=list)
    updated_at: datetime


class TestnetLiveOrderRequest(CamelModel):
    account_key: str = Field(min_length=1, max_length=64)
    coin: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    notional_usd: Decimal = Field(gt=0, le=Decimal("1000000"))
    limit_price: Decimal = Field(gt=0)
    leverage: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("100"))
    margin_mode: Literal["cross", "isolated"] = "cross"
    reduce_only: bool = False


class LiveOrderSubmitResponse(CamelModel):
    order: TradingOrderRead
    submitted: bool
    updated_at: datetime


class LiveReconciliationResponse(CamelModel):
    account_key: str
    user_address: str
    run_id: UUID | None = None
    fetched_fills: int
    inserted_fills: int
    fetched_funding_payments: int = 0
    inserted_funding_payments: int = 0
    updated_orders: int
    open_positions: int
    removed_positions: int
    status: Literal["complete", "partial"]
    incomplete_components: list[str] = Field(default_factory=list)
    component_errors: dict[str, str] = Field(default_factory=dict)
    reconciled_at: datetime


class LiveCloseAllResponse(CamelModel):
    account_key: str
    operation_id: UUID
    operation_status: Literal[
        "pending",
        "running",
        "partially_completed",
        "completed",
        "failed",
    ]
    submitted_orders: int
    failed_orders: int
    status: Literal["disabled", "enabled", "exit_only"]
    updated_at: datetime
