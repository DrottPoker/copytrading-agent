from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.db.models import TradingAccount
from app.services import live_trading_service
from app.services.live_trading_service import (
    LiveAccountDeleteError,
    LiveReconciliationResult,
    LiveTradingServiceError,
    delete_live_trading_account,
    start_live_trading_account,
    stop_live_trading_account,
)


class StartSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commit_count = 0
        self.flush_count = 0

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commit_count += 1

    async def flush(self) -> None:
        self.flush_count += 1


def live_account() -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status="enabled",
        network="testnet",
        wallet_address="0x" + "2" * 40,
        lifecycle_version=0,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
    )


def make_testnet_settings() -> Settings:
    settings = Settings()
    settings.hyperliquid_network = "testnet"
    return settings


@pytest.mark.asyncio
async def test_start_does_not_overwrite_newer_lifecycle_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    session = StartSession()
    lock_count = 0

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        nonlocal lock_count
        lock_count += 1
        if lock_count == 3:
            account.lifecycle_version += 1
            account.status_reason = "newer_stop_command"
        yield

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_reconciliation(*_args: object, **_kwargs: object) -> LiveReconciliationResult:
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            status="complete",
        )

    monkeypatch.setattr(
        live_trading_service,
        "validate_live_trading_configuration",
        lambda *_: None,
    )
    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "run_live_trading_account_reconciliation",
        fake_reconciliation,
    )

    with pytest.raises(LiveTradingServiceError, match="lifecycle changed"):
        await start_live_trading_account(
            session,  # type: ignore[arg-type]
            account_key=account.key,
            settings=make_testnet_settings(),
            info_client=object(),  # type: ignore[arg-type]
            actor="admin",
        )

    assert account.status == "disabled"
    assert account.lifecycle_version == 1
    assert account.status_reason == "newer_stop_command"


@pytest.mark.asyncio
async def test_stop_on_disabled_account_advances_lifecycle_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    session = StartSession()

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_cancel(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(live_trading_service, "cancel_unsent_live_entries", fake_cancel)

    stopped = await stop_live_trading_account(
        session,  # type: ignore[arg-type]
        account_key=account.key,
        actor="admin",
    )

    assert stopped.status == "disabled"
    assert stopped.lifecycle_version == 1
    assert stopped.status_reason == "stopped_by_dashboard"


@pytest.mark.asyncio
async def test_archive_reconciles_before_trusting_flat_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = live_account()
    account.status = "disabled"
    account.last_reconciled_at = datetime.now(UTC)
    account.config_payload = {
        "lastReconciliation": {
            "status": "complete",
            "unifiedAvailableUsd": "100",
        }
    }
    session = StartSession()
    reconciliation_called = False

    @asynccontextmanager
    async def fake_job_lock(*_args: object, **_kwargs: object) -> AsyncIterator[None]:
        yield

    async def fake_load_account(*_args: object, **_kwargs: object) -> TradingAccount:
        return account

    async def fake_reconciliation(*_args: object, **_kwargs: object) -> LiveReconciliationResult:
        nonlocal reconciliation_called
        reconciliation_called = True
        account.status = "exit_only"
        account.lifecycle_version += 1
        account.status_reason = "external_exposure_detected"
        return LiveReconciliationResult(
            account_key=account.key,
            user_address=account.wallet_address or "",
            status="complete",
            open_positions=1,
        )

    monkeypatch.setattr(live_trading_service, "job_lock", fake_job_lock)
    monkeypatch.setattr(
        live_trading_service,
        "load_live_account_for_update",
        fake_load_account,
    )
    monkeypatch.setattr(
        live_trading_service,
        "run_live_trading_account_reconciliation",
        fake_reconciliation,
    )

    with pytest.raises(LiveAccountDeleteError, match="Disable the live account"):
        await delete_live_trading_account(
            session,  # type: ignore[arg-type]
            account_key=account.key,
            settings=make_testnet_settings(),
            info_client=object(),  # type: ignore[arg-type]
            actor="admin",
        )

    assert reconciliation_called is True
    assert account.archived_at is None
    assert account.status == "exit_only"
