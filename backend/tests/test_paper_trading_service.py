from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db.models import PaperPosition, WalletMonitoringStat
from app.services import paper_trading_service
from app.services.copy_execution_cache import AllocationSnapshotCache, SourceAccountStateCache
from app.services.copy_signal_service import (
    ExecutionMarketPrices,
    SourceFillPart,
    SourceStateCache,
    build_execution_context,
    load_source_account_state,
    load_source_account_states,
    parse_source_leverages,
    parse_source_margin_modes,
    source_fill_age_exceeds_entry_limit,
    warm_source_state_cache,
)
from app.services.paper_trading_service import (
    CopyAllocationCache,
    apply_wallet_monitoring_snapshot,
    copy_allocations,
    live_open_copy_source_select,
    monitored_hours,
    open_copy_source_select,
    paper_monitor_status,
    paper_position_read,
    paper_source_status,
    paper_wallet_performance_reads,
    pnl_per_monitored_hour,
    select_realtime_slot_sources,
    wallet_monitoring_summary,
)
from app.services.realtime_subscription_state_service import RealtimeSubscriptionSnapshot
from app.services.trading_core import (
    build_client_order_id,
    trade_is_buy,
)


def test_source_position_margin_setting_parses_leverage_and_mode() -> None:
    payload = {
        "assetPositions": [
            {
                "position": {
                    "coin": "VVV",
                    "leverage": {"type": "isolated", "value": 1},
                    "szi": "2",
                }
            },
            {
                "position": {
                    "coin": "BTC",
                    "leverage": {"type": "cross", "value": 5},
                    "szi": "0.1",
                }
            },
        ]
    }

    assert parse_source_leverages(payload) == {
        "VVV": Decimal("1"),
        "BTC": Decimal("5"),
    }
    assert parse_source_margin_modes(payload) == {
        "VVV": "isolated",
        "BTC": "cross",
    }


