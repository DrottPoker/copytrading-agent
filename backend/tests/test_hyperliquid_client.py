from typing import Any

import pytest

from app.integrations.hyperliquid_client import HyperliquidClient


@pytest.mark.asyncio
async def test_user_non_funding_ledger_updates_uses_bounded_info_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = HyperliquidClient()
    observed: dict[str, Any] = {}

    async def fake_post_info(payload: dict[str, Any]) -> list[dict[str, Any]]:
        observed.update(payload)
        return [{"delta": {"type": "deposit", "usdc": "100"}, "time": 1500}]

    monkeypatch.setattr(client, "post_info", fake_post_info)

    result = await client.user_non_funding_ledger_updates(
        user="0xuser",
        start_time_ms=1000,
        end_time_ms=2000,
    )

    assert observed == {
        "type": "userNonFundingLedgerUpdates",
        "user": "0xuser",
        "startTime": 1000,
        "endTime": 2000,
    }
    assert len(result) == 1
