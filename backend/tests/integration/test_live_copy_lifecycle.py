from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import PaperTradingAccountConfig, Settings
from app.db.models import (
    LiveCopyFillState,
    PaperCopyAllocation,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletScore,
    WatchedWallet,
)
from app.services import live_trading_service
from app.services.live_copy_service import (
    finalize_live_copy_fill_disposition,
    repair_owned_live_source_positions_for_recovery,
    synchronize_live_copy_lanes,
)
from app.services.live_copy_state_service import (
    LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
    LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    LIVE_COPY_OUTCOME_RETRYABLE,
    LIVE_COPY_OUTCOME_TERMINAL_SKIP,
    activate_live_copy_account_sources,
    claim_live_copy_fill_part,
    ensure_live_copy_fill_plan_states,
    ensure_live_copy_source_state,
    get_live_copy_source_state,
    live_copy_unresolved_order_predicate,
    load_live_copy_recovery_candidate_fills,
    load_live_copy_source_eligibility_epochs,
    load_owned_live_copy_account_source_pairs,
    mark_live_copy_fill_baseline_ignored,
    mark_live_copy_fill_complete_if_durable,
    mark_live_copy_fill_terminal_skip,
    synchronize_live_copy_account_source_activity,
    synchronize_live_copy_source_activity,
)
from app.services.live_trading_service import (
    LiveCopyEntryLifecycleDeferred,
    submit_live_trade_intent,
)
from app.services.paper_trading_service import SourceFillPart, refresh_paper_copy_allocations
from app.services.trading_core import build_copy_trade_intent

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_global_source_epoch_activates_a_distinct_live_account_lane(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xepoch-source"
    live_account_key = "live_execution"
    paper_account_key = "paper_control"
    start_epoch = datetime.now(UTC) + timedelta(seconds=1)
    settings = Settings(
        live_trading_enabled=True,
        paper_copy_accounts=[
            PaperTradingAccountConfig(
                key=paper_account_key,
                label="Paper control account",
                starting_balance_usd=Decimal("1000"),
                enabled=True,
            )
        ],
    )

    live_account = TradingAccount(
        key=live_account_key,
        account_type="live",
        label="Live execution account",
        status="enabled",
        network="testnet",
        status_changed_at=start_epoch,
    )
    async with integration_sessionmaker() as session:
        session.add_all(
            [
                live_account,
                WatchedWallet(
                    address=source_wallet,
                    enabled=True,
                    eligible=True,
                    copy_enabled=False,
                    polling_tier="pool",
                ),
                WalletScore(
                    wallet_address=source_wallet,
                    score=Decimal("100"),
                    current_drawdown_status="ok",
                ),
            ]
        )
        await session.flush()

        await refresh_paper_copy_allocations(session, settings=settings)

        allocation = await session.scalar(
            select(PaperCopyAllocation).where(
                PaperCopyAllocation.account_key == paper_account_key,
                PaperCopyAllocation.source_wallet == source_wallet,
                PaperCopyAllocation.active.is_(True),
            )
        )
        assert allocation is not None
        assert allocation.account_key != live_account_key

        source_epochs = await load_live_copy_source_eligibility_epochs(session)
        assert source_epochs[source_wallet] is not None
        await synchronize_live_copy_lanes(
            session,
            accounts=[live_account],
            active_source_wallets=set(source_epochs),
        )
        source_state = await get_live_copy_source_state(
            session,
            account_key=live_account_key,
            source_wallet=source_wallet,
        )
        assert source_state is not None
        assert source_state.status == "active"
        assert source_state.entry_eligible is True
        assert source_state.activated_at == start_epoch
        assert source_state.baseline_completed_at is not None
        assert source_state.baseline_completed_at < start_epoch

        post_start_timestamp_ms = int(start_epoch.timestamp() * 1000)
        post_start_fill = wallet_fill(
            source_wallet,
            "first-post-start",
            timestamp_ms=post_start_timestamp_ms,
        )
        post_start_fill.received_at = start_epoch
        session.add(post_start_fill)
        await session.flush()
        part = SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("0"),
        )
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=source_fill(
                "first-post-start",
                timestamp_ms=post_start_timestamp_ms,
                direction="Open Long",
            ),
            planned_parts=(part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            first_observed_at=post_start_fill.received_at,
        )
        claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=source_fill(
                "first-post-start",
                timestamp_ms=post_start_timestamp_ms,
                direction="Open Long",
            ),
            part=part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        assert claim.claimed


