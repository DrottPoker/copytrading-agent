"""Production live trading signs only with a Hyperliquid API wallet."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from eth_account import Account

from app.core.config import Settings
from app.db.models import TradingAccount
from app.schemas.ops import OpsLiveSigner
from app.services import live_trading_service
from app.services.live_signer_service import (
    LIVE_SIGNER_CHECK_INTERVAL,
    LIVE_SIGNER_PAYLOAD_KEY,
    LIVE_SIGNER_RETRY_INTERVAL,
    SIGNER_API_WALLET,
    SIGNER_MAIN_WALLET,
    SIGNER_NOT_REGISTERED,
    SIGNER_OTHER_WALLET,
    SIGNER_UNAVAILABLE,
    SIGNER_UNCHECKED,
    SIGNER_WRONG_ACCOUNT,
    account_live_signer,
    classify_user_role,
    current_live_signer,
    live_signer_address,
    live_signer_entry_block,
    refresh_live_account_signer,
)
from app.services.live_trading_service import (
    LiveOrderSubmitError,
    LiveTradingServiceError,
    validate_live_account_can_start,
    validate_live_entry_risk_guardrails,
)
from app.services.ops_monitoring_service import live_signer_notes

# Throwaway keys that never hold funds.
AGENT_KEY = "0x" + "11" * 32
MASTER_KEY = "0x" + "22" * 32
AGENT = Account.from_key(AGENT_KEY).address.lower()
MASTER = Account.from_key(MASTER_KEY).address.lower()
NOW = datetime(2026, 10, 10, 12, tzinfo=UTC)


class RoleClient:
    def __init__(self, response: object | Exception) -> None:
        self.response = response
        self.calls: list[str] = []

    async def user_role(self, *, user: str) -> Any:
        self.calls.append(user)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def settings(*, key: str = AGENT_KEY, production: bool = True) -> Settings:
    return Settings(_env_file=None).model_copy(
        update={
            "app_env": "production" if production else "development",
            "hyperliquid_private_key": key,
            "hyperliquid_wallet_address": MASTER,
            "live_trading_max_orders_per_minute": 0,
        }
    )


def account(*, status: str = "enabled") -> TradingAccount:
    return TradingAccount(
        key="live_main",
        account_type="live",
        label="Live",
        status=status,
        network="mainnet",
        wallet_address=MASTER,
        realized_pnl_usd=Decimal("0"),
        fee_usd=Decimal("0"),
        config_payload={},
    )


async def verified(response: object, *, key: str = AGENT_KEY) -> TradingAccount:
    live = account()
    await refresh_live_account_signer(
        live,
        client=RoleClient(response),
        settings=settings(key=key),
        now=NOW,
    )
    return live


def agent_of(address: str) -> dict[str, object]:
    return {"role": "agent", "data": {"user": address}}


def test_signer_address_comes_from_the_key_and_bad_keys_give_none() -> None:
    assert live_signer_address(AGENT_KEY) == AGENT
    assert live_signer_address("not-a-key") is None
    assert live_signer_address(None) is None


@pytest.mark.parametrize(
    ("response", "status"),
    [
        (agent_of(MASTER), SIGNER_API_WALLET),
        (agent_of(MASTER.upper().replace("0X", "0x")), SIGNER_API_WALLET),
        (agent_of("0x" + "9" * 40), SIGNER_WRONG_ACCOUNT),
        ({"role": "missing"}, SIGNER_NOT_REGISTERED),
        ({"role": "user"}, SIGNER_OTHER_WALLET),
        ({"role": "subAccount", "data": {"master": MASTER}}, SIGNER_OTHER_WALLET),
    ],
)
def test_user_role_maps_to_a_signer_status(response: object, status: str) -> None:
    assert classify_user_role(master=MASTER, response=response)[0] == status


def test_unexpected_user_role_shapes_are_errors() -> None:
    with pytest.raises(ValueError):
        classify_user_role(master=MASTER, response=["agent"])
    with pytest.raises(ValueError):
        classify_user_role(master=MASTER, response={})


@pytest.mark.asyncio
async def test_the_accounts_own_key_is_the_main_wallet_without_asking_hyperliquid() -> None:
    live = account()
    client = RoleClient(AssertionError("no request expected"))

    await refresh_live_account_signer(
        live, client=client, settings=settings(key=MASTER_KEY), now=NOW
    )

    check = account_live_signer(live)
    assert check is not None
    assert check.status == SIGNER_MAIN_WALLET
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_verified_signer_is_rechecked_hourly_or_when_forced() -> None:
    live = await verified(agent_of(MASTER))
    client = RoleClient(agent_of(MASTER))
    production = settings()

    soon = NOW + LIVE_SIGNER_CHECK_INTERVAL - timedelta(seconds=1)
    assert not await refresh_live_account_signer(live, client=client, settings=production, now=soon)
    assert await refresh_live_account_signer(
        live, client=client, settings=production, now=soon, force=True
    )
    later = soon + LIVE_SIGNER_CHECK_INTERVAL
    assert await refresh_live_account_signer(live, client=client, settings=production, now=later)
    assert client.calls == [AGENT, AGENT]


@pytest.mark.asyncio
async def test_a_network_failure_keeps_the_verified_answer() -> None:
    live = await verified(agent_of(MASTER))
    later = NOW + LIVE_SIGNER_CHECK_INTERVAL

    await refresh_live_account_signer(
        live,
        client=RoleClient(TimeoutError()),
        settings=settings(),
        now=later,
    )

    check = account_live_signer(live)
    assert check is not None
    assert check.status == SIGNER_API_WALLET
    assert check.checked_at == NOW
    assert check.attempted_at == later
    assert check.error == "TimeoutError"
    # The failed attempt is retried sooner than a normal recheck.
    retry_client = RoleClient(agent_of(MASTER))
    assert await refresh_live_account_signer(
        live,
        client=retry_client,
        settings=settings(),
        now=later + LIVE_SIGNER_RETRY_INTERVAL,
    )


@pytest.mark.asyncio
async def test_a_network_failure_without_an_answer_is_unavailable() -> None:
    live = await verified(TimeoutError())

    assert current_live_signer(live, settings=settings()).status == SIGNER_UNAVAILABLE


@pytest.mark.asyncio
async def test_a_rotated_key_is_unchecked_until_verified_again() -> None:
    live = await verified(agent_of(MASTER))
    other_key = "0x" + "33" * 32

    assert current_live_signer(live, settings=settings(key=other_key)).status == SIGNER_UNCHECKED


@pytest.mark.asyncio
async def test_only_production_enforces_the_api_wallet() -> None:
    live = await verified({"role": "user"})

    assert live_signer_entry_block(live, settings=settings(production=False)) is None
    block = live_signer_entry_block(live, settings=settings())
    assert block is not None
    assert block.rule == "live_signer_not_api_wallet"
    assert block.trip_account


@pytest.mark.asyncio
async def test_a_verified_api_wallet_is_allowed_in_production() -> None:
    live = await verified(agent_of(MASTER))

    assert live_signer_entry_block(live, settings=settings()) is None


def test_an_unchecked_signer_waits_without_tripping_the_account() -> None:
    block = live_signer_entry_block(account(), settings=settings())

    assert block is not None
    assert block.rule == "live_signer_unverified"
    assert not block.trip_account


class CommitSession:
    async def commit(self) -> None:
        return None


def guarded(live: TradingAccount) -> TradingAccount:
    live.equity_usd = Decimal("1000")
    live.last_reconciled_at = datetime.now(UTC)
    live.config_payload = {
        **(live.config_payload or {}),
        "lastReconciliation": {"status": "complete"},
    }
    return live


@pytest.mark.asyncio
async def test_the_entry_gate_trips_an_account_that_signs_with_its_main_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tripped: dict[str, object] = {}

    async def fake_trip(*_args: object, **kwargs: object) -> None:
        tripped.update(kwargs)

    monkeypatch.setattr(live_trading_service, "trip_live_account_risk", fake_trip)
    live = guarded(await verified({"role": "user"}, key=MASTER_KEY))

    with pytest.raises(LiveOrderSubmitError, match="API wallet"):
        await validate_live_entry_risk_guardrails(
            CommitSession(),  # type: ignore[arg-type]
            account=live,
            settings=settings(key=MASTER_KEY),
        )

    assert tripped["rule"] == "live_signer_not_api_wallet"
    assert tripped["observed"] == SIGNER_MAIN_WALLET


@pytest.mark.asyncio
async def test_the_entry_gate_passes_a_verified_api_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_history(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(live_trading_service, "live_account_loss_state", no_history)
    live = guarded(await verified(agent_of(MASTER)))

    await validate_live_entry_risk_guardrails(
        CommitSession(),  # type: ignore[arg-type]
        account=live,
        settings=settings(),
    )


@pytest.mark.asyncio
async def test_account_start_refuses_a_main_wallet_key_in_production() -> None:
    live = await verified({"role": "user"}, key=MASTER_KEY)
    live.status = "disabled"
    live.last_reconciled_at = NOW
    live.config_payload = {
        **(live.config_payload or {}),
        "lastReconciliation": {
            "status": "complete",
            "unifiedAvailableUsd": "100",
            "userAbstraction": "unifiedAccount",
        },
    }

    with pytest.raises(LiveTradingServiceError, match="API wallet"):
        validate_live_account_can_start(live, settings=settings(key=MASTER_KEY), now=NOW)


def signer_row(*, status: str, account_status: str = "enabled") -> OpsLiveSigner:
    return OpsLiveSigner(
        account_key="live_main",
        account_status=account_status,
        status=status,
        description=status,
        required=True,
        signer_address=AGENT,
        master_address=MASTER,
        checked_at=NOW,
        attempted_at=NOW,
        error=None,
    )


def test_ops_warns_only_about_tradable_accounts_without_an_api_wallet() -> None:
    live_on = settings().model_copy(update={"live_trading_enabled": True})

    assert live_signer_notes([signer_row(status=SIGNER_API_WALLET)], settings=live_on) == []
    assert (
        live_signer_notes(
            [signer_row(status=SIGNER_MAIN_WALLET, account_status="disabled")], settings=live_on
        )
        == []
    )
    assert live_signer_notes([signer_row(status=SIGNER_MAIN_WALLET)], settings=settings()) == []
    assert live_signer_notes([signer_row(status=SIGNER_MAIN_WALLET)], settings=live_on) == [
        "Live account live_main signs with main_wallet."
    ]


@pytest.mark.asyncio
async def test_the_stored_signer_check_never_contains_the_key() -> None:
    live = await verified(agent_of(MASTER))

    stored = str((live.config_payload or {})[LIVE_SIGNER_PAYLOAD_KEY])
    assert AGENT in stored
    assert AGENT_KEY.removeprefix("0x") not in stored
