import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.models import (
    PaperCopyAllocation,
    PaperCopyFill,
    PaperPosition,
    PaperTradingAccount,
    WalletScore,
    WatchedWallet,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.paper_trading import (
    PaperTradingPolicyRead,
    PaperTradingSummaryResponse,
)
from app.services.wallet_current_state_service import object_or_empty

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
POSITION_EPSILON = Decimal("0.00000001")
BPS_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True)
class PaperSourceAllocation:
    source_wallet: str
    rank: int
    score: Decimal | None
    allocation_pct: Decimal


@dataclass(frozen=True)
class PaperSourceAccountState:
    dex: str
    account_value: Decimal
    leverage_by_coin: dict[str, Decimal]
    skip_reason: str | None = None


@dataclass(frozen=True)
class SourceFillPart:
    action: str
    side: str
    source_size: Decimal
    source_notional_usd: Decimal
    sequence_index: int
    close_ratio: Decimal | None = None
    start_position: Decimal | None = None


@dataclass(frozen=True)
class PaperExecutionContext:
    source_price: Decimal
    observed_price: Decimal
    execution_price: Decimal
    price_drift_bps: Decimal
    slippage_bps: Decimal
    latency_ms: int
    price_source: str


@dataclass(frozen=True)
class PaperCopyBatchResult:
    processed_fills: int = 0
    skipped_fills: int = 0
    accounts_updated: int = 0
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO


async def get_paper_trading_summary(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    recent_fill_limit: int = 100,
) -> PaperTradingSummaryResponse:
    resolved_settings = settings or get_settings()
    await refresh_paper_copy_allocations(session, settings=resolved_settings)
    await session.commit()

    accounts_result = await session.execute(
        select(PaperTradingAccount).order_by(PaperTradingAccount.key.asc())
    )
    allocations_result = await session.execute(
        select(PaperCopyAllocation).order_by(
            PaperCopyAllocation.account_key.asc(),
            PaperCopyAllocation.rank.asc(),
        )
    )
    positions_result = await session.execute(
        select(PaperPosition).order_by(
            PaperPosition.account_key.asc(),
            PaperPosition.source_wallet.asc(),
            PaperPosition.coin.asc(),
        )
    )
    fills_result = await session.execute(
        select(PaperCopyFill)
        .order_by(PaperCopyFill.filled_at.desc(), PaperCopyFill.created_at.desc())
        .limit(recent_fill_limit)
    )
    return PaperTradingSummaryResponse(
        policy=paper_trading_policy(resolved_settings),
        accounts=list(accounts_result.scalars().all()),
        allocations=list(allocations_result.scalars().all()),
        positions=list(positions_result.scalars().all()),
        recent_fills=list(fills_result.scalars().all()),
    )


async def process_paper_copy_fills(
    session: AsyncSession,
    *,
    source_wallet: str,
    fills: list[dict[str, Any]],
    settings: Settings | None = None,
    client: HyperliquidClient | None = None,
) -> PaperCopyBatchResult:
    resolved_settings = settings or get_settings()
    if (
        not resolved_settings.paper_trading_enabled
        or not resolved_settings.paper_copy_enabled
        or not fills
    ):
        return PaperCopyBatchResult()

    allocations = await refresh_paper_copy_allocations(session, settings=resolved_settings)
    allocation = allocations.get(source_wallet.lower())
    if allocation is None:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    accounts = await load_enabled_paper_accounts(session)
    if not accounts:
        return PaperCopyBatchResult(skipped_fills=len(fills))

    if client is None:
        async with HyperliquidClient(resolved_settings) as hyperliquid_client:
            return await process_paper_copy_fills(
                session,
                source_wallet=source_wallet,
                fills=fills,
                settings=resolved_settings,
                client=hyperliquid_client,
            )

    source_account_states = await load_source_account_states(
        client=client,
        source_wallet=source_wallet,
        fills=fills,
    )

    market_prices = await load_execution_market_prices(
        client=client,
        fills=fills,
        settings=resolved_settings,
    )

    processed = 0
    skipped = 0
    realized_pnl = ZERO
    fee_usd = ZERO
    accounts_updated: set[str] = set()

    for fill in fills:
        source_account_state = source_account_states.get(dex_from_coin(fill.get("coin")))
        if source_account_state is None:
            skip_result = await record_skip_for_accounts(
                session,
                accounts=accounts,
                allocation=allocation,
                fill=fill,
                reason="source_account_state_missing",
                settings=resolved_settings,
            )
            skipped += skip_result.skipped_fills
            continue
        if source_account_state.skip_reason is not None:
            skip_result = await record_skip_for_accounts(
                session,
                accounts=accounts,
                allocation=allocation,
                fill=fill,
                reason=source_account_state.skip_reason,
                settings=resolved_settings,
            )
            skipped += skip_result.skipped_fills
            continue

        parts = plan_source_fill(fill)
        if not parts:
            skip_result = await record_skip_for_accounts(
                session,
                accounts=accounts,
                allocation=allocation,
                fill=fill,
                reason="unsupported_source_fill_direction",
                settings=resolved_settings,
            )
            skipped += skip_result.skipped_fills
            continue

        for account in accounts:
            for part in parts:
                fill_result = await apply_paper_fill_part(
                    session,
                    account=account,
                    allocation=allocation,
                    fill=fill,
                    part=part,
                    source_account_value=source_account_state.account_value,
                    source_leverages=source_account_state.leverage_by_coin,
                    market_prices=market_prices,
                    settings=resolved_settings,
                )
                processed += fill_result.processed_fills
                skipped += fill_result.skipped_fills
                realized_pnl += fill_result.realized_pnl_usd
                fee_usd += fill_result.fee_usd
                if fill_result.accounts_updated > 0:
                    accounts_updated.add(account.key)

    await session.commit()
    return PaperCopyBatchResult(
        processed_fills=processed,
        skipped_fills=skipped,
        accounts_updated=len(accounts_updated),
        realized_pnl_usd=realized_pnl,
        fee_usd=fee_usd,
    )