@pytest.mark.asyncio
async def test_reselected_retained_lane_rebaselines_historical_entries(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xreselected-source"
    account_key = "live_reselected"
    first_epoch = datetime(2026, 7, 18, 10, tzinfo=UTC)
    reselected_epoch = first_epoch + timedelta(minutes=10)
    old_fill_observed_at = first_epoch + timedelta(minutes=5)
    async with integration_sessionmaker() as session:
        account = TradingAccount(
            key=account_key,
            account_type="live",
            label="Reselected retained lane",
            status="enabled",
            network="testnet",
            status_changed_at=first_epoch,
        )
        watched_wallet = WatchedWallet(
            address=source_wallet,
            enabled=True,
            eligible=True,
            copy_enabled=False,
            polling_tier="pool",
            copy_eligibility_started_at=first_epoch,
        )
        session.add_all([account, watched_wallet])
        await session.flush()

        await synchronize_live_copy_lanes(
            session,
            accounts=[account],
            active_source_wallets={source_wallet},
        )
        source_state = await get_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
        )
        assert source_state is not None
        assert source_state.activated_at == first_epoch
        assert source_state.entry_eligible is True

        session.add(
            TradingPosition(
                account_key=account_key,
                account_type="live",
                source_wallet=source_wallet,
                coin="BTC",
                side="long",
                size=Decimal("1"),
                entry_price=Decimal("10"),
                notional_usd=Decimal("10"),
                leverage=Decimal("1"),
                margin_mode="cross",
                margin_usd=Decimal("10"),
                realized_pnl_usd=Decimal("0"),
                fee_usd=Decimal("0"),
                opened_at=first_epoch,
            )
        )
        await session.flush()
        await synchronize_live_copy_lanes(
            session,
            accounts=[account],
            active_source_wallets=set(),
        )
        assert source_state.status == "active"
        assert source_state.entry_eligible is False

        old_fill = wallet_fill(
            source_wallet,
            "retained-historical-entry",
            timestamp_ms=int(old_fill_observed_at.timestamp() * 1000),
        )
        old_fill.received_at = old_fill_observed_at
        session.add(old_fill)
        watched_wallet.copy_eligibility_started_at = reselected_epoch
        await session.flush()

        await synchronize_live_copy_lanes(
            session,
            accounts=[account],
            active_source_wallets={source_wallet},
        )
        assert source_state.activated_at == reselected_epoch
        assert source_state.entry_eligible is True
        assert source_state.baseline_fill_ids == ["retained-historical-entry"]

        open_part = SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("0"),
        )
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=source_fill(
                "retained-historical-entry",
                timestamp_ms=old_fill.timestamp_ms,
                direction="Open Long",
            ),
            planned_parts=(open_part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            first_observed_at=old_fill_observed_at,
        )
        old_claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=source_fill(
                "retained-historical-entry",
                timestamp_ms=old_fill.timestamp_ms,
                direction="Open Long",
            ),
            part=open_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        assert old_claim.reason == "baseline"
        assert old_claim.state is not None
        await mark_live_copy_fill_baseline_ignored(
            session,
            source_state=source_state,
            fill_state=old_claim.state,
            part=open_part,
            reason="live_copy_baseline_entry",
            record_preexisting_market=False,
        )
        assert old_claim.state.outcome == LIVE_COPY_OUTCOME_BASELINE_IGNORED

        post_reselection_fill = wallet_fill(
            source_wallet,
            "post-reselection-entry",
            timestamp_ms=int(reselected_epoch.timestamp() * 1000),
        )
        post_reselection_fill.received_at = reselected_epoch
        session.add(post_reselection_fill)
        await session.flush()
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=source_fill(
                "post-reselection-entry",
                timestamp_ms=post_reselection_fill.timestamp_ms,
                direction="Open Long",
            ),
            planned_parts=(open_part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            first_observed_at=reselected_epoch,
        )
        post_reselection_claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=source_fill(
                "post-reselection-entry",
                timestamp_ms=post_reselection_fill.timestamp_ms,
                direction="Open Long",
            ),
            part=open_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        assert post_reselection_claim.claimed


