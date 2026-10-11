"""Discovery candidate backfill: equity precheck and fill paging on a real database."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import DiscoveryWalletCandidate, WalletFill, WatchedWallet
from app.schemas.fill import WalletFillImportRequest
from app.services import discovery_service
from app.services.discovery_service import (
    DiscoverySourceResult,
    backfill_discovery_candidates,
    run_discovery_import,
)
from app.services.fill_import_service import import_address_fills
from app.services.pool_fill_import_service import load_due_pool_fill_targets

pytestmark = pytest.mark.integration

DAY_MS = 86_400_000
DRAWDOWN_WALLET = "0x" + "d" * 40
LOSING_WALLET = "0x" + "e" * 40
HEALTHY_WALLET = "0x" + "f" * 40


def curve(values: list[tuple[str, str]]) -> list[Any]:
    """Daily perp portfolio history ending now, from (account value, cumulative PnL)."""

    end_ms = int(datetime.now(UTC).timestamp() * 1000)
    start_ms = end_ms - (len(values) - 1) * DAY_MS
    points = [(start_ms + index * DAY_MS, value, pnl) for index, (value, pnl) in enumerate(values)]
    return [
        [
            "perpMonth",
            {
                "accountValueHistory": [[time_ms, value] for time_ms, value, _ in points],
                "pnlHistory": [[time_ms, pnl] for time_ms, _, pnl in points],
                "vlm": "0",
            },
        ]
    ]


PORTFOLIOS = {
    # 2000 to 1000 is a 50% drawdown, past the 40% discovery limit.
    DRAWDOWN_WALLET: curve([("2000", "0"), ("2000", "0"), ("1000", "-1000"), ("1100", "-900")]),
    # A steady loss with a small drawdown: rejected only for its negative return.
    LOSING_WALLET: curve([("2000", "0"), ("1960", "-40"), ("1920", "-80"), ("1900", "-100")]),
    HEALTHY_WALLET: curve([("2000", "0"), ("2050", "50"), ("2100", "100"), ("2150", "150")]),
}


def hyperliquid_fill(tid: int, time_ms: int) -> dict[str, Any]:
    return {
        "coin": "BTC",
        "px": "100",
        "sz": "1",
        "side": "B",
        "time": time_ms,
        "startPosition": "0",
        "dir": "Open Long",
        "closedPnl": "0",
        "hash": f"0x{tid:064x}",
        "oid": tid,
        "crossed": True,
        "fee": "0.01",
        "tid": tid,
        "feeToken": "USDC",
    }


class FakeHyperliquidClient:
    instances: list[FakeHyperliquidClient] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.portfolio_requests: list[str] = []
        self.fill_requests: list[tuple[str, int]] = []
        FakeHyperliquidClient.instances.append(self)

    async def __aenter__(self) -> FakeHyperliquidClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def portfolio(self, *, user: str) -> list[Any]:
        self.portfolio_requests.append(user)
        return PORTFOLIOS[user]

    async def user_fills_by_time(self, *, user: str, start_time_ms: int, **_: object) -> list:
        self.fill_requests.append((user, start_time_ms))
        return []


def settings() -> Settings:
    return Settings(
        _env_file=None,
        scoring_equity_min_points=3,
        scoring_equity_min_coverage_days=2,
        scoring_equity_min_account_value_usd=Decimal("10"),
        fill_import_storage_guard_enabled=False,
    )


@pytest.mark.asyncio
async def test_candidates_failing_the_equity_curve_never_fetch_fills(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeHyperliquidClient.instances = []
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FakeHyperliquidClient)
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                DiscoveryWalletCandidate(
                    wallet_address=address,
                    source="test",
                    status="accepted",
                    backfill_status="not_started",
                )
                for address in (DRAWDOWN_WALLET, LOSING_WALLET, HEALTHY_WALLET)
            ]
        )
        await session.commit()

        result = await backfill_discovery_candidates(
            session,
            source=None,
            limit=10,
            retry_failed=False,
            settings=settings(),
        )
        candidates = {
            candidate.wallet_address: candidate
            for candidate in (await session.scalars(select(DiscoveryWalletCandidate))).all()
        }

    [client] = FakeHyperliquidClient.instances
    fetched_fills_for = {user for user, _ in client.fill_requests}
    assert sorted(client.portfolio_requests) == sorted(PORTFOLIOS)
    assert fetched_fills_for == {HEALTHY_WALLET}
    assert result.reject_reasons["max_drawdown_too_high"] == 1
    assert result.reject_reasons["equity_return_not_positive"] == 1
    assert candidates[DRAWDOWN_WALLET].fail_reason == "max_drawdown_too_high"
    assert candidates[DRAWDOWN_WALLET].max_drawdown_pct == Decimal("0.500000")
    assert candidates[DRAWDOWN_WALLET].backfill_fetched_count == 0
    assert candidates[LOSING_WALLET].fail_reason == "equity_return_not_positive"
    # The healthy wallet passed the curve and was judged on its fills (none here).
    assert candidates[HEALTHY_WALLET].fail_reason == "no_perp_fills"


class PagedFillClient:
    """Two full pages whose boundary falls inside a burst of same-millisecond fills."""

    def __init__(self, start_ms: int) -> None:
        self.burst_ms = start_ms + 1_000_000
        first_page = [hyperliquid_fill(tid, start_ms + tid) for tid in range(1, 1998)]
        burst = [hyperliquid_fill(tid, self.burst_ms) for tid in range(1998, 2006)]
        self.pages = [first_page + burst[:3], burst + [hyperliquid_fill(2006, self.burst_ms + 5)]]
        self.requests: list[int] = []

    async def user_fills_by_time(self, *, start_time_ms: int, **_: object) -> list:
        self.requests.append(start_time_ms)
        return self.pages[len(self.requests) - 1] if len(self.requests) <= len(self.pages) else []


@pytest.mark.asyncio
async def test_fills_sharing_the_last_timestamp_of_a_page_are_not_lost(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    start_ms = int((datetime.now(UTC) - timedelta(days=30)).timestamp() * 1000)
    client = PagedFillClient(start_ms)
    async with integration_sessionmaker() as session:
        result = await import_address_fills(
            session,
            address=HEALTHY_WALLET,
            payload=WalletFillImportRequest(
                start_time_ms=start_ms,
                max_pages=3,
                target_fills=10000,
            ),
            client=client,  # type: ignore[arg-type]
        )
        await session.commit()
        stored = await session.scalar(select(func.count()).select_from(WalletFill))

    # The second page restarts at the burst timestamp instead of one millisecond after it.
    assert client.requests[1] == client.burst_ms
    assert stored == 2006
    assert result.inserted == 2006


@pytest.mark.asyncio
async def test_a_discovery_run_finishes_candidates_an_interrupted_run_left_behind(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_new_candidates(*_args: object, **_kwargs: object) -> DiscoverySourceResult:
        return DiscoverySourceResult(
            fetched=0,
            skipped=0,
            skip_reasons={},
            candidates=[],
            metadata={},
        )

    FakeHyperliquidClient.instances = []
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FakeHyperliquidClient)
    monkeypatch.setattr(discovery_service, "fetch_discovery_source", no_new_candidates)
    interrupted_run = uuid4()
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                # Prefiltered but never backfilled before the worker stopped.
                DiscoveryWalletCandidate(
                    wallet_address=HEALTHY_WALLET,
                    source="hyperliquid_leaderboard_week",
                    status="accepted",
                    backfill_status="not_started",
                    last_import_run_id=interrupted_run,
                ),
                # Imported but never prefiltered before the worker stopped.
                DiscoveryWalletCandidate(
                    wallet_address=DRAWDOWN_WALLET,
                    source="hyperliquid_leaderboard_week",
                    status="discovered",
                    source_account_value_usd=Decimal("50000"),
                    source_pnl_usd=Decimal("5000"),
                    source_roi_pct=Decimal("0.1"),
                    last_import_run_id=interrupted_run,
                ),
            ]
        )
        await session.commit()

        await run_discovery_import(
            session,
            sources=["hyperliquid_leaderboard_week"],
            settings=settings(),
        )
        candidates = {
            candidate.wallet_address: candidate
            for candidate in (await session.scalars(select(DiscoveryWalletCandidate))).all()
        }

    assert candidates[HEALTHY_WALLET].backfill_status == "succeeded"
    assert candidates[DRAWDOWN_WALLET].backfill_status == "succeeded"
    assert candidates[DRAWDOWN_WALLET].fail_reason == "max_drawdown_too_high"


def closed_trade_fills(start_ms: int, count: int) -> list[dict[str, Any]]:
    """Profitable long round trips, one per hour, that pass the trade quality checks."""

    fills: list[dict[str, Any]] = []
    for index in range(count):
        open_ms = start_ms + index * 3_600_000
        fills.append(hyperliquid_fill(2 * index + 1, open_ms))
        fills.append(
            hyperliquid_fill(2 * index + 2, open_ms + 1_800_000)
            | {"px": "102", "side": "A", "dir": "Close Long", "startPosition": "1"}
            | {"closedPnl": "2"}
        )
    return fills


class FillServingHyperliquidClient(FakeHyperliquidClient):
    fills: list[dict[str, Any]] = []

    async def user_fills_by_time(self, *, user: str, start_time_ms: int, **_: object) -> list:
        self.fill_requests.append((user, start_time_ms))
        return [fill for fill in self.fills if fill["time"] >= start_time_ms]


@pytest.mark.asyncio
async def test_a_promoted_candidate_continues_pool_import_from_its_backfill(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_ms = int((datetime.now(UTC) - timedelta(days=10)).timestamp() * 1000)
    FillServingHyperliquidClient.fills = closed_trade_fills(start_ms, 25)
    FakeHyperliquidClient.instances = []
    monkeypatch.setattr(discovery_service, "HyperliquidClient", FillServingHyperliquidClient)
    backfill_settings = settings().model_copy(
        update={"discovery_candidate_backfill_days": 90, "pool_fill_import_days": 90}
    )
    async with integration_sessionmaker() as session:
        session.add(
            DiscoveryWalletCandidate(
                wallet_address=HEALTHY_WALLET,
                source="test",
                status="accepted",
                backfill_status="not_started",
            )
        )
        await session.commit()

        result = await backfill_discovery_candidates(
            session,
            source=None,
            limit=10,
            retry_failed=False,
            settings=backfill_settings,
        )
        candidate = await session.scalar(select(DiscoveryWalletCandidate))
        wallet = await session.scalar(
            select(WatchedWallet).where(WatchedWallet.address == HEALTHY_WALLET)
        )
        due = await load_due_pool_fill_targets(
            session,
            limit=10,
            min_wallet_interval_seconds=1800,
        )

    latest_fill_ms = max(fill["time"] for fill in FillServingHyperliquidClient.fills)
    assert result.pool_inserted == 1
    assert candidate is not None and wallet is not None
    assert candidate.status == "promoted"
    # The backfill counts as the first pool poll, so the wallet is not due again yet.
    assert wallet.last_polled_at == candidate.last_backfilled_at
    assert wallet.last_seen_fill_at == datetime.fromtimestamp(latest_fill_ms / 1000, tz=UTC)
    assert due == []
