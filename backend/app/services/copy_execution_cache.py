"""In-process caches that keep slow lookups off the copy execution hot path.

The trading worker owns one instance of each cache and shares it between the
realtime, execution, and warm-up loops. Every cache is optional for callers, so
services and tests without a cache keep their direct, uncached behavior.
"""

import time
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass


class AllocationSnapshotCache[T]:
    """Latest copy allocation snapshot with a version for the active source set."""

    def __init__(
        self,
        *,
        max_age_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self._clock = clock
        self._value: T | None = None
        self._stored_at = 0.0
        self._signature: Hashable | None = None
        self.version = 0

    def store(self, value: T, *, signature: Hashable) -> None:
        if signature != self._signature:
            self.version += 1
            self._signature = signature
        self._value = value
        self._stored_at = self._clock()

    def fresh(self) -> T | None:
        if self._value is None or self._clock() - self._stored_at > self.max_age_seconds:
            return None
        return self._value


@dataclass(frozen=True)
class CachedSourceState[T]:
    state: T
    fetched_at: float


class SourceAccountStateCache[T]:
    """Recent source account states keyed by source wallet and perp dex."""

    def __init__(
        self,
        *,
        max_age_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self._clock = clock
        self._entries: dict[tuple[str, str], CachedSourceState[T]] = {}

    def get(self, source_wallet: str, dex: str) -> CachedSourceState[T] | None:
        entry = self._entries.get((source_wallet.lower(), dex))
        if entry is None or self._clock() - entry.fetched_at > self.max_age_seconds:
            return None
        return entry

    def now(self) -> float:
        return self._clock()

    def store(self, source_wallet: str, dex: str, state: T, *, requested_at: float) -> None:
        """Store a state fetched by a request that started at ``requested_at``.

        The age counts from the request start, and a state whose request started
        before the stored one finished is dropped, so a slow fetch never replaces
        a newer state.
        """

        key = (source_wallet.lower(), dex)
        current = self._entries.get(key)
        if current is not None and current.fetched_at > requested_at:
            return
        self._entries[key] = CachedSourceState(state=state, fetched_at=requested_at)

    def retain(self, source_wallets: set[str]) -> None:
        """Drop states for sources that are no longer copied."""

        keep = {wallet.lower() for wallet in source_wallets}
        for key in [key for key in self._entries if key[0] not in keep]:
            del self._entries[key]

    def due_for_refresh(
        self,
        source_wallets: Iterable[str],
        *,
        refresh_after_seconds: float,
        keep_warm: Callable[[T], bool],
    ) -> list[tuple[str, str]]:
        """Return (wallet, dex) keys the warm-up loop should fetch.

        Sources without any state get their default dex fetched once. Known
        states are refetched when they age past the refresh interval and
        ``keep_warm`` says a later fill could reuse them.
        """

        now = self._clock()
        due: list[tuple[str, str]] = []
        for wallet in sorted({wallet.lower() for wallet in source_wallets}):
            keys = sorted(key for key in self._entries if key[0] == wallet)
            if not keys:
                due.append((wallet, ""))
                continue
            for key in keys:
                entry = self._entries[key]
                if now - entry.fetched_at >= refresh_after_seconds and keep_warm(entry.state):
                    due.append(key)
        return due


class LiveCopyLaneSyncTracker:
    """Remembers lane synchronizations so unchanged inputs skip the database work.

    Keys include the allocation version and the account lifecycle signature, so
    keys from older states never match again and the set only needs a size bound.
    """

    def __init__(self, *, max_keys: int = 256) -> None:
        self._max_keys = max_keys
        self._synced: set[Hashable] = set()

    def is_synced(self, key: Hashable) -> bool:
        return key in self._synced

    def mark_synced(self, key: Hashable) -> None:
        if len(self._synced) >= self._max_keys:
            self._synced.clear()
        self._synced.add(key)