@pytest.mark.asyncio
async def test_retained_source_lane_claims_its_first_add_after_bootstrap(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xretained-source"
    account_key = "live_retained"
    activated_at = datetime(2026, 7, 18, 12, tzinfo=UTC)
    async with integration_sessionmaker() as session:
        account = TradingAccount(
            key=account_key,
            account_type="live",
            label="Retained source account",
            status="enabled",
            network="testnet",
        )
        session.add_all(
            [
                account,
                TradingPosition(
                    account_key=account_key,
                    account_type="live",
                    source_wallet=source_wallet,
                    coin="HYPE",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("10"),
                    notional_usd=Decimal("10"),
                    leverage=Decimal("1"),
                    margin_mode="cross",
                    margin_usd=Decimal("10"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=activated_at,
                    source_lifecycle_timestamp_ms=1_500,
                    source_lifecycle_direction_rank=1,
                    source_lifecycle_position=Decimal("0"),
                    source_lifecycle_fill_id="retained-opening",
                ),
            ]
        )
        await session.flush()

        retained_pairs = await load_owned_live_copy_account_source_pairs(session)
        assert retained_pairs == {(account_key, source_wallet)}
        await synchronize_live_copy_source_activity(
            session,
            eligible_account_source_pairs=retained_pairs,
            entry_eligible_account_source_pairs=set(),
        )
        source_state = await get_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
        )
        assert source_state is not None
        assert source_state.status == "active"
        assert source_state.entry_eligible is False

        add_fill = source_fill(
            "retained-first-add",
            timestamp_ms=2_000,
            direction="Open Long",
        )
        part = SourceFillPart(
            action="add",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("1"),
        )
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=add_fill,
            planned_parts=(part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            first_observed_at=source_state.activated_at - timedelta(seconds=1),
        )
        claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=add_fill,
            part=part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        assert claim.claimed


@pytest.mark.asyncio
async def test_retained_lane_loader_excludes_zero_and_dust_positions(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account_key = "live_retained_dust"
    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Dust retained account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        for source_wallet, coin, size in (
            ("0xzero", "ZERO", Decimal("0")),
            ("0xdust", "DUST", Decimal("0.000000000001")),
            ("0xowned", "OWNED", Decimal("0.000000000002")),
        ):
            session.add(
                TradingPosition(
                    account_key=account_key,
                    account_type="live",
                    source_wallet=source_wallet,
                    coin=coin,
                    side="long",
                    size=size,
                    entry_price=Decimal("10"),
                    notional_usd=Decimal("10"),
                    leverage=Decimal("1"),
                    margin_mode="cross",
                    margin_usd=Decimal("10"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=datetime.now(UTC),
                )
            )
        manual_order = lifecycle_order(
            account_key=account_key,
            source_wallet="__manual_testnet__",
            source_fill_id="manual-in-flight",
            reduce_only=False,
        )
        manual_order.status = "accepted"
        session.add(manual_order)
        await session.flush()

        pairs = await load_owned_live_copy_account_source_pairs(
            session,
            account_keys={account_key},
        )

        assert pairs == {(account_key, "0xowned")}


@pytest.mark.asyncio
async def test_legacy_position_repair_unblocks_baselined_exit_recovery(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xlegacy-source"
    account_key = "live_legacy"
    captured_at = datetime(2026, 7, 18, 15, tzinfo=UTC)
    historical_received_at = captured_at - timedelta(minutes=10)
    async with integration_sessionmaker() as session:
        account = TradingAccount(
            key=account_key,
            account_type="live",
            label="Legacy repair account",
            status="enabled",
            network="testnet",
        )
        opening_fill = wallet_fill(source_wallet, "legacy-open", timestamp_ms=1_000)
        opening_fill.raw_json = {
            "tid": "legacy-open",
            "dir": "Open Long",
            "startPosition": "0",
        }
        opening_fill.received_at = historical_received_at
        exit_fill = wallet_fill(source_wallet, "legacy-exit", timestamp_ms=2_000)
        exit_fill.raw_json = {
            "tid": "legacy-exit",
            "dir": "Close Long",
            "startPosition": "1",
        }
        exit_fill.received_at = historical_received_at
        order = lifecycle_order(
            account_key=account_key,
            source_wallet=source_wallet,
            source_fill_id="legacy-open",
            reduce_only=False,
        )
        session.add_all(
            [
                account,
                opening_fill,
                exit_fill,
                order,
                TradingPosition(
                    account_key=account_key,
                    account_type="live",
                    source_wallet=source_wallet,
                    coin="HYPE",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("10"),
                    notional_usd=Decimal("10"),
                    leverage=Decimal("1"),
                    margin_mode="cross",
                    margin_usd=Decimal("10"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=captured_at,
                ),
                TradingPosition(
                    account_key=account_key,
                    account_type="live",
                    source_wallet="__exchange__",
                    coin="HYPE",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("10"),
                    notional_usd=Decimal("10"),
                    leverage=Decimal("1"),
                    margin_mode="cross",
                    margin_usd=Decimal("10"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=captured_at,
                ),
            ]
        )
        await session.flush()
        session.add(
            TradingFill(
                order_id=order.id,
                account_key=account_key,
                account_type="live",
                source_wallet=source_wallet,
                source_fill_id="legacy-open",
                sequence_index=0,
                exchange_fill_id="exchange-legacy-open",
                coin="HYPE",
                action="open",
                side="long",
                price=Decimal("10"),
                size=Decimal("1"),
                notional_usd=Decimal("10"),
                fee_usd=Decimal("0"),
                realized_pnl_usd=Decimal("0"),
                filled_at=historical_received_at,
            )
        )
        await session.flush()
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=captured_at,
        )
        assert source_state.baseline_fill_ids == ["legacy-exit"]

        repaired = await repair_owned_live_source_positions_for_recovery(
            session,
            account=account,
            source_wallet=source_wallet,
        )
        assert repaired == 1
        source_position = await session.scalar(
            select(TradingPosition).where(
                TradingPosition.account_key == account_key,
                TradingPosition.source_wallet == source_wallet,
                TradingPosition.coin == "HYPE",
            )
        )
        assert source_position is not None
        assert source_position.source_lifecycle_fill_id == "legacy-open"

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=10,
            now=captured_at,
        )
        assert [fill.external_fill_id for fill in candidates] == ["legacy-exit"]


@pytest.mark.asyncio
async def test_stale_terminal_entry_has_no_order_and_is_not_reselected_for_recovery(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xstale-source"
    account_key = "live_stale_terminal"
    observed_at = datetime(2026, 7, 19, 12, tzinfo=UTC)
    activated_at = observed_at - timedelta(minutes=10)
    stale_timestamp_ms = int((observed_at - timedelta(minutes=1)).timestamp() * 1000)
    source_fill_id = "stale-entry"
    part = SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("10"),
        sequence_index=0,
        start_position=Decimal("0"),
    )
    payload = source_fill(
        source_fill_id,
        timestamp_ms=stale_timestamp_ms,
        direction="Open Long",
    )

    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Stale terminal integration account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=activated_at,
        )
        stored_fill = wallet_fill(
            source_wallet,
            source_fill_id,
            timestamp_ms=stale_timestamp_ms,
        )
        stored_fill.raw_json = payload["rawJson"]
        stored_fill.received_at = observed_at - timedelta(minutes=1)
        session.add(stored_fill)
        await session.flush()

        fill_state = (
            await ensure_live_copy_fill_plan_states(
                session,
                source_state=source_state,
                fill=payload,
                planned_parts=(part,),
                origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
                observed_at=stored_fill.received_at,
                first_observed_at=stored_fill.received_at,
            )
        )[0]
        claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=payload,
            part=part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
            entry_is_stale=True,
        )

        assert claim.claimed
        assert claim.state is fill_state
        await mark_live_copy_fill_terminal_skip(
            session,
            fill_state=fill_state,
            reason="live_source_fill_too_old",
        )
        assert await mark_live_copy_fill_complete_if_durable(
            session,
            source_state=source_state,
            source_fill_id=source_fill_id,
            planned_parts=(part,),
        )
        await session.commit()

        stored_state = await session.scalar(
            select(LiveCopyFillState).where(
                LiveCopyFillState.account_key == account_key,
                LiveCopyFillState.source_wallet == source_wallet,
                LiveCopyFillState.source_fill_id == source_fill_id,
            )
        )
        orders = list(
            (
                await session.scalars(
                    select(TradingOrder).where(
                        TradingOrder.account_key == account_key,
                        TradingOrder.source_wallet == source_wallet,
                        TradingOrder.source_fill_id == source_fill_id,
                    )
                )
            ).all()
        )
        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=10,
            now=observed_at,
            max_entry_age_seconds=15,
        )

    assert orders == []
    assert stored_state is not None
    assert stored_state.outcome == LIVE_COPY_OUTCOME_TERMINAL_SKIP
    assert stored_state.reason == "live_source_fill_too_old"
    assert stored_state.fill_complete is True
    assert stored_state.trading_order_id is None
    assert candidates == []


@pytest.mark.asyncio
async def test_recovery_uses_baseline_and_durable_disposition_before_limit(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xsource"
    account_key = "live_main"
    observed_at = datetime(2026, 7, 17, 22, 0, tzinfo=UTC)
    baseline_timestamp_ms = int(observed_at.timestamp() * 1000)
    old_timestamp_ms = baseline_timestamp_ms - 100
    newer_timestamp_ms = baseline_timestamp_ms + 100
    after_newer_timestamp_ms = baseline_timestamp_ms + 200
    inactive_timestamp_ms = baseline_timestamp_ms + 300

    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Lifecycle integration account",
                status="enabled",
                network="testnet",
            )
        )
        baseline_fills = [
            wallet_fill(source_wallet, "old", timestamp_ms=old_timestamp_ms),
            wallet_fill(source_wallet, "baseline-a", timestamp_ms=baseline_timestamp_ms),
            wallet_fill(source_wallet, "baseline-b", timestamp_ms=baseline_timestamp_ms),
        ]
        for baseline_fill in baseline_fills:
            baseline_fill.received_at = observed_at - timedelta(seconds=1)
        session.add_all(baseline_fills)
        await session.flush()

        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )

        assert source_state.baseline_source_timestamp_ms == baseline_timestamp_ms
        assert source_state.baseline_fill_ids == ["baseline-a", "baseline-b"]

        post_activation_fills = [
            wallet_fill(
                source_wallet,
                "late-same-timestamp",
                timestamp_ms=baseline_timestamp_ms,
            ),
            wallet_fill(source_wallet, "newer", timestamp_ms=newer_timestamp_ms),
            wallet_fill(
                source_wallet,
                "after-newer",
                timestamp_ms=after_newer_timestamp_ms,
            ),
        ]
        for post_activation_fill in post_activation_fills:
            post_activation_fill.received_at = observed_at
        session.add_all(post_activation_fills)
        await session.flush()

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=1,
            now=observed_at,
        )

        assert [fill.external_fill_id for fill in candidates] == ["late-same-timestamp"]

        completed_state = LiveCopyFillState(
            account_key=account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id="late-same-timestamp",
            sequence_index=0,
            coin="HYPE",
            action="open",
            side="long",
            source_timestamp_ms=baseline_timestamp_ms,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="integration_complete",
            attempt_count=1,
            fill_complete=True,
        )
        retry_state = LiveCopyFillState(
            account_key=account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id="newer",
            sequence_index=0,
            coin="HYPE",
            action="open",
            side="long",
            source_timestamp_ms=newer_timestamp_ms,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            outcome=LIVE_COPY_OUTCOME_RETRYABLE,
            reason="integration_retry",
            attempt_count=1,
            last_attempt_at=observed_at,
            next_attempt_at=observed_at + timedelta(minutes=5),
            fill_complete=False,
        )
        terminal_flip_part = LiveCopyFillState(
            account_key=account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id="newer",
            sequence_index=1,
            coin="HYPE",
            action="flip_close",
            side="short",
            source_timestamp_ms=newer_timestamp_ms,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="integration_partial_terminal",
            attempt_count=1,
            fill_complete=False,
        )
        session.add_all([completed_state, retry_state, terminal_flip_part])
        after_newer_part = SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("0"),
        )
        after_newer_state = (
            await ensure_live_copy_fill_plan_states(
                session,
                source_state=source_state,
                fill={
                    "externalFillId": "after-newer",
                    "coin": "HYPE",
                    "timestampMs": after_newer_timestamp_ms,
                },
                planned_parts=(after_newer_part,),
                origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
                observed_at=observed_at,
                first_observed_at=observed_at,
            )
        )[0]
        await session.flush()

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=1,
            now=observed_at,
        )

        assert candidates == []
        blocked_claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill={
                "externalFillId": "after-newer",
                "coin": "HYPE",
                "timestampMs": after_newer_timestamp_ms,
            },
            part=after_newer_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
        )

        assert blocked_claim.reason == "blocked"
        assert blocked_claim.state is retry_state

        retry_state.next_attempt_at = observed_at - timedelta(seconds=1)
        await session.flush()

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=1,
            now=observed_at,
        )

        assert [fill.external_fill_id for fill in candidates] == ["newer"]

        retry_state.outcome = LIVE_COPY_OUTCOME_BASELINE_IGNORED
        retry_state.reason = "integration_retry_complete"
        retry_state.next_attempt_at = None
        retry_state.fill_complete = True
        terminal_flip_part.fill_complete = True
        await session.flush()

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=1,
            now=observed_at,
        )

        assert [fill.external_fill_id for fill in candidates] == ["after-newer"]

        after_newer_state.outcome = LIVE_COPY_OUTCOME_RETRYABLE
        after_newer_state.reason = "integration_inactive_retry"
        after_newer_state.attempt_count = 1
        after_newer_state.next_attempt_at = observed_at + timedelta(minutes=10)
        after_newer_state.fill_complete = False
        await session.flush()

        deactivated = await synchronize_live_copy_source_activity(
            session,
            eligible_account_source_pairs=set(),
        )

        assert deactivated == 1
        assert source_state.status == "inactive"
        assert after_newer_state.outcome == LIVE_COPY_OUTCOME_BASELINE_IGNORED
        assert after_newer_state.reason == "live_source_deactivated"
        assert after_newer_state.fill_complete is True

        session.add(
            wallet_fill(source_wallet, "inactive-history", timestamp_ms=inactive_timestamp_ms)
        )
        await session.flush()

        reactivated_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=observed_at + timedelta(hours=1),
        )

        assert reactivated_state is source_state
        assert reactivated_state.status == "active"
        assert reactivated_state.baseline_source_timestamp_ms == inactive_timestamp_ms
        assert reactivated_state.baseline_fill_ids == ["inactive-history"]

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=reactivated_state,
            limit=10,
            now=observed_at + timedelta(hours=1),
        )

        assert candidates == []


