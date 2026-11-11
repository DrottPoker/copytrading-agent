"""A copied live position is closed when its source no longer holds it."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.db.models import RiskEvent, TradingAccount, TradingPosition
from app.services import live_copy_service
from app.services.copy_signal_service import (
    SourceAccountState,
    SourceCurrentPosition,
    plan_source_fill,
)
from app.services.live_copy_service import (
    LIVE_SOURCE_FLAT_CLOSE_GRACE_SECONDS,
    LIVE_SOURCE_FLAT_MARKER,
    close_live_positions_without_source,
)
from app.services.live_trading_service import (
    LiveOrderLifecycleResult,
    LiveOrderSubmitError,
    infer_fill_action,
    infer_position_side,
)
from app.services.source_fill_ordering import forced_close_side, source_fill_order_key

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
SOURCE = "0xsource"


def source_fill(direction: str, *, start_position: str | None = "2") -> dict[str, Any]:
    raw_json: dict[str, Any] = {"dir": direction}
    if start_position is not None:
        raw_json["startPosition"] = start_position
    return {
        "coin": "BTC",
        "price": "100000",
        "size": "1",
        "notionalUsd": "100000",
        "timestampMs": 1,
        "externalFillId": "1",
        "rawJson": raw_json,
    }


@pytest.mark.parametrize(
    ("direction", "start_position", "expected_side"),
    [
        ("Liquidated Cross Long", "2", "long"),
        ("Liquidated Isolated Long", "2", "long"),
        ("Liquidated Cross Short", "-2", "short"),
        ("Liquidated Isolated Short", "-2", "short"),
        ("Auto-Deleveraging", "2", "long"),
        ("Auto-Deleveraging", "-2", "short"),
    ],
)
def test_forced_source_exit_is_planned_as_a_close(
    direction: str,
    start_position: str,
    expected_side: str,
) -> None:
    parts = plan_source_fill(source_fill(direction, start_position=start_position))

    assert [(part.action, part.side) for part in parts] == [("close", expected_side)]
    assert parts[0].close_ratio == Decimal("0.5")


def test_auto_deleveraging_without_a_known_position_is_not_planned() -> None:
    assert plan_source_fill(source_fill("Auto-Deleveraging", start_position=None)) == []
    assert forced_close_side("Auto-Deleveraging", Decimal("0")) is None
    assert forced_close_side("Open Long", Decimal("1")) is None


def test_forced_source_exit_orders_before_an_entry_at_the_same_time() -> None:
    liquidation = source_fill("Liquidated Cross Long")
    reopen = source_fill("Open Long", start_position="0")

    assert source_fill_order_key(liquidation) < source_fill_order_key(reopen)


@pytest.mark.parametrize(
    ("fill", "expected_action", "expected_side"),
    [
        ({"dir": "Liquidated Cross Long", "side": "A"}, "close", "long"),
        ({"dir": "Liquidated Isolated Short", "side": "B"}, "close", "short"),
        ({"dir": "Auto-Deleveraging", "side": "B"}, "close", "short"),
        ({"dir": "Auto-Deleveraging", "side": "A"}, "close", "long"),
        ({"dir": "Close Long", "side": "A"}, "close", "long"),
        ({"dir": "Open Short", "side": "A"}, "open", "short"),
    ],
)
def test_own_forced_exit_fill_is_recorded_as_a_close(
    fill: dict[str, Any],
    expected_action: str,
    expected_side: str,
) -> None:
    assert infer_fill_action(fill) == expected_action
    assert infer_position_side(fill) == expected_side


class ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class FakeSession:
    def __init__(self, positions: list[TradingPosition]) -> None:
        self.positions = positions
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    async def scalars(self, _query: Any) -> ScalarResult:
        return ScalarResult(self.positions)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def live_account(*, status: str = "enabled") -> TradingAccount:
    return TradingAccount(
        key="live_test",
        account_type="live",
        label="Live Test",
        status=status,
        network="mainnet",
    )


def live_position(*, flat_observed_at: datetime | None = None) -> TradingPosition:
    raw_payload: dict[str, Any] = {"source": "live_fill"}
    if flat_observed_at is not None:
        raw_payload[LIVE_SOURCE_FLAT_MARKER] = {"observedAt": flat_observed_at.isoformat()}
    return TradingPosition(
        account_key="live_test",
        account_type="live",
        source_wallet=SOURCE,
        coin="HYPE",
        side="long",
        size=Decimal("0.5"),
        entry_price=Decimal("40"),
        notional_usd=Decimal("20"),
        leverage=Decimal("5"),
        margin_mode="cross",
        margin_usd=Decimal("4"),
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        raw_payload=raw_payload,
        opened_at=NOW - timedelta(hours=1),
    )


def source_state(
    *,
    positions: dict[str, SourceCurrentPosition] | None = None,
    skip_reason: str | None = None,
) -> dict[str, SourceAccountState]:
    return {
        "": SourceAccountState(
            dex="",
            perp_equity=Decimal("1000"),
            leverage_by_coin={},
            positions_by_coin=positions or {},
            skip_reason=skip_reason,
        )
    }


class CloseHarness:
    """Replaces the exchange and account lookups around the reconciliation."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        account: TradingAccount | None = None,
        pending_close_size: Decimal = Decimal("0"),
        submit_error: Exception | None = None,
    ) -> None:
        self.account = account or live_account()
        self.intents: list[Any] = []
        self.pending_close_size = pending_close_size
        self.submit_error = submit_error
        monkeypatch.setattr(live_copy_service, "load_live_close_mids", self.load_mids)
        monkeypatch.setattr(live_copy_service, "load_live_account", self.load_account)
        monkeypatch.setattr(
            live_copy_service,
            "live_pending_close_size_for_position",
            self.pending_close,
        )
        monkeypatch.setattr(live_copy_service, "submit_live_trade_intent", self.submit)

    async def load_mids(self, *_args: Any, **_kwargs: Any) -> dict[str, Decimal]:
        return {"HYPE": Decimal("50")}

    async def load_account(self, *_args: Any, **_kwargs: Any) -> TradingAccount:
        return self.account

    async def pending_close(self, *_args: Any, **_kwargs: Any) -> Decimal:
        return self.pending_close_size

    async def submit(self, *_args: Any, **kwargs: Any) -> LiveOrderLifecycleResult:
        self.intents.append(kwargs["intent"])
        if self.submit_error is not None:
            raise self.submit_error
        return LiveOrderLifecycleResult(order=object(), exchange_result=None, submitted=True)  # type: ignore[arg-type]


