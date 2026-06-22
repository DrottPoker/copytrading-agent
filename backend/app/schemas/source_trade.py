from decimal import Decimal

from app.schemas.base import CamelModel


class SourceTradeRead(CamelModel):
    id: str
    wallet_address: str
    coin: str
    side: str
    status: str
    opened_at_ms: int
    closed_at_ms: int | None
    duration_ms: int | None
    entry_size: Decimal
    closed_size: Decimal
    remaining_size: Decimal
    entry_notional_usd: Decimal
    close_notional_usd: Decimal
    average_entry_price: Decimal | None
    average_exit_price: Decimal | None
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    net_pnl_usd: Decimal
    entry_fill_count: int
    close_fill_count: int
    has_liquidation: bool = False
    liquidation_fill_count: int = 0
    liquidation_notional_usd: Decimal = Decimal("0")


class SourceTradeSummary(CamelModel):
    closed_trade_count: int
    open_trade_count: int
    unmatched_close_fill_count: int
    preexisting_open_fill_count: int
    total_entry_notional_usd: Decimal
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    net_pnl_usd: Decimal
    liquidation_trade_count: int = 0
    liquidation_notional_usd: Decimal = Decimal("0")


class SourceTradeWindowStats(CamelModel):
    label: str
    closed_trade_count: int
    open_trade_count: int
    entry_notional_usd: Decimal
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    net_pnl_usd: Decimal
    roi_pct: Decimal | None
    win_rate: Decimal | None


class SourceTradeListResponse(CamelModel):
    items: list[SourceTradeRead]
    total: int
    limit: int
    offset: int
    days: int | None
    summary: SourceTradeSummary
    windows: list[SourceTradeWindowStats]