@pytest.mark.asyncio
async def test_multipart_plan_blocks_later_fill_after_committed_first_flip_part(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xsource"
    account_key = "live_multipart"
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)
    activation_timestamp_ms = int(observed_at.timestamp() * 1000)
    flip_timestamp_ms = activation_timestamp_ms + 100
    later_timestamp_ms = activation_timestamp_ms + 200
    flip_fill = {
        "externalFillId": "flip",
        "coin": "HYPE",
        "timestampMs": flip_timestamp_ms,
        "rawJson": {"dir": "Long > Short", "startPosition": "1"},
    }
    later_fill = {
        "externalFillId": "later",
        "coin": "HYPE",
        "timestampMs": later_timestamp_ms,
        "rawJson": {"dir": "Open Long", "startPosition": "0"},
    }
    flip_parts = (
        SourceFillPart(
            action="flip_close",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("1"),
            close_ratio=Decimal("1"),
        ),
        SourceFillPart(
            action="flip_open",
            side="short",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=1,
            start_position=Decimal("0"),
        ),
    )

    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Multipart lifecycle integration account",
                status="enabled",
                network="testnet",
            )
        )
        baseline_fill = wallet_fill(
            source_wallet,
            "baseline",
            timestamp_ms=activation_timestamp_ms,
        )
        baseline_fill.received_at = observed_at - timedelta(seconds=1)
        session.add(baseline_fill)
        await session.flush()
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )
        post_activation_fills = [
            wallet_fill(source_wallet, "flip", timestamp_ms=flip_timestamp_ms),
            wallet_fill(source_wallet, "later", timestamp_ms=later_timestamp_ms),
        ]
        post_activation_fills[0].raw_json = flip_fill["rawJson"]
        post_activation_fills[1].raw_json = later_fill["rawJson"]
        for post_activation_fill in post_activation_fills:
            post_activation_fill.received_at = observed_at
        session.add_all(post_activation_fills)
        await session.flush()

        plan_states = await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=flip_fill,
            planned_parts=flip_parts,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
            observed_at=observed_at,
            first_observed_at=observed_at,
        )

        later_part = SourceFillPart(
            action="open",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("0"),
        )
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=later_fill,
            planned_parts=(later_part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            observed_at=observed_at,
            first_observed_at=observed_at,
        )

        assert [state.sequence_index for state in plan_states] == [0, 1]
        assert [state.expected_part_count for state in plan_states] == [2, 2]
        assert [state.plan_version for state in plan_states] == [1, 1]

        # Simulate a committed first flip part followed by a worker crash before seq1 runs.
        plan_states[0].outcome = LIVE_COPY_OUTCOME_BASELINE_IGNORED
        plan_states[0].reason = "test_terminal_first_flip_part"
        await session.commit()

        candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=10,
            now=observed_at,
        )
        assert [fill.external_fill_id for fill in candidates] == ["flip"]

        stale_candidates = await load_live_copy_recovery_candidate_fills(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=10,
            now=observed_at + timedelta(seconds=10),
            max_entry_age_seconds=1,
        )
        assert [fill.external_fill_id for fill in stale_candidates] == ["flip", "later"]

        later_claim = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=later_fill,
            part=later_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
        )

        assert later_claim.reason == "blocked"
        assert later_claim.state is not None
        assert later_claim.state.source_fill_id == "flip"
        assert later_claim.state.sequence_index == 1