async def refresh_paper_copy_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
) -> dict[str, PaperSourceAllocation]:
    accounts = await sync_paper_trading_accounts(session, settings=settings)
    account_keys = [account.key for account in accounts]
    if account_keys:
        await session.execute(
            update(PaperCopyAllocation)
            .where(PaperCopyAllocation.account_key.in_(account_keys))
            .values(active=False)
        )

    source_allocations = await load_paper_source_allocations(session, settings=settings)
    for account in accounts:
        for allocation in source_allocations:
            source_wallet = allocation.source_wallet.lower()
            allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
            stmt = insert(PaperCopyAllocation).values(
                account_key=account.key,
                source_wallet=source_wallet,
                rank=allocation.rank,
                score=allocation.score,
                allocation_pct=allocation.allocation_pct,
                allocation_usd=allocation_usd,
                max_total_allocation_pct=settings.paper_copy_max_total_allocation_pct,
                active=account.enabled,
            )
            await session.execute(
                stmt.on_conflict_do_update(
                    constraint="ux_paper_copy_allocations_account_source",
                    set_={
                        "rank": stmt.excluded.rank,
                        "score": stmt.excluded.score,
                        "allocation_pct": stmt.excluded.allocation_pct,
                        "allocation_usd": stmt.excluded.allocation_usd,
                        "max_total_allocation_pct": stmt.excluded.max_total_allocation_pct,
                        "active": stmt.excluded.active,
                    },
                )
            )

    return {allocation.source_wallet.lower(): allocation for allocation in source_allocations}


async def sync_paper_trading_accounts(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[PaperTradingAccount]:
    for account_config in settings.paper_copy_accounts:
        config_payload = account_config.model_dump(mode="json")
        stmt = insert(PaperTradingAccount).values(
            key=account_config.key,
            label=account_config.label,
            starting_balance_usd=account_config.starting_balance_usd,
            cash_balance_usd=account_config.starting_balance_usd,
            equity_usd=account_config.starting_balance_usd,
            enabled=account_config.enabled,
            config_payload=config_payload,
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["key"],
                set_={
                    "label": stmt.excluded.label,
                    "enabled": stmt.excluded.enabled,
                    "config_payload": stmt.excluded.config_payload,
                },
            )
        )

    result = await session.execute(
        select(PaperTradingAccount).where(
            PaperTradingAccount.key.in_([account.key for account in settings.paper_copy_accounts])
        )
    )
    return list(result.scalars().all())


