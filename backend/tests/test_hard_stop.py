"""The hard stop closes every position once drawdown reaches its level."""

from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.db.models import AuditLog, RiskEvent, TradingAccount
from app.services import live_trading_service
from app.services.copy_risk_rules import AccountLossState, hard_stop_breached
from app.services.live_trading_service import (
    LIVE_HARD_STOP_MARKER,
    LiveCloseAllResult,
    enforce_live_hard_stop,
)


def loss_state(drawdown: str) -> AccountLossState:
    return AccountLossState(
        daily_loss_pct=Decimal("0"),
        weekly_loss_pct=Decimal("0"),
        drawdown_pct=Decimal(drawdown),
    )


def hard_stop_settings(level: str = "0.35") -> Settings:
    return Settings(_env_file=None, live_trading_hard_stop_drawdown_pct=Decimal(level))


def test_hard_stop_is_off_by_default() -> None:
    assert Settings(_env_file=None).live_trading_hard_stop_drawdown_pct == Decimal("0")
    assert hard_stop_breached(loss_state("0.99"), settings=Settings(_env_file=None)) is False


@pytest.mark.parametrize(
    ("drawdown", "expected"),
    [("0.3499", False), ("0.35", True), ("0.60", True)],
)
def test_hard_stop_trips_at_its_drawdown(drawdown: str, expected: bool) -> None:
    assert hard_stop_breached(loss_state(drawdown), settings=hard_stop_settings()) is expected


def test_hard_stop_needs_loss_history() -> None:
    assert hard_stop_breached(None, settings=hard_stop_settings()) is False


def test_hard_stop_must_not_fire_before_the_entry_limit() -> None:
    with pytest.raises(ValueError, match="live_trading_hard_stop_drawdown_pct"):
        Settings(
            _env_file=None,
            live_trading_max_drawdown_pct=Decimal("0.25"),
            live_trading_hard_stop_drawdown_pct=Decimal("0.20"),
        )


class FakeSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


def live_account(*, status: str = "enabled", tripped: bool = False) -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="mainnet",
        config_payload={LIVE_HARD_STOP_MARKER: {"trippedAt": "earlier"}} if tripped else {},
    )


class HardStopHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        drawdown: str | None,
        positions: int = 2,
        close_in_progress: bool = False,
    ) -> None:
        self.drawdown = drawdown
        self.positions = positions
        self.close_in_progress = close_in_progress
        self.close_all_calls = 0
        monkeypatch.setattr(live_trading_service, "live_account_loss_state", self.loss_state)
        monkeypatch.setattr(
            live_trading_service,
            "load_live_exchange_positions",
            self.exchange_positions,
        )
        monkeypatch.setattr(
            live_trading_service,
            "live_account_has_incomplete_close_operation",
            self.incomplete_close,
        )
        monkeypatch.setattr(
            live_trading_service,
            "close_all_live_account_positions",
            self.close_all,
        )

    async def loss_state(self, *_args: Any, **_kwargs: Any) -> AccountLossState | None:
        return loss_state(self.drawdown) if self.drawdown is not None else None

    async def exchange_positions(self, *_args: Any, **_kwargs: Any) -> list[object]:
        return [object()] * self.positions

    async def incomplete_close(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.close_in_progress

    async def close_all(self, *_args: Any, **kwargs: Any) -> LiveCloseAllResult:
        self.close_all_calls += 1
        return LiveCloseAllResult(
            account_key=kwargs["account"].key,
            operation_id=None,  # type: ignore[arg-type]
            operation_status="completed",
            submitted_orders=self.positions,
            failed_orders=0,
            status="disabled",
        )


async def enforce(
    account: TradingAccount,
    session: FakeSession,
    *,
    settings: Settings | None = None,
) -> LiveCloseAllResult | None:
    return await enforce_live_hard_stop(
        session,  # type: ignore[arg-type]
        account=account,
        settings=settings or hard_stop_settings(),
    )


@pytest.mark.asyncio
async def test_live_hard_stop_closes_everything_and_records_one_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = HardStopHarness(monkeypatch, drawdown="0.40")
    account = live_account()
    session = FakeSession()

    result = await enforce(account, session)

    assert result is not None
    assert harness.close_all_calls == 1
    events = [row for row in session.added if isinstance(row, RiskEvent)]
    assert [(event.event_type, event.severity) for event in events] == [
        ("live_hard_stop", "critical")
    ]
    assert events[0].payload["observed"] == "0.40"
    assert events[0].payload["limit"] == "0.35"
    assert [row.action for row in session.added if isinstance(row, AuditLog)] == [
        "live_account.hard_stop"
    ]
    assert account.config_payload[LIVE_HARD_STOP_MARKER]["rule"] == "hard_stop_drawdown"


@pytest.mark.asyncio
async def test_live_hard_stop_retry_does_not_repeat_the_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = HardStopHarness(monkeypatch, drawdown="0.40")
    session = FakeSession()

    result = await enforce(live_account(status="exit_only", tripped=True), session)

    assert result is not None
    assert harness.close_all_calls == 1
    assert session.added == []


@pytest.mark.parametrize(
    ("drawdown", "positions", "close_in_progress", "status", "level"),
    [
        # Short of the hard stop.
        ("0.30", 2, False, "enabled", "0.35"),
        # No performance history yet.
        (None, 2, False, "enabled", "0.35"),
        # Nothing left to close.
        ("0.40", 0, False, "enabled", "0.35"),
        # The durable close-all is already running and is resumed elsewhere.
        ("0.40", 2, True, "exit_only", "0.35"),
        # A disabled account was verified flat by the operator.
        ("0.40", 2, False, "disabled", "0.35"),
        # The hard stop is off.
        ("0.90", 2, False, "enabled", "0"),
    ],
)
@pytest.mark.asyncio
async def test_live_hard_stop_does_nothing_outside_its_conditions(
    monkeypatch: pytest.MonkeyPatch,
    drawdown: str | None,
    positions: int,
    close_in_progress: bool,
    status: str,
    level: str,
) -> None:
    harness = HardStopHarness(
        monkeypatch,
        drawdown=drawdown,
        positions=positions,
        close_in_progress=close_in_progress,
    )
    session = FakeSession()

    result = await enforce(
        live_account(status=status),
        session,
        settings=hard_stop_settings(level),
    )

    assert result is None
    assert harness.close_all_calls == 0
    assert session.added == []