@pytest.mark.asyncio
async def test_claim_requires_complete_preplan_and_preserves_crash_ordering(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xclaim-source"
    account_key = "live_claim_plan"
    activated_at = datetime(2026, 7, 18, tzinfo=UTC)
    first_observed_at = activated_at + timedelta(seconds=1)
    activation_timestamp_ms = int(activated_at.timestamp() * 1000)
    flip_fill = source_fill(
        "2",
        timestamp_ms=activation_timestamp_ms + 100,
        direction="Open Long",
    )
    later_fill = source_fill(
        "10",
        timestamp_ms=activation_timestamp_ms + 200,
        direction="Open Long",
    )
    flip_parts = (
        SourceFillPart(
            action="flip_close",
            side="long",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=0,
            start_position=Decimal("1"),
            close_ratio=Decimal("1"),
        ),
        SourceFillPart(
            action="flip_open",
            side="short",
            source_size=Decimal("1"),
            source_notional_usd=Decimal("10"),
            sequence_index=1,
            start_position=Decimal("0"),
        ),
    )
    later_part = SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("10"),
        sequence_index=0,
        start_position=Decimal("0"),
    )

    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Claim plan account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=activated_at,
        )

        missing = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=flip_fill,
            part=flip_parts[0],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=first_observed_at,
        )
        assert missing.reason == "missing_plan"

        states = await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=flip_fill,
            planned_parts=flip_parts,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            observed_at=first_observed_at,
            first_observed_at=first_observed_at,
        )
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=later_fill,
            planned_parts=(later_part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            observed_at=first_observed_at,
            first_observed_at=first_observed_at,
        )
        seq0 = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=flip_fill,
            part=flip_parts[0],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=first_observed_at,
        )
        assert seq0.claimed
        seq1_blocked = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=flip_fill,
            part=flip_parts[1],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=first_observed_at,
        )
        assert seq1_blocked.reason == "blocked"
        assert seq1_blocked.state is states[0]

        states[0].outcome = LIVE_COPY_OUTCOME_TERMINAL_SKIP
        states[0].next_attempt_at = None
        seq1 = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=flip_fill,
            part=flip_parts[1],
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=first_observed_at,
        )
        assert seq1.claimed
        await session.commit()

    async with integration_sessionmaker() as session:
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=first_observed_at,
        )
        later = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=later_fill,
            part=later_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=first_observed_at,
        )
        assert later.reason == "blocked"
        assert later.state is not None
        assert later.state.source_fill_id == "2"
        assert later.state.sequence_index == 1


