from datetime import datetime
from decimal import Decimal

from pydantic import Field

from app.schemas.base import CamelModel


class DatabaseConnectionStats(CamelModel):
    total: int
    active: int
    idle: int
    idle_in_transaction: int
    max_connections: int | None
    usage_pct: Decimal | None


class DatabaseTableStats(CamelModel):
    name: str
    estimated_rows: int
    dead_rows: int
    table_size_bytes: int
    index_size_bytes: int
    total_size_bytes: int
    seq_scan_count: int
    index_scan_count: int
    last_vacuum_at: datetime | None
    last_autovacuum_at: datetime | None
    last_analyze_at: datetime | None
    last_autoanalyze_at: datetime | None


class DatabaseIndexStats(CamelModel):
    table_name: str
    index_name: str
    index_size_bytes: int
    index_scan_count: int
    tuples_read: int
    tuples_fetched: int
    is_unique: bool
    is_primary: bool


class DatabaseWalletStats(CamelModel):
    total: int
    enabled: int
    eligible: int
    copy_enabled: int
    unpolled: int
    stale_24h: int
    last_polled_at: datetime | None
    last_seen_fill_at: datetime | None
    tiers: dict[str, int]


class DatabaseFillStats(CamelModel):
    exact: bool = True
    total: int
    snapshot: int
    realtime: int
    wallet_count: int
    pool_wallet_count: int
    orphan_wallet_count: int
    coin_count: int
    total_notional_usd: Decimal
    total_fee_usd: Decimal
    total_pnl_usd: Decimal
    first_fill_time_ms: int | None
    last_fill_time_ms: int | None
    last_inserted_at: datetime | None


class DatabaseScoreStats(CamelModel):
    scored_wallets: int
    average_score: Decimal | None
    best_score: Decimal | None
    zero_or_negative: int
    above_70: int
    last_scored_at: datetime | None


class DatabaseCopyTradeStats(CamelModel):
    total: int
    open: int
    closed: int
    error: int
    total_size_usd: Decimal
    total_pnl_usd: Decimal
    last_created_at: datetime | None
    statuses: dict[str, int]
    modes: dict[str, int]


class DatabaseSignalStats(CamelModel):
    total: int
    copy_count: int = Field(alias="copy")
    skip: int
    exit: int
    observe: int
    last_created_at: datetime | None


class DatabaseOperationalStats(CamelModel):
    active_copy_wallets: int
    realtime_slots_used: int
    active_copy_statuses: dict[str, int]
    source_trade_links: int
    risk_events: int
    audit_logs: int
    settings: int


class DatabaseStatsResponse(CamelModel):
    measured_at: datetime
    database_name: str
    database_size_bytes: int
    database_size_pretty: str
    table_count: int
    connections: DatabaseConnectionStats
    wallets: DatabaseWalletStats
    fills: DatabaseFillStats
    scores: DatabaseScoreStats
    copy_trades: DatabaseCopyTradeStats
    signals: DatabaseSignalStats
    operational: DatabaseOperationalStats
    tables: list[DatabaseTableStats]
    indexes: list[DatabaseIndexStats]


class FillRawJsonCompactResponse(CamelModel):
    dry_run: bool
    candidate_fills: int
    processed_fills: int
    remaining_candidates: int | None
    before_raw_json_bytes: int
    after_raw_json_bytes: int
    saved_raw_json_bytes: int
    kept_fields: list[str]
    batch_size: int
    max_rows: int
    note: str


class FillRetentionCleanupResponse(CamelModel):
    dry_run: bool
    retention_days: int
    cutoff_time_ms: int
    protected_wallets: int
    candidate_fills: int
    candidate_wallets: int
    candidate_source_trades: int
    candidate_ignored_fills: int
    deleted_fills: int
    deleted_source_trades: int
    deleted_ignored_fills: int
    affected_wallets: int
    remaining_candidate_fills: int | None
    batch_size: int
    max_rows: int
    protect_top_score_wallets: int
    note: str


class IgnoredFillCleanupResponse(CamelModel):
    dry_run: bool
    min_age_days: int
    cutoff_time_ms: int
    candidate_fills: int
    candidate_wallets: int
    candidate_preexisting_open_fills: int
    candidate_unmatched_close_fills: int
    excluded_potential_trade_close_fills: int
    deleted_fills: int
    deleted_ignored_fill_markers: int
    affected_wallets: int
    remaining_candidate_fills: int | None
    max_rows: int
    note: str
