import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import (
    PaperCopyAllocation,
    TradingAccount,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletScore,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import HyperliquidLiveTradingClient
from app.services.live_trading_service import (
    LIVE_CAPITAL_MODE_UNIFIED,
    LIVE_EXCHANGE_SOURCE,
    POSITION_EPSILON,
    LiveOrderSubmitError,
    live_capital_mode,
    live_perp_equity_usd,
    live_tradable_equity_usd,
    live_unified_equity_usd,
    load_live_source_position,
    reconcile_live_trading_account,
    submit_live_trade_intent,
)
from app.services.market_price_cache import MarketPriceCache, dex_from_coin
from app.services.paper_trading_service import (
    ExecutionMarketPrices,
    PaperCopyBatchResult,
    PaperSourceAccountState,
    PaperSourceAllocation,
    SourceFillPart,
    build_execution_context,
    combine_skip_reasons,
    decimal_or_zero,
    fill_datetime,
    is_preexisting_source_add,
    leverage_for_fill,
    load_execution_market_prices,
    load_source_account_states,
    lock_paper_source_mutation,
    paper_source_fill_from_wallet_fill,
    part_requires_source_equity,
    plan_source_fill,
    refresh_paper_copy_allocations,
    resolve_source_current_position,
    sorted_paper_source_fills,
    source_fill_age_exceeds_entry_limit,
    source_fill_age_seconds,
    source_state_available_for_reconciliation,
)
from app.services.trading_core import (
    TradeIntent,
    adjust_open_sizing_to_min_order,
    build_client_order_id,
    build_copy_trade_intent,
    margin_from_notional,
    trade_is_buy,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
LIVE_COPY_RECOVERY_OVERLAP_MS = 5 * 60 * 1000
PENDING_CLOSE_ORDER_STATUSES = {"submitted", "accepted", "partially_filled", "filled"}


def live_skip(reason: str, count: int = 1) -> PaperCopyBatchResult:
    return PaperCopyBatchResult(
        skipped_fills=count,
        skip_reasons={reason: count} if count > 0 else {},
    )


def live_copy_allocation_equity_usd(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None = None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        equity_usd = live_unified_equity_usd(account)
        return equity_usd if equity_usd > ZERO else account.equity_usd or ZERO
    return live_perp_equity_usd(account, dex=dex)


async def process_live_copy_fills(
    session: AsyncSession,
    *,
    source_wallet: str,
    fills: list[dict[str, Any]],
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    price_cache: MarketPriceCache | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.live_trading_enabled
        or not resolved_settings.live_trading_copy_enabled
        or not fills
    ):
        return PaperCopyBatchResult()

    normalized_source_wallet = source_wallet.lower()
    allocations = await refresh_paper_copy_allocations(session, settings=resolved_settings)
    allocation = allocations.get(normalized_source_wallet)
    if allocation is None:
        return live_skip("live_allocation_missing", len(fills))

    accounts = await load_live_accounts_for_source_copy(
        session,
        source_wallet=normalized_source_wallet,
    )
    if not accounts:
        return live_skip("live_no_enabled_accounts", len(fills))

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_live_copy_fills(
                session,
                source_wallet=source_wallet,
                fills=fills,
                settings=resolved_settings,
                client=hyperliquid_client,
                trading_client=trading_client,
                price_cache=price_cache,
            )

    source_account_states_task = load_source_account_states(
        client=client,
        source_wallet=normalized_source_wallet,
        fills=fills,
    )
    market_prices_task = load_execution_market_prices(
        client=client,
        fills=fills,
        latency_ms=0,
        settings=resolved_settings,
        price_cache=price_cache,
    )
    source_account_states, market_prices = await gather_two(
        source_account_states_task,
        market_prices_task,
    )

    await lock_paper_source_mutation(session, source_wallet=normalized_source_wallet)
    accounts = await load_live_accounts_for_source_copy(
        session,
        source_wallet=normalized_source_wallet,
        for_update=True,
    )
    if not accounts:
        return live_skip("live_no_enabled_accounts", len(fills))
    await refresh_stale_live_copy_accounts(
        session,
        accounts=accounts,
        settings=resolved_settings,
        client=client,
    )

    processed = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}
    touched_accounts: dict[str, TradingAccount] = {}
    live_client = trading_client or HyperliquidLiveTradingClient(settings=resolved_settings)

    def add_skip(reason: str, count: int = 1) -> None:
        nonlocal skipped
        skipped += count
        skip_reasons[reason] = skip_reasons.get(reason, 0) + count

    for fill in sorted_paper_source_fills(fills):
        parts = plan_source_fill(fill)
        if not parts:
            add_skip("live_no_planned_source_parts", len(accounts))
            continue

        source_account_state = source_account_states.get(dex_from_coin(fill.get("coin")))
        if source_account_state is None:
            source_perp_equity = ZERO
            source_leverages: dict[str, Decimal] = {}
            source_state_skip_reason = "source_account_state_missing"
        else:
            source_perp_equity = source_account_state.perp_equity
            source_leverages = source_account_state.leverage_by_coin
            source_state_skip_reason = source_account_state.skip_reason

        for account in accounts:
            for part in parts:
                if account.status != "enabled" and part_requires_source_equity(part):
                    add_skip("live_account_exit_only")
                    continue
                if source_state_skip_reason is not None and part_requires_source_equity(part):
                    add_skip(source_state_skip_reason)
                    continue

                fill_result = await apply_live_copy_part(
                    session,
                    account=account,
                    allocation=allocation,
                    fill=fill,
                    part=part,
                    source_account_state=source_account_state,
                    source_perp_equity=source_perp_equity,
                    source_leverages=source_leverages,
                    market_prices=market_prices,
                    settings=resolved_settings,
                    trading_client=live_client,
                )
                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
                skip_reasons = combine_skip_reasons(skip_reasons, fill_result.skip_reasons)
                if fill_result.processed_fills > 0:
                    touched_accounts[account.key] = account
                    await session.commit()
                    await lock_paper_source_mutation(
                        session,
                        source_wallet=normalized_source_wallet,
                    )

    for account in touched_accounts.values():
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=resolved_settings,
            info_client=client,
        )

    await session.commit()
    return PaperCopyBatchResult(
        processed_fills=processed,
        skipped_fills=skipped,
        accounts_updated=len(touched_accounts),
        skip_reasons=skip_reasons,
    )


async def process_live_copy_recovery(
    session: AsyncSession,
    *,
    source_wallet: str | None = None,
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
    price_cache: MarketPriceCache | None = None,
    max_sources: int = 100,
    fill_limit_per_source: int = 1000,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.live_trading_enabled
        or not resolved_settings.live_trading_copy_enabled
    ):
        return PaperCopyBatchResult()

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_live_copy_recovery(
                session,
                source_wallet=source_wallet,
                settings=resolved_settings,
                client=hyperliquid_client,
                price_cache=price_cache,
                max_sources=max_sources,
                fill_limit_per_source=fill_limit_per_source,
            )

    if source_wallet:
        source_wallets = [source_wallet.lower()]
    else:
        await refresh_paper_copy_allocations(session, settings=resolved_settings)
        source_wallets = await load_live_copy_recovery_sources(
            session,
            max_sources=max_sources,
        )

    total = PaperCopyBatchResult()
    for wallet in source_wallets:
        start_time_ms = await live_copy_recovery_start_time_ms(session, source_wallet=wallet)
        if start_time_ms is None:
            continue
        fills = await load_wallet_fills_for_live_copy_recovery(
            session,
            source_wallet=wallet,
            start_time_ms=start_time_ms,
            limit=fill_limit_per_source,
        )
        if not fills:
            continue
        result = await process_live_copy_fills(
            session,
            source_wallet=wallet,
            fills=fills,
            settings=resolved_settings,
            client=client,
            price_cache=price_cache,
        )
        total = combine_batch_results(total, result)
    return total


async def refresh_stale_live_copy_accounts(
    session: AsyncSession,
    *,
    accounts: list[TradingAccount],
    settings: Settings,
    client: HyperliquidClient,
) -> None:
    if not settings.live_trading_reconciliation_enabled:
        return
    now = datetime.now(UTC)
    for account in accounts:
        if not live_copy_account_snapshot_is_stale(account, settings=settings, now=now):
            continue
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
    await session.flush()


def live_copy_account_snapshot_is_stale(
    account: TradingAccount,
    *,
    settings: Settings,
    now: datetime,
) -> bool:
    if account.last_reconciled_at is None:
        return True
    last_reconciled_at = account.last_reconciled_at
    if last_reconciled_at.tzinfo is None:
        last_reconciled_at = last_reconciled_at.replace(tzinfo=UTC)
    max_age_seconds = max(settings.live_trading_reconciliation_interval_seconds, 1)
    return (now - last_reconciled_at).total_seconds() >= max_age_seconds


def live_stale_entry_skip_hidden_from_activity(
    fill: dict[str, Any],
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    activity_seconds = settings.trading_copy_stale_entry_skip_activity_seconds
    if activity_seconds <= 0:
        return True
    age_seconds = source_fill_age_seconds(fill, now=now)
    return age_seconds is not None and age_seconds > activity_seconds


async def apply_live_copy_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return live_skip("live_source_fill_id_missing")
    if await live_order_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    ):
        return PaperCopyBatchResult()

    if part.action in {"open", "flip_open"}:
        return await apply_live_open_part(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_perp_equity=source_perp_equity,
            source_leverages=source_leverages,
            market_prices=market_prices,
            settings=settings,
            trading_client=trading_client,
        )
    return await apply_live_close_part(
        session,
        account=account,
        allocation=allocation,
        fill=fill,
        part=part,
        source_account_state=source_account_state,
        source_perp_equity=source_perp_equity,
        source_leverages=source_leverages,
        market_prices=market_prices,
        settings=settings,
        trading_client=trading_client,
    )


async def apply_live_open_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
    if account.status != "enabled":
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_not_enabled",
            leverage=source_leverage,
        )
    if source_perp_equity <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_source_equity_missing",
            leverage=source_leverage,
        )
    if source_fill_age_exceeds_entry_limit(fill, settings=settings):
        fill_age_seconds = source_fill_age_seconds(fill)
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_source_fill_too_old",
            leverage=source_leverage,
            hidden_from_activity=live_stale_entry_skip_hidden_from_activity(
                fill,
                settings=settings,
            ),
            source_fill_age_seconds=fill_age_seconds,
        )
    coin = str(fill.get("coin") or "")
    dex = dex_from_coin(coin)
    tradable_equity_usd = live_tradable_equity_usd(
        account,
        dex=dex,
        settings=settings,
    )
    if tradable_equity_usd <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_no_tradable_equity",
            leverage=source_leverage,
        )

    allocation_equity_usd = live_copy_allocation_equity_usd(
        account,
        dex=dex,
        settings=settings,
    )
    if allocation_equity_usd <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_account_no_allocation_equity",
            leverage=source_leverage,
        )

    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_preexisting_source_add",
            leverage=source_leverage,
        )
    if position is not None and position.side != part.side:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_position_side_mismatch",
            leverage=source_leverage,
        )
    if position is None and not allocation.active:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_allocation_inactive",
            leverage=source_leverage,
        )
    if await live_market_is_reserved_by_other_source(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    ):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_market_reserved_by_other_source",
            leverage=source_leverage,
        )
    exchange_position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=LIVE_EXCHANGE_SOURCE,
        coin=coin,
    )
    exchange_conflict = live_exchange_position_conflict(
        source_position=position,
        exchange_position=exchange_position,
        side=part.side,
    )
    if exchange_conflict is not None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason=exchange_conflict,
            leverage=source_leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=settings.live_trading_limit_slippage_bps,
        latency_ms=0,
    )
    if execution_context is None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_execution_price_unavailable",
            leverage=source_leverage,
        )
    if execution_context.price_drift_bps > settings.trading_copy_max_price_drift_bps:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_price_drift_too_high",
            leverage=source_leverage,
            limit_price=execution_context.execution_price,
        )

    price = execution_context.execution_price
    allocation_usd = allocation_equity_usd * allocation.allocation_pct
    source_exposure_pct = part.source_notional_usd / source_perp_equity
    target_notional = allocation_usd * source_exposure_pct
    target_margin = margin_from_notional(target_notional, source_leverage)
    source_remaining = max(
        allocation_usd
        - await live_open_margin_for_source(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
        ),
        ZERO,
    )
    global_remaining = max(
        allocation_equity_usd * settings.trading_copy_max_total_allocation_pct
        - await live_open_margin_for_account(session, account_key=account.key),
        ZERO,
    )
    margin_usd = min(target_margin, source_remaining, global_remaining)
    notional_usd = margin_usd * source_leverage
    margin_usd, notional_usd, _ = adjust_open_sizing_to_min_order(
        target_notional=target_notional,
        margin_usd=margin_usd,
        notional_usd=notional_usd,
        source_remaining=source_remaining,
        global_remaining=global_remaining,
        source_leverage=source_leverage,
        settings=settings,
    )
    if notional_usd < live_min_order_notional_usd(settings):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_below_min_order_notional",
            leverage=source_leverage,
            limit_price=price,
            margin_usd=margin_usd,
            requested_notional_usd=notional_usd,
            requested_size=notional_usd / price if price > ZERO else ZERO,
        )

    action = "add" if position is not None and part.action == "open" else part.action
    intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=action,
        side=part.side,
        size=notional_usd / price,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        leverage=source_leverage,
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=allocation_usd,
        source_perp_equity_usd=source_perp_equity,
        source_exposure_pct=source_exposure_pct,
        created_at=fill_datetime(fill),
    )
    return await submit_live_copy_intent(
        session,
        account=account,
        intent=intent,
        settings=settings,
        trading_client=trading_client,
    )


