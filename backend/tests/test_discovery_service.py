from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.services import discovery_service
from app.services.discovery_service import (
    HYPERDASH_PNL_COHORT_SOURCES,
    HYPERTRACKER_LEADERBOARD_SOURCES,
    HYPERTRACKER_SEGMENT_SOURCES,
    DiscoveryFetchContext,
    candidate_backfill_covers_pool_import,
    fetch_hyperliquid_leaderboard_source,
    hyperdash_cohort_id_from_url,
    hypertracker_leaderboard_candidate,
    hypertracker_leaderboard_url,
    hypertracker_segment_candidate,
    hypertracker_segment_wallets_url,
    normalize_requested_sources,
)
from app.services.hyperliquid_leaderboard_source import fetch_master_subaccounts

MASTER_A = "0x" + "a" * 40
MASTER_B = "0x" + "b" * 40
MASTER_C = "0x" + "c" * 40
SUBACCOUNT = "0x" + "2" * 40


def test_hypertracker_sources_are_known_discovery_sources() -> None:
    assert normalize_requested_sources(
        [
            "hypertracker_money_printer",
            "hypertracker_smart_money",
            "hypertracker_grinder",
            "hypertracker_humble_earner",
            "hypertracker_avg_daily_perp_pnl",
        ]
    ) == [
        "hypertracker_money_printer",
        "hypertracker_smart_money",
        "hypertracker_grinder",
        "hypertracker_humble_earner",
        "hypertracker_avg_daily_perp_pnl",
    ]


def test_hyperdash_profitable_cohorts_are_known_discovery_sources() -> None:
    sources = [
        "hyperdash_cohorts",
        "hyperdash_cohorts_very_profitable",
        "hyperdash_cohorts_extremely_profitable",
    ]

    assert normalize_requested_sources(sources) == sources
    assert HYPERDASH_PNL_COHORT_SOURCES == set(sources)
    assert (
        hyperdash_cohort_id_from_url("https://hyperdash.com/explore/cohorts/extremely_profitable")
        == "extremely_profitable"
    )


def test_hypertracker_segment_candidate_maps_wallet_metrics() -> None:
    row = {
        "address": "0x1111111111111111111111111111111111111111",
        "displayName": "Trader One",
        "segments": [7, 9],
        "favoriteCount": 12,
        "totalEquity": 2000000,
        "perpEquity": 1500000,
        "perpPnl": 300000,
        "exposureRatio": 2.5,
        "perpBias": -1.5,
        "openValue": 3750000,
        "sumUpnl": 12000,
        "earliestActivityAt": "2026-06-01T00:00:00.000Z",
        "closestLiq": {"coin": "BTC", "progress": 25},
    }

    candidate = hypertracker_segment_candidate(
        source="hypertracker_smart_money",
        row=row,
        rank=3,
    )

    assert candidate is not None
    assert candidate.wallet_address == "0x1111111111111111111111111111111111111111"
    assert candidate.source == "hypertracker_smart_money"
    assert candidate.source_rank == 3
    assert candidate.source_label == "Trader One"
    assert candidate.source_cohort == "Smart Money"
    assert candidate.account_value == Decimal("2000000")
    assert candidate.source_pnl == Decimal("300000")
    assert candidate.source_roi == Decimal("17.64705882352941176470588235")
    assert candidate.raw_payload == {
        "address": "0x1111111111111111111111111111111111111111",
        "displayName": "Trader One",
        "verified": None,
        "segments": [7, 9],
        "favoriteCount": 12,
        "totalEquity": 2000000,
        "perpEquity": 1500000,
        "perpPnl": 300000,
        "exposureRatio": 2.5,
        "perpBias": -1.5,
        "openValue": 3750000,
        "sumUpnl": 12000,
        "earliestActivityAt": "2026-06-01T00:00:00.000Z",
        "vaultLeader": None,
        "vaultName": None,
        "closestLiquidationCoin": "BTC",
        "closestLiquidationProgress": 25,
        "sourceSegmentId": HYPERTRACKER_SEGMENT_SOURCES["hypertracker_smart_money"],
        "sourceSegmentSlug": "smart-money",
        "sourceRank": 3,
    }