async def run_close(
    session: FakeSession,
    states: dict[str, SourceAccountState],
    *,
    now: datetime = NOW,
) -> int:
    return await close_live_positions_without_source(
        session,  # type: ignore[arg-type]
        source_wallet=SOURCE,
        settings=Settings(),
        info_client=object(),  # type: ignore[arg-type]
        trading_client=object(),  # type: ignore[arg-type]
        source_states=states,
        now=now,
    )


@pytest.mark.asyncio
async def test_first_flat_observation_only_starts_the_grace_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    position = live_position()

    submitted = await run_close(FakeSession([position]), source_state())

    assert submitted == 0
    assert harness.intents == []
    assert position.raw_payload[LIVE_SOURCE_FLAT_MARKER] == {"observedAt": NOW.isoformat()}


@pytest.mark.asyncio
async def test_flat_source_inside_the_grace_period_is_left_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    observed_at = NOW - timedelta(seconds=LIVE_SOURCE_FLAT_CLOSE_GRACE_SECONDS - 1)

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=observed_at)]),
        source_state(),
    )

    assert submitted == 0
    assert harness.intents == []


@pytest.mark.asyncio
async def test_flat_source_after_the_grace_period_closes_the_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    observed_at = NOW - timedelta(seconds=LIVE_SOURCE_FLAT_CLOSE_GRACE_SECONDS)
    position = live_position(flat_observed_at=observed_at)
    session = FakeSession([position])

    submitted = await run_close(session, source_state())

    assert submitted == 1
    (intent,) = harness.intents
    assert intent.reduce_only is True
    assert intent.is_buy is False
    assert intent.source_wallet == SOURCE
    assert intent.coin == "HYPE"
    assert intent.size == Decimal("0.5")
    assert intent.source_fill_id.startswith("source-flat-HYPE-")
    assert intent.price_source == "source_flat_reconciliation"
    events = [row for row in session.added if isinstance(row, RiskEvent)]
    assert [event.event_type for event in events] == ["live_source_flat_close"]
    assert position.raw_payload[LIVE_SOURCE_FLAT_MARKER]["closeRequestedAt"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_retried_close_does_not_repeat_the_risk_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    observed_at = NOW - timedelta(minutes=5)
    position = live_position(flat_observed_at=observed_at)
    position.raw_payload[LIVE_SOURCE_FLAT_MARKER]["closeRequestedAt"] = observed_at.isoformat()
    session = FakeSession([position])

    submitted = await run_close(session, source_state())

    assert submitted == 1
    assert len(harness.intents) == 1
    assert not any(isinstance(row, RiskEvent) for row in session.added)


@pytest.mark.asyncio
async def test_opposite_source_position_counts_as_flat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    states = source_state(
        positions={"HYPE": SourceCurrentPosition(coin="HYPE", side="short", size=Decimal("3"))}
    )

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))]),
        states,
    )

    assert submitted == 1
    assert len(harness.intents) == 1


