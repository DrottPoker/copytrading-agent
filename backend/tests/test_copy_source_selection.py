"""Copy sources keep their place near the cut-off instead of flipping every scoring."""

from typing import Any

import pytest

from app.core.config import Settings
from app.services.paper_trading_service import (
    paper_retained_status_reason,
    select_copy_candidates,
)


def eligible(count: int, *, copied: set[int] | None = None) -> list[dict[str, Any]]:
    """Eligible wallets ranked 1..count; `copied` holds the ranks that are copy sources."""

    return [
        {"address": f"0x{rank:02d}", "pool_rank": rank, "copied": rank in (copied or set())}
        for rank in range(1, count + 1)
    ]


def ranks(rows: list[dict[str, Any]]) -> list[int]:
    return [row["pool_rank"] for row in rows]


def test_without_copied_wallets_the_best_ranked_are_selected() -> None:
    selected = select_copy_candidates(eligible(15), top_count=10, retain_rank=12)

    assert ranks(selected) == list(range(1, 11))


def test_copied_wallet_keeps_its_place_inside_the_retain_rank() -> None:
    # Ranks 11 and 12 were copied before two newcomers passed them.
    rows = eligible(15, copied={1, 2, 3, 4, 5, 6, 7, 8, 11, 12})

    selected = select_copy_candidates(rows, top_count=10, retain_rank=12)

    assert ranks(selected) == [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]


def test_copied_wallet_below_the_retain_rank_is_replaced() -> None:
    rows = eligible(15, copied={1, 2, 3, 4, 5, 6, 7, 8, 9, 13})

    selected = select_copy_candidates(rows, top_count=10, retain_rank=12)

    assert ranks(selected) == list(range(1, 11))


def test_newcomers_always_come_from_the_top_ranks() -> None:
    rows = eligible(15, copied={11, 12})

    selected = select_copy_candidates(rows, top_count=10, retain_rank=12)

    assert ranks(selected) == [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]


def test_more_copied_wallets_than_places_keeps_the_best_ranked() -> None:
    rows = eligible(12, copied=set(range(1, 13)))

    selected = select_copy_candidates(rows, top_count=10, retain_rank=12)

    assert ranks(selected) == list(range(1, 11))


def test_retain_rank_equal_to_the_top_count_disables_the_margin() -> None:
    rows = eligible(15, copied={1, 2, 3, 4, 5, 6, 7, 8, 11, 12})

    selected = select_copy_candidates(rows, top_count=10, retain_rank=10)

    assert ranks(selected) == list(range(1, 11))


def test_a_small_pool_selects_everything_eligible() -> None:
    selected = select_copy_candidates(eligible(3, copied={2}), top_count=10, retain_rank=12)

    assert ranks(selected) == [1, 2, 3]


def test_retain_rank_must_cover_the_top_count() -> None:
    with pytest.raises(ValueError, match="trading_copy_source_retain_rank"):
        Settings(trading_copy_top_wallet_count=10, trading_copy_source_retain_rank=9)


def test_retained_source_below_the_minimum_score_is_explained() -> None:
    row = {
        "score": "42",
        "pool_rank": 3,
        "wallet_enabled": True,
        "polling_tier": "pool",
        "current_drawdown_status": "ok",
    }

    reason = paper_retained_status_reason(
        row,
        settings=Settings(trading_copy_min_source_score=50),
        has_realtime_slot=True,
    )

    assert reason == "score_below_copy_minimum"