async def load_paper_source_allocations(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[PaperSourceAllocation]:
    result = await session.execute(
        select(WatchedWallet.address, WalletScore.score)
        .join(WalletScore, WalletScore.wallet_address == WatchedWallet.address)
        .where(
            WatchedWallet.enabled.is_(True),
            WatchedWallet.polling_tier != "cooldown",
            WalletScore.score > ZERO,
        )
        .order_by(WalletScore.score.desc(), WalletScore.updated_at.desc())
        .limit(settings.paper_copy_top_wallet_count)
    )
    allocations: list[PaperSourceAllocation] = []
    for index, row in enumerate(result.mappings().all(), start=1):
        allocation_pct = (
            settings.paper_copy_top_tier_allocation_pct
            if index <= settings.paper_copy_top_tier_wallet_count
            else settings.paper_copy_standard_allocation_pct
        )
        allocations.append(
            PaperSourceAllocation(
                source_wallet=str(row["address"]).lower(),
                rank=index,
                score=row["score"],
                allocation_pct=allocation_pct,
            )
        )
    return allocations


async def load_enabled_paper_accounts(session: AsyncSession) -> list[PaperTradingAccount]:
    result = await session.execute(
        select(PaperTradingAccount)
        .where(PaperTradingAccount.enabled.is_(True))
        .order_by(PaperTradingAccount.key.asc())
    )
    return list(result.scalars().all())


async def load_source_account_states(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    fills: list[dict[str, Any]],
) -> dict[str, PaperSourceAccountState]:
    dexes = {dex_from_coin(fill.get("coin")) for fill in fills}
    if not dexes:
        dexes.add("")

    states: dict[str, PaperSourceAccountState] = {}
    for dex in sorted(dexes):
        states[dex] = await load_source_account_state(
            client=client,
            source_wallet=source_wallet,
            dex=dex,
        )
    return states


async def load_source_account_state(
    *,
    client: HyperliquidClient,
    source_wallet: str,
    dex: str,
) -> PaperSourceAccountState:
    try:
        clearinghouse_state = await client.clearinghouse_state(
            user=source_wallet,
            dex=dex or None,
        )
    except Exception as exc:
        logger.warning(
            "paper copy source account fetch failed wallet=%s dex=%s error=%s",
            source_wallet,
            dex or "default",
            exc,
        )
        return PaperSourceAccountState(
            dex=dex,
            account_value=ZERO,
            leverage_by_coin={},
            skip_reason="source_account_state_fetch_failed",
        )

    margin_summary_raw = clearinghouse_state.get("marginSummary")
    if not isinstance(margin_summary_raw, dict):
        return PaperSourceAccountState(
            dex=dex,
            account_value=ZERO,
            leverage_by_coin=parse_source_leverages(clearinghouse_state),
            skip_reason="source_account_margin_summary_missing",
        )

    account_value = decimal_or_none(margin_summary_raw.get("accountValue"))
    if account_value is None:
        return PaperSourceAccountState(
            dex=dex,
            account_value=ZERO,
            leverage_by_coin=parse_source_leverages(clearinghouse_state),
            skip_reason="source_account_value_missing",
        )
    if account_value <= ZERO:
        return PaperSourceAccountState(
            dex=dex,
            account_value=ZERO,
            leverage_by_coin=parse_source_leverages(clearinghouse_state),
            skip_reason="source_account_value_zero",
        )

    return PaperSourceAccountState(
        dex=dex,
        account_value=account_value,
        leverage_by_coin=parse_source_leverages(clearinghouse_state),
    )


def parse_source_leverages(payload: dict[str, Any]) -> dict[str, Decimal]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return {}

    leverages: dict[str, Decimal] = {}
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = object_or_empty(item.get("position"))
        coin = str(position.get("coin") or "")
        leverage = object_or_empty(position.get("leverage"))
        leverage_value = decimal_or_none(leverage.get("value"))
        if coin and leverage_value is not None and leverage_value > ZERO:
            leverages[coin] = leverage_value
    return leverages


async def load_execution_market_prices(
    *,
    client: HyperliquidClient,
    fills: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Decimal]:
    if settings.paper_copy_latency_ms > 0:
        await asyncio.sleep(settings.paper_copy_latency_ms / 1000)
    if not settings.paper_copy_use_live_mid_price:
        return {}

    coins = {str(fill.get("coin") or "") for fill in fills}
    coins.discard("")
    if not coins:
        return {}

    try:
        mids = await client.all_mids()
    except Exception as exc:
        logger.warning("paper copy allMids fetch failed error=%s", exc)
        return {}

    market_prices: dict[str, Decimal] = {}
    for coin in coins:
        price = resolve_coin_decimal(mids, coin)
        if price is not None and price > ZERO:
            market_prices[coin] = price
    return market_prices


def plan_source_fill(fill: dict[str, Any]) -> list[SourceFillPart]:
    raw_json = raw_json_from_fill(fill)
    direction = str(raw_json.get("dir") or "")
    source_size = decimal_or_zero(fill.get("size"))
    source_price = decimal_or_zero(fill.get("price"))
    source_notional = decimal_or_zero(fill.get("notionalUsd")) or source_price * source_size
    start_position = decimal_or_none(raw_json.get("startPosition"))

    if source_size <= ZERO or source_price <= ZERO or source_notional <= ZERO:
        return []
    if direction == "Open Long":
        return [
            SourceFillPart(
                action="open",
                side="long",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Open Short":
        return [
            SourceFillPart(
                action="open",
                side="short",
                source_size=source_size,
                source_notional_usd=source_notional,
                sequence_index=0,
                start_position=start_position,
            )
        ]
    if direction == "Close Long":
        return close_parts(
            side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Close Short":
        return close_parts(
            side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="close",
        )
    if direction == "Long > Short":
        return flip_parts(
            close_side="long",
            open_side="short",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    if direction == "Short > Long":
        return flip_parts(
            close_side="short",
            open_side="long",
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
        )
    return []


def close_parts(
    *,
    side: str,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
    action: str,
) -> list[SourceFillPart]:
    close_ratio = close_ratio_from_start_position(source_size, start_position)
    return [
        SourceFillPart(
            action=action,
            side=side,
            source_size=source_size,
            source_notional_usd=source_notional,
            sequence_index=0,
            close_ratio=close_ratio,
            start_position=start_position,
        )
    ]


def flip_parts(
    *,
    close_side: str,
    open_side: str,
    source_size: Decimal,
    source_notional: Decimal,
    start_position: Decimal | None,
) -> list[SourceFillPart]:
    if start_position is None:
        return close_parts(
            side=close_side,
            source_size=source_size,
            source_notional=source_notional,
            start_position=start_position,
            action="flip_close",
        )

    close_size = min(source_size, start_position.copy_abs())
    open_size = max(source_size - close_size, ZERO)
    parts: list[SourceFillPart] = []
    if close_size > ZERO:
        close_notional = proportional_value(source_notional, close_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_close",
                side=close_side,
                source_size=close_size,
                source_notional_usd=close_notional,
                sequence_index=0,
                close_ratio=close_ratio_from_start_position(close_size, start_position),
                start_position=start_position,
            )
        )
    if open_size > ZERO:
        open_notional = proportional_value(source_notional, open_size, source_size)
        parts.append(
            SourceFillPart(
                action="flip_open",
                side=open_side,
                source_size=open_size,
                source_notional_usd=open_notional,
                sequence_index=1,
                start_position=ZERO,
            )
        )
    return parts


async def apply_paper_fill_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_value: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return PaperCopyBatchResult(skipped_fills=1)
    if await paper_fill_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    ):
        return PaperCopyBatchResult()

    if part.action in {"open", "flip_open"}:
        return await apply_open_part(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            source_leverages=source_leverages,
            market_prices=market_prices,
            settings=settings,
        )
    return await apply_close_part(
        session,
        account=account,
        allocation=allocation,
        fill=fill,
        part=part,
        source_account_value=source_account_value,
        source_leverages=source_leverages,
        market_prices=market_prices,
        settings=settings,
    )


async def apply_open_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_value: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="invalid_price",
            settings=settings,
            leverage=source_leverage,
        )

    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=str(fill.get("coin") or ""),
    )
    if position is None and is_preexisting_source_add(part.start_position, side=part.side):
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="preexisting_source_position",
            settings=settings,
            leverage=source_leverage,
        )
    if position is not None and position.side != part.side:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="opposite_paper_position",
            settings=settings,
            leverage=source_leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="execution_price_unavailable",
            settings=settings,
            leverage=source_leverage,
        )
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="price_drift_too_high",
            settings=settings,
            execution_context=execution_context,
            leverage=source_leverage,
        )
    price = execution_context.execution_price

    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    source_exposure_pct = part.source_notional_usd / source_account_value
    target_notional = allocation_usd * source_exposure_pct
    target_margin = margin_from_notional(target_notional, source_leverage)
    source_remaining = max(
        allocation_usd
        - await open_margin_for_source(
            session,
            account_key=account.key,
            source_wallet=allocation.source_wallet,
        ),
        ZERO,
    )
    global_remaining = max(
        max(account.equity_usd, ZERO) * settings.paper_copy_max_total_allocation_pct
        - await open_margin_for_account(session, account_key=account.key),
        ZERO,
    )
    margin_usd = min(target_margin, source_remaining, global_remaining)
    notional_usd = margin_usd * source_leverage
    if notional_usd < settings.paper_copy_min_order_notional_usd:
        reason = open_notional_skip_reason(
            target_notional=target_notional,
            source_remaining=source_remaining * source_leverage,
            global_remaining=global_remaining * source_leverage,
            min_order_notional=settings.paper_copy_min_order_notional_usd,
        )
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason=reason,
            settings=settings,
            leverage=source_leverage,
        )

    paper_size = notional_usd / price
    fee = notional_usd * settings.paper_copy_fee_rate
    filled_at = fill_datetime(fill)
    action = "add" if position is not None and part.action == "open" else part.action

    if position is None:
        position = PaperPosition(
            account_key=account.key,
            source_wallet=allocation.source_wallet,
            coin=str(fill.get("coin") or ""),
            side=part.side,
            size=paper_size,
            entry_price=price,
            notional_usd=notional_usd,
            leverage=source_leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            opened_at=filled_at,
        )
        session.add(position)
    else:
        next_size = position.size + paper_size
        position.entry_price = weighted_average_price(
            position.entry_price,
            position.size,
            price,
            paper_size,
        )
        position.size = next_size
        position.notional_usd = next_size * price
        position.leverage = source_leverage
        position.margin_usd = margin_from_notional(position.notional_usd, source_leverage)
        position.fee_usd += fee

    apply_account_fee(account, fee)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action=action,
            price=price,
            size=paper_size,
            notional_usd=notional_usd,
            leverage=source_leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=ZERO,
            source_account_value=source_account_value,
            allocation_usd=allocation_usd,
            settings=settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(processed_fills=1, accounts_updated=1, fee_usd=fee)


