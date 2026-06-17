from decimal import Decimal

from app.schemas.base import CamelModel


class WalletWindowStats(CamelModel):
    label: str
    fill_count: int
    notional_usd: Decimal
    pnl_usd: Decimal
    fee_usd: Decimal


class WalletCoinStats(CamelModel):
    coin: str
    fill_count: int
    buy_count: int
    sell_count: int
    notional_usd: Decimal
    pnl_usd: Decimal
    fee_usd: Decimal
    last_fill_time_ms: int | None


class WalletPerpPositionStats(CamelModel):
    coin: str
    side: str
    size: Decimal
    entry_price: Decimal | None
    position_value_usd: Decimal | None
    unrealized_pnl_usd: Decimal | None
    return_on_equity: Decimal | None
    margin_used_usd: Decimal | None
    liquidation_price: Decimal | None
    leverage_type: str | None
    leverage_value: int | None


class WalletSpotBalanceStats(CamelModel):
    coin: str
    token: int | None
    total: Decimal
    hold: Decimal
    entry_notional_usd: Decimal


class WalletCurrentStateStats(CamelModel):
    state_time_ms: int | None
    perp_equity_usd: Decimal
    account_value_usd: Decimal
    withdrawable_usd: Decimal
    total_position_notional_usd: Decimal
    total_margin_used_usd: Decimal
    total_unrealized_pnl_usd: Decimal
    open_position_count: int
    spot_balance_count: int
    spot_entry_notional_usd: Decimal
    spot_usdc_balance: Decimal
    positions: list[WalletPerpPositionStats]
    spot_balances: list[WalletSpotBalanceStats]
    error: str | None = None


class WalletStatsResponse(CamelModel):
    wallet_address: str
    fill_count: int
    snapshot_fill_count: int
    realtime_fill_count: int
    unique_coin_count: int
    buy_count: int
    sell_count: int
    profitable_fill_count: int
    losing_fill_count: int
    win_rate: Decimal | None
    total_notional_usd: Decimal
    average_fill_notional_usd: Decimal
    total_pnl_usd: Decimal
    total_fee_usd: Decimal
    average_ingest_latency_ms: Decimal | None
    max_ingest_latency_ms: int | None
    first_fill_time_ms: int | None
    last_fill_time_ms: int | None
    windows: list[WalletWindowStats]
    top_coins: list[WalletCoinStats]
    current_state: WalletCurrentStateStats | None
