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
    settings = Settings(**config)

    assert settings.live_trading_enabled is True
    assert settings.live_trading_acknowledged is True
    assert settings.live_trading_mainnet_acknowledged is True
    assert settings.live_trading_copy_enabled is True
    assert settings.live_trading_min_order_notional_usd == Decimal("10")
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


def test_paper_config_does_not_seed_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_live_env(monkeypatch)
    config = load_app_config()
    settings = Settings(**config)

    assert "paper_copy_accounts" not in config
    assert settings.paper_copy_accounts == []
