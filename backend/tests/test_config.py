from decimal import Decimal

from app.core.config import Settings, load_app_config


def test_live_trading_config_is_loaded_from_dedicated_file() -> None:
    config = load_app_config()
    settings = Settings(**config)

    assert settings.live_trading_enabled is False
    assert settings.live_trading_acknowledged is False
    assert settings.live_trading_mainnet_acknowledged is False
    assert settings.live_trading_copy_enabled is False
    assert settings.live_trading_min_order_notional_usd == Decimal("10")
    assert settings.live_trading_max_order_notional_usd == Decimal("1000")
    assert settings.live_trading_reconciliation_enabled is True
    assert settings.live_trading_reconciliation_interval_seconds == 30
    assert settings.live_trading_reconciliation_lookback_minutes == 120
    assert settings.live_trading_allowed_coins == []
    assert settings.live_trading_blocked_coins == []


def test_paper_config_does_not_seed_accounts() -> None:
    config = load_app_config()
    settings = Settings(**config)

    assert "paper_copy_accounts" not in config
    assert settings.paper_copy_accounts == []