def test_hypertracker_avg_daily_perp_pnl_candidate_maps_wallet_metrics() -> None:
    row = {
        "address": "0x2222222222222222222222222222222222222222",
        "age": "2026-06-21T19:49:54.367Z",
        "avgPnl": 1200,
        "greenDays": 18,
        "volume": 500000,
        "highestPnl": 8000,
        "perpEquity": 31200,
        "exposureRatio": 2.2,
        "bias": -1.4,
        "rank": 4,
        "profile": {
            "displayName": "Avg Trader",
            "verified": True,
            "segments": [7, 8],
            "favoriteCount": 9,
            "totalEquity": 45000,
            "perpEquity": 31200,
            "perpPnl": 99000,
            "earliestActivityAt": "2026-01-01T00:00:00.000Z",
            "vault": {"leader": "0x3333333333333333333333333333333333333333", "name": "Vault"},
        },
    }

    candidate = hypertracker_leaderboard_candidate(
        source="hypertracker_avg_daily_perp_pnl",
        row=row,
        fallback_rank=1,
    )

    assert candidate is not None
    assert candidate.wallet_address == "0x2222222222222222222222222222222222222222"
    assert candidate.source == "hypertracker_avg_daily_perp_pnl"
    assert candidate.source_rank == 4
    assert candidate.source_label == "Avg Trader"
    assert candidate.source_cohort == "Avg Daily Perp PnL"
    assert candidate.account_value == Decimal("31200")
    assert candidate.source_pnl == Decimal("1200")
    assert candidate.source_roi == Decimal("4.00")
    assert candidate.raw_payload == {
        "address": "0x2222222222222222222222222222222222222222",
        "age": "2026-06-21T19:49:54.367Z",
        "avgPnl": 1200,
        "greenDays": 18,
        "volume": 500000,
        "highestPnl": 8000,
        "perpEquity": 31200,
        "exposureRatio": 2.2,
        "bias": -1.4,
        "profileDisplayName": "Avg Trader",
        "profileVerified": True,
        "profileSegments": [7, 8],
        "profileFavoriteCount": 9,
        "profileTotalEquity": 45000,
        "profilePerpEquity": 31200,
        "profilePerpPnl": 99000,
        "profileEarliestActivityAt": "2026-01-01T00:00:00.000Z",
        "vaultLeader": "0x3333333333333333333333333333333333333333",
        "vaultName": "Vault",
        "sourceLeaderboardSlug": HYPERTRACKER_LEADERBOARD_SOURCES[
            "hypertracker_avg_daily_perp_pnl"
        ]["slug"],
        "sourceRank": 4,
    }


@pytest.mark.asyncio
async def test_subaccount_candidates_use_unified_account_value_when_perp_value_is_zero() -> None:
    client = FakeUnifiedSubaccountClient()
    subaccounts = await fetch_master_subaccounts(
        client=client,  # type: ignore[arg-type]
        master_address="0x1111111111111111111111111111111111111111",
        max_subaccounts=1,
    )

    assert subaccounts is not None
    assert len(subaccounts) == 1
    assert subaccounts[0].account_value == "200"
    assert client.requests == ["subAccounts", "userAbstraction", "spotClearinghouseState"]


@pytest.mark.asyncio
async def test_subaccount_without_spot_usdc_skips_the_account_mode_lookup() -> None:
    client = FakeUnifiedSubaccountClient(spot_state={"balances": [{"coin": "USDC", "total": "0"}]})

    subaccounts = await fetch_master_subaccounts(
        client=client,  # type: ignore[arg-type]
        master_address="0x1111111111111111111111111111111111111111",
        max_subaccounts=1,
    )

    assert subaccounts is not None
    assert subaccounts[0].account_value == "0"
    assert client.requests == ["subAccounts"]


