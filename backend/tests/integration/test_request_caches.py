"""Maintenance request caches: subaccount checks and account modes on a real database."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import DiscoverySubaccountCheck, DiscoveryWalletCandidate, WatchedWallet
from app.services import discovery_service, wallet_score_service
from app.services.discovery_service import run_discovery_import
from app.services.wallet_score_service import base_metrics_from_row, metrics_with_current_drawdowns

pytestmark = pytest.mark.integration

MASTER_A = "0x" + "a" * 40
MASTER_B = "0x" + "b" * 40
SUBACCOUNT = "0x" + "2" * 40
WALLET = "0x" + "1" * 40


def leaderboard_row(address: str) -> dict[str, Any]:
    return {
        "ethAddress": address,
        "accountValue": "50000",
        "displayName": None,
        "windowPerformances": [
            ["day", {"pnl": "100", "roi": "0.1"}],
            ["week", {"pnl": "100", "roi": "0.1"}],
        ],
    }


class FakeLeaderboardClient:
    def __init__(self, *_args: object) -> None:
        pass

    async def get_leaderboard(self) -> dict[str, Any]:
        return {"leaderboardRows": [leaderboard_row(MASTER_A), leaderboard_row(MASTER_B)]}


class FakeSubaccountClient:
    lookups: list[str] = []

    def __init__(self, *_args: object) -> None:
        pass

    def open(self) -> FakeSubaccountClient:
        return self

    async def aclose(self) -> None:
        return None

    async def post_info(self, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        assert payload["type"] == "subAccounts"
        FakeSubaccountClient.lookups.append(payload["user"])
        if payload["user"] != MASTER_A:
            return None
        return [
            {
                "clearinghouseState": {
                    "assetPositions": [],
                    "marginSummary": {"accountValue": "5000"},
                    "withdrawable": "5000",
                },
                "name": "Sub",
                "subAccountUser": SUBACCOUNT,
            }
        ]


@pytest.mark.asyncio
async def test_leaderboard_subaccounts_are_listed_once_per_recheck_interval(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSubaccountClient.lookups = []
    monkeypatch.setattr(discovery_service, "HyperliquidLeaderboardClient", FakeLeaderboardClient)
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FakeSubaccountClient)
    settings = Settings(_env_file=None)
    sources = ["hyperliquid_leaderboard_day", "hyperliquid_leaderboard_week"]

    async with integration_sessionmaker() as session:
        await run_discovery_import(session, sources=sources, run_pipeline=False, settings=settings)
        first_run_lookups = list(FakeSubaccountClient.lookups)
        await run_discovery_import(session, sources=sources, run_pipeline=False, settings=settings)
        checks = {
            check.master_address: check
            for check in (await session.scalars(select(DiscoverySubaccountCheck))).all()
        }
        candidates = set(
            (await session.scalars(select(DiscoveryWalletCandidate.wallet_address))).all()
        )

    # Both windows list both masters, yet each master is looked up once over two runs.
    assert first_run_lookups == [MASTER_A, MASTER_B]
    assert FakeSubaccountClient.lookups == first_run_lookups
    assert checks[MASTER_A].subaccount_count == 1
    assert checks[MASTER_B].subaccount_count == 0
    assert candidates == {MASTER_A, MASTER_B, SUBACCOUNT}


@pytest.mark.asyncio
async def test_an_expired_subaccount_check_is_looked_up_again(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSubaccountClient.lookups = []
    monkeypatch.setattr(discovery_service, "HyperliquidLeaderboardClient", FakeLeaderboardClient)
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FakeSubaccountClient)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                DiscoverySubaccountCheck(
                    master_address=MASTER_A,
                    subaccount_count=0,
                    checked_at=datetime.now(UTC) - timedelta(hours=25),
                ),
                DiscoverySubaccountCheck(
                    master_address=MASTER_B,
                    subaccount_count=0,
                    checked_at=datetime.now(UTC) - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

        await run_discovery_import(
            session,
            sources=["hyperliquid_leaderboard_day"],
            run_pipeline=False,
            settings=Settings(_env_file=None),
        )
        refreshed = await session.get(DiscoverySubaccountCheck, MASTER_A)

    assert FakeSubaccountClient.lookups == [MASTER_A]
    assert refreshed is not None
    assert refreshed.subaccount_count == 1
    assert refreshed.checked_at > datetime.now(UTC) - timedelta(minutes=5)


class FakeLiveStateClient:
    abstraction_requests = 0

    def __init__(self, *_args: object) -> None:
        pass

    async def __aenter__(self) -> FakeLiveStateClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict:
        return {
            "assetPositions": [],
            "marginSummary": {"accountValue": "1000", "totalMarginUsed": "0", "totalNtlPos": "0"},
            "withdrawable": "1000",
        }

    async def user_abstraction(self, *, user: str) -> str:
        FakeLiveStateClient.abstraction_requests += 1
        return "default"


@pytest.mark.asyncio
async def test_scoring_caches_the_account_mode_between_runs(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeLiveStateClient.abstraction_requests = 0
    monkeypatch.setattr(wallet_score_service, "HyperliquidClient", FakeLiveStateClient)
    metric = replace(
        base_metrics_from_row(
            {
                "wallet_address": WALLET,
                "fill_count": 60,
                "first_fill_time_ms": None,
                "last_activity_time_ms": None,
                "liquidation_fill_count": 0,
                "liquidation_event_count": 0,
                "liquidation_notional_usd": 0,
            }
        ),
        trade_count=30,
    )
    settings = Settings(_env_file=None)

    async with integration_sessionmaker() as session:
        session.add(WatchedWallet(address=WALLET, enabled=True))
        await session.commit()

        first = await metrics_with_current_drawdowns(session, metrics=[metric], settings=settings)
        await session.commit()
        second = await metrics_with_current_drawdowns(session, metrics=[metric], settings=settings)
        await session.commit()
        wallet = await session.scalar(select(WatchedWallet).where(WatchedWallet.address == WALLET))

    assert FakeLiveStateClient.abstraction_requests == 1
    assert first[0].current_account_value_usd == second[0].current_account_value_usd
    assert second[0].current_drawdown_status == "ok"
    assert wallet is not None
    assert wallet.user_abstraction == "default"
    assert wallet.user_abstraction_checked_at is not None