async def apply_close_part(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_value: Decimal,
    source_leverages: dict[str, Decimal],
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperCopyBatchResult:
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="invalid_price",
            settings=settings,
        )
    position = await load_paper_position(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        coin=str(fill.get("coin") or ""),
    )
    if position is None or position.side != part.side:
        source_leverage = leverage_for_fill(fill=fill, source_leverages=source_leverages)
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="no_matching_paper_position",
            settings=settings,
            leverage=source_leverage,
        )
    leverage = safe_leverage(position.leverage)
    if part.close_ratio is None or part.close_ratio <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="missing_source_start_position",
            settings=settings,
            leverage=leverage,
        )

    execution_context = build_execution_context(
        fill=fill,
        part=part,
        market_prices=market_prices,
        settings=settings,
    )
    if execution_context is None:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="execution_price_unavailable",
            settings=settings,
            leverage=leverage,
        )
    if execution_context.price_drift_bps > settings.paper_copy_max_price_drift_bps:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="price_drift_too_high",
            settings=settings,
            execution_context=execution_context,
            leverage=leverage,
        )
    price = execution_context.execution_price

    close_ratio = min(part.close_ratio, Decimal("1"))
    close_size = min(position.size, position.size * close_ratio)
    if close_size <= POSITION_EPSILON or price <= ZERO:
        return await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=source_account_value,
            reason="invalid_close_size",
            settings=settings,
            leverage=leverage,
        )

    notional_usd = close_size * price
    margin_usd = margin_from_notional(notional_usd, leverage)
    realized_pnl = realized_pnl_for_close(
        side=position.side,
        entry_price=position.entry_price,
        exit_price=price,
        size=close_size,
    )
    fee = notional_usd * settings.paper_copy_fee_rate
    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    position.realized_pnl_usd += realized_pnl
    position.fee_usd += fee
    remaining_size = position.size - close_size
    if remaining_size <= POSITION_EPSILON:
        await session.delete(position)
        action = "close" if part.action == "close" else part.action
    else:
        position.size = remaining_size
        position.notional_usd = remaining_size * price
        position.margin_usd = margin_from_notional(position.notional_usd, leverage)
        action = "reduce" if part.action == "close" else part.action

    apply_account_realized_result(account, pnl_usd=realized_pnl, fee_usd=fee)
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action=action,
            price=price,
            size=close_size,
            notional_usd=notional_usd,
            leverage=leverage,
            margin_usd=margin_usd,
            fee_usd=fee,
            realized_pnl_usd=realized_pnl,
            source_account_value=source_account_value,
            allocation_usd=allocation_usd,
            settings=settings,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(
        processed_fills=1,
        accounts_updated=1,
        realized_pnl_usd=realized_pnl,
        fee_usd=fee,
    )


