from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.schemas.base import CamelModel


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
