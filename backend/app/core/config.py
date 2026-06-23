import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = BACKEND_ROOT / "config"
APP_CONFIG_PATH = CONFIG_DIR / "app.json"
PRUNE_CONFIG_PATH = CONFIG_DIR / "prune.json"
DISCOVERY_CONFIG_PATH = CONFIG_DIR / "discovery.json"
POOL_FILL_IMPORT_CONFIG_PATH = CONFIG_DIR / "pool_fill_import.json"
DATABASE_CONFIG_PATH = CONFIG_DIR / "database.json"
SCORING_CONFIG_PATH = CONFIG_DIR / "scoring.json"
PAPER_TRADING_CONFIG_PATH = CONFIG_DIR / "paper_trading.json"
DISCOVERY_CONFIG_PATH_MAP: dict[tuple[str, ...], str] = {
    ("enabled",): "discovery_enabled",
    ("sources", "default"): "discovery_default_sources",
    ("sources", "hyperliquid", "sort_metric"): "discovery_hyperliquid_sort_metric",
    ("sources", "hyperliquid", "url"): "discovery_hyperliquid_leaderboard_url",
    ("sources", "hyperliquid", "vaults_url"): "discovery_hyperliquid_vaults_url",
    ("sources", "hyperdash", "urls", "copytrading"): (
        "discovery_hyperdash_copytrading_url"
    ),
    ("sources", "hyperdash", "urls", "cohorts"): (
        "discovery_hyperdash_cohorts_url"
    ),
    ("sources", "hyperdash", "urls", "very_profitable"): (
        "discovery_hyperdash_very_profitable_url"
    ),
    ("sources", "hyperdash", "urls", "extremely_profitable"): (
        "discovery_hyperdash_extremely_profitable_url"
    ),
    ("sources", "hyperdash", "urls", "tagged"): (
        "discovery_hyperdash_tagged_url"
    ),
    ("sources", "hypertracker", "static_base_url"): (
        "discovery_hypertracker_static_base_url"
    ),
    ("import", "limit"): "discovery_import_limit",
    ("import", "interval_seconds"): "discovery_import_interval_seconds",
    ("import", "run_on_worker_start"): "discovery_import_on_worker_start",
    ("import", "subaccounts", "enabled"): "discovery_import_subaccounts_enabled",
    ("import", "subaccounts", "max_per_wallet"): (
        "discovery_import_max_subaccounts_per_wallet"
    ),
    ("prefilter", "enabled"): "discovery_prefilter_enabled",
    ("prefilter", "run_after_import"): "discovery_prefilter_run_after_import",
    ("prefilter", "reject_missing_source_pnl"): (
        "discovery_prefilter_reject_missing_source_pnl"
    ),
    ("prefilter", "require_positive_source_pnl"): (
        "discovery_prefilter_require_positive_source_pnl"
    ),
    ("prefilter", "min_source_pnl_usd"): "discovery_prefilter_min_source_pnl_usd",
    ("prefilter", "min_source_roi"): "discovery_prefilter_min_source_roi",
    ("prefilter", "min_account_value_usd"): (
        "discovery_prefilter_min_account_value_usd"
    ),
    ("prefilter", "max_account_value_usd"): (
        "discovery_prefilter_max_account_value_usd"
    ),
    ("prefilter", "min_copy_score"): "discovery_prefilter_min_copy_score",
    ("prefilter", "max_source_rank"): "discovery_prefilter_max_source_rank",
    ("prefilter", "accept_if_metrics_missing"): (
        "discovery_prefilter_accept_if_metrics_missing"
    ),
    ("candidate_backfill", "days"): "discovery_candidate_backfill_days",
    ("candidate_backfill", "max_pages"): (
        "discovery_candidate_backfill_max_pages"
    ),
    ("candidate_backfill", "target_fills"): (
        "discovery_candidate_backfill_target_fills"
    ),
    ("candidate_backfill", "batch_size"): (
        "discovery_candidate_backfill_batch_size"
    ),
    ("quality", "min_fills"): "discovery_quality_min_fills",
    ("quality", "min_closed_trades"): "discovery_quality_min_closed_trades",
    ("quality", "require_positive_net_pnl"): (
        "discovery_quality_require_positive_net_pnl"
    ),
    ("quality", "min_profit_factor"): "discovery_quality_min_profit_factor",
    ("quality", "min_win_rate"): "discovery_quality_min_win_rate",
    ("quality", "max_drawdown_pct"): "discovery_quality_max_drawdown_pct",
    ("quality", "min_average_trade_notional_usd"): (
        "discovery_quality_min_average_trade_notional_usd"
    ),
    ("quality", "max_average_trade_notional_usd"): (
        "discovery_quality_max_average_trade_notional_usd"
    ),
    ("promotion", "batch_size"): "discovery_promotion_batch_size",
    ("promotion", "require_backfill"): "discovery_promotion_require_backfill",
}
POOL_FILL_IMPORT_CONFIG_PATH_MAP: dict[tuple[str, ...], str] = {
    ("pool_fill_import", "enabled"): "pool_fill_import_enabled",
    ("pool_fill_import", "run_on_worker_start"): "pool_fill_import_run_on_worker_start",
    ("pool_fill_import", "start_delay_seconds"): "pool_fill_import_start_delay_seconds",
    ("pool_fill_import", "interval_seconds"): "pool_fill_import_interval_seconds",
    ("pool_fill_import", "min_wallet_interval_seconds"): (
        "pool_fill_import_min_wallet_interval_seconds"
    ),
    ("pool_fill_import", "batch_size"): "pool_fill_import_batch_size",
    ("pool_fill_import", "max_batches"): "pool_fill_import_max_batches",
    ("pool_fill_import", "days"): "pool_fill_import_days",
    ("pool_fill_import", "max_pages"): "pool_fill_import_max_pages",
    ("pool_fill_import", "overlap_seconds"): "pool_fill_import_overlap_seconds",
    ("fill_import", "storage_guard", "enabled"): "fill_import_storage_guard_enabled",
    ("fill_import", "storage_guard", "min_free_database_mb"): (
        "fill_import_min_free_database_mb"
    ),
    ("fill_import", "market_filter"): "fill_import_market_filter",
    ("fill_import", "raw_json_fields"): "fill_import_raw_json_fields",
}
DATABASE_CONFIG_PATH_MAP: dict[tuple[str, ...], str] = {
    ("fill_retention", "days"): "fill_retention_days",
    ("fill_retention", "batch_size"): "fill_retention_batch_size",
    ("fill_retention", "max_rows"): "fill_retention_max_rows",
    ("fill_retention", "protect_top_score_wallets"): (
        "fill_retention_protect_top_score_wallets"
    ),
}
PRUNE_CONFIG_PATH_MAP: dict[tuple[str, ...], str] = {
    ("rules", "current_drawdown", "unrealized_loss_ratio"): (
        "wallet_prune_unrealized_loss_ratio"
    ),
    ("rules", "current_drawdown", "concurrency"): (
        "wallet_prune_current_state_concurrency"
    ),
    ("rules", "minimum_closed_trades", "min_closed_trades"): (
        "wallet_prune_min_closed_trades"
    ),
    ("rules", "stale_fills", "min_days_without_fill"): (
        "wallet_prune_stale_fill_days"
    ),
    ("rules", "realized_drawdown", "max_drawdown_pct"): (
        "wallet_prune_max_drawdown_pct"
    ),
    ("rules", "low_score", "min_closed_trades"): (
        "wallet_prune_low_score_min_closed_trades"
    ),
    ("rules", "low_score", "threshold"): "wallet_prune_low_score_threshold",
    ("rules", "low_score", "operator"): "wallet_prune_low_score_operator",
    ("schedule", "after_pool_import_enabled"): "wallet_prune_after_pool_import_enabled",
    ("worker", "dry_run"): "wallet_prune_worker_dry_run",
    ("worker", "limit"): "wallet_prune_worker_limit",
}
SCORING_CONFIG_PATH_MAP: dict[tuple[str, ...], str] = {
    ("enabled",): "scoring_enabled",
    ("schedule", "run_on_worker_start"): "scoring_run_on_worker_start",
    ("schedule", "interval_seconds"): "scoring_interval_seconds",
    ("window", "days"): "scoring_window_days",
    ("window", "min_fills"): "scoring_min_fills",
    ("window", "target_fills"): "scoring_target_fills",
    ("window", "min_trades"): "scoring_min_trades",
    ("window", "sample_cap_max_score"): "scoring_sample_cap_max_score",
    ("component_weights", "profitability"): "scoring_weight_pnl",
    ("component_weights", "consistency"): "scoring_weight_consistency",
    ("component_weights", "risk"): "scoring_weight_risk",
    ("component_weights", "copyability"): "scoring_weight_copyability",
    ("component_weights", "recency"): "scoring_weight_recency",
    ("profitability", "weights", "net_roi"): "scoring_profitability_weight_net_roi",
    (
        "profitability",
        "weights",
        "average_trade_roi",
    ): "scoring_profitability_weight_average_trade_roi",
    (
        "profitability",
        "weights",
        "median_trade_roi",
    ): "scoring_profitability_weight_median_trade_roi",
    ("profitability", "roi_score", "full_score_at"): (
        "scoring_profitability_roi_full_score_at"
    ),
    ("profitability", "average_trade_roi", "cap_min"): (
        "scoring_profitability_average_trade_roi_cap_min"
    ),
    ("profitability", "average_trade_roi", "cap_max"): (
        "scoring_profitability_average_trade_roi_cap_max"
    ),
    ("consistency", "weights", "profit_distribution"): (
        "scoring_consistency_weight_profit_distribution"
    ),
    ("consistency", "weights", "largest_win_dependency"): (
        "scoring_consistency_weight_largest_win_dependency"
    ),
    ("consistency", "weights", "trade_roi_stability"): (
        "scoring_consistency_weight_trade_roi_stability"
    ),
    ("consistency", "weights", "downside_stability"): (
        "scoring_consistency_weight_downside_stability"
    ),
    ("consistency", "weights", "active_day_regularity"): (
        "scoring_consistency_weight_active_day_regularity"
    ),
    ("consistency", "weights", "max_inactive_gap"): (
        "scoring_consistency_weight_max_inactive_gap"
    ),
    ("consistency", "profit_distribution", "full_score_ratio"): (
        "scoring_consistency_profit_distribution_full_score_ratio"
    ),
    ("consistency", "largest_win_dependency", "full_score_at_or_below"): (
        "scoring_consistency_largest_win_full_score_at_or_below"
    ),
    ("consistency", "largest_win_dependency", "zero_score_at_or_above"): (
        "scoring_consistency_largest_win_zero_score_at_or_above"
    ),
    ("consistency", "trade_roi_stability", "full_score_stddev_at_or_below"): (
        "scoring_consistency_trade_roi_stddev_full_score_at_or_below"
    ),
    ("consistency", "trade_roi_stability", "zero_score_stddev_at_or_above"): (
        "scoring_consistency_trade_roi_stddev_zero_score_at_or_above"
    ),
    ("consistency", "downside_stability", "full_score_stddev_at_or_below"): (
        "scoring_consistency_downside_stddev_full_score_at_or_below"
    ),
    ("consistency", "downside_stability", "zero_score_stddev_at_or_above"): (
        "scoring_consistency_downside_stddev_zero_score_at_or_above"
    ),
    ("consistency", "active_day_regularity", "full_score_active_day_ratio"): (
        "scoring_consistency_active_day_full_score_ratio"
    ),
    ("consistency", "max_inactive_gap", "full_score_days"): (
        "scoring_consistency_max_inactive_gap_full_score_days"
    ),
    ("consistency", "max_inactive_gap", "zero_score_days"): (
        "scoring_consistency_max_inactive_gap_zero_score_days"
    ),
    ("risk", "loss_ratio", "penalty_per_ratio"): (
        "scoring_risk_loss_ratio_penalty_per_ratio"
    ),
    ("risk", "loss_ratio", "penalty_max"): "scoring_risk_loss_ratio_penalty_max",
    ("risk", "realized_drawdown", "penalty_per_ratio"): (
        "scoring_risk_realized_drawdown_penalty_per_ratio"
    ),
    ("risk", "realized_drawdown", "penalty_max"): (
        "scoring_risk_realized_drawdown_penalty_max"
    ),
    ("risk", "losing_trade_rate", "penalty_per_ratio"): (
        "scoring_risk_losing_trade_rate_penalty_per_ratio"
    ),
    ("risk", "current_drawdown", "enabled"): "scoring_current_drawdown_enabled",
    ("risk", "current_drawdown", "concurrency"): "scoring_current_drawdown_concurrency",
    ("risk", "current_drawdown", "missing_penalty"): (
        "scoring_current_drawdown_missing_penalty"
    ),
    ("risk", "current_drawdown", "penalty_start_ratio"): (
        "scoring_current_drawdown_penalty_start_ratio"
    ),
    ("risk", "current_drawdown", "full_penalty_ratio"): (
        "scoring_current_drawdown_full_penalty_ratio"
    ),
    ("risk", "current_drawdown", "penalty_max"): (
        "scoring_current_drawdown_penalty_max"
    ),
    ("risk", "current_drawdown", "score_cap_start_ratio"): (
        "scoring_current_drawdown_score_cap_start_ratio"
    ),
    ("risk", "current_drawdown", "score_cap_zero_ratio"): (
        "scoring_current_drawdown_score_cap_zero_ratio"
    ),
    ("risk", "open_position_stress", "notional_full_ratio"): (
        "scoring_open_position_stress_notional_full_ratio"
    ),
    ("risk", "open_position_stress", "penalty_max"): (
        "scoring_open_position_stress_penalty_max"
    ),
    ("risk", "forced_exit", "event_gap_seconds"): (
        "scoring_forced_exit_event_gap_seconds"
    ),
    ("risk", "forced_exit", "notional_full_ratio"): (
        "scoring_forced_exit_notional_full_ratio"
    ),
    ("risk", "forced_exit", "penalty_max"): "scoring_forced_exit_penalty_max",
    ("copyability", "weights", "copyable_trade_ratio"): (
        "scoring_copyability_weight_copyable_trade_ratio"
    ),
    ("copyability", "weights", "median_trade_notional"): (
        "scoring_copyability_weight_median_trade_notional"
    ),
    ("copyability", "weights", "p25_trade_notional"): (
        "scoring_copyability_weight_p25_trade_notional"
    ),
    ("copyability", "weights", "execution_simplicity"): (
        "scoring_copyability_weight_execution_simplicity"
    ),
    ("copyability", "weights", "forced_exit_fill_ratio"): (
        "scoring_copyability_weight_forced_exit_fill_ratio"
    ),
    ("copyability", "copyable_trade_ratio", "min_trade_notional_usd"): (
        "scoring_copyability_copyable_trade_min_notional_usd"
    ),
    ("copyability", "trade_notional", "min_full_score_usd"): (
        "scoring_copyability_trade_notional_min_full_score_usd"
    ),
    ("copyability", "trade_notional", "max_full_score_usd"): (
        "scoring_copyability_trade_notional_max_full_score_usd"
    ),
    ("copyability", "trade_notional", "too_large_min_score_usd"): (
        "scoring_copyability_trade_notional_too_large_min_score_usd"
    ),
    ("copyability", "trade_notional", "too_small_max_score"): (
        "scoring_copyability_trade_notional_too_small_max_score"
    ),
    ("copyability", "trade_notional", "too_large_min_score"): (
        "scoring_copyability_trade_notional_too_large_min_score"
    ),
    ("copyability", "execution_simplicity", "full_score_fills_per_trade_at_or_below"): (
        "scoring_copyability_execution_full_score_fills_per_trade_at_or_below"
    ),
    ("copyability", "execution_simplicity", "zero_score_fills_per_trade_at_or_above"): (
        "scoring_copyability_execution_zero_score_fills_per_trade_at_or_above"
    ),
    ("copyability", "forced_exit_fill_ratio", "zero_score_ratio"): (
        "scoring_copyability_forced_exit_fill_ratio_zero_score_ratio"
    ),
    ("recency", "stale_days"): "scoring_stale_days",
    ("penalties", "no_closed_trades"): "scoring_penalty_no_closed_trades",
    ("penalties", "low_sample_max"): "scoring_penalty_low_sample_max",
    ("penalties", "negative_pnl_max"): "scoring_penalty_negative_pnl_max",
    ("penalties", "stale_recency"): "scoring_penalty_stale_recency",
    ("penalties", "open_only"): "scoring_penalty_open_only",
    ("penalties", "confidence", "target_trades"): "scoring_confidence_target_trades",
    ("penalties", "confidence", "max"): "scoring_confidence_penalty_max",
    ("window_scores", "activity_trade_cap"): "scoring_window_score_activity_trade_cap",
    ("window_scores", "weight_profitability"): (
        "scoring_window_score_weight_profitability"
    ),
    ("window_scores", "weight_activity"): "scoring_window_score_weight_activity",
}