def test_live_source_projection_retains_unresolved_orders_and_exits_without_positions() -> None:
    sql = str(
        live_open_copy_source_select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "trading_positions" in sql
    assert "trading_orders" in sql
    assert "live_copy_fill_states" in sql
    assert "accepted" in sql
    assert "partially_filled" in sql
    assert "retryable" in sql
    assert "flip_close" in sql


@pytest.mark.parametrize(
    ("has_realtime_slot", "can_open_new_positions", "open_position_count", "expected"),
    [
        (True, True, 1, "trading"),
        (True, False, 1, "retained"),
        (False, True, 1, "retained"),
        (False, False, 0, "waiting_for_slot"),
        (True, True, 0, "waiting_for_trades"),
        (True, False, 0, "retained"),
    ],
)
def test_paper_source_status_matches_current_slot_state(
    has_realtime_slot: bool,
    can_open_new_positions: bool,
    open_position_count: int,
    expected: str,
) -> None:
    assert (
        paper_source_status(
            has_realtime_slot=has_realtime_slot,
            can_open_new_positions=can_open_new_positions,
            open_position_count=open_position_count,
        )
        == expected
    )


def test_paper_monitor_status_uses_acknowledged_subscription_truth() -> None:
    snapshot = RealtimeSubscriptionSnapshot(
        status="connecting",
        desired_wallets=("0xconnected", "0xpending"),
        monitored_wallets=frozenset({"0xconnected"}),
        worker_role="trading",
        worker_instance_id="worker-1",
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert (
        paper_monitor_status(
            source_wallet="0xconnected",
            has_realtime_slot=True,
            realtime_monitoring=snapshot,
        )
        == "monitored"
    )
    assert (
        paper_monitor_status(
            source_wallet="0xpending",
            has_realtime_slot=True,
            realtime_monitoring=snapshot,
        )
        == "connecting"
    )
    assert (
        paper_monitor_status(
            source_wallet="0xoffline",
            has_realtime_slot=True,
            realtime_monitoring=snapshot,
        )
        == "offline"
    )
    assert (
        paper_monitor_status(
            source_wallet="0xwaiting",
            has_realtime_slot=False,
            realtime_monitoring=snapshot,
        )
        == "waiting"
    )


def test_wallet_performance_sorts_by_total_pnl_before_realized_pnl() -> None:
    snapshot = RealtimeSubscriptionSnapshot(
        status="connected",
        desired_wallets=(),
        monitored_wallets=frozenset(),
        worker_role="trading",
        worker_instance_id="worker-1",
        updated_at=datetime(2026, 7, 10, tzinfo=UTC),
    )

    rows = paper_wallet_performance_reads(
        allocations=[],
        positions=[
            {
                "source_wallet": "0xhighrealized",
                "unrealized_pnl_usd": Decimal("-9"),
                "current_notional_usd": Decimal("10"),
                "notional_usd": Decimal("10"),
                "margin_usd": Decimal("2"),
            }
        ],
        fill_performance_rows=[
            {
                "source_wallet": "0xhighrealized",
                "realized_pnl_usd": Decimal("10"),
            },
            {
                "source_wallet": "0xhightotal",
                "realized_pnl_usd": Decimal("2"),
            },
        ],
        source_allocations={},
        source_labels={},
        monitoring_stats={},
        realtime_monitoring=snapshot,
    )

    assert [row["source_wallet"] for row in rows] == [
        "0xhightotal",
        "0xhighrealized",
    ]


def test_paper_position_read_exposes_position_pnl_and_fill_counts() -> None:
    opened_at = datetime(2026, 1, 1, 12, tzinfo=UTC)
    created_at = opened_at + timedelta(milliseconds=150)
    position = PaperPosition(
        id=uuid4(),
        account_key="paper_test",
        source_wallet="0xsource",
        coin="HYPE",
        side="long",
        size=Decimal("2"),
        entry_price=Decimal("10"),
        notional_usd=Decimal("20"),
        leverage=Decimal("5"),
        margin_usd=Decimal("4"),
        realized_pnl_usd=Decimal("1.23"),
        fee_usd=Decimal("0.02"),
        opened_at=opened_at,
        created_at=created_at,
        updated_at=created_at,
    )

    read = paper_position_read(
        position,
        mark_price=Decimal("11.25"),
        price_updated_at=created_at,
        source_label="Test source",
        fill_counts=(4, 1),
    )

    assert read["realized_pnl_usd"] == Decimal("1.23")
    assert read["unrealized_pnl_usd"] == Decimal("2.50")
    assert read["add_fill_count"] == 4
    assert read["close_fill_count"] == 1
    assert read["entry_execution_delay_ms"] == 150


def test_source_fill_age_limit_allows_fresh_entries() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(seconds=5)).timestamp() * 1000)}

    assert not source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=15),
        now=now,
    )


def test_source_fill_age_limit_blocks_stale_entries() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(seconds=16)).timestamp() * 1000)}

    assert source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=15),
        now=now,
    )


def test_source_fill_age_limit_can_be_disabled() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    fill = {"timestampMs": int((now - timedelta(minutes=5)).timestamp() * 1000)}

    assert not source_fill_age_exceeds_entry_limit(
        fill,
        settings=Settings(trading_copy_max_entry_age_seconds=0),
        now=now,
    )


def test_build_client_order_id_is_deterministic_hyperliquid_cloid() -> None:
    first = build_client_order_id(
        account_key="live_1",
        source_wallet="0xABC",
        source_fill_id="123",
        sequence_index=0,
        action="open",
    )
    second = build_client_order_id(
        account_key="LIVE_1",
        source_wallet="0xabc",
        source_fill_id="123",
        sequence_index=0,
        action="open",
    )

    assert first == second
    assert first.startswith("0x")
    assert len(first) == 34


def test_trade_is_buy_matches_side_and_reduce_only() -> None:
    assert trade_is_buy(side="long", reduce_only=False) is True
    assert trade_is_buy(side="long", reduce_only=True) is False
    assert trade_is_buy(side="short", reduce_only=False) is False
    assert trade_is_buy(side="short", reduce_only=True) is True