async def apply_live_close_part(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    coin = str(fill.get("coin") or "")
    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    if position is None or position.side != part.side:
        source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_matching_position_missing",
            leverage=source_leverage,
        )
    pending_close_size = await live_pending_close_size_for_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
        since=position.last_reconciled_at,
    )
    available_size = max(position.size - pending_close_size, ZERO)
    if available_size <= POSITION_EPSILON:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_already_pending",
            leverage=position.leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
        slippage_bps=settings.live_trading_limit_slippage_bps,
        latency_ms=0,
    )
    if execution_context is None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_execution_price_unavailable",
            leverage=position.leverage,
        )
    if execution_context.price_drift_bps > settings.trading_copy_max_price_drift_bps:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_price_drift_too_high",
            leverage=position.leverage,
            limit_price=execution_context.execution_price,
        )

    close_size = live_close_size_for_part(
        position=position,
        part=part,
        source_account_state=source_account_state,
        coin=coin,
        available_size=available_size,
    )
    if close_size is None:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_ratio_missing",
            leverage=position.leverage,
        )
    if close_size <= ZERO:
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_size_zero",
            leverage=position.leverage,
        )
    price = execution_context.execution_price
    notional_usd = close_size * price
    if live_close_below_min_order_notional(notional_usd, settings=settings):
        return await record_live_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            reason="live_close_below_min_order_notional",
            leverage=position.leverage,
            limit_price=price,
            margin_usd=margin_from_notional(notional_usd, position.leverage),
            requested_notional_usd=notional_usd,
            requested_size=close_size,
        )
    leverage = (
        position.leverage
        if position.leverage > ZERO
        else leverage_for_fill(
            fill=fill,
            source_leverages=source_leverages,
        )
    )
    intent = build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=coin,
        action=part.action,
        side=part.side,
        size=close_size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        limit_price=price,
        source_price=execution_context.source_price,
        observed_price=execution_context.observed_price,
        price_drift_bps=execution_context.price_drift_bps,
        price_source=execution_context.price_source,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=None,
        source_perp_equity_usd=source_perp_equity if source_perp_equity > ZERO else None,
        source_exposure_pct=None,
        created_at=fill_datetime(fill),
    )
    return await submit_live_copy_intent(
        session,
        account=account,
        intent=intent,
        settings=settings,
        trading_client=trading_client,
    )