async def record_batch_skip(
    session: AsyncSession,
    *,
    accounts: list[PaperTradingAccount],
    allocation: PaperSourceAllocation,
    fills: list[dict[str, Any]],
    reason: str,
    settings: Settings,
) -> PaperCopyBatchResult:
    skipped = 0
    for fill in fills:
        result = await record_skip_for_accounts(
            session,
            accounts=accounts,
            allocation=allocation,
            fill=fill,
            reason=reason,
            settings=settings,
        )
        skipped += result.skipped_fills
    return PaperCopyBatchResult(skipped_fills=skipped)


async def record_skip_for_accounts(
    session: AsyncSession,
    *,
    accounts: list[PaperTradingAccount],
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    reason: str,
    settings: Settings,
) -> PaperCopyBatchResult:
    skipped = 0
    part = SourceFillPart(
        action="skip",
        side=side_from_fill_direction(raw_json_from_fill(fill).get("dir")),
        source_size=decimal_or_zero(fill.get("size")),
        source_notional_usd=decimal_or_zero(fill.get("notionalUsd")),
        sequence_index=0,
    )
    for account in accounts:
        result = await record_skip(
            session,
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            source_account_value=ZERO,
            reason=reason,
            settings=settings,
        )
        skipped += result.skipped_fills
    return PaperCopyBatchResult(skipped_fills=skipped)