@pytest.mark.asyncio
async def test_unreconciled_filled_order_blocks_later_source_fill(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xorder-barrier"
    account_key = "live_order_barrier"
    activated_at = datetime(2026, 7, 18, tzinfo=UTC)
    observed_at = activated_at + timedelta(seconds=1)
    activation_timestamp_ms = int(activated_at.timestamp() * 1000)
    source_timestamp_ms = activation_timestamp_ms + 100
    first_fill = source_fill("2", timestamp_ms=source_timestamp_ms, direction="Close Long")
    later_fill = source_fill("10", timestamp_ms=source_timestamp_ms, direction="Open Long")
    close_part = SourceFillPart(
        action="close",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("10"),
        sequence_index=0,
        start_position=Decimal("1"),
        close_ratio=Decimal("1"),
    )
    open_part = SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("10"),
        sequence_index=0,
        start_position=Decimal("0"),
    )

    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Order barrier account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        source_state = await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=activated_at,
        )
        first_state = (
            await ensure_live_copy_fill_plan_states(
                session,
                source_state=source_state,
                fill=first_fill,
                planned_parts=(close_part,),
                origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
                observed_at=observed_at,
                first_observed_at=observed_at,
            )
        )[0]
        await ensure_live_copy_fill_plan_states(
            session,
            source_state=source_state,
            fill=later_fill,
            planned_parts=(open_part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            observed_at=observed_at,
            first_observed_at=observed_at,
        )
        order = lifecycle_order(
            account_key=account_key,
            source_wallet=source_wallet,
            source_fill_id="2",
            reduce_only=True,
        )
        session.add(order)
        await session.flush()
        first_state.trading_order_id = order.id
        first_state.outcome = "order"
        first_state.fill_complete = True

        blocked = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=later_fill,
            part=open_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
        )
        assert blocked.reason == "blocked"

        session.add(
            TradingFill(
                order_id=order.id,
                account_key=account_key,
                account_type="live",
                source_wallet=source_wallet,
                source_fill_id="2",
                sequence_index=0,
                exchange_fill_id="barrier-fill",
                coin="HYPE",
                action="close",
                side="long",
                price=Decimal("10"),
                size=Decimal("1"),
                notional_usd=Decimal("10"),
                fee_usd=Decimal("0"),
                realized_pnl_usd=Decimal("0"),
                filled_at=observed_at,
            )
        )
        await session.flush()
        eligible = await claim_live_copy_fill_part(
            session,
            source_state=source_state,
            fill=later_fill,
            part=open_part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            now=observed_at,
        )
        assert eligible.claimed


@pytest.mark.asyncio
async def test_account_source_activity_is_isolated_and_reactivation_uses_a_fresh_baseline(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xshared-source"
    enabled_account_key = "live_enabled"
    disabled_account_key = "live_disabled"
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)

    async with integration_sessionmaker() as session:
        session.add_all(
            [
                TradingAccount(
                    key=enabled_account_key,
                    account_type="live",
                    label="Eligible lifecycle account",
                    status="enabled",
                    network="testnet",
                ),
                TradingAccount(
                    key=disabled_account_key,
                    account_type="live",
                    label="Disabled lifecycle account",
                    status="disabled",
                    network="testnet",
                ),
                wallet_fill(source_wallet, "initial-baseline", timestamp_ms=1_000),
            ]
        )
        await session.flush()
        enabled_state = await ensure_live_copy_source_state(
            session,
            account_key=enabled_account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )
        disabled_state = await ensure_live_copy_source_state(
            session,
            account_key=disabled_account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )

        deactivated = await synchronize_live_copy_account_source_activity(
            session,
            account_key=disabled_account_key,
            eligible_source_wallets=set(),
        )

        assert deactivated == 1
        assert enabled_state.status == "active"
        assert disabled_state.status == "inactive"

        session.add(wallet_fill(source_wallet, "reenable-baseline", timestamp_ms=1_100))
        await session.flush()
        activated_states = await activate_live_copy_account_sources(
            session,
            account_key=disabled_account_key,
            source_wallets={source_wallet},
            now=observed_at + timedelta(hours=1),
        )
        reactivated_state = activated_states[0]

        assert reactivated_state is disabled_state
        assert reactivated_state.status == "active"
        assert reactivated_state.baseline_source_timestamp_ms == 1_100
        assert reactivated_state.baseline_fill_ids == ["reenable-baseline"]

        pending_entry = LiveCopyFillState(
            account_key=disabled_account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id="pending-before-restart",
            sequence_index=0,
            expected_part_count=1,
            plan_version=1,
            coin="HYPE",
            action="add",
            side="long",
            source_timestamp_ms=1_150,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            outcome=LIVE_COPY_OUTCOME_RETRYABLE,
            reason="source_account_state_missing",
            attempt_count=1,
            next_attempt_at=observed_at + timedelta(minutes=5),
            fill_complete=False,
        )
        pending_exit = LiveCopyFillState(
            account_key=disabled_account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id="exit-before-restart",
            sequence_index=0,
            expected_part_count=1,
            plan_version=1,
            coin="HYPE",
            action="close",
            side="long",
            source_timestamp_ms=1_160,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            outcome=LIVE_COPY_OUTCOME_RETRYABLE,
            reason="live_execution_price_unavailable",
            attempt_count=1,
            next_attempt_at=observed_at + timedelta(minutes=5),
            fill_complete=False,
        )
        session.add_all([pending_entry, pending_exit])
        session.add(wallet_fill(source_wallet, "restart-baseline", timestamp_ms=1_200))
        await session.flush()
        restarted_states = await activate_live_copy_account_sources(
            session,
            account_key=disabled_account_key,
            source_wallets={source_wallet},
            now=observed_at + timedelta(hours=2),
        )

        assert restarted_states == [disabled_state]
        assert disabled_state.baseline_source_timestamp_ms == 1_200
        assert disabled_state.baseline_fill_ids == ["restart-baseline"]
        assert pending_entry.outcome == LIVE_COPY_OUTCOME_BASELINE_IGNORED
        assert pending_entry.reason == "live_account_restart_baseline"
        assert pending_entry.fill_complete is True
        assert pending_exit.outcome == LIVE_COPY_OUTCOME_RETRYABLE
        assert pending_exit.fill_complete is False


