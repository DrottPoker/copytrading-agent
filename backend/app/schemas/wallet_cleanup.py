from app.schemas.base import CamelModel


class ZeroFillWalletCandidate(CamelModel):
    address: str
    label: str | None
    fill_count: int
    score: str | None = None
    last_polled_at: str | None
    last_seen_fill_at: str | None


class ZeroFillWalletPruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    deleted_wallets: int
    deleted_fills: int
    items: list[ZeroFillWalletCandidate]


class OrphanFillWalletCandidate(CamelModel):
    address: str
    label: str | None = None
    fill_count: int
    score: str | None = None
    last_seen_fill_at: str | None


class OrphanFillPruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    deleted_wallets: int
    deleted_fills: int
    items: list[OrphanFillWalletCandidate]


class CurrentDrawdownWalletCandidate(CamelModel):
    address: str
    label: str | None
    score: str | None = None
    perp_equity_usd: str | None = None
    account_value_usd: str | None = None
    total_unrealized_pnl_usd: str | None = None
    unrealized_loss_ratio: str | None = None
    open_position_count: int = 0
    top_position_coin: str | None = None
    top_position_unrealized_pnl_usd: str | None = None
    top_position_value_usd: str | None = None
    error: str | None = None


class CurrentDrawdownPruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    errored_wallets: int = 0
    deleted_wallets: int
    deleted_fills: int
    threshold_ratio: str
    items: list[CurrentDrawdownWalletCandidate]


class MinClosedTradesWalletCandidate(CamelModel):
    address: str
    label: str | None
    fill_count: int
    closed_trade_count: int
    score: str | None = None
    last_polled_at: str | None
    last_seen_fill_at: str | None


class MinClosedTradesPruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    deleted_wallets: int
    deleted_fills: int
    min_closed_trades: int
    items: list[MinClosedTradesWalletCandidate]


class MaxDrawdownWalletCandidate(CamelModel):
    address: str
    label: str | None
    fill_count: int
    closed_trade_count: int
    score: str | None = None
    max_drawdown_pct: str
    last_polled_at: str | None
    last_seen_fill_at: str | None


class MaxDrawdownPruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    deleted_wallets: int
    deleted_fills: int
    threshold_pct: str
    items: list[MaxDrawdownWalletCandidate]


class LowScoreWalletCandidate(CamelModel):
    address: str
    label: str | None
    fill_count: int
    closed_trade_count: int
    score: str
    last_polled_at: str | None
    last_seen_fill_at: str | None


class LowScorePruneResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    deleted_wallets: int
    deleted_fills: int
    min_closed_trades: int
    score_threshold: str
    score_operator: str
    items: list[LowScoreWalletCandidate]


class WalletPruneCandidate(CamelModel):
    address: str
    label: str | None
    fill_count: int | None = None
    closed_trade_count: int | None = None
    score: str | None = None
    max_drawdown_pct: str | None = None
    last_polled_at: str | None = None
    last_seen_fill_at: str | None = None
    perp_equity_usd: str | None = None
    account_value_usd: str | None = None
    total_unrealized_pnl_usd: str | None = None
    detail: str | None = None
    error: str | None = None


class WalletPruneRuleResult(CamelModel):
    key: str
    label: str
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    errored_wallets: int = 0
    deleted_wallets: int
    deleted_fills: int
    rule: str
    items: list[WalletPruneCandidate]


class WalletPruneAllResponse(CamelModel):
    dry_run: bool
    scanned_wallets: int
    candidate_wallets: int
    errored_wallets: int = 0
    deleted_wallets: int
    deleted_fills: int
    rules: list[WalletPruneRuleResult]