@pytest.mark.asyncio
async def test_subaccount_spot_balances_from_the_lookup_replace_the_spot_request() -> None:
    client = FakeUnifiedSubaccountClient(
        spot_state={"balances": [{"coin": "USDC", "hold": "0", "total": "350"}]}
    )

    subaccounts = await fetch_master_subaccounts(
        client=client,  # type: ignore[arg-type]
        master_address="0x1111111111111111111111111111111111111111",
        max_subaccounts=1,
    )

    assert subaccounts is not None
    assert subaccounts[0].account_value == "350"
    assert client.requests == ["subAccounts", "userAbstraction"]


@pytest.mark.asyncio
async def test_failed_subaccount_lookup_is_not_an_empty_result() -> None:
    class FailingClient:
        async def post_info(self, payload: dict) -> list[dict]:
            raise RuntimeError("rate limited")

    subaccounts = await fetch_master_subaccounts(
        client=FailingClient(),  # type: ignore[arg-type]
        master_address=MASTER_A,
        max_subaccounts=10,
    )

    assert subaccounts is None


@pytest.mark.asyncio
async def test_leaderboard_windows_share_one_download_and_one_lookup_per_master(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leaderboard = FakeLeaderboardClient(
        [
            leaderboard_row(MASTER_A, day="30", week="10"),
            leaderboard_row(MASTER_B, day="20", week="30"),
            leaderboard_row(MASTER_C, day="10", week="20"),
        ]
    )
    subaccount_client = FakeSubaccountLookupClient({MASTER_A: [subaccount_row(SUBACCOUNT)]})
    monkeypatch.setattr(discovery_service, "HyperliquidLeaderboardClient", leaderboard)
    monkeypatch.setattr(discovery_service, "HyperliquidClient", subaccount_client)
    settings = Settings(_env_file=None)

    async with DiscoveryFetchContext(
        settings,
        recently_checked_masters=frozenset({MASTER_C}),
    ) as context:
        day = await fetch_hyperliquid_leaderboard_source(
            "hyperliquid_leaderboard_day",
            limit=2,
            settings=settings,
            context=context,
        )
        day_checks = context.take_pending_subaccount_checks()
        week = await fetch_hyperliquid_leaderboard_source(
            "hyperliquid_leaderboard_week",
            limit=2,
            settings=settings,
            context=context,
        )
        week_checks = context.take_pending_subaccount_checks()

    # Day lists A and B, week lists B and C; C was checked by an earlier run.
    assert leaderboard.downloads == 1
    assert subaccount_client.lookups == [MASTER_A, MASTER_B]
    assert subaccount_client.closed is True
    assert [candidate.wallet_address for candidate in day.candidates] == [
        MASTER_A,
        SUBACCOUNT,
        MASTER_B,
    ]
    assert [candidate.wallet_address for candidate in week.candidates] == [MASTER_B, MASTER_C]
    assert day.metadata["subaccountLookups"] == 2
    assert week.metadata["subaccountLookups"] == 0
    assert week.metadata["subaccountLookupsSkipped"] == 2
    assert set(day_checks) == {MASTER_A, MASTER_B}
    assert len(day_checks[MASTER_A].subaccounts) == 1
    assert set(week_checks) == {MASTER_B}


@pytest.mark.asyncio
async def test_discarded_subaccount_checks_are_not_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FakeSubaccountLookupClient({}))
    async with DiscoveryFetchContext(Settings(_env_file=None)) as context:
        await context.master_subaccounts(MASTER_A)
        context.discard_pending_subaccount_checks()
        discarded = context.take_pending_subaccount_checks()
        # A later source that lists the same master reuses the lookup and records it.
        await context.master_subaccounts(MASTER_A)
        reused = context.take_pending_subaccount_checks()

    assert discarded == {}
    assert set(reused) == {MASTER_A}
    assert context.subaccount_lookups == 1


