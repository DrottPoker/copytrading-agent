from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.core.config import Settings, load_app_config, mainnet_live_entry_arming_error


def _clear_live_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HYPERLIQUID_NETWORK",
        "HYPERLIQUID_PRIVATE_KEY",
        "HYPERLIQUID_WALLET_ADDRESS",
        "LIVE_TRADING_ENABLED",
        "LIVE_TRADING_ACKNOWLEDGED",
        "LIVE_TRADING_MAINNET_ACKNOWLEDGED",
        "LIVE_TRADING_COPY_ENABLED",
        "LIVE_TRADING_ALLOWED_COINS",
        "LIVE_TRADING_BLOCKED_COINS",
        "LIVE_TRADING_MAINNET_ARMING_TOKEN",
        "LIVE_TRADING_MAINNET_ARMED_AT",
        "LIVE_TRADING_MAINNET_ARMED_UNTIL",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "test-dashboard-password")


def test_live_trading_config_is_loaded_from_dedicated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.hyperliquid_network == "mainnet"
    assert settings.live_trading_enabled is False
    assert settings.live_trading_acknowledged is False
    assert settings.live_trading_mainnet_acknowledged is False
    assert settings.live_trading_capital_mode == "unified"
    assert settings.live_trading_copy_enabled is False
    assert settings.live_trading_limit_slippage_bps == Decimal("20")
    assert settings.live_trading_min_order_notional_usd == Decimal("10")
    assert settings.live_trading_min_order_notional_buffer_usd == Decimal("0.1")
    assert settings.live_trading_max_order_notional_usd == Decimal("100")
    assert settings.live_trading_max_account_open_notional_usd == Decimal("500")
    assert settings.live_trading_max_open_positions == 5
    assert settings.live_trading_max_daily_loss_usd == Decimal("50")
    assert settings.live_trading_max_weekly_loss_usd == Decimal("150")
    assert settings.live_trading_max_leverage == Decimal("5")
    assert settings.live_trading_max_orders_per_minute == 10
    assert settings.live_trading_reduce_only_when_stopped is True
    assert settings.live_trading_reconciliation_enabled is True
    assert settings.live_trading_reconciliation_interval_seconds == 30
    assert settings.live_trading_reconciliation_lookback_minutes == 120
    assert settings.live_trading_reconciliation_max_snapshot_age_seconds == 90
    assert settings.live_trading_entry_intent_ttl_seconds == 30
    assert settings.live_trading_allowed_coins == []
    assert settings.live_trading_blocked_coins == []


def test_repository_defaults_start_without_live_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_live_env_overrides(monkeypatch)

    settings = Settings(_env_file=None, **load_app_config())

    assert settings.live_trading_enabled is False
    assert settings.live_trading_copy_enabled is False
    assert settings.hyperliquid_private_key is None
    assert settings.hyperliquid_wallet_address is None


def test_live_trading_max_leverage_must_match_exchange_integer_semantics() -> None:
    with pytest.raises(ValueError, match="live_trading_max_leverage must be a whole number"):
        Settings(live_trading_max_leverage=Decimal("2.5"))


def test_production_dashboard_auth_requires_nontrivial_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "too-short")
    with pytest.raises(ValueError, match="at least 16 characters") as error:
        Settings(app_env="production")
    assert "too-short" not in str(error.value)

    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", " ")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "long-enough-password")
    with pytest.raises(ValueError, match="DASHBOARD_AUTH_USERNAME must not be empty"):
        Settings(app_env="production")


def test_mainnet_entry_arming_window_is_limited_to_24_hours() -> None:
    now = datetime(2026, 7, 9, 12, tzinfo=UTC)
    settings = Settings()
    settings.hyperliquid_network = "mainnet"
    settings.live_trading_mainnet_arming_token = "ARM_MAINNET_LIVE_TRADING"
    settings.live_trading_mainnet_armed_at = now
    settings.live_trading_mainnet_armed_until = now + timedelta(hours=25)

    assert mainnet_live_entry_arming_error(settings, now=now) == (
        "Mainnet live entry arming cannot last more than 24 hours."
    )

    settings.live_trading_mainnet_armed_until = now + timedelta(hours=1)
    assert mainnet_live_entry_arming_error(settings, now=now) is None


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
    assert Decimal(str(config["trading_copy_top_tier_allocation_pct"])) == Decimal("0.2")
    assert Decimal(str(config["trading_copy_standard_allocation_pct"])) == Decimal("0.2")
    assert Decimal(str(config["trading_copy_max_total_allocation_pct"])) == Decimal("0.8")
    assert Decimal(str(config["trading_copy_min_order_notional_usd"])) == Decimal("10")
    assert config["trading_copy_adjust_small_orders_to_min_order"] is True
    assert config["trading_copy_max_entry_age_seconds"] == 15
    assert config["trading_copy_stale_entry_skip_activity_seconds"] == 300
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

    assert settings.wallet_prune_low_score_min_closed_trades == 50
    assert settings.wallet_prune_low_score_threshold == Decimal("60")
    assert settings.wallet_prune_low_score_operator == "lt"


def test_paper_config_does_not_seed_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_live_env_overrides(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert "paper_copy_accounts" not in config
    assert settings.paper_copy_accounts == []
    assert settings.paper_copy_recovery_interval_seconds == 15
    assert settings.realtime_subscription_refresh_seconds == 15
