from datetime import datetime
from decimal import Decimal

from app.schemas.base import CamelModel


class AnalyticsOverview(CamelModel):
    pool_wallet_count: int
    enabled_wallet_count: int
    scored_wallet_count: int
    scoring_coverage_pct: Decimal | None
    average_score: Decimal | None
    active_source_count: int
    open_paper_source_count: int
    open_paper_position_count: int
    paper_realized_pnl_usd: Decimal
    paper_fee_usd: Decimal
    paper_open_margin_usd: Decimal
    paper_skip_rate_pct: Decimal | None


class AnalyticsScoreAverages(CamelModel):
    score: Decimal | None
    profitability_score: Decimal | None
    consistency_score: Decimal | None
    risk_score: Decimal | None
    copyability_score: Decimal | None
    recency_score: Decimal | None
    penalty_score: Decimal | None


class AnalyticsBucket(CamelModel):
    label: str
    count: int
    pct: Decimal | None


class AnalyticsWalletRow(CamelModel):
    wallet_address: str
    label: str | None = None
    pool_rank: int | None = None
    score: Decimal | None = None
    trade_count: int
    copyable_pnl_usd: Decimal
    win_rate: Decimal | None = None
    profit_factor: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    current_drawdown_pct: Decimal | None = None
    margin_stress_pct: Decimal | None = None
    current_drawdown_status: str
    last_seen_fill_at: datetime | None = None


class AnalyticsSourcePerformanceRow(CamelModel):
    source_wallet: str
    source_label: str | None = None
    pool_rank: int | None = None
    score: Decimal | None = None
    closed_trade_count: int
    win_rate: Decimal | None = None
    net_pnl_usd: Decimal
    fee_usd: Decimal
    entry_notional_usd: Decimal
    roi_pct: Decimal | None = None
    average_duration_hours: Decimal | None = None
    last_closed_at: datetime | None = None


class AnalyticsCoinPerformanceRow(CamelModel):
    coin: str
    closed_trade_count: int
    win_rate: Decimal | None = None
    net_pnl_usd: Decimal
    fee_usd: Decimal
    entry_notional_usd: Decimal
    roi_pct: Decimal | None = None
    average_duration_hours: Decimal | None = None


class AnalyticsPaperSourceRow(CamelModel):
    source_wallet: str
    source_label: str | None = None
    copied_fill_count: int
    skipped_fill_count: int
    skip_rate_pct: Decimal | None = None
    realized_pnl_usd: Decimal
    fee_usd: Decimal
    open_position_count: int
    open_margin_usd: Decimal
    last_fill_at: datetime | None = None


class AnalyticsSkipReasonRow(CamelModel):
    reason: str
    count: int
    pct: Decimal | None = None
    last_seen_at: datetime | None = None


class AnalyticsDiscoverySourceRow(CamelModel):
    source: str
    total: int
    discovered: int
    accepted: int
    rejected: int
    promoted: int
    backfill_succeeded: int
    average_roi_pct: Decimal | None = None
    average_account_value_usd: Decimal | None = None
    last_seen_at: datetime | None = None


class AnalyticsFreshness(CamelModel):
    latest_wallet_fill_at: datetime | None = None
    latest_scoring_at: datetime | None = None
    latest_position_snapshot_at: datetime | None = None
    stale_enabled_wallet_count: int
    current_drawdown_unavailable_count: int
    generated_at: datetime


class AnalyticsResponse(CamelModel):
    overview: AnalyticsOverview
    score_averages: AnalyticsScoreAverages
    score_buckets: list[AnalyticsBucket]
    drawdown_status_buckets: list[AnalyticsBucket]
    opportunity_wallets: list[AnalyticsWalletRow]
    risk_watchlist: list[AnalyticsWalletRow]
    source_performance: list[AnalyticsSourcePerformanceRow]
    coin_performance: list[AnalyticsCoinPerformanceRow]
    paper_sources: list[AnalyticsPaperSourceRow]
    skip_reasons: list[AnalyticsSkipReasonRow]
    discovery_sources: list[AnalyticsDiscoverySourceRow]
    freshness: AnalyticsFreshness