class PaperTradingAccountConfig(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    label: str = Field(min_length=1, max_length=120)
    starting_balance_usd: Decimal = Field(gt=0)
    enabled: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_name: str = "Hyperliquid Copy Agent"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "info"
    worker_run_in_api_process: bool = True
    worker_role: Literal["all", "trading", "maintenance"] = "all"
    worker_heartbeat_interval_seconds: int = Field(default=60, ge=10, le=3600)
    worker_heartbeat_stale_seconds: int = Field(default=180, ge=30, le=7200)
    ops_disk_path: str = "/"
    backup_status_enabled: bool = False
    backup_status_directory: str = "/app/backups/postgres"
    backup_status_stale_seconds: int = Field(default=129600, ge=3600, le=2592000)

    database_url: str | None = None
    database_url_direct: str | None = None
    redis_url: str = "redis://redis:6379/0"

    hyperliquid_network: Literal["mainnet", "testnet"] = "testnet"
    hyperliquid_private_key: str | None = None
    hyperliquid_wallet_address: str | None = None
    hyperliquid_info_request_retries: int = Field(default=4, ge=0, le=10)
    hyperliquid_info_retry_base_delay_seconds: float = Field(default=1.5, ge=0, le=60)
    hyperliquid_info_retry_max_delay_seconds: float = Field(default=20.0, ge=0, le=120)
    hyperliquid_info_min_request_interval_seconds: float = Field(default=0.5, ge=0, le=10)

    paper_trading_enabled: bool = True
    live_trading_enabled: bool = False
    live_trading_acknowledged: bool = False
    paper_copy_enabled: bool = True
    paper_copy_accounts: list[PaperTradingAccountConfig] = Field(
        default_factory=lambda: [
            PaperTradingAccountConfig(
                key="paper_1000",
                label="Paper 1,000 USD",
                starting_balance_usd=Decimal("1000"),
            ),
            PaperTradingAccountConfig(
                key="paper_10000",
                label="Paper 10,000 USD",
                starting_balance_usd=Decimal("10000"),
            ),
        ],
        max_length=10,
    )
    paper_copy_top_wallet_count: int = Field(default=10, ge=1, le=10)
    paper_copy_top_tier_wallet_count: int = Field(default=3, ge=0, le=10)
    paper_copy_top_tier_allocation_pct: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    paper_copy_standard_allocation_pct: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    paper_copy_max_total_allocation_pct: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    paper_copy_min_order_notional_usd: Decimal = Field(default=Decimal("5"), ge=0)
    paper_copy_fee_rate: Decimal = Field(default=Decimal("0.0004"), ge=0, le=1)
    paper_copy_slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=10000)
    paper_copy_latency_ms: int = Field(default=750, ge=0, le=60000)
    paper_copy_max_price_drift_bps: Decimal = Field(default=Decimal("20"), ge=0, le=10000)
    paper_copy_use_live_mid_price: bool = True
    paper_copy_recovery_interval_seconds: int = Field(default=60, ge=10, le=3600)

    active_copy_wallets: int = Field(default=10, ge=1, le=10)
    max_realtime_wallets: int = Field(default=10, ge=1, le=10)
    realtime_subscription_refresh_seconds: int = Field(default=300, ge=30)
    realtime_reconnect_seconds: int = Field(default=5, ge=1)
    discovery_enabled: bool = True
    discovery_default_sources: list[str] = Field(
        default_factory=lambda: [
            "hyperliquid_leaderboard_day",
            "hyperliquid_leaderboard_week",
            "hyperliquid_leaderboard_month",
            "hyperliquid_vault_leaders_week",
            "hyperliquid_vault_leaders",
            "hypertracker_money_printer",
            "hypertracker_smart_money",
            "hypertracker_grinder",
            "hypertracker_humble_earner",
            "hypertracker_avg_daily_perp_pnl",
        ],
        max_length=16,
    )
    discovery_import_limit: int = Field(default=100, ge=1, le=500)
    discovery_import_interval_seconds: int = Field(default=3600, ge=3600)
    discovery_import_on_worker_start: bool = True
    discovery_hyperliquid_sort_metric: Literal["pnl", "roi"] = "pnl"
    discovery_hyperliquid_leaderboard_url: str | None = None
    discovery_hyperliquid_vaults_url: str | None = None
    discovery_import_subaccounts_enabled: bool = True
    discovery_import_max_subaccounts_per_wallet: int = Field(default=10, ge=0, le=50)
    discovery_hyperdash_copytrading_url: str | None = None
    discovery_hyperdash_cohorts_url: str | None = None
    discovery_hyperdash_very_profitable_url: str | None = None
    discovery_hyperdash_extremely_profitable_url: str | None = None
    discovery_hyperdash_tagged_url: str | None = None
    discovery_hypertracker_static_base_url: str = (
        "https://dw3ji7n7thadj.cloudfront.net/aggregator"
    )
    discovery_prefilter_enabled: bool = True
    discovery_prefilter_run_after_import: bool = True
    discovery_prefilter_reject_missing_source_pnl: bool = False
    discovery_prefilter_require_positive_source_pnl: bool = True
    discovery_prefilter_min_source_pnl_usd: Decimal | None = None
    discovery_prefilter_min_source_roi: Decimal | None = None
    discovery_prefilter_min_account_value_usd: Decimal | None = None
    discovery_prefilter_max_account_value_usd: Decimal | None = None
    discovery_prefilter_min_copy_score: Decimal | None = None
    discovery_prefilter_max_source_rank: int | None = None
    discovery_prefilter_accept_if_metrics_missing: bool = True
    discovery_candidate_backfill_days: int = Field(default=60, ge=1, le=365)
    discovery_candidate_backfill_max_pages: int = Field(default=5, ge=1, le=50)
    discovery_candidate_backfill_target_fills: int = Field(default=10000, ge=1, le=10000)
    discovery_candidate_backfill_batch_size: int = Field(default=10, ge=1, le=100)
    discovery_quality_min_fills: int = Field(default=20, ge=0)
    discovery_quality_min_closed_trades: int = Field(default=5, ge=0)
    discovery_quality_require_positive_net_pnl: bool = True
    discovery_quality_min_profit_factor: Decimal | None = Decimal("1.2")
    discovery_quality_min_win_rate: Decimal | None = None
    discovery_quality_max_drawdown_pct: Decimal | None = Decimal("0.25")
    discovery_quality_min_average_trade_notional_usd: Decimal | None = Decimal("50")
    discovery_quality_max_average_trade_notional_usd: Decimal | None = Decimal("250000")
    discovery_promotion_batch_size: int = Field(default=25, ge=1, le=250)
    discovery_promotion_require_backfill: bool = True
    pool_fill_import_enabled: bool = True
    pool_fill_import_run_on_worker_start: bool = True
    pool_fill_import_start_delay_seconds: int = Field(default=0, ge=0)
    pool_fill_import_interval_seconds: int = Field(default=600, ge=60)
    pool_fill_import_min_wallet_interval_seconds: int = Field(default=600, ge=60)
    pool_fill_import_batch_size: int = Field(default=50, ge=1, le=100)
    pool_fill_import_max_batches: int = Field(default=200, ge=1, le=1000)
    pool_fill_import_days: int = Field(default=90, ge=1, le=365)
    pool_fill_import_max_pages: int = Field(default=5, ge=1, le=50)
    pool_fill_import_overlap_seconds: int = Field(default=300, ge=0, le=86400)
    wallet_prune_unrealized_loss_ratio: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    wallet_prune_current_state_concurrency: int = Field(default=8, ge=1, le=25)
    wallet_prune_min_closed_trades: int = Field(default=5, ge=0)
    wallet_prune_stale_fill_days: int = Field(default=30, ge=1, le=3650)
    wallet_prune_max_drawdown_pct: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    wallet_prune_low_score_min_closed_trades: int = Field(default=5, ge=0)
    wallet_prune_low_score_threshold: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    wallet_prune_low_score_operator: Literal["lt", "lte", "gt", "gte"] = "lt"
    wallet_prune_after_pool_import_enabled: bool = True
    wallet_prune_worker_dry_run: bool = False
    wallet_prune_worker_limit: int = Field(default=1000, ge=1, le=5000)
    fill_import_storage_guard_enabled: bool = True
    fill_import_min_free_database_mb: int = Field(default=24, ge=0)
    fill_import_market_filter: Literal["all", "perp"] = "perp"
    fill_import_raw_json_fields: list[str] = Field(
        default_factory=lambda: ["dir", "liquidation", "startPosition", "twapId"],
        max_length=16,
    )
    fill_retention_days: int = Field(default=90, ge=61, le=730)
    fill_retention_batch_size: int = Field(default=5000, ge=100, le=25000)
    fill_retention_max_rows: int = Field(default=50000, ge=100, le=250000)
    fill_retention_protect_top_score_wallets: int = Field(default=50, ge=0, le=1000)
    scoring_enabled: bool = True
    scoring_run_on_worker_start: bool = True
    scoring_interval_seconds: int = Field(default=600, ge=60)
    scoring_window_days: int = Field(default=60, ge=1, le=365)
    scoring_min_fills: int = Field(default=20, ge=1)
    scoring_target_fills: int = Field(default=100, ge=1)
    scoring_min_trades: int = Field(default=5, ge=1)
    scoring_stale_days: int = Field(default=7, ge=1)
    scoring_sample_cap_max_score: Decimal = Field(default=Decimal("45"), ge=0, le=100)
    scoring_confidence_target_trades: int = Field(default=50, ge=1)
    scoring_confidence_penalty_max: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    scoring_current_drawdown_enabled: bool = True
    scoring_current_drawdown_concurrency: int = Field(default=8, ge=1, le=25)
    scoring_current_drawdown_missing_penalty: Decimal = Field(
        default=Decimal("18"),
        ge=0,
        le=100,
    )
    scoring_current_drawdown_penalty_start_ratio: Decimal = Field(
        default=Decimal("0.05"),
        ge=0,
        le=1,
    )
    scoring_current_drawdown_full_penalty_ratio: Decimal = Field(
        default=Decimal("0.75"),
        gt=0,
        le=1,
    )
    scoring_current_drawdown_penalty_max: Decimal = Field(
        default=Decimal("100"),
        ge=0,
        le=100,
    )
    scoring_current_drawdown_score_cap_start_ratio: Decimal = Field(
        default=Decimal("0.25"),
        ge=0,
        le=1,
    )
    scoring_current_drawdown_score_cap_zero_ratio: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        le=1,
    )
    scoring_open_position_stress_notional_full_ratio: Decimal = Field(
        default=Decimal("10"),
        gt=0,
        le=100,
    )
    scoring_open_position_stress_penalty_max: Decimal = Field(
        default=Decimal("25"),
        ge=0,
        le=100,
    )
    scoring_forced_exit_event_gap_seconds: int = Field(default=300, ge=1)
    scoring_forced_exit_notional_full_ratio: Decimal = Field(
        default=Decimal("0.25"),
        gt=0,
        le=1,
    )
    scoring_forced_exit_penalty_max: Decimal = Field(default=Decimal("15"), ge=0, le=100)
    scoring_weight_pnl: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    scoring_weight_consistency: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_risk: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_copyability: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_recency: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)
    scoring_profitability_weight_net_roi: Decimal = Field(default=Decimal("0.55"), ge=0, le=1)
    scoring_profitability_weight_average_trade_roi: Decimal = Field(
        default=Decimal("0.30"),
        ge=0,
        le=1,
    )
    scoring_profitability_weight_median_trade_roi: Decimal = Field(
        default=Decimal("0.15"),
        ge=0,
        le=1,
    )
    scoring_profitability_roi_full_score_at: Decimal = Field(
        default=Decimal("0.05"),
        gt=0,
        le=10,
    )
    scoring_profitability_average_trade_roi_cap_min: Decimal = Field(
        default=Decimal("-0.05"),
        ge=-10,
        le=10,
    )
    scoring_profitability_average_trade_roi_cap_max: Decimal = Field(
        default=Decimal("0.10"),
        ge=-10,
        le=10,
    )
    scoring_consistency_weight_profit_distribution: Decimal = Field(
        default=Decimal("0.25"),
        ge=0,
        le=1,
    )
    scoring_consistency_weight_largest_win_dependency: Decimal = Field(
        default=Decimal("0.20"),
        ge=0,
        le=1,
    )
    scoring_consistency_weight_trade_roi_stability: Decimal = Field(
        default=Decimal("0.20"),
        ge=0,
        le=1,
    )
    scoring_consistency_weight_downside_stability: Decimal = Field(
        default=Decimal("0.15"),
        ge=0,
        le=1,
    )
    scoring_consistency_weight_active_day_regularity: Decimal = Field(
        default=Decimal("0.10"),
        ge=0,
        le=1,
    )
    scoring_consistency_weight_max_inactive_gap: Decimal = Field(
        default=Decimal("0.10"),
        ge=0,
        le=1,
    )
    scoring_consistency_profit_distribution_full_score_ratio: Decimal = Field(
        default=Decimal("0.75"),
        gt=0,
        le=1,
    )
    scoring_consistency_largest_win_full_score_at_or_below: Decimal = Field(
        default=Decimal("0.15"),
        ge=0,
        le=1,
    )
    scoring_consistency_largest_win_zero_score_at_or_above: Decimal = Field(
        default=Decimal("0.60"),
        ge=0,
        le=1,
    )
    scoring_consistency_trade_roi_stddev_full_score_at_or_below: Decimal = Field(
        default=Decimal("0.01"),
        ge=0,
        le=10,
    )
    scoring_consistency_trade_roi_stddev_zero_score_at_or_above: Decimal = Field(
        default=Decimal("0.10"),
        gt=0,
        le=10,
    )
    scoring_consistency_downside_stddev_full_score_at_or_below: Decimal = Field(
        default=Decimal("0.005"),
        ge=0,
        le=10,
    )
    scoring_consistency_downside_stddev_zero_score_at_or_above: Decimal = Field(
        default=Decimal("0.05"),
        gt=0,
        le=10,
    )
    scoring_consistency_active_day_full_score_ratio: Decimal = Field(
        default=Decimal("0.50"),
        gt=0,
        le=1,
    )
    scoring_consistency_max_inactive_gap_full_score_days: int = Field(
        default=2,
        ge=0,
        le=365,
    )
    scoring_consistency_max_inactive_gap_zero_score_days: int = Field(
        default=21,
        ge=1,
        le=365,
    )
    scoring_risk_loss_ratio_penalty_per_ratio: Decimal = Field(
        default=Decimal("40"),
        ge=0,
        le=1000,
    )
    scoring_risk_loss_ratio_penalty_max: Decimal = Field(default=Decimal("40"), ge=0, le=100)
    scoring_risk_realized_drawdown_penalty_per_ratio: Decimal = Field(
        default=Decimal("35"),
        ge=0,
        le=1000,
    )
    scoring_risk_realized_drawdown_penalty_max: Decimal = Field(
        default=Decimal("35"),
        ge=0,
        le=100,
    )
    scoring_risk_losing_trade_rate_penalty_per_ratio: Decimal = Field(
        default=Decimal("15"),
        ge=0,
        le=1000,
    )
    scoring_copyability_weight_copyable_trade_ratio: Decimal = Field(
        default=Decimal("0.35"),
        ge=0,
        le=1,
    )
    scoring_copyability_weight_median_trade_notional: Decimal = Field(
        default=Decimal("0.22"),
        ge=0,
        le=1,
    )
    scoring_copyability_weight_p25_trade_notional: Decimal = Field(
        default=Decimal("0.18"),
        ge=0,
        le=1,
    )
    scoring_copyability_weight_execution_simplicity: Decimal = Field(
        default=Decimal("0.10"),
        ge=0,
        le=1,
    )
    scoring_copyability_weight_forced_exit_fill_ratio: Decimal = Field(
        default=Decimal("0.15"),
        ge=0,
        le=1,
    )
    scoring_copyability_copyable_trade_min_notional_usd: Decimal = Field(
        default=Decimal("5"),
        gt=0,
    )
    scoring_copyability_trade_notional_min_full_score_usd: Decimal = Field(
        default=Decimal("50"),
        gt=0,
    )
    scoring_copyability_trade_notional_max_full_score_usd: Decimal = Field(
        default=Decimal("250000"),
        gt=0,
    )
    scoring_copyability_trade_notional_too_large_min_score_usd: Decimal = Field(
        default=Decimal("1000000"),
        gt=0,
    )
    scoring_copyability_trade_notional_too_small_max_score: Decimal = Field(
        default=Decimal("70"),
        ge=0,
        le=100,
    )
    scoring_copyability_trade_notional_too_large_min_score: Decimal = Field(
        default=Decimal("40"),
        ge=0,
        le=100,
    )
    scoring_copyability_execution_full_score_fills_per_trade_at_or_below: Decimal = Field(
        default=Decimal("4"),
        gt=0,
        le=100,
    )
    scoring_copyability_execution_zero_score_fills_per_trade_at_or_above: Decimal = Field(
        default=Decimal("20"),
        gt=0,
        le=100,
    )
    scoring_copyability_forced_exit_fill_ratio_zero_score_ratio: Decimal = Field(
        default=Decimal("0.20"),
        gt=0,
        le=1,
    )
    scoring_penalty_no_closed_trades: Decimal = Field(default=Decimal("100"), ge=0, le=100)
    scoring_penalty_low_sample_max: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    scoring_penalty_negative_pnl_max: Decimal = Field(default=Decimal("30"), ge=0, le=100)
    scoring_penalty_stale_recency: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    scoring_penalty_open_only: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    scoring_window_score_activity_trade_cap: int = Field(default=10, ge=1, le=1000)
    scoring_window_score_weight_profitability: Decimal = Field(
        default=Decimal("0.80"),
        ge=0,
        le=1,
    )
    scoring_window_score_weight_activity: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)

    dashboard_auth_enabled: bool = True
    dashboard_auth_username: str = "admin"
    dashboard_auth_password: str = "change-me"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("cors_origins", mode="before")
    @classmethod
    def normalize_cors_origins(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("fill_import_raw_json_fields", mode="before")
    @classmethod
    def normalize_raw_json_fields(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [field.strip() for field in value.split(",") if field.strip()]
        return value

    @field_validator("discovery_default_sources", mode="before")
    @classmethod
    def normalize_discovery_sources(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            return [source.strip() for source in value.split(",") if source.strip()]
        return value

    @model_validator(mode="after")
    def guard_live_trading(self) -> "Settings":
        account_keys = [account.key for account in self.paper_copy_accounts]
        if len(account_keys) != len(set(account_keys)):
            raise ValueError("paper_copy_accounts keys must be unique.")
        if (
            self.discovery_prefilter_min_account_value_usd is not None
            and self.discovery_prefilter_max_account_value_usd is not None
            and self.discovery_prefilter_min_account_value_usd
            > self.discovery_prefilter_max_account_value_usd
        ):
            raise ValueError(
                "discovery_prefilter_min_account_value_usd must be less than or "
                "equal to discovery_prefilter_max_account_value_usd."
            )
        if (
            self.discovery_quality_min_average_trade_notional_usd is not None
            and self.discovery_quality_max_average_trade_notional_usd is not None
            and self.discovery_quality_min_average_trade_notional_usd
            > self.discovery_quality_max_average_trade_notional_usd
        ):
            raise ValueError(
                "discovery_quality_min_average_trade_notional_usd must be less than "
                "or equal to discovery_quality_max_average_trade_notional_usd."
            )
        if (
            self.scoring_profitability_average_trade_roi_cap_min
            > self.scoring_profitability_average_trade_roi_cap_max
        ):
            raise ValueError(
                "scoring_profitability_average_trade_roi_cap_min must be less than "
                "or equal to scoring_profitability_average_trade_roi_cap_max."
            )
        if (
            self.scoring_consistency_largest_win_full_score_at_or_below
            >= self.scoring_consistency_largest_win_zero_score_at_or_above
        ):
            raise ValueError(
                "scoring_consistency_largest_win_full_score_at_or_below must be "
                "less than scoring_consistency_largest_win_zero_score_at_or_above."
            )
        if (
            self.scoring_consistency_trade_roi_stddev_full_score_at_or_below
            >= self.scoring_consistency_trade_roi_stddev_zero_score_at_or_above
        ):
            raise ValueError(
                "scoring_consistency_trade_roi_stddev_full_score_at_or_below must "
                "be less than scoring_consistency_trade_roi_stddev_zero_score_at_or_above."
            )
        if (
            self.scoring_consistency_downside_stddev_full_score_at_or_below
            >= self.scoring_consistency_downside_stddev_zero_score_at_or_above
        ):
            raise ValueError(
                "scoring_consistency_downside_stddev_full_score_at_or_below must "
                "be less than scoring_consistency_downside_stddev_zero_score_at_or_above."
            )
        if (
            self.scoring_consistency_max_inactive_gap_full_score_days
            >= self.scoring_consistency_max_inactive_gap_zero_score_days
        ):
            raise ValueError(
                "scoring_consistency_max_inactive_gap_full_score_days must be "
                "less than scoring_consistency_max_inactive_gap_zero_score_days."
            )
        if (
            self.scoring_current_drawdown_penalty_start_ratio
            >= self.scoring_current_drawdown_full_penalty_ratio
        ):
            raise ValueError(
                "scoring_current_drawdown_penalty_start_ratio must be less than "
                "scoring_current_drawdown_full_penalty_ratio."
            )
        if (
            self.scoring_current_drawdown_score_cap_start_ratio
            >= self.scoring_current_drawdown_score_cap_zero_ratio
        ):
            raise ValueError(
                "scoring_current_drawdown_score_cap_start_ratio must be less than "
                "scoring_current_drawdown_score_cap_zero_ratio."
            )
        if (
            self.scoring_copyability_trade_notional_min_full_score_usd
            > self.scoring_copyability_trade_notional_max_full_score_usd
        ):
            raise ValueError(
                "scoring_copyability_trade_notional_min_full_score_usd must be less "
                "than or equal to scoring_copyability_trade_notional_max_full_score_usd."
            )
        if (
            self.scoring_copyability_trade_notional_max_full_score_usd
            >= self.scoring_copyability_trade_notional_too_large_min_score_usd
        ):
            raise ValueError(
                "scoring_copyability_trade_notional_too_large_min_score_usd must be "
                "greater than scoring_copyability_trade_notional_max_full_score_usd."
            )
        if (
            self.scoring_copyability_execution_full_score_fills_per_trade_at_or_below
            >= self.scoring_copyability_execution_zero_score_fills_per_trade_at_or_above
        ):
            raise ValueError(
                "scoring_copyability_execution_full_score_fills_per_trade_at_or_below "
                "must be less than "
                "scoring_copyability_execution_zero_score_fills_per_trade_at_or_above."
            )
        if self.live_trading_enabled and not self.live_trading_acknowledged:
            raise ValueError(
                "LIVE_TRADING_ENABLED requires LIVE_TRADING_ACKNOWLEDGED=true. "
                "Live trading must never be enabled accidentally."
            )
        if (
            self.app_env == "production"
            and self.dashboard_auth_enabled
            and self.dashboard_auth_password == "change-me"
        ):
            raise ValueError(
                "DASHBOARD_AUTH_PASSWORD must be changed before production startup."
            )
        if self.app_env == "production" and not self.dashboard_auth_enabled:
            raise ValueError("DASHBOARD_AUTH_ENABLED=false is not allowed in production.")
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        return env_settings, dotenv_settings, init_settings, file_secret_settings

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.cors_origins if origin.strip()]
        if self.app_env != "production":
            expanded = set(origins)
            for origin in origins:
                if "localhost" in origin:
                    expanded.add(origin.replace("localhost", "127.0.0.1"))
                if "127.0.0.1" in origin:
                    expanded.add(origin.replace("127.0.0.1", "localhost"))
            return sorted(expanded)
        return origins

    @property
    def cors_origin_regex(self) -> str | None:
        if self.app_env == "production":
            return None
        return r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"

    @property
    def system_mode(self) -> str:
        if self.live_trading_enabled:
            return "live_small"
        if self.paper_trading_enabled:
            return "paper"
        return "monitor"

    @property
    def hyperliquid_api_url(self) -> str:
        if self.hyperliquid_network == "testnet":
            return "https://api.hyperliquid-testnet.xyz"
        return "https://api.hyperliquid.xyz"

    @property
    def hyperliquid_ws_url(self) -> str:
        if self.hyperliquid_network == "testnet":
            return "wss://api.hyperliquid-testnet.xyz/ws"
        return "wss://api.hyperliquid.xyz/ws"


@lru_cache
def load_app_config() -> dict[str, Any]:
    config: dict[str, Any] = {}
    for config_path in (
        APP_CONFIG_PATH,
        PRUNE_CONFIG_PATH,
        DISCOVERY_CONFIG_PATH,
        POOL_FILL_IMPORT_CONFIG_PATH,
        DATABASE_CONFIG_PATH,
        SCORING_CONFIG_PATH,
        PAPER_TRADING_CONFIG_PATH,
    ):
        config.update(load_json_config(config_path))

    return config


def load_json_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as config_file:
        config = json.load(config_file)

    if not isinstance(config, dict):
        raise ValueError(f"{config_path} must contain a JSON object.")
    if config_path == DISCOVERY_CONFIG_PATH:
        return normalize_nested_config(config, DISCOVERY_CONFIG_PATH_MAP)
    if config_path == POOL_FILL_IMPORT_CONFIG_PATH:
        return normalize_nested_config(config, POOL_FILL_IMPORT_CONFIG_PATH_MAP)
    if config_path == DATABASE_CONFIG_PATH:
        return normalize_nested_config(config, DATABASE_CONFIG_PATH_MAP)
    if config_path == PRUNE_CONFIG_PATH:
        return normalize_nested_config(config, PRUNE_CONFIG_PATH_MAP)
    if config_path == SCORING_CONFIG_PATH:
        return normalize_nested_config(config, SCORING_CONFIG_PATH_MAP)
    return config


def normalize_nested_config(
    config: dict[str, Any],
    path_map: dict[tuple[str, ...], str],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for nested_path, flat_key in path_map.items():
        value = get_nested_config_value(config, nested_path)
        if value is not None:
            normalized[flat_key] = value

    return normalized


def get_nested_config_value(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


@lru_cache
def get_settings() -> Settings:
    return Settings(**load_app_config())