async def record_skip(
    session: AsyncSession,
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    source_account_value: Decimal,
    reason: str,
    settings: Settings,
    execution_context: PaperExecutionContext | None = None,
    leverage: Decimal | None = None,
) -> PaperCopyBatchResult:
    source_fill_id = str(fill.get("externalFillId") or "")
    if not source_fill_id:
        return PaperCopyBatchResult(skipped_fills=1)
    if await paper_fill_exists(
        session,
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=source_fill_id,
        sequence_index=part.sequence_index,
    ):
        return PaperCopyBatchResult()

    allocation_usd = max(account.equity_usd, ZERO) * allocation.allocation_pct
    session.add(
        paper_copy_fill(
            account=account,
            allocation=allocation,
            fill=fill,
            part=part,
            action="skip",
            price=decimal_or_none(fill.get("price")),
            size=None,
            notional_usd=None,
            leverage=leverage,
            margin_usd=None,
            fee_usd=ZERO,
            realized_pnl_usd=ZERO,
            source_account_value=source_account_value,
            allocation_usd=allocation_usd,
            settings=settings,
            skipped_reason=reason,
            execution_context=execution_context,
        )
    )
    return PaperCopyBatchResult(skipped_fills=1)


def paper_copy_fill(
    *,
    account: PaperTradingAccount,
    allocation: PaperSourceAllocation,
    fill: dict[str, Any],
    part: SourceFillPart,
    action: str,
    price: Decimal | None,
    size: Decimal | None,
    notional_usd: Decimal | None,
    leverage: Decimal | None,
    margin_usd: Decimal | None,
    fee_usd: Decimal,
    realized_pnl_usd: Decimal,
    source_account_value: Decimal,
    allocation_usd: Decimal,
    settings: Settings,
    skipped_reason: str | None = None,
    execution_context: PaperExecutionContext | None = None,
) -> PaperCopyFill:
    source_exposure_pct = (
        part.source_notional_usd / source_account_value if source_account_value > ZERO else None
    )
    return PaperCopyFill(
        account_key=account.key,
        source_wallet=allocation.source_wallet,
        source_fill_id=str(fill.get("externalFillId") or ""),
        sequence_index=part.sequence_index,
        coin=str(fill.get("coin") or ""),
        action=action,
        side=part.side,
        price=price,
        size=size,
        notional_usd=notional_usd,
        leverage=leverage,
        margin_usd=margin_usd,
        fee_usd=fee_usd,
        realized_pnl_usd=realized_pnl_usd,
        source_price=decimal_or_none(fill.get("price")),
        source_size=part.source_size,
        source_notional_usd=part.source_notional_usd,
        source_account_value_usd=source_account_value if source_account_value > ZERO else None,
        source_exposure_pct=source_exposure_pct,
        allocation_pct=allocation.allocation_pct,
        allocation_usd=allocation_usd,
        skipped_reason=skipped_reason,
        filled_at=fill_datetime(fill),
        raw_payload=paper_fill_payload(
            fill=fill,
            settings=settings,
            execution_context=execution_context,
            leverage=leverage,
            margin_usd=margin_usd,
        ),
    )


async def load_paper_position(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> PaperPosition | None:
    return await session.scalar(
        select(PaperPosition).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
            PaperPosition.coin == coin,
        )
    )


async def paper_fill_exists(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
) -> bool:
    existing = await session.scalar(
        select(PaperCopyFill.id).where(
            PaperCopyFill.account_key == account_key,
            PaperCopyFill.source_wallet == source_wallet,
            PaperCopyFill.source_fill_id == source_fill_id,
            PaperCopyFill.sequence_index == sequence_index,
        )
    )
    return existing is not None


async def open_margin_for_source(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.margin_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
            PaperPosition.source_wallet == source_wallet,
        )
    )
    return decimal_or_zero(value)


async def open_margin_for_account(session: AsyncSession, *, account_key: str) -> Decimal:
    value = await session.scalar(
        select(func.coalesce(func.sum(PaperPosition.margin_usd), ZERO)).where(
            PaperPosition.account_key == account_key,
        )
    )
    return decimal_or_zero(value)


def leverage_for_fill(
    *,
    fill: dict[str, Any],
    source_leverages: dict[str, Decimal],
) -> Decimal:
    coin = str(fill.get("coin") or "")
    return safe_leverage(resolve_coin_decimal(source_leverages, coin))


