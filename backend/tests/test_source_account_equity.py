"""The equity a copy is sized on depends on how the source holds its collateral."""

from decimal import Decimal
from typing import Any

import pytest

from app.services.copy_execution_cache import SourceAccountModeCache
from app.services.copy_signal_service import (
    load_source_account_state,
    load_source_account_states,
    load_source_collateral,
)


class SourceClient:
    """Info client for one source with a configurable account mode."""

    def __init__(
        self,
        *,
        mode: Any = "default",
        perp_account_value: str = "1000",
        spot_usdc_total: str = "0",
        mode_error: bool = False,
        spot_error: bool = False,
    ) -> None:
        self.mode = mode
        self.perp_account_value = perp_account_value
        self.spot_usdc_total = spot_usdc_total
        self.mode_error = mode_error
        self.spot_error = spot_error
        self.user_abstraction_calls = 0
        self.spot_state_calls = 0

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict[str, Any]:
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "leverage": {"type": "cross", "value": 10},
                        "szi": "-2",
                    }
                }
            ],
            "marginSummary": {"accountValue": self.perp_account_value},
        }

    async def user_abstraction(self, *, user: str) -> Any:
        self.user_abstraction_calls += 1
        if self.mode_error:
            raise RuntimeError("offline")
        return self.mode

    async def spot_clearinghouse_state(self, *, user: str) -> dict[str, Any]:
        self.spot_state_calls += 1
        if self.spot_error:
            raise RuntimeError("offline")
        return {"balances": [{"coin": "USDC", "total": self.spot_usdc_total, "hold": "0"}]}


async def load(client: SourceClient, *, dex: str = "") -> Any:
    return await load_source_account_state(
        client=client,  # type: ignore[arg-type]
        source_wallet="0xsource",
        dex=dex,
    )


@pytest.mark.parametrize("mode", ["unifiedAccount", "portfolioMargin"])
@pytest.mark.asyncio
async def test_unified_source_is_sized_on_its_spot_balance_not_the_perp_fragment(
    mode: str,
) -> None:
    # Figures from a real unified account: the perp account value was a fifth of the equity.
    client = SourceClient(
        mode=mode,
        perp_account_value="1172465.54",
        spot_usdc_total="5904929.35",
    )

    state = await load(client)

    assert state.skip_reason is None
    assert state.perp_equity == Decimal("5904929.35")
    assert state.positions_by_coin["BTC"].side == "short"


@pytest.mark.parametrize("mode", ["default", "disabled", "dexAbstraction"])
@pytest.mark.asyncio
async def test_standard_source_is_sized_on_its_perp_account_value(mode: str) -> None:
    client = SourceClient(mode=mode, perp_account_value="2500", spot_usdc_total="900000")

    state = await load(client)

    assert state.skip_reason is None
    assert state.perp_equity == Decimal("2500")
    assert client.spot_state_calls == 0


@pytest.mark.asyncio
async def test_standard_source_without_perp_equity_is_not_sized_on_spot() -> None:
    client = SourceClient(mode="default", perp_account_value="0", spot_usdc_total="900000")

    state = await load(client)

    assert state.skip_reason == "source_perp_equity_zero"
    assert state.perp_equity == Decimal("0")


@pytest.mark.parametrize("client", [SourceClient(mode_error=True), SourceClient(mode=None)])
@pytest.mark.asyncio
async def test_unknown_account_mode_blocks_entries_but_keeps_positions(
    client: SourceClient,
) -> None:
    state = await load(client)

    # Entries wait, exits still see the source's positions.
    assert state.skip_reason == "source_account_mode_unavailable"
    assert state.perp_equity == Decimal("0")
    assert "BTC" in state.positions_by_coin
    assert state.leverage_by_coin == {"BTC": Decimal("10")}


@pytest.mark.asyncio
async def test_unified_source_without_readable_spot_balance_blocks_entries() -> None:
    client = SourceClient(mode="unifiedAccount", perp_account_value="1172465", spot_error=True)

    state = await load(client)

    assert state.skip_reason == "source_unified_equity_unavailable"
    assert "BTC" in state.positions_by_coin


@pytest.mark.asyncio
async def test_unified_source_with_empty_spot_balance_has_no_equity() -> None:
    client = SourceClient(mode="unifiedAccount", perp_account_value="1172465")

    state = await load(client)

    assert state.skip_reason == "source_perp_equity_zero"


@pytest.mark.asyncio
async def test_account_mode_is_looked_up_once_and_the_balance_every_time() -> None:
    client = SourceClient(mode="unifiedAccount", spot_usdc_total="500")

    await load(client)
    client.spot_usdc_total = "400"
    second = await load(client)

    assert client.user_abstraction_calls == 1
    assert client.spot_state_calls == 2
    assert second.perp_equity == Decimal("400")


@pytest.mark.asyncio
async def test_failed_account_mode_lookup_is_retried() -> None:
    client = SourceClient(mode="default", mode_error=True)

    failed = await load(client)
    client.mode_error = False
    recovered = await load(client)

    assert failed.skip_reason == "source_account_mode_unavailable"
    assert recovered.skip_reason is None
    assert client.user_abstraction_calls == 2


@pytest.mark.asyncio
async def test_several_dexes_share_one_collateral_lookup() -> None:
    client = SourceClient(mode="unifiedAccount", spot_usdc_total="750")

    states = await load_source_account_states(
        client=client,  # type: ignore[arg-type]
        source_wallet="0xsource",
        fills=[{"coin": "BTC"}, {"coin": "xyz:GOLD"}],
    )

    assert {dex: state.perp_equity for dex, state in states.items()} == {
        "": Decimal("750"),
        "xyz": Decimal("750"),
    }
    assert client.user_abstraction_calls == 1
    assert client.spot_state_calls == 1


@pytest.mark.asyncio
async def test_account_mode_is_refreshed_after_its_maximum_age() -> None:
    now = [100.0]
    mode_cache = SourceAccountModeCache(max_age_seconds=900, clock=lambda: now[0])
    client = SourceClient(mode="default")

    first = await load_source_collateral(
        client=client,  # type: ignore[arg-type]
        source_wallet="0xSource",
        mode_cache=mode_cache,
    )
    client.mode = "unifiedAccount"
    client.spot_usdc_total = "300"
    now[0] += 900
    cached = await load_source_collateral(
        client=client,  # type: ignore[arg-type]
        source_wallet="0xsource",
        mode_cache=mode_cache,
    )
    now[0] += 1
    refreshed = await load_source_collateral(
        client=client,  # type: ignore[arg-type]
        source_wallet="0xsource",
        mode_cache=mode_cache,
    )

    assert first.unified is False
    assert cached.unified is False
    assert refreshed.unified is True
    assert refreshed.unified_equity == Decimal("300")
    assert client.user_abstraction_calls == 2