def test_open_copy_source_select_includes_live_source_positions() -> None:
    compiled = str(
        open_copy_source_select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "paper_positions" in compiled
    assert "trading_positions" in compiled
    assert "trading_positions.account_type = 'live'" in compiled
    assert "trading_positions.source_wallet != '__exchange__'" in compiled


def test_live_open_copy_source_select_excludes_paper_and_exchange_positions() -> None:
    compiled = str(
        live_open_copy_source_select().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "paper_positions" not in compiled
    assert "trading_positions.account_type = 'live'" in compiled
    assert "trading_positions.source_wallet != '__exchange__'" in compiled


def test_live_realtime_slots_do_not_reserve_paper_only_sources() -> None:
    selected = select_realtime_slot_sources(
        open_source_wallets=["0xpaper", "0xlive"],
        live_open_source_wallets={"0xlive"},
        candidate_source_wallets=["0xcandidate1", "0xcandidate2"],
        live_trading_enabled=True,
        max_realtime_slots=3,
    )

    assert selected == ["0xlive", "0xcandidate1", "0xcandidate2"]


def test_paper_only_realtime_slots_still_prioritize_open_paper_sources() -> None:
    selected = select_realtime_slot_sources(
        open_source_wallets=["0xpaper", "0xlive"],
        live_open_source_wallets={"0xlive"},
        candidate_source_wallets=["0xcandidate1"],
        live_trading_enabled=False,
        max_realtime_slots=2,
    )

    assert selected == ["0xpaper", "0xlive"]


def test_wallet_monitoring_snapshot_accumulates_closes_and_restarts() -> None:
    wallet = "0x1111111111111111111111111111111111111111"
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    stat = WalletMonitoringStat(
        wallet_address=wallet,
        first_monitored_at=started_at,
        current_monitoring_started_at=started_at,
        last_monitored_at=started_at,
        total_monitored_seconds=0,
    )

    apply_wallet_monitoring_snapshot(
        {wallet: stat},
        monitored_wallets={wallet},
        observed_at=started_at + timedelta(seconds=30),
        max_gap_seconds=60,
    )

    assert stat.total_monitored_seconds == 30
    assert stat.current_monitoring_started_at == started_at
    assert stat.last_monitored_at == started_at + timedelta(seconds=30)

    apply_wallet_monitoring_snapshot(
        {wallet: stat},
        monitored_wallets=set(),
        observed_at=started_at + timedelta(minutes=10),
        max_gap_seconds=60,
    )

    assert stat.total_monitored_seconds == 90
    assert stat.current_monitoring_started_at is None
    assert stat.last_monitored_at == started_at + timedelta(seconds=90)

    apply_wallet_monitoring_snapshot(
        {wallet: stat},
        monitored_wallets={wallet},
        observed_at=started_at + timedelta(minutes=20),
        max_gap_seconds=60,
    )

    assert stat.total_monitored_seconds == 90
    assert stat.current_monitoring_started_at == started_at + timedelta(minutes=20)
    assert stat.last_monitored_at == started_at + timedelta(minutes=20)


def test_wallet_monitoring_summary_caps_open_session_read_time() -> None:
    wallet = "0x1111111111111111111111111111111111111111"
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    stat = WalletMonitoringStat(
        wallet_address=wallet,
        first_monitored_at=started_at,
        current_monitoring_started_at=started_at,
        last_monitored_at=started_at,
        total_monitored_seconds=120,
    )

    summary = wallet_monitoring_summary(
        stat,
        now=started_at + timedelta(minutes=10),
        max_gap_seconds=60,
    )

    assert summary.monitored_seconds == 180
    assert summary.current_monitoring_started_at == started_at


def test_monitored_hour_metrics_use_raw_seconds() -> None:
    assert monitored_hours(5400) == Decimal("1.5000")
    assert pnl_per_monitored_hour(Decimal("3"), 5400) == Decimal("2.0000")
    assert pnl_per_monitored_hour(Decimal("3"), 0) is None


def test_execution_context_uses_adverse_drift_for_long_entries() -> None:
    favorable = build_execution_context(
        fill={"coin": "HYPE", "price": "100"},
        part=source_part(side="long", action="open"),
        market_prices=ExecutionMarketPrices(
            prices={"HYPE": Decimal("99")},
            sources={"HYPE": "test_mid"},
        ),
        settings=Settings(),
        slippage_bps=Decimal("0"),
        latency_ms=0,
    )
    adverse = build_execution_context(
        fill={"coin": "HYPE", "price": "100"},
        part=source_part(side="long", action="open"),
        market_prices=ExecutionMarketPrices(
            prices={"HYPE": Decimal("101")},
            sources={"HYPE": "test_mid"},
        ),
        settings=Settings(),
        slippage_bps=Decimal("0"),
        latency_ms=0,
    )

    assert favorable is not None
    assert favorable.price_drift_bps == Decimal("0")
    assert adverse is not None
    assert adverse.price_drift_bps == Decimal("100")


def test_execution_context_uses_adverse_drift_for_short_entries() -> None:
    favorable = build_execution_context(
        fill={"coin": "HYPE", "price": "100"},
        part=source_part(side="short", action="open"),
        market_prices=ExecutionMarketPrices(
            prices={"HYPE": Decimal("101")},
            sources={"HYPE": "test_mid"},
        ),
        settings=Settings(),
        slippage_bps=Decimal("0"),
        latency_ms=0,
    )
    adverse = build_execution_context(
        fill={"coin": "HYPE", "price": "100"},
        part=source_part(side="short", action="open"),
        market_prices=ExecutionMarketPrices(
            prices={"HYPE": Decimal("99")},
            sources={"HYPE": "test_mid"},
        ),
        settings=Settings(),
        slippage_bps=Decimal("0"),
        latency_ms=0,
    )

    assert favorable is not None
    assert favorable.price_drift_bps == Decimal("0")
    assert adverse is not None
    assert adverse.price_drift_bps == Decimal("100")


@pytest.mark.asyncio
async def test_load_source_account_state_uses_unified_spot_equity_when_perp_zero() -> None:
    client = FakeUnifiedSourceClient()

    state = await load_source_account_state(
        client=client,
        source_wallet="0xsource",
        dex="xyz",
        unified_equity_cache={},
    )

    assert state.perp_equity == Decimal("200")
    assert state.skip_reason is None
    assert client.user_abstraction_calls == 1
    assert client.spot_state_calls == 1


class FakeUnifiedSourceClient:
    def __init__(self) -> None:
        self.user_abstraction_calls = 0
        self.spot_state_calls = 0

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict:
        return {
            "assetPositions": [],
            "marginSummary": {"accountValue": "0"},
        }

    async def user_abstraction(self, *, user: str) -> str:
        self.user_abstraction_calls += 1
        return "unifiedAccount"

    async def spot_clearinghouse_state(self, *, user: str) -> dict:
        self.spot_state_calls += 1
        return {
            "balances": [{"coin": "USDC", "total": "200", "hold": "0"}],
            "tokenToAvailableAfterMaintenance": [[0, "200"]],
        }


def source_part(*, side: str, action: str) -> SourceFillPart:
    return SourceFillPart(
        action=action,
        side=side,
        source_size=Decimal("1"),
        source_notional_usd=Decimal("100"),
        sequence_index=0,
        close_ratio=None,
        start_position=Decimal("0"),
    )


class CountingSourceClient:
    """Source info client that records clearinghouse lookups per dex."""

    def __init__(self, *, positions: dict[str, list[str]] | None = None) -> None:
        self.positions = {"": ["BTC"]} if positions is None else positions
        self.state_calls: list[tuple[str, str]] = []
        self.user_abstraction_calls = 0
        self.fail = False

    async def clearinghouse_state(self, *, user: str, dex: str | None = None) -> dict:
        self.state_calls.append((user, dex or ""))
        if self.fail:
            raise RuntimeError("offline")
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": coin,
                        "leverage": {"type": "cross", "value": 5},
                        "szi": "1",
                    }
                }
                for coin in self.positions.get(dex or "", [])
            ],
            "marginSummary": {"accountValue": "1000"},
        }

    async def user_abstraction(self, *, user: str) -> str:
        self.user_abstraction_calls += 1
        return "default"


