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
SCORING_CONFIG_PATH = CONFIG_DIR / "scoring.json"
PAPER_TRADING_CONFIG_PATH = CONFIG_DIR / "paper_trading.json"


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

    active_copy_wallets: int = Field(default=10, ge=1, le=10)
    max_realtime_wallets: int = Field(default=10, ge=1, le=10)
    realtime_subscription_refresh_seconds: int = Field(default=300, ge=30)
    realtime_reconnect_seconds: int = Field(default=5, ge=1)
    leaderboard_import_enabled: bool = True
    leaderboard_import_limit: int = Field(default=100, ge=1, le=500)
    leaderboard_import_window: Literal["day", "week", "month", "allTime"] = "month"
    leaderboard_import_sort_metric: Literal["pnl", "roi"] = "pnl"
    leaderboard_import_interval_seconds: int = Field(default=86400, ge=3600)
    leaderboard_import_on_worker_start: bool = True
    leaderboard_import_url: str | None = None
    leaderboard_import_subaccounts_enabled: bool = True
    leaderboard_import_max_subaccounts_per_wallet: int = Field(default=25, ge=0, le=100)
    leaderboard_auto_import_fills_enabled: bool = True
    leaderboard_auto_import_fills_days: int = Field(default=30, ge=1, le=365)
    leaderboard_auto_import_fills_max_pages: int = Field(default=5, ge=1, le=50)
    leaderboard_auto_import_fills_for_unpolled_duplicates: bool = True
    leaderboard_auto_import_fills_for_ranked_wallets: bool = True
    leaderboard_auto_import_fills_overlap_seconds: int = Field(default=300, ge=0, le=86400)
    leaderboard_prune_non_perp_wallets_enabled: bool = True
    discovery_enabled: bool = True
    discovery_default_sources: list[str] = Field(
        default_factory=lambda: [
            "hyperliquid_leaderboard_week",
            "hyperliquid_leaderboard_month",
        ],
        max_length=16,
    )
    discovery_import_limit: int = Field(default=100, ge=1, le=500)
    discovery_import_interval_seconds: int = Field(default=3600, ge=3600)
    discovery_import_on_worker_start: bool = True
    discovery_hyperliquid_sort_metric: Literal["pnl", "roi"] = "pnl"
    discovery_import_subaccounts_enabled: bool = False
    discovery_import_max_subaccounts_per_wallet: int = Field(default=10, ge=0, le=50)
    discovery_hyperdash_copytrading_url: str | None = None
    discovery_hyperdash_cohorts_url: str | None = None
    discovery_hyperdash_tagged_url: str | None = None
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
    discovery_quality_max_ignored_fill_ratio: Decimal | None = Decimal("0.50")
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
    wallet_prune_unrealized_loss_ratio: Decimal = Field(default=Decimal("0.40"), ge=0, le=1)
    wallet_prune_current_state_concurrency: int = Field(default=8, ge=1, le=25)
    wallet_prune_min_closed_trades: int = Field(default=1, ge=0)
    wallet_prune_max_drawdown_pct: Decimal = Field(default=Decimal("0.60"), ge=0, le=1)
    wallet_prune_low_score_min_fills: int = Field(default=5000, ge=0)
    wallet_prune_low_score_threshold: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    wallet_prune_low_score_operator: Literal["lte", "gte"] = "lte"
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
    scoring_enabled: bool = True
    scoring_run_on_worker_start: bool = True
    scoring_interval_seconds: int = Field(default=600, ge=60)
    scoring_window_days: int = Field(default=30, ge=1, le=365)
    scoring_min_fills: int = Field(default=20, ge=1)
    scoring_target_fills: int = Field(default=100, ge=1)
    scoring_min_trades: int = Field(default=5, ge=1)
    scoring_target_trades: int = Field(default=25, ge=1)
    scoring_target_active_days: int = Field(default=10, ge=1)
    scoring_stale_days: int = Field(default=14, ge=1)
    scoring_liquidation_event_gap_seconds: int = Field(default=300, ge=1)
    scoring_liquidation_penalty_per_event: Decimal = Field(default=Decimal("2"), ge=0, le=100)
    scoring_liquidation_penalty_max: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    scoring_current_drawdown_enabled: bool = True
    scoring_current_drawdown_concurrency: int = Field(default=8, ge=1, le=25)
    scoring_current_drawdown_full_penalty_ratio: Decimal = Field(
        default=Decimal("0.40"),
        gt=0,
        le=1,
    )
    scoring_current_drawdown_penalty_max: Decimal = Field(
        default=Decimal("35"),
        ge=0,
        le=100,
    )
    scoring_weight_pnl: Decimal = Field(default=Decimal("0.30"), ge=0, le=1)
    scoring_weight_consistency: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_risk: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_copyability: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    scoring_weight_recency: Decimal = Field(default=Decimal("0.10"), ge=0, le=1)

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
    return config


@lru_cache
def get_settings() -> Settings:
    return Settings(**load_app_config())