@pytest.mark.asyncio
async def test_source_holding_the_position_clears_the_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(monkeypatch)
    position = live_position(flat_observed_at=NOW - timedelta(minutes=5))
    states = source_state(
        positions={"HYPE": SourceCurrentPosition(coin="HYPE", side="long", size=Decimal("3"))}
    )

    submitted = await run_close(FakeSession([position]), states)

    assert submitted == 0
    assert harness.intents == []
    assert position.raw_payload[LIVE_SOURCE_FLAT_MARKER] is None


@pytest.mark.parametrize(
    "skip_reason",
    ["source_account_state_fetch_failed", "source_account_margin_summary_missing"],
)
@pytest.mark.asyncio
async def test_unknown_source_state_never_closes(
    monkeypatch: pytest.MonkeyPatch,
    skip_reason: str,
) -> None:
    harness = CloseHarness(monkeypatch)
    position = live_position(flat_observed_at=NOW - timedelta(minutes=5))

    submitted = await run_close(FakeSession([position]), source_state(skip_reason=skip_reason))

    assert submitted == 0
    assert harness.intents == []


@pytest.mark.asyncio
async def test_missing_dex_state_never_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = CloseHarness(monkeypatch)

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))]),
        {},
    )

    assert submitted == 0
    assert harness.intents == []


@pytest.mark.asyncio
async def test_close_in_flight_is_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = CloseHarness(monkeypatch, pending_close_size=Decimal("0.5"))

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))]),
        source_state(),
    )

    assert submitted == 0
    assert harness.intents == []


@pytest.mark.asyncio
async def test_disabled_account_is_left_to_the_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = CloseHarness(monkeypatch, account=live_account(status="disabled"))

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))]),
        source_state(),
    )

    assert submitted == 0
    assert harness.intents == []


@pytest.mark.asyncio
async def test_exit_only_account_still_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = CloseHarness(monkeypatch, account=live_account(status="exit_only"))

    submitted = await run_close(
        FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))]),
        source_state(),
    )

    assert submitted == 1
    assert len(harness.intents) == 1


@pytest.mark.asyncio
async def test_failed_submit_is_retried_on_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = CloseHarness(
        monkeypatch,
        submit_error=LiveOrderSubmitError("Another live order is being dispatched."),
    )
    session = FakeSession([live_position(flat_observed_at=NOW - timedelta(minutes=5))])

    submitted = await run_close(session, source_state())

    assert submitted == 0
    assert len(harness.intents) == 1
    assert session.rollbacks == 1