def test_candidate_backfill_covers_pool_import_only_when_it_fetches_as_much() -> None:
    def covers(**overrides: Any) -> bool:
        values: dict[str, Any] = {
            "discovery_candidate_backfill_days": 90,
            "discovery_candidate_backfill_max_pages": 5,
            "discovery_candidate_backfill_target_fills": 10000,
            "pool_fill_import_days": 90,
            "pool_fill_import_max_pages": 5,
        }
        return candidate_backfill_covers_pool_import(
            Settings(_env_file=None, **(values | overrides))
        )

    assert covers() is True
    assert covers(discovery_candidate_backfill_days=60) is False
    assert covers(discovery_candidate_backfill_max_pages=2) is False
    assert covers(discovery_candidate_backfill_target_fills=4000) is False


def test_hypertracker_segment_wallets_url_uses_configured_base_url() -> None:
    settings = Settings(discovery_hypertracker_static_base_url="https://example.com/aggregator/")

    assert (
        hypertracker_segment_wallets_url("hypertracker_humble_earner", settings)
        == "https://example.com/aggregator/segment_11_wallets.json"
    )


def test_hypertracker_leaderboard_url_uses_configured_base_url() -> None:
    settings = Settings(discovery_hypertracker_static_base_url="https://example.com/aggregator/")

    assert (
        hypertracker_leaderboard_url("hypertracker_avg_daily_perp_pnl", settings)
        == "https://example.com/aggregator/avg_daily_perp_pnl_leaderboard.json"
    )


class FakeUnifiedSubaccountClient:
    def __init__(self, *, spot_state: dict[str, Any] | None = None) -> None:
        self.spot_state = spot_state
        self.requests: list[str] = []

    async def post_info(self, payload: dict) -> list[dict]:
        assert payload["type"] == "subAccounts"
        self.requests.append("subAccounts")
        subaccount: dict[str, Any] = {
            "clearinghouseState": {
                "assetPositions": [],
                "marginSummary": {"accountValue": "0"},
                "withdrawable": "0",
            },
            "name": "Unified Sub",
            "subAccountUser": "0x2222222222222222222222222222222222222222",
        }
        if self.spot_state is not None:
            subaccount["spotState"] = self.spot_state
        return [subaccount]

    async def user_abstraction(self, *, user: str) -> str:
        self.requests.append("userAbstraction")
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        self.requests.append("spotClearinghouseState")
        return {
            "balances": [{"coin": "USDC", "hold": "0", "total": "200"}],
            "tokenToAvailableAfterMaintenance": [[0, "200"]],
        }


def leaderboard_row(address: str, *, day: str, week: str) -> dict[str, Any]:
    return {
        "ethAddress": address,
        "accountValue": "50000",
        "displayName": None,
        "windowPerformances": [
            ["day", {"pnl": day, "roi": "0.1"}],
            ["week", {"pnl": week, "roi": "0.1"}],
        ],
    }


def subaccount_row(address: str) -> dict[str, Any]:
    return {
        "clearinghouseState": {
            "assetPositions": [],
            "marginSummary": {"accountValue": "5000"},
            "withdrawable": "5000",
        },
        "name": "Sub",
        "subAccountUser": address,
    }


class FakeLeaderboardClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.downloads = 0

    def __call__(self, *_args: object) -> FakeLeaderboardClient:
        return self

    async def get_leaderboard(self) -> dict[str, Any]:
        self.downloads += 1
        return {"leaderboardRows": self.rows}


class FakeSubaccountLookupClient:
    def __init__(self, subaccounts_by_master: dict[str, list[dict[str, Any]]]) -> None:
        self.subaccounts_by_master = subaccounts_by_master
        self.lookups: list[str] = []
        self.closed = False

    def __call__(self, *_args: object) -> FakeSubaccountLookupClient:
        return self

    def open(self) -> FakeSubaccountLookupClient:
        return self

    async def aclose(self) -> None:
        self.closed = True

    async def post_info(self, payload: dict[str, Any]) -> list[dict[str, Any]] | None:
        assert payload["type"] == "subAccounts"
        self.lookups.append(payload["user"])
        return self.subaccounts_by_master.get(payload["user"])