def source_fill(direction: str, *, coin: str = "BTC", start_position: str = "1") -> dict:
    return {
        "coin": coin,
        "size": "1",
        "price": "100",
        "rawJson": {"dir": direction, "startPosition": start_position},
    }


def state_cache() -> SourceStateCache:
    return SourceAccountStateCache(max_age_seconds=15)


@pytest.mark.asyncio
async def test_cached_source_state_serves_entries_on_held_coins() -> None:
    client = CountingSourceClient()
    cache = state_cache()

    await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Open Long")], cache=cache
    )
    states = await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Open Long")], cache=cache
    )

    assert client.state_calls == [("0xsource", "")]
    assert states[""].perp_equity == Decimal("1000")


@pytest.mark.asyncio
async def test_exits_always_read_fresh_source_state() -> None:
    client = CountingSourceClient()
    cache = state_cache()
    await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Open Long")], cache=cache
    )

    await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Close Long")], cache=cache
    )

    assert len(client.state_calls) == 2


@pytest.mark.asyncio
async def test_entries_on_coins_without_cached_leverage_read_fresh_state() -> None:
    client = CountingSourceClient()
    cache = state_cache()
    await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Open Long")], cache=cache
    )

    await load_source_account_states(
        client=client,
        source_wallet="0xsource",
        fills=[source_fill("Open Long", coin="ETH", start_position="0")],
        cache=cache,
    )

    assert len(client.state_calls) == 2