async def submit_live_copy_intent(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    try:
        result = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=settings,
            client=trading_client,
        )
    except LiveOrderSubmitError as exc:
        logger.warning(
            "live copy order submit failed account=%s source=%s coin=%s reason=%s",
            account.key,
            intent.source_wallet,
            intent.coin,
            str(exc) or exc.__class__.__name__,
        )
        return live_skip("live_order_submit_error")
    if not result.submitted:
        return live_skip("live_order_not_submitted")
    if result.order.status in {"rejected", "failed", "canceled"}:
        return live_skip(f"live_order_{result.order.status}")
    return PaperCopyBatchResult(processed_fills=1 if result.submitted else 0)


async def record_live_skip(
    session: AsyncSession,
    *,
    account: TradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    reason: str,
    leverage: Decimal | None = None,
    limit_price: Decimal | None = None,
    margin_usd: Decimal | None = None,
    requested_notional_usd: Decimal | None = None,
    requested_size: Decimal | None = None,
    hidden_from_activity: bool = False,
    source_fill_age_seconds: float | None = None,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return live_skip(reason)
    coin = str(fill.get("coin") or "")
    source_price = decimal_or_zero(fill.get("price"))
    resolved_price = limit_price if limit_price is not None and limit_price > ZERO else source_price
    resolved_notional = (
        requested_notional_usd
        if requested_notional_usd is not None and requested_notional_usd > ZERO
        else max(part.source_notional_usd, ZERO)
    )
    resolved_size = (
        requested_size
        if requested_size is not None and requested_size > ZERO
        else max(part.source_size, ZERO)
    )
    if resolved_size <= ZERO and resolved_notional > ZERO and resolved_price > ZERO:
        resolved_size = resolved_notional / resolved_price
    if resolved_notional <= ZERO and resolved_size > ZERO and resolved_price > ZERO:
        resolved_notional = resolved_size * resolved_price
    resolved_leverage = leverage if leverage is not None and leverage > ZERO else Decimal("1")
    reduce_only = part.action in {"reduce", "close", "flip_close"}
    raw_payload: dict[str, Any] = {
        "skipReason": reason,
        "sourceFill": {
            "externalFillId": source_fill_id,
            "coin": coin,
            "price": str(fill.get("price") or ""),
            "time": fill.get("time"),
        },
    }
    if hidden_from_activity:
        raw_payload["hiddenFromActivity"] = True
    if source_fill_age_seconds is not None:
        raw_payload["sourceFillAgeSeconds"] = max(round(source_fill_age_seconds, 3), 0)

    stmt = insert(TradingOrder).values(
        account_key=account.key,
        account_type="live",
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
        client_order_id=build_client_order_id(
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            source_fill_id=source_fill_id,
            sequence_index=part.sequence_index,
            action=part.action,
        ),
        coin=coin,
        action=part.action,
        side=part.side,
        is_buy=trade_is_buy(side=part.side, reduce_only=reduce_only),
        reduce_only=reduce_only,
        order_type="skip",
        status="failed",
        requested_size=resolved_size,
        requested_notional_usd=resolved_notional,
        margin_usd=margin_usd,
        leverage=resolved_leverage,
        limit_price=resolved_price if resolved_price > ZERO else None,
        filled_size=ZERO,
        filled_notional_usd=ZERO,
        fee_usd=ZERO,
        error=f"skip:{reason}",
        raw_payload=raw_payload,
        created_at=fill_datetime(fill),
    )
    await session.execute(
        stmt.on_conflict_do_nothing(
            constraint="ux_trading_orders_account_source_fill_sequence"
        )
    )
    return live_skip(reason)


async def load_live_copy_recovery_sources(
    session: AsyncSession,
    *,
    max_sources: int,
) -> list[str]:
    position_result = await session.execute(
        select(
            TradingPosition.source_wallet,
            func.max(WalletScore.score).label("score"),
        )
        .outerjoin(WalletScore, WalletScore.wallet_address == TradingPosition.source_wallet)
        .where(
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != "",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
        .group_by(TradingPosition.source_wallet)
        .order_by(
            func.max(WalletScore.score).desc().nulls_last(),
            TradingPosition.source_wallet.asc(),
        )
        .limit(max_sources)
    )
    sources = [str(row.source_wallet).lower() for row in position_result.all() if row.source_wallet]
    remaining = max(max_sources - len(sources), 0)
    if remaining <= 0:
        return unique_strings(sources)

    allocation_result = await session.execute(
        select(
            PaperCopyAllocation.source_wallet,
            func.min(PaperCopyAllocation.rank).label("first_rank"),
        )
        .where(
            PaperCopyAllocation.active.is_(True),
            PaperCopyAllocation.source_wallet != "",
        )
        .group_by(PaperCopyAllocation.source_wallet)
        .order_by(
            func.min(PaperCopyAllocation.rank).asc(),
            PaperCopyAllocation.source_wallet.asc(),
        )
        .limit(remaining)
    )
    sources.extend(
        str(row.source_wallet).lower() for row in allocation_result.all() if row.source_wallet
    )
    return unique_strings(sources)


async def load_live_accounts_for_source_copy(
    session: AsyncSession,
    *,
    source_wallet: str,
    for_update: bool = False,
) -> list[TradingAccount]:
    normalized_source = source_wallet.lower()
    open_exposure_exists = (
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == TradingAccount.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == normalized_source,
        )
        .exists()
    )
    query = (
        select(TradingAccount)
        .where(
            TradingAccount.account_type == "live",
            (TradingAccount.status == "enabled")
            | ((TradingAccount.status == "exit_only") & open_exposure_exists),
        )
        .order_by(TradingAccount.key.asc())
    )
    if for_update:
        query = query.with_for_update()
    result = await session.scalars(query)
    return list(result.all())


async def live_order_exists(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
) -> bool:
    existing = await session.scalar(
        select(TradingOrder.id).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == source_wallet,
            TradingOrder.source_fill_id == source_fill_id,
            TradingOrder.sequence_index == sequence_index,
        )
    )
    return existing is not None


