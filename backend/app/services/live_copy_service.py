import asyncio
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
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
    LIVE_EXCHANGE_SOURCE,
    LiveOrderSubmitError,
    live_tradable_equity_usd,
    load_live_source_position,
    reconcile_live_trading_account,
    submit_live_trade_intent,
)
from app.services.market_price_cache import MarketPriceCache, dex_from_coin
from app.services.paper_trading_service import (
    ExecutionMarketPrices,
    PaperCopyBatchResult,
    PaperSourceAllocation,
    SourceFillPart,
    build_execution_context,
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
    sorted_paper_source_fills,
)
from app.services.trading_core import TradeIntent, build_copy_trade_intent, margin_from_notional

ZERO = Decimal("0")
LIVE_COPY_RECOVERY_OVERLAP_MS = 5 * 60 * 1000


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
        return PaperCopyBatchResult(skipped_fills=len(fills))

    accounts = await load_live_accounts_for_source_copy(
        session,
        source_wallet=normalized_source_wallet,
    )
    if not accounts:
        return PaperCopyBatchResult(skipped_fills=len(fills))

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
        return PaperCopyBatchResult(skipped_fills=len(fills))

    processed = 0
    skipped = 0
    touched_accounts: dict[str, TradingAccount] = {}
    live_client = trading_client or HyperliquidLiveTradingClient(settings=resolved_settings)

    for fill in sorted_paper_source_fills(fills):
        parts = plan_source_fill(fill)
        if not parts:
            skipped += len(accounts)
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
                    skipped += 1
                    continue
                if source_state_skip_reason is not None and part_requires_source_equity(part):
                    skipped += 1
                    continue

                fill_result = await apply_live_copy_part(
                    session,
                    account=account,
                    allocation=allocation,
                    fill=fill,
                    part=part,
                    source_perp_equity=source_perp_equity,
                    source_leverages=source_leverages,
                    market_prices=market_prices,
                    settings=resolved_settings,
                    trading_client=live_client,
                )
                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
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


async def apply_live_copy_part(
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
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return PaperCopyBatchResult(skipped_fills=1)
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
        return PaperCopyBatchResult(skipped_fills=1)
    if source_perp_equity <= ZERO:
        return PaperCopyBatchResult(skipped_fills=1)
    coin = str(fill.get("coin") or "")
    tradable_equity_usd = live_tradable_equity_usd(
        account,
        dex=dex_from_coin(coin),
        settings=settings,
    )
    if tradable_equity_usd <= ZERO:
        return PaperCopyBatchResult(skipped_fills=1)

    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=coin,
    )
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return PaperCopyBatchResult(skipped_fills=1)
    if position is not None and position.side != part.side:
        return PaperCopyBatchResult(skipped_fills=1)
    if position is None and not allocation.active:
        return PaperCopyBatchResult(skipped_fills=1)

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return PaperCopyBatchResult(skipped_fills=1)
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return PaperCopyBatchResult(skipped_fills=1)

    price = execution_context.execution_price
    allocation_usd = tradable_equity_usd * allocation.allocation_pct
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
        tradable_equity_usd * settings.paper_copy_max_total_allocation_pct
        - await live_open_margin_for_account(session, account_key=account.key),
        ZERO,
    )
    margin_usd = min(target_margin, source_remaining, global_remaining)
    notional_usd = margin_usd * source_leverage
    if notional_usd < settings.live_trading_min_order_notional_usd:
        return PaperCopyBatchResult(skipped_fills=1)

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
    source_perp_equity: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: ExecutionMarketPrices,
    settings: Settings,
    trading_client: HyperliquidLiveTradingClient,
) -> PaperCopyBatchResult:
    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=str(fill.get("coin") or ""),
    )
    if position is None or position.side != part.side:
        return PaperCopyBatchResult(skipped_fills=1)
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return PaperCopyBatchResult(skipped_fills=1)

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return PaperCopyBatchResult(skipped_fills=1)
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return PaperCopyBatchResult(skipped_fills=1)

    close_size = min(position.size, position.size * part.close_ratio)
    if close_size <= ZERO:
        return PaperCopyBatchResult(skipped_fills=1)
    price = execution_context.execution_price
    notional_usd = close_size * price
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
        coin=str(fill.get("coin") or ""),
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
    except LiveOrderSubmitError:
        return PaperCopyBatchResult(skipped_fills=1)
    if not result.submitted or result.order.status in {"rejected", "failed", "canceled"}:
        return PaperCopyBatchResult(skipped_fills=1)
    return PaperCopyBatchResult(processed_fills=1 if result.submitted else 0)


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