@pytest.mark.asyncio
async def test_failed_source_state_fetch_is_not_cached() -> None:
    client = CountingSourceClient()
    client.fail = True
    cache = state_cache()

    states = await load_source_account_states(
        client=client, source_wallet="0xsource", fills=[source_fill("Open Long")], cache=cache
    )

    assert states[""].skip_reason == "source_account_state_fetch_failed"
    assert cache.get("0xsource", "") is None


@pytest.mark.asyncio
async def test_source_states_for_several_dexes_share_one_unified_equity_lookup() -> None:
    client = FakeUnifiedSourceClient()

    states = await load_source_account_states(
        client=client,
        source_wallet="0xsource",
        fills=[source_fill("Open Long", coin="xyz:GOLD"), source_fill("Open Long", coin="BTC")],
    )

    assert set(states) == {"", "xyz"}
    assert all(state.perp_equity == Decimal("200") for state in states.values())
    assert client.user_abstraction_calls == 1


@pytest.mark.asyncio
async def test_warm_up_fetches_new_sources_and_refreshes_sources_with_positions() -> None:
    client = CountingSourceClient(positions={"": ["BTC"]})
    clock_now = [0.0]
    cache: SourceStateCache = SourceAccountStateCache(
        max_age_seconds=15,
        clock=lambda: clock_now[0],
    )
    flat_client = CountingSourceClient(positions={})
    await warm_source_state_cache(
        client=flat_client, cache=cache, source_wallets={"0xflat"}, refresh_after_seconds=5
    )

    fetched = await warm_source_state_cache(
        client=client,
        cache=cache,
        source_wallets={"0xholding", "0xflat"},
        refresh_after_seconds=5,
    )
    assert fetched == 1
    assert client.state_calls == [("0xholding", "")]

    clock_now[0] = 5
    await warm_source_state_cache(
        client=client,
        cache=cache,
        source_wallets={"0xholding", "0xflat"},
        refresh_after_seconds=5,
    )
    assert client.state_calls == [("0xholding", ""), ("0xholding", "")]

    await warm_source_state_cache(
        client=client, cache=cache, source_wallets={"0xflat"}, refresh_after_seconds=5
    )
    assert cache.get("0xholding", "") is None


@pytest.mark.asyncio
async def test_copy_allocations_reuse_a_fresh_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshes: list[int] = []

    async def fake_refresh(*_args: object, **_kwargs: object) -> dict:
        refreshes.append(1)
        return {"0xsource": SimpleNamespace(active=True)}

    monkeypatch.setattr(paper_trading_service, "refresh_paper_copy_allocations", fake_refresh)
    cache: CopyAllocationCache = AllocationSnapshotCache(max_age_seconds=30)
    settings = Settings(_env_file=None)

    first = await copy_allocations(object(), settings=settings, cache=cache)  # type: ignore[arg-type]
    second = await copy_allocations(object(), settings=settings, cache=cache)  # type: ignore[arg-type]
    await copy_allocations(object(), settings=settings)  # type: ignore[arg-type]

    assert first is second
    assert len(refreshes) == 2
    assert cache.version == 1