async def live_pending_close_size_for_position(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    since: datetime | None,
) -> Decimal:
    query = select(TradingOrder).where(
        TradingOrder.account_key == account_key,
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet == source_wallet,
        TradingOrder.coin == coin,
        TradingOrder.reduce_only.is_(True),
        TradingOrder.status.in_(PENDING_CLOSE_ORDER_STATUSES),
    )
    if since is not None:
        query = query.where(TradingOrder.created_at >= since)
    result = await session.scalars(query)
    return live_pending_close_size_from_orders(result.all())


def live_pending_close_size_from_orders(orders: list[TradingOrder]) -> Decimal:
    total = ZERO
    for order in orders:
        if order.status not in PENDING_CLOSE_ORDER_STATUSES:
            continue
        if order.status == "filled" and order.filled_size > ZERO:
            total += order.filled_size
            continue
        total += order.requested_size
    return total


async def live_market_is_reserved_by_other_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> bool:
    existing_position_id = await session.scalar(
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == coin,
            TradingPosition.source_wallet != source_wallet,
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
        .limit(1)
    )
    return existing_position_id is not None


def live_exchange_position_conflict(
    *,
    source_position: TradingPosition | None,
    exchange_position: TradingPosition | None,
    side: str,
) -> str | None:
    if exchange_position is None:
        return None
    if source_position is None:
        return "live_exchange_position_conflict"
    if exchange_position.side != side:
        return "live_exchange_position_side_conflict"
    return None


