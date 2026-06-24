from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import CamelModel


class LiveTradingAccountCreateRequest(CamelModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=120)
    wallet_address: str | None = Field(default=None, max_length=128)
    vault_address: str | None = Field(default=None, max_length=128)
    status: Literal["disabled", "enabled", "exit_only"] = "disabled"


class TradingAccountStatusRequest(CamelModel):
    status: Literal["disabled", "enabled", "exit_only"]


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
    last_reconciled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TradingAccountsResponse(CamelModel):
    accounts: list[TradingAccountRead]
    updated_at: datetime


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


class TestnetLiveOrderRequest(CamelModel):
    account_key: str = Field(min_length=1, max_length=64)
    coin: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    notional_usd: Decimal = Field(gt=0, le=Decimal("1000000"))
    limit_price: Decimal = Field(gt=0)
    leverage: Decimal = Field(default=Decimal("1"), gt=0, le=Decimal("100"))
    reduce_only: bool = False


class LiveOrderSubmitResponse(CamelModel):
    order: TradingOrderRead
    submitted: bool
    updated_at: datetime


class LiveReconciliationResponse(CamelModel):
    account_key: str
    user_address: str
    fetched_fills: int
    inserted_fills: int
    updated_orders: int
    open_positions: int
    removed_positions: int
    reconciled_at: datetime
