from decimal import Decimal

from app.core.config import Settings
from app.services.discovery_service import (
    HYPERDASH_PNL_COHORT_SOURCES,
    HYPERTRACKER_SEGMENT_SOURCES,
    hyperdash_cohort_id_from_url,
    hypertracker_segment_candidate,
    hypertracker_segment_wallets_url,
    normalize_requested_sources,
)


def test_hypertracker_sources_are_known_discovery_sources() -> None:
    assert normalize_requested_sources(
        [
            "hypertracker_money_printer",
            "hypertracker_smart_money",
            "hypertracker_grinder",
            "hypertracker_humble_earner",
        ]
    ) == [
        "hypertracker_money_printer",
        "hypertracker_smart_money",
        "hypertracker_grinder",
        "hypertracker_humble_earner",
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
    assert candidate.account_value == Decimal("1500000")
    assert candidate.source_pnl == Decimal("300000")
    assert candidate.source_roi == Decimal("25.00")
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


def test_hypertracker_segment_wallets_url_uses_configured_base_url() -> None:
    settings = Settings(
        discovery_hypertracker_static_base_url="https://example.com/aggregator/"
    )

    assert (
        hypertracker_segment_wallets_url("hypertracker_humble_earner", settings)
        == "https://example.com/aggregator/segment_11_wallets.json"
    )