def live_min_order_notional_usd(settings: Settings) -> Decimal:
    return max(
        settings.trading_copy_min_order_notional_usd,
        settings.live_trading_min_order_notional_usd,
    )


def live_close_below_min_order_notional(
    notional_usd: Decimal,
    *,
    settings: Settings,
) -> bool:
    return notional_usd < live_min_order_notional_usd(settings)


def live_close_size_for_part(
    *,
    position: TradingPosition,
    part: SourceFillPart,
    source_account_state: PaperSourceAccountState | None,
    coin: str,
    available_size: Decimal | None = None,
) -> Decimal | None:
    position_size = (
        min(position.size, available_size)
        if available_size is not None
        else position.size
    )
    if live_source_position_is_final_close(
        source_account_state,
        coin=coin,
        side=part.side,
    ):
        return position_size
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return None
    return min(position_size, position_size * part.close_ratio)


def live_source_position_is_final_close(
    source_account_state: PaperSourceAccountState | None,
    *,
    coin: str,
    side: str,
) -> bool:
    if not source_state_available_for_reconciliation(source_account_state):
        return False
    source_position = resolve_source_current_position(
        source_account_state.positions_by_coin,
        coin,
    )
    return source_position is None or source_position.side != side


async def live_copy_recovery_start_time_ms(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> int | None:
    latest_order_at = await session.scalar(
        select(func.max(TradingOrder.created_at)).where(
            TradingOrder.account_type == "live",
            TradingOrder.source_wallet == source_wallet,
        )
    )
    earliest_opened_at = await session.scalar(
        select(func.min(TradingPosition.opened_at)).where(
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
        )
    )
    anchor = earliest_opened_at or latest_order_at
    if anchor is None:
        return None
    return max(0, int(anchor.timestamp() * 1000) - LIVE_COPY_RECOVERY_OVERLAP_MS)


async def load_wallet_fills_for_live_copy_recovery(
    session: AsyncSession,
    *,
    source_wallet: str,
    start_time_ms: int,
    limit: int,
) -> list[dict[str, Any]]:
    result = await session.execute(
        select(WalletFill)
        .where(
            WalletFill.wallet_address == source_wallet,
            WalletFill.timestamp_ms >= start_time_ms,
        )
        .order_by(WalletFill.timestamp_ms.asc(), WalletFill.external_fill_id.asc())
        .limit(limit)
    )
    return [paper_source_fill_from_wallet_fill(fill) for fill in result.scalars().all()]


async def live_open_margin_for_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.margin_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
        )
    )
    return decimal_or_zero(value)


async def live_open_margin_for_account(session: AsyncSession, *, account_key: str) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.margin_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    return decimal_or_zero(value)


async def gather_two(first: Any, second: Any) -> tuple[Any, Any]:
    return await asyncio.gather(first, second)


def combine_batch_results(
    left: PaperCopyBatchResult,
    right: PaperCopyBatchResult,
) -> PaperCopyBatchResult:
    return PaperCopyBatchResult(
        processed_fills=left.processed_fills + right.processed_fills,
        skipped_fills=left.skipped_fills + right.skipped_fills,
        accounts_updated=left.accounts_updated + right.accounts_updated,
        realized_pnl_usd=left.realized_pnl_usd + right.realized_pnl_usd,
        fee_usd=left.fee_usd + right.fee_usd,
        skip_reasons=combine_skip_reasons(left.skip_reasons, right.skip_reasons),
    )


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
