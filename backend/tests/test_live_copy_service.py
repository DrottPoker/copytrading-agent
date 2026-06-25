from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.db.models import TradingAccount
from app.services.live_copy_service import live_copy_account_snapshot_is_stale


def test_live_copy_account_snapshot_without_reconcile_is_stale() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(last_reconciled_at=None)

    assert live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_live_copy_account_snapshot_older_than_interval_is_stale() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(
        last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC) - timedelta(seconds=31)
    )

    assert live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_live_copy_account_snapshot_inside_interval_is_fresh() -> None:
    settings = Settings(live_trading_reconciliation_interval_seconds=30)
    account = live_account(
        last_reconciled_at=datetime(2026, 1, 1, tzinfo=UTC) - timedelta(seconds=10)
    )

    assert not live_copy_account_snapshot_is_stale(
        account,
        settings=settings,
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )


def live_account(*, last_reconciled_at: datetime | None) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="mainnet",
        last_reconciled_at=last_reconciled_at,
    )
