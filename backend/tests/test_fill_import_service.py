"""Fill window reading: a page limit must cut the oldest history, never the newest."""

from typing import Any

import pytest

from app.core.config import Settings
from app.services.fill_import_service import FillPageReader, read_fill_window

PAGE = 2000
END_MS = 1_800_000_000_000
DAY_MS = 86_400_000
START_MS = END_MS - 90 * DAY_MS


def fill(tid: int, time_ms: int, *, direction: str = "Open Long") -> dict[str, Any]:
    return {
        "coin": "BTC" if direction != "Buy" else "@107",
        "px": "100",
        "sz": "1",
        "side": "B",
        "time": time_ms,
        "dir": direction,
        "startPosition": "0",
        "closedPnl": "0",
        "fee": "0.01",
        "tid": tid,
    }


def evenly_spread(count: int, *, first_ms: int, last_ms: int) -> list[dict[str, Any]]:
    step = (last_ms - first_ms) / max(count - 1, 1)
    return [fill(index + 1, int(first_ms + index * step)) for index in range(count)]


class FakeHyperliquid:
    """Answers like Hyperliquid: by time gives the oldest page of a range, userFills the newest."""

    def __init__(self, fills: list[dict[str, Any]]) -> None:
        self.fills = sorted(fills, key=lambda item: (item["time"], item["tid"]))
        self.requests: list[str] = []

    async def user_fills_by_time(
        self,
        *,
        user: str,
        start_time_ms: int,
        end_time_ms: int | None = None,
        aggregate_by_time: bool = False,
    ) -> list[dict[str, Any]]:
        self.requests.append("userFillsByTime")
        end = end_time_ms if end_time_ms is not None else 2**63
        return [item for item in self.fills if start_time_ms <= item["time"] <= end][:PAGE]

    async def user_fills(self, *, user: str, aggregate_by_time: bool = False) -> list[dict]:
        self.requests.append("userFills")
        return list(reversed(self.fills[-PAGE:]))


async def read(
    fills: list[dict[str, Any]],
    *,
    max_pages: int = 5,
    target_fills: int = 10_000,
    newest_page_available: bool = True,
) -> tuple[Any, list[dict[str, Any]], FakeHyperliquid]:
    client = FakeHyperliquid(fills)
    reader = FillPageReader(
        client=client,  # type: ignore[arg-type]
        user="0x" + "1" * 40,
        start_time_ms=START_MS,
        end_time_ms=END_MS,
        max_pages=max_pages,
        aggregate_by_time=False,
        settings=Settings(_env_file=None, fill_import_market_filter="perp"),
    )
    window = await read_fill_window(
        reader,
        target_fills=target_fills,
        newest_page_available=newest_page_available,
    )
    return window, reader.records_between(window.history_start_ms, window.history_end_ms), client


def tids(records: list[dict[str, Any]]) -> set[str]:
    return {record["external_fill_id"] for record in records}


@pytest.mark.asyncio
async def test_a_wallet_with_less_than_a_page_takes_one_request() -> None:
    fills = evenly_spread(800, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, client = await read(fills)

    assert window.complete is True
    assert len(records) == 800
    assert client.requests == ["userFillsByTime"]


@pytest.mark.asyncio
async def test_a_window_that_fits_the_page_budget_is_read_whole() -> None:
    fills = evenly_spread(7_000, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, client = await read(fills)

    assert window.complete is True
    assert tids(records) == {str(item["tid"]) for item in fills}
    assert len(client.requests) <= 5


@pytest.mark.asyncio
async def test_a_busy_wallet_keeps_its_newest_fills_without_a_gap() -> None:
    # 30,000 fills cannot fit five pages; the old reader kept the oldest 10,000.
    fills = evenly_spread(30_000, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, client = await read(fills)

    expected = {str(item["tid"]) for item in fills if item["time"] >= window.history_start_ms}
    assert window.complete is False
    assert window.history_end_ms == END_MS
    assert str(fills[-1]["tid"]) in tids(records)
    # Every fill from the start of the kept history to now is there.
    assert tids(records) == expected
    assert len(records) >= 4_000
    assert len(client.requests) == 5
    assert client.requests[:2] == ["userFillsByTime", "userFills"]


@pytest.mark.asyncio
async def test_activity_that_rises_lately_is_read_back_from_the_newest_fill() -> None:
    quiet = evenly_spread(2_500, first_ms=START_MS + DAY_MS, last_ms=END_MS - 30 * DAY_MS)
    busy = [
        fill(10_000 + index, END_MS - 30 * DAY_MS + 1 + index * 200_000) for index in range(12_000)
    ]

    window, records, _client = await read(quiet + busy)

    newest = max(busy, key=lambda item: item["time"])
    assert str(newest["tid"]) in tids(records)
    assert tids(records) == {
        str(item["tid"]) for item in quiet + busy if item["time"] >= window.history_start_ms
    }


@pytest.mark.asyncio
async def test_bursts_on_one_timestamp_are_not_lost_at_window_edges() -> None:
    fills = []
    tid = 0
    for burst in range(600):
        time_ms = END_MS - DAY_MS + burst * 100_000
        for _ in range(9):
            tid += 1
            fills.append(fill(tid, time_ms))

    window, records, _client = await read(fills, max_pages=10)

    assert window.complete is True
    assert len(records) == len(fills)


@pytest.mark.asyncio
async def test_the_fill_target_cuts_the_oldest_fills() -> None:
    fills = evenly_spread(5_000, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, _client = await read(fills, target_fills=3_000)

    assert window.complete is False
    assert len(records) == 3_000
    assert tids(records) == {str(item["tid"]) for item in fills[-3_000:]}


@pytest.mark.asyncio
async def test_a_single_page_budget_keeps_the_oldest_page_and_says_where_it_ends() -> None:
    fills = evenly_spread(5_000, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, _client = await read(fills, max_pages=1)

    # The import then records this end as its poll time, so the next poll continues here.
    assert window.complete is False
    assert window.history_end_ms == fills[PAGE - 1]["time"]
    assert len(records) == PAGE


@pytest.mark.asyncio
async def test_a_first_page_of_spot_fills_does_not_stop_the_reader() -> None:
    spot = [fill(index + 1, START_MS + DAY_MS + index, direction="Buy") for index in range(PAGE)]
    perp = evenly_spread(500, first_ms=END_MS - 10 * DAY_MS, last_ms=END_MS - 1000)
    for index, item in enumerate(perp):
        item["tid"] = 100_000 + index

    window, records, _client = await read(spot + perp)

    assert window.complete is True
    assert len(records) == 500


@pytest.mark.asyncio
async def test_without_the_newest_page_reading_goes_forward_and_reports_where_it_stopped() -> None:
    # An import that ends at a fixed time cannot anchor on the newest page.
    fills = evenly_spread(30_000, first_ms=START_MS + DAY_MS, last_ms=END_MS - 1000)

    window, records, client = await read(fills, newest_page_available=False)

    assert window.complete is False
    assert window.history_start_ms == START_MS
    assert window.history_end_ms == records[-1]["timestamp_ms"]
    assert tids(records) == {str(item["tid"]) for item in fills[: len(records)]}
    assert "userFills" not in client.requests


@pytest.mark.asyncio
async def test_old_activity_is_kept_when_the_window_ends_at_a_fixed_time() -> None:
    burst = evenly_spread(2_400, first_ms=START_MS + DAY_MS, last_ms=START_MS + DAY_MS + 3_600_000)

    window, records, _client = await read(burst, max_pages=3, newest_page_available=False)

    assert window.complete is True
    assert len(records) == 2_400
