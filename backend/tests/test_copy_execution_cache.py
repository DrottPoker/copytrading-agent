"""In-process caches on the copy execution hot path."""

from app.services.copy_execution_cache import (
    AllocationSnapshotCache,
    LiveCopyLaneSyncTracker,
    SourceAccountStateCache,
)


class ManualClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_allocation_snapshot_is_fresh_until_its_max_age() -> None:
    clock = ManualClock()
    cache: AllocationSnapshotCache[dict[str, int]] = AllocationSnapshotCache(
        max_age_seconds=30,
        clock=clock,
    )
    assert cache.fresh() is None

    cache.store({"0xa": 1}, signature=frozenset({"0xa"}))
    clock.now += 30
    assert cache.fresh() == {"0xa": 1}

    clock.now += 0.1
    assert cache.fresh() is None


def test_allocation_version_only_changes_with_the_active_source_set() -> None:
    cache: AllocationSnapshotCache[dict[str, int]] = AllocationSnapshotCache(max_age_seconds=30)

    cache.store({"0xa": 1}, signature=frozenset({"0xa"}))
    first = cache.version
    cache.store({"0xa": 2}, signature=frozenset({"0xa"}))
    assert cache.version == first

    cache.store({"0xa": 2, "0xb": 1}, signature=frozenset({"0xa", "0xb"}))
    assert cache.version == first + 1


def test_source_state_expires_and_is_keyed_case_insensitively() -> None:
    clock = ManualClock()
    cache: SourceAccountStateCache[str] = SourceAccountStateCache(max_age_seconds=15, clock=clock)

    cache.store("0xABC", "", "state", requested_at=clock.now)
    entry = cache.get("0xabc", "")
    assert entry is not None
    assert entry.state == "state"
    assert cache.get("0xabc", "xyz") is None

    clock.now += 15.1
    assert cache.get("0xabc", "") is None


def test_source_state_age_counts_from_the_request_start() -> None:
    clock = ManualClock()
    cache: SourceAccountStateCache[str] = SourceAccountStateCache(max_age_seconds=15, clock=clock)
    requested_at = clock.now

    clock.now += 10
    cache.store("0xa", "", "state", requested_at=requested_at)
    clock.now += 5.1

    assert cache.get("0xa", "") is None


def test_slow_fetch_never_replaces_a_newer_state() -> None:
    clock = ManualClock()
    cache: SourceAccountStateCache[str] = SourceAccountStateCache(max_age_seconds=15, clock=clock)
    slow_request_started = clock.now

    clock.now += 1
    cache.store("0xa", "", "newer", requested_at=clock.now)
    clock.now += 1
    cache.store("0xa", "", "older", requested_at=slow_request_started)

    entry = cache.get("0xa", "")
    assert entry is not None
    assert entry.state == "newer"


def test_retain_drops_sources_that_are_no_longer_copied() -> None:
    cache: SourceAccountStateCache[str] = SourceAccountStateCache(max_age_seconds=15)
    cache.store("0xa", "", "a", requested_at=cache.now())
    cache.store("0xb", "", "b", requested_at=cache.now())

    cache.retain({"0xA"})

    assert cache.get("0xa", "") is not None
    assert cache.get("0xb", "") is None


def test_due_for_refresh_fetches_unseen_sources_and_keeps_open_positions_warm() -> None:
    clock = ManualClock()
    cache: SourceAccountStateCache[str] = SourceAccountStateCache(max_age_seconds=15, clock=clock)
    cache.store("0xholding", "", "positions", requested_at=clock.now)
    cache.store("0xholding", "xyz", "positions", requested_at=clock.now)
    cache.store("0xflat", "", "flat", requested_at=clock.now)

    def keep_warm(state: str) -> bool:
        return state == "positions"

    sources = {"0xholding", "0xflat", "0xNEW"}
    assert cache.due_for_refresh(sources, refresh_after_seconds=5, keep_warm=keep_warm) == [
        ("0xnew", "")
    ]

    clock.now += 5
    assert cache.due_for_refresh(sources, refresh_after_seconds=5, keep_warm=keep_warm) == [
        ("0xholding", ""),
        ("0xholding", "xyz"),
        ("0xnew", ""),
    ]


def test_lane_sync_tracker_remembers_keys_within_its_bound() -> None:
    tracker = LiveCopyLaneSyncTracker(max_keys=2)
    tracker.mark_synced("a")
    tracker.mark_synced("b")
    assert tracker.is_synced("a")
    assert tracker.is_synced("b")

    tracker.mark_synced("c")

    assert tracker.is_synced("c")
    assert not tracker.is_synced("a")
