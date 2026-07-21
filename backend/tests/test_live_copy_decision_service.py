from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.live_copy_decision_service import (
    live_copy_decision_identity,
    load_live_copy_decision_execution_diagnostics,
)


class ScalarRows:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class DiagnosticsSession:
    def __init__(self, rows: list[list[object]]) -> None:
        self.rows = rows

    async def scalars(self, _statement: object) -> ScalarRows:
        return ScalarRows(self.rows.pop(0))


@pytest.mark.asyncio
async def test_decision_diagnostics_uses_the_latest_exchange_attempt() -> None:
    order_id = uuid4()
    order = SimpleNamespace(
        id=order_id,
        account_key="live-test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=0,
        status="rejected",
        error="exchange_ioc_no_match: Order could not immediately match.",
    )
    first_attempt = SimpleNamespace(
        order_id=order_id,
        attempt_number=1,
        client_order_id="0xattemptone",
        status="completed",
        exchange_status="rejected",
        exchange_error_code="exchange_ioc_no_match",
        exchange_error_message="Order could not immediately match.",
        exchange_response={"status": "ok"},
        attempt_count=1,
        status_lookup_count=0,
        last_status_lookup_at=None,
        last_status_lookup_error=None,
    )
    latest_attempt = SimpleNamespace(
        order_id=order_id,
        attempt_number=2,
        client_order_id="0xattempttwo",
        status="uncertain",
        exchange_status="unknown",
        exchange_error_code="uncertain_submit",
        exchange_error_message="timeout",
        exchange_response=None,
        attempt_count=1,
        status_lookup_count=2,
        last_status_lookup_at=None,
        last_status_lookup_error="timeout",
    )

    diagnostics = await load_live_copy_decision_execution_diagnostics(
        DiagnosticsSession([[order], [latest_attempt, first_attempt]]),  # type: ignore[arg-type]
        decision_identities={("live-test", "0xsource", "fill-1", 0)},
    )

    result = diagnostics[("live-test", "0xsource", "fill-1", 0)]
    assert result.logical_order_status == "rejected"
    assert result.latest_dispatch_attempt_number == 2
    assert result.latest_dispatch_client_order_id == "0xattempttwo"
    assert result.submit_attempt_count == 2
    assert result.status_lookup_count == 2


def test_decision_identity_does_not_require_a_terminal_order_link() -> None:
    decision = SimpleNamespace(
        account_key="live-test",
        source_wallet="0xsource",
        source_fill_id="fill-1",
        sequence_index=2,
        trading_order_id=None,
    )

    assert live_copy_decision_identity(decision) == (
        "live-test",
        "0xsource",
        "fill-1",
        2,
    )
