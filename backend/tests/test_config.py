from decimal import Decimal
from pathlib import Path

import pytest

from app.core.config import Settings, load_app_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _clear_live_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HYPERLIQUID_NETWORK",
        "HYPERLIQUID_PRIVATE_KEY",
        "HYPERLIQUID_WALLET_ADDRESS",
        "LIVE_TRADING_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "test-dashboard-password")


def test_live_trading_config_is_loaded_from_dedicated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.hyperliquid_network == "mainnet"
    assert settings.live_trading_enabled is False
    assert settings.live_trading_capital_mode == "unified"
    assert settings.live_trading_limit_slippage_bps == Decimal("20")
    assert settings.live_trading_min_order_notional_usd == Decimal("10")
    assert settings.live_trading_min_order_notional_buffer_usd == Decimal("0.1")
    assert settings.live_trading_max_weekly_loss_pct == Decimal("0.5")
    assert settings.live_trading_max_orders_per_minute == 50
    assert settings.live_trading_reduce_only_when_stopped is True
    assert settings.live_trading_reconciliation_enabled is True
    assert settings.live_trading_reconciliation_interval_seconds == 30
    assert settings.live_trading_reconciliation_lookback_minutes == 120
    assert settings.live_trading_reconciliation_max_snapshot_age_seconds == 90
    assert settings.live_trading_entry_intent_ttl_seconds == 30


def test_repository_defaults_start_without_live_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env_overrides(monkeypatch)

    settings = Settings(_env_file=None, **load_app_config())

    assert settings.live_trading_enabled is False
    assert settings.hyperliquid_private_key is None
    assert settings.hyperliquid_wallet_address is None


def test_app_config_owns_worker_ops_and_backup_status_settings() -> None:
    config = load_app_config()

    assert config["worker_heartbeat_interval_seconds"] == 60
    assert config["worker_capability_lease_ttl_seconds"] == 90
    assert config["realtime_execution_queue_size"] == 1000
    assert config["ops_disk_path"] == "/"
    assert config["backup_status_enabled"] is True
    assert config["backup_status_directory"] == "/app/backups/postgres"
    assert config["backup_status_stale_seconds"] == 129600
    assert config["dashboard_auth_enabled"] is True


def test_env_example_contains_only_deployment_specific_values() -> None:
    allowed_keys = {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "DATABASE_URL_DIRECT",
        "REDIS_URL",
        "HYPERLIQUID_PRIVATE_KEY",
        "HYPERLIQUID_WALLET_ADDRESS",
        "LIVE_TRADING_ENABLED",
        "DASHBOARD_AUTH_USERNAME",
        "DASHBOARD_AUTH_PASSWORD",
        "DASHBOARD_DOMAIN",
    }
    configured_keys: set[str] = set()
    for raw_line in (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        candidate = raw_line.strip()
        if candidate.startswith("# "):
            candidate = candidate[2:].strip()
        if not candidate or "=" not in candidate:
            continue
        configured_keys.add(candidate.split("=", 1)[0].strip())

    assert configured_keys == allowed_keys


def test_production_dashboard_auth_requires_configured_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "too-short")
    settings = Settings(app_env="production")
    assert settings.dashboard_auth_password == "too-short"

    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", " ")
    with pytest.raises(ValueError, match="DASHBOARD_AUTH_USERNAME must not be empty"):
        Settings(app_env="production")

    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "admin")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "change-me")
    with pytest.raises(
        ValueError,
        match="DASHBOARD_AUTH_PASSWORD must be changed before production startup",
    ):
        Settings(app_env="production")


def test_app_config_loads_wallet_pool_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.wallet_pool_page_limit == 300


def test_shared_trading_config_is_loaded_from_dedicated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()

    assert config["trading_copy_top_wallet_count"] == 10
    assert config["trading_copy_top_tier_wallet_count"] == 3
    assert Decimal(str(config["trading_copy_top_tier_allocation_pct"])) == Decimal("0.25")
    assert Decimal(str(config["trading_copy_standard_allocation_pct"])) == Decimal("0.25")
    assert Decimal(str(config["trading_copy_max_total_allocation_pct"])) == Decimal("0.8")
    assert Decimal(str(config["trading_copy_min_order_notional_usd"])) == Decimal("10")
    assert config["trading_copy_adjust_small_orders_to_min_order"] is True
    assert config["trading_copy_max_entry_age_seconds"] == 15
    assert Decimal(str(config["trading_copy_max_price_drift_bps"])) == Decimal("50")
    assert config["trading_copy_use_live_mid_price"] is True
    assert config["trading_copy_market_price_cache_enabled"] is True
    assert config["trading_copy_market_price_cache_stale_seconds"] == 2
    assert config["trading_copy_market_price_cache_refresh_seconds"] == 1
    assert config["trading_copy_market_price_cache_dexes"] == []
    assert "paper_copy_top_wallet_count" not in config
    assert "paper_copy_max_total_allocation_pct" not in config
    assert "paper_copy_min_order_notional_usd" not in config
    assert "paper_copy_max_entry_age_seconds" not in config


def test_prune_config_loads_low_score_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.wallet_prune_low_score_min_closed_trades == 40
    assert settings.wallet_prune_low_score_threshold == Decimal("70")
    assert settings.wallet_prune_low_score_operator == "lt"


def test_paper_config_does_not_seed_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert "paper_copy_accounts" not in config
    assert settings.paper_copy_accounts == []
    assert settings.paper_copy_recovery_interval_seconds == 15
    assert settings.realtime_subscription_refresh_seconds == 15
