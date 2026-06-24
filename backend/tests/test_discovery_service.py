from decimal import Decimal

import pytest

from app.core.config import Settings
from app.services.discovery_service import (
    HYPERDASH_PNL_COHORT_SOURCES,
    HYPERTRACKER_LEADERBOARD_SOURCES,
    HYPERTRACKER_SEGMENT_SOURCES,
    hyperdash_cohort_id_from_url,
    hypertracker_leaderboard_candidate,
    hypertracker_leaderboard_url,
    hypertracker_segment_candidate,
    hypertracker_segment_wallets_url,
    normalize_requested_sources,
)
from app.services.hyperliquid_leaderboard_source import load_subaccount_wallet_candidates


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
        hyperdash_cohort_id_from_url(
            "https://hyperdash.com/explore/cohorts/extremely_profitable"
        )
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
    candidates = await load_subaccount_wallet_candidates(
        client=FakeUnifiedSubaccountClient(),
        master_address="0x1111111111111111111111111111111111111111",
        rank=1,
        display_name="Master",
        row={"windowPerformances": [["day", {"pnl": "10", "roi": "5"}]]},
        window="day",
        max_subaccounts=1,
    )

    assert len(candidates) == 1
    assert candidates[0].account_value == "200"


def test_hypertracker_segment_wallets_url_uses_configured_base_url() -> None:
    settings = Settings(
        discovery_hypertracker_static_base_url="https://example.com/aggregator/"
    )

    assert (
        hypertracker_segment_wallets_url("hypertracker_humble_earner", settings)
        == "https://example.com/aggregator/segment_11_wallets.json"
    )


def test_hypertracker_leaderboard_url_uses_configured_base_url() -> None:
    settings = Settings(
        discovery_hypertracker_static_base_url="https://example.com/aggregator/"
    )

    assert (
        hypertracker_leaderboard_url("hypertracker_avg_daily_perp_pnl", settings)
        == "https://example.com/aggregator/avg_daily_perp_pnl_leaderboard.json"
    )


class FakeUnifiedSubaccountClient:
    async def post_info(self, payload: dict) -> list[dict]:
        assert payload["type"] == "subAccounts"
        return [
            {
                "clearinghouseState": {
                    "assetPositions": [],
                    "marginSummary": {"accountValue": "0"},
                    "withdrawable": "0",
                },
                "name": "Unified Sub",
                "subAccountUser": "0x2222222222222222222222222222222222222222",
            }
        ]

    async def user_abstraction(self, *, user: str) -> str:
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        return {
            "balances": [{"coin": "USDC", "hold": "0", "total": "200"}],
            "tokenToAvailableAfterMaintenance": [[0, "200"]],
        }