def resolve_coin_decimal(values_by_coin: dict[str, Any], coin: str) -> Decimal | None:
    candidates = coin_symbol_candidates(coin)
    if not candidates:
        return None

    for candidate in candidates:
        value = decimal_or_none(values_by_coin.get(candidate))
        if value is not None:
            return value

    casefold_index = {
        str(key).casefold(): value
        for key, value in values_by_coin.items()
        if str(key).strip()
    }
    for candidate in candidates:
        value = decimal_or_none(casefold_index.get(candidate.casefold()))
        if value is not None:
            return value

    normalized_candidates = {normalize_coin_symbol(candidate) for candidate in candidates}
    normalized_candidates.discard("")
    for key, raw_value in values_by_coin.items():
        if normalize_coin_symbol(str(key)) in normalized_candidates:
            value = decimal_or_none(raw_value)
            if value is not None:
                return value
    return None


def coin_symbol_candidates(coin: str) -> list[str]:
    value = str(coin or "").strip()
    if not value:
        return []

    candidates = [value]
    if ":" in value:
        candidates.append(value.rsplit(":", maxsplit=1)[-1])
    if "/" in value:
        candidates.append(value.split("/", maxsplit=1)[0])

    expanded: list[str] = []
    for candidate in candidates:
        if candidate:
            expanded.append(candidate)
            expanded.append(candidate.upper())
            expanded.append(candidate.lower())
    return unique_strings(expanded)


def normalize_coin_symbol(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def safe_leverage(value: Decimal | None) -> Decimal:
    if value is None or value <= ZERO:
        return Decimal("1")
    return value


def margin_from_notional(notional_usd: Decimal, leverage: Decimal) -> Decimal:
    resolved_leverage = safe_leverage(leverage)
    if notional_usd <= ZERO:
        return ZERO
    return notional_usd / resolved_leverage


def open_notional_skip_reason(
    *,
    target_notional: Decimal,
    source_remaining: Decimal,
    global_remaining: Decimal,
    min_order_notional: Decimal,
) -> str:
    if target_notional < min_order_notional:
        return "below_min_order_notional"

    source_cap_blocked = source_remaining < min_order_notional
    total_cap_blocked = global_remaining < min_order_notional
    if source_cap_blocked and total_cap_blocked:
        return "source_and_total_allocation_caps_reached"
    if source_cap_blocked:
        return "source_allocation_cap_reached"
    if total_cap_blocked:
        return "total_allocation_cap_reached"
    return "below_min_order_notional"


def paper_trading_policy(settings: Settings) -> PaperTradingPolicyRead:
    return PaperTradingPolicyRead(
        enabled=settings.paper_trading_enabled and settings.paper_copy_enabled,
        top_wallet_count=settings.paper_copy_top_wallet_count,
        top_tier_wallet_count=settings.paper_copy_top_tier_wallet_count,
        top_tier_allocation_pct=settings.paper_copy_top_tier_allocation_pct,
        standard_allocation_pct=settings.paper_copy_standard_allocation_pct,
        max_total_allocation_pct=settings.paper_copy_max_total_allocation_pct,
        min_order_notional_usd=settings.paper_copy_min_order_notional_usd,
        fee_rate=settings.paper_copy_fee_rate,
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        max_price_drift_bps=settings.paper_copy_max_price_drift_bps,
        use_live_mid_price=settings.paper_copy_use_live_mid_price,
    )


def close_ratio_from_start_position(
    source_size: Decimal,
    start_position: Decimal | None,
) -> Decimal | None:
    if start_position is None or start_position.copy_abs() <= ZERO:
        return None
    return min(source_size / start_position.copy_abs(), Decimal("1"))


def is_preexisting_source_add(start_position: Decimal | None, *, side: str) -> bool:
    if start_position is None:
        return False
    if side == "long":
        return start_position > POSITION_EPSILON
    return start_position < -POSITION_EPSILON


def apply_account_fee(account: PaperTradingAccount, fee: Decimal) -> None:
    account.fee_usd += fee
    account.cash_balance_usd -= fee
    account.equity_usd = account.cash_balance_usd


def apply_account_realized_result(
    account: PaperTradingAccount,
    *,
    pnl_usd: Decimal,
    fee_usd: Decimal,
) -> None:
    account.realized_pnl_usd += pnl_usd
    account.fee_usd += fee_usd
    account.cash_balance_usd += pnl_usd - fee_usd
    account.equity_usd = account.cash_balance_usd


def realized_pnl_for_close(
    *,
    side: str,
    entry_price: Decimal,
    exit_price: Decimal,
    size: Decimal,
) -> Decimal:
    if side == "long":
        return (exit_price - entry_price) * size
    return (entry_price - exit_price) * size


def weighted_average_price(
    current_price: Decimal,
    current_size: Decimal,
    added_price: Decimal,
    added_size: Decimal,
) -> Decimal:
    total_size = current_size + added_size
    if total_size <= ZERO:
        return added_price
    return ((current_price * current_size) + (added_price * added_size)) / total_size


def proportional_value(value: Decimal, part: Decimal, total: Decimal) -> Decimal:
    if total <= ZERO:
        return ZERO
    return value * part / total


def build_execution_context(
    *,
    fill: dict[str, Any],
    part: SourceFillPart,
    market_prices: dict[str, Decimal],
    settings: Settings,
) -> PaperExecutionContext | None:
    source_price = decimal_or_zero(fill.get("price"))
    if source_price <= ZERO:
        return None

    coin = str(fill.get("coin") or "")
    observed_price = market_prices.get(coin) if settings.paper_copy_use_live_mid_price else None
    if observed_price is None:
        if settings.paper_copy_use_live_mid_price:
            return None
        observed_price = source_price
    if observed_price <= ZERO:
        return None

    execution_price = apply_adverse_slippage(
        price=observed_price,
        side=part.side,
        action=part.action,
        slippage_bps=settings.paper_copy_slippage_bps,
    )
    if execution_price <= ZERO:
        return None

    return PaperExecutionContext(
        source_price=source_price,
        observed_price=observed_price,
        execution_price=execution_price,
        price_drift_bps=price_drift_bps(source_price=source_price, observed_price=observed_price),
        slippage_bps=settings.paper_copy_slippage_bps,
        latency_ms=settings.paper_copy_latency_ms,
        price_source="live_mid" if settings.paper_copy_use_live_mid_price else "source_fill",
    )


def apply_adverse_slippage(
    *,
    price: Decimal,
    side: str,
    action: str,
    slippage_bps: Decimal,
) -> Decimal:
    adjustment = slippage_bps / BPS_DENOMINATOR
    if is_buy_execution(side=side, action=action):
        return price * (Decimal("1") + adjustment)
    return price * max(Decimal("1") - adjustment, ZERO)


def is_buy_execution(*, side: str, action: str) -> bool:
    is_open = action in {"open", "add", "flip_open"}
    if side == "long":
        return is_open
    return not is_open


def price_drift_bps(*, source_price: Decimal, observed_price: Decimal) -> Decimal:
    if source_price <= ZERO:
        return ZERO
    return (observed_price - source_price).copy_abs() / source_price * BPS_DENOMINATOR


def raw_json_from_fill(fill: dict[str, Any]) -> dict[str, Any]:
    raw_json = fill.get("rawJson")
    return raw_json if isinstance(raw_json, dict) else {}


def dex_from_coin(value: Any) -> str:
    coin = str(value or "").strip()
    if ":" not in coin:
        return ""
    dex = coin.split(":", maxsplit=1)[0].strip()
    return dex


def side_from_fill_direction(value: Any) -> str:
    direction = str(value or "")
    if "Short" in direction:
        return "short"
    return "long"


def fill_datetime(fill: dict[str, Any]) -> datetime:
    timestamp_ms = int(fill.get("timestampMs") or 0)
    if timestamp_ms <= 0:
        return datetime.now(UTC)
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)