@pytest.mark.asyncio
async def test_source_activity_keeps_owned_exposure_and_nonterminal_orders_active(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    source_wallet = "0xprotected-source"
    position_account_key = "live_owned_exposure"
    order_account_key = "live_pending_order"
    observed_at = datetime(2026, 7, 18, tzinfo=UTC)

    async with integration_sessionmaker() as session:
        session.add_all(
            [
                TradingAccount(
                    key=position_account_key,
                    account_type="live",
                    label="Owned exposure lifecycle account",
                    status="exit_only",
                    network="testnet",
                ),
                TradingAccount(
                    key=order_account_key,
                    account_type="live",
                    label="Pending order lifecycle account",
                    status="exit_only",
                    network="testnet",
                ),
                wallet_fill(source_wallet, "initial-baseline", timestamp_ms=1_000),
            ]
        )
        await session.flush()
        position_state = await ensure_live_copy_source_state(
            session,
            account_key=position_account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )
        order_state = await ensure_live_copy_source_state(
            session,
            account_key=order_account_key,
            source_wallet=source_wallet,
            now=observed_at,
        )
        session.add_all(
            [
                TradingPosition(
                    account_key=position_account_key,
                    account_type="live",
                    source_wallet=source_wallet,
                    coin="HYPE",
                    side="long",
                    size=Decimal("1"),
                    entry_price=Decimal("10"),
                    notional_usd=Decimal("10"),
                    leverage=Decimal("1"),
                    margin_mode="cross",
                    margin_usd=Decimal("10"),
                    realized_pnl_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                    opened_at=observed_at,
                ),
                TradingOrder(
                    account_key=order_account_key,
                    account_type="live",
                    source_wallet=source_wallet,
                    source_fill_id="pending-source-fill",
                    sequence_index=0,
                    client_order_id="lifecycle-pending-order",
                    coin="HYPE",
                    action="open",
                    side="long",
                    is_buy=True,
                    reduce_only=False,
                    order_type="ioc",
                    status="ready",
                    requested_size=Decimal("1"),
                    requested_notional_usd=Decimal("10"),
                    filled_size=Decimal("0"),
                    filled_notional_usd=Decimal("0"),
                    fee_usd=Decimal("0"),
                ),
            ]
        )
        await session.flush()

        deactivated = await synchronize_live_copy_source_activity(
            session,
            eligible_account_source_pairs=set(),
        )

        assert deactivated == 0
        assert position_state.status == "active"
        assert order_state.status == "active"


@pytest.mark.asyncio
async def test_filled_order_waits_for_all_materialized_exchange_fills(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account_key = "live_materialized_fill"
    source_wallet = "0xmaterialized-source"
    observed_at = datetime.now(UTC)
    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Materialized fill account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        order = lifecycle_order(
            account_key=account_key,
            source_wallet=source_wallet,
            source_fill_id="materialized-source-fill",
            reduce_only=False,
        )
        session.add(order)
        await session.flush()
        session.add(
            TradingFill(
                order_id=order.id,
                account_key=account_key,
                account_type="live",
                source_wallet=source_wallet,
                source_fill_id=order.source_fill_id,
                sequence_index=order.sequence_index,
                exchange_fill_id="materialized-partial-1",
                coin="HYPE",
                action="open",
                side="long",
                price=Decimal("10"),
                size=Decimal("0.4"),
                notional_usd=Decimal("4"),
                fee_usd=Decimal("0"),
                realized_pnl_usd=Decimal("0"),
                filled_at=observed_at,
            )
        )
        await session.flush()

        unresolved = await session.scalar(
            select(TradingOrder.id).where(
                TradingOrder.id == order.id,
                live_copy_unresolved_order_predicate(),
            )
        )
        assert unresolved == order.id

        session.add(
            TradingFill(
                order_id=order.id,
                account_key=account_key,
                account_type="live",
                source_wallet=source_wallet,
                source_fill_id=order.source_fill_id,
                sequence_index=order.sequence_index,
                exchange_fill_id="materialized-partial-2",
                coin="HYPE",
                action="open",
                side="long",
                price=Decimal("10"),
                size=Decimal("0.6"),
                notional_usd=Decimal("6"),
                fee_usd=Decimal("0"),
                realized_pnl_usd=Decimal("0"),
                filled_at=observed_at,
            )
        )
        await session.flush()

        unresolved = await session.scalar(
            select(TradingOrder.id).where(
                TradingOrder.id == order.id,
                live_copy_unresolved_order_predicate(),
            )
        )
        assert unresolved is None


@pytest.mark.asyncio
async def test_filled_order_without_exchange_fills_remains_unresolved_when_size_is_unset(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    account_key = "live_unmaterialized_fill"
    source_wallet = "0xunmaterialized-source"
    async with integration_sessionmaker() as session:
        session.add(
            TradingAccount(
                key=account_key,
                account_type="live",
                label="Unmaterialized filled order account",
                status="enabled",
                network="testnet",
            )
        )
        await session.flush()
        order = lifecycle_order(
            account_key=account_key,
            source_wallet=source_wallet,
            source_fill_id="missing-exchange-fill",
            reduce_only=False,
        )
        order.filled_size = Decimal("0")
        session.add(order)
        await session.flush()

        unresolved = await session.scalar(
            select(TradingOrder.id).where(
                TradingOrder.id == order.id,
                live_copy_unresolved_order_predicate(),
            )
        )

    assert unresolved == order.id


@pytest.mark.asyncio
async def test_concurrent_retained_reclassification_creates_no_order_and_finishes_decision(
    integration_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account_key = "live_concurrent_retained"
    source_wallet = "0xconcurrent-retained"
    source_fill_id = "9001"
    observed_at = datetime.now(UTC)
    activated_at = observed_at - timedelta(minutes=1)
    fill = wallet_fill(
        source_wallet, source_fill_id, timestamp_ms=int(observed_at.timestamp() * 1000)
    )
    fill.received_at = observed_at
    fill.raw_json = {
        "tid": source_fill_id,
        "dir": "Open Long",
        "startPosition": "0",
    }
    source_payload = {
        "externalFillId": source_fill_id,
        "coin": "HYPE",
        "timestampMs": fill.timestamp_ms,
        "rawJson": fill.raw_json,
    }
    part = SourceFillPart(
        action="open",
        side="long",
        source_size=Decimal("1"),
        source_notional_usd=Decimal("10"),
        sequence_index=0,
        start_position=Decimal("0"),
    )

    async with integration_sessionmaker() as stale_session:
        account = TradingAccount(
            key=account_key,
            account_type="live",
            label="Concurrent retained account",
            status="enabled",
            network="testnet",
        )
        stale_session.add(account)
        await stale_session.flush()
        source_state = await ensure_live_copy_source_state(
            stale_session,
            account_key=account_key,
            source_wallet=source_wallet,
            now=activated_at,
            entry_eligible=True,
        )
        stale_session.add(fill)
        await stale_session.flush()
        await ensure_live_copy_fill_plan_states(
            stale_session,
            source_state=source_state,
            fill=source_payload,
            planned_parts=(part,),
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
            first_observed_at=observed_at,
        )
        claim = await claim_live_copy_fill_part(
            stale_session,
            source_state=source_state,
            fill=source_payload,
            part=part,
            origin=LIVE_COPY_ORIGIN_PERIODIC_RECOVERY,
        )
        assert claim.claimed
        assert claim.state is not None
        await stale_session.commit()

        async with integration_sessionmaker() as reclassification_session:
            reclassified_source_state = await get_live_copy_source_state(
                reclassification_session,
                account_key=account_key,
                source_wallet=source_wallet,
                for_update=True,
            )
            assert reclassified_source_state is not None
            reclassified_source_state.entry_eligible = False
            await reclassification_session.commit()

        async def allow_risk_guardrails(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(
            live_trading_service,
            "validate_live_entry_risk_guardrails",
            allow_risk_guardrails,
        )

        class NoSubmitClient:
            def validate_account_order(self, **_kwargs: object) -> None:
                return None

            async def submit_order(self, **_kwargs: object) -> None:
                raise AssertionError("A retained new-market entry reached the exchange client.")

        intent = build_copy_trade_intent(
            account_key=account_key,
            account_type="live",
            source_wallet=source_wallet,
            source_fill_id=source_fill_id,
            sequence_index=0,
            coin="HYPE",
            action="open",
            side="long",
            size=Decimal("1"),
            notional_usd=Decimal("10"),
            margin_usd=Decimal("10"),
            leverage=Decimal("1"),
            limit_price=Decimal("10"),
            source_price=Decimal("10"),
            observed_price=Decimal("10"),
            price_drift_bps=Decimal("0"),
            price_source="source",
            allocation_pct=Decimal("0.25"),
            allocation_usd=Decimal("250"),
            source_perp_equity_usd=Decimal("1000"),
            source_exposure_pct=Decimal("0.01"),
            created_at=observed_at,
        )
        with pytest.raises(
            LiveCopyEntryLifecycleDeferred,
            match="live_source_lifecycle_reclassified",
        ) as exc_info:
            await submit_live_trade_intent(
                stale_session,
                account=account,
                intent=intent,
                settings=Settings(
                    live_trading_enabled=True,
                    hyperliquid_network="testnet",
                ),
                client=NoSubmitClient(),  # type: ignore[arg-type]
            )
        assert exc_info.value.state_reclassified is True

        assert await finalize_live_copy_fill_disposition(
            stale_session,
            fill_state=claim.state,
        )
        assert await mark_live_copy_fill_complete_if_durable(
            stale_session,
            source_state=source_state,
            source_fill_id=source_fill_id,
            planned_parts=(part,),
        )
        await stale_session.commit()

        stored_fill_state = await stale_session.scalar(
            select(LiveCopyFillState).where(
                LiveCopyFillState.account_key == account_key,
                LiveCopyFillState.source_wallet == source_wallet,
                LiveCopyFillState.source_fill_id == source_fill_id,
                LiveCopyFillState.sequence_index == 0,
            )
        )
        stored_order = await stale_session.scalar(
            select(TradingOrder).where(
                TradingOrder.account_key == account_key,
                TradingOrder.source_wallet == source_wallet,
                TradingOrder.source_fill_id == source_fill_id,
            )
        )

    assert stored_order is None
    assert stored_fill_state is not None
    assert stored_fill_state.outcome == LIVE_COPY_OUTCOME_BASELINE_IGNORED
    assert stored_fill_state.reason == "live_retained_source_new_market"
    assert stored_fill_state.fill_complete is True
    assert stored_fill_state.trading_order_id is None


def wallet_fill(source_wallet: str, external_fill_id: str, *, timestamp_ms: int) -> WalletFill:
    return WalletFill(
        wallet_address=source_wallet,
        external_fill_id=external_fill_id,
        coin="HYPE",
        side="buy",
        price=Decimal("10"),
        size=Decimal("1"),
        notional_usd=Decimal("10"),
        fee_usd=Decimal("0"),
        pnl_usd=Decimal("0"),
        timestamp_ms=timestamp_ms,
        raw_json={"tid": external_fill_id},
    )


def source_fill(
    external_fill_id: str,
    *,
    timestamp_ms: int,
    direction: str,
) -> dict[str, object]:
    return {
        "externalFillId": external_fill_id,
        "coin": "HYPE",
        "timestampMs": timestamp_ms,
        "rawJson": {"dir": direction, "startPosition": "0"},
    }


def lifecycle_order(
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    reduce_only: bool,
) -> TradingOrder:
    return TradingOrder(
        account_key=account_key,
        account_type="live",
        source_wallet=source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=0,
        client_order_id=f"lifecycle-{source_fill_id}",
        coin="HYPE",
        action="close" if reduce_only else "open",
        side="long",
        is_buy=False,
        reduce_only=reduce_only,
        order_type="ioc",
        status="filled",
        requested_size=Decimal("1"),
        requested_notional_usd=Decimal("10"),
        filled_size=Decimal("1"),
        filled_notional_usd=Decimal("10"),
        fee_usd=Decimal("0"),
    )
