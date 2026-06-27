from decimal import Decimal

import pytest

from app.core.config import Settings, load_app_config


def _set_required_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "HYPERLIQUID_PRIVATE_KEY",
        "0x1111111111111111111111111111111111111111111111111111111111111111",
    )
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "0x2222222222222222222222222222222222222222")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD", "test-dashboard-password")


def test_live_trading_config_is_loaded_from_dedicated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_live_env(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.live_trading_enabled is True
    assert settings.live_trading_acknowledged is True
    assert settings.live_trading_mainnet_acknowledged is True
    assert settings.live_trading_capital_mode == "unified"
    assert settings.live_trading_copy_enabled is True
    assert settings.live_trading_limit_slippage_bps == Decimal("20")
    assert settings.live_trading_min_order_notional_usd == Decimal("10")
    assert settings.live_trading_min_order_notional_buffer_usd == Decimal("0.1")
    assert settings.live_trading_max_order_notional_usd == Decimal("1000")
    assert settings.live_trading_max_account_open_notional_usd == Decimal("5000")
    assert settings.live_trading_max_open_positions == 50
    assert settings.live_trading_max_daily_loss_usd == Decimal("5000")
    assert settings.live_trading_max_orders_per_minute == 10
    assert settings.live_trading_reduce_only_when_stopped is True
    assert settings.live_trading_reconciliation_enabled is True
    assert settings.live_trading_reconciliation_interval_seconds == 30
    assert settings.live_trading_reconciliation_lookback_minutes == 120
    assert settings.live_trading_allowed_coins == []
    assert settings.live_trading_blocked_coins == []


def test_app_config_loads_wallet_pool_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_live_env(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.wallet_pool_page_limit == 300


def test_shared_trading_config_is_loaded_from_dedicated_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_live_env(monkeypatch)
    config = load_app_config()

    assert config["trading_copy_top_wallet_count"] == 10
    assert config["trading_copy_top_tier_wallet_count"] == 3
    assert Decimal(str(config["trading_copy_top_tier_allocation_pct"])) == Decimal("0.2")
    assert Decimal(str(config["trading_copy_standard_allocation_pct"])) == Decimal("0.2")
    assert Decimal(str(config["trading_copy_max_total_allocation_pct"])) == Decimal("1.0")
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
    _set_required_live_env(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert settings.wallet_prune_low_score_min_closed_trades == 50
    assert settings.wallet_prune_low_score_threshold == Decimal("50")
    assert settings.wallet_prune_low_score_operator == "lt"


def test_paper_config_does_not_seed_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_live_env(monkeypatch)
    config = load_app_config()
    settings = Settings(_env_file=None, **config)

    assert "paper_copy_accounts" not in config
    assert settings.paper_copy_accounts == []
    assert settings.paper_copy_recovery_interval_seconds == 15
    assert settings.realtime_subscription_refresh_seconds == 15