def paper_fill_payload(
    *,
    fill: dict[str, Any],
    settings: Settings,
    execution_context: PaperExecutionContext | None = None,
    leverage: Decimal | None = None,
    margin_usd: Decimal | None = None,
) -> dict[str, Any]:
    return {
        "sourceFill": {
            "externalFillId": fill.get("externalFillId"),
            "coin": fill.get("coin"),
            "side": fill.get("side"),
            "price": fill.get("price"),
            "size": fill.get("size"),
            "timestampMs": fill.get("timestampMs"),
            "rawJson": fill.get("rawJson"),
        },
        "policy": {
            "feeRate": str(settings.paper_copy_fee_rate),
            "maxTotalAllocationPct": str(settings.paper_copy_max_total_allocation_pct),
            "slippageBps": str(settings.paper_copy_slippage_bps),
            "latencyMs": settings.paper_copy_latency_ms,
            "maxPriceDriftBps": str(settings.paper_copy_max_price_drift_bps),
            "useLiveMidPrice": settings.paper_copy_use_live_mid_price,
        },
        "execution": execution_payload(execution_context),
        "paper": {
            "leverage": str(leverage) if leverage is not None else None,
            "marginUsd": str(margin_usd) if margin_usd is not None else None,
        },
    }


def execution_payload(context: PaperExecutionContext | None) -> dict[str, Any] | None:
    if context is None:
        return None
    return {
        "sourcePrice": str(context.source_price),
        "observedPrice": str(context.observed_price),
        "executionPrice": str(context.execution_price),
        "priceDriftBps": str(context.price_drift_bps),
        "slippageBps": str(context.slippage_bps),
        "latencyMs": context.latency_ms,
        "priceSource": context.price_source,
    }


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def decimal_or_zero(value: Any) -> Decimal:
    return decimal_or_none(value) or ZERO
