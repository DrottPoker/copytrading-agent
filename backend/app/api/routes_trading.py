from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import db_session
from app.core.config import Settings, get_settings
from app.db.models import (
    DiscoveryWalletCandidate,
    TradingAccount,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WalletScore,
    WatchedWallet,
)
from app.schemas.trading import (
    LiveCloseAllResponse,
    LiveOrderSubmitResponse,
    LiveReconciliationResponse,
    LiveRiskLimitsRead,
    LiveTradingAccountCreateRequest,
    TestnetLiveOrderRequest,
    TradingAccountRead,
    TradingAccountsResponse,
    TradingAccountStatusRequest,
    TradingCapitalBalanceRead,
    TradingClosedTradeRead,
    TradingFillRead,
    TradingOrderRead,
    TradingPositionRead,
    TradingSourceMetadataRead,
)
from app.services.live_trading_service import (
    LIVE_EXCHANGE_SOURCE,
    LiveTradingServiceError,
    account_last_reconciliation,
    account_last_reconciliation_attempt,
    build_testnet_live_trade_intent,
    close_all_live_account_positions,
    close_live_account_position,
    create_live_trading_account,
    decimal_or_none,
    delete_live_trading_account,
    disable_live_trading_account,
    live_capital_mode,
    live_perp_equity_usd,
    live_position_current_notional,
    live_position_mark_price,
    live_position_unrealized_pnl,
    live_position_unrealized_pnl_pct,
    live_reconciliation_status,
    live_spot_available_usd,
    live_spot_balance_usd,
    live_tradable_equity_usd,
    live_unified_available_usd,
    live_unified_equity_usd,
    load_live_account,
    load_live_closed_trades,
    normalize_user_abstraction,
    reconcile_live_trading_account,
    start_live_trading_account,
    stop_live_trading_account,
    submit_live_trade_intent,
)
from app.services.paper_trading_service import load_wallet_monitoring_stats
from app.services.trading_account_service import list_trading_accounts
from app.services.wallet_service import wallet_pool_rank_cte

router = APIRouter(prefix="/trading", tags=["trading"])

TRADING_ACTIVITY_LIMIT = 100
TRADING_CLOSED_TRADE_LIMIT = 100
TRADING_CLOSED_TRADE_FILL_SCAN_LIMIT = 5000
POSITION_ADD_FILL_ACTIONS = frozenset({"add"})
POSITION_CLOSE_FILL_ACTIONS = frozenset({"reduce", "close", "flip_close"})


def request_audit_actor(request: Request) -> str:
    return str(getattr(request.state, "audit_actor", "dashboard"))


def live_risk_limits_read(settings: Settings) -> LiveRiskLimitsRead:
    return LiveRiskLimitsRead(
        max_weekly_loss_pct=settings.live_trading_max_weekly_loss_pct,
        max_orders_per_minute=settings.live_trading_max_orders_per_minute,
        reconciliation_max_snapshot_age_seconds=(
            settings.live_trading_reconciliation_max_snapshot_age_seconds
        ),
        entry_intent_ttl_seconds=settings.live_trading_entry_intent_ttl_seconds,
        reduce_only_when_stopped=settings.live_trading_reduce_only_when_stopped,
    )


def source_allocation_pct_for_rank(
    pool_rank: int | None,
    *,
    settings: Settings,
) -> Decimal | None:
    if pool_rank is None:
        return None
    if pool_rank <= max(settings.trading_copy_top_tier_wallet_count, 0):
        return settings.trading_copy_top_tier_allocation_pct
    return settings.trading_copy_standard_allocation_pct


def normalize_source_wallet(value: str | None) -> str | None:
    source = str(value or "").strip().lower()
    if not source or source == "__exchange__":
        return None
    return source


def collect_live_source_wallets(
    positions: list[TradingPosition],
    fills: list[TradingFill],
    orders: list[TradingOrder],
    closed_trades: list[object],
) -> list[str]:
    sources: set[str] = set()
    for item in [*positions, *fills, *orders, *closed_trades]:
        source = normalize_source_wallet(getattr(item, "source_wallet", None))
        if source is not None:
            sources.add(source)
    return sorted(sources)


async def load_trading_source_metadata(
    session: AsyncSession,
    *,
    source_wallets: list[str],
    settings: Settings,
) -> list[TradingSourceMetadataRead]:
    normalized_sources = sorted(
        source
        for source in {normalize_source_wallet(source_wallet) for source_wallet in source_wallets}
        if source is not None
    )
    if not normalized_sources:
        return []

    ranked_scores = wallet_pool_rank_cte()
    result = await session.execute(
        select(
            WalletScore.wallet_address.label("source_wallet"),
            WatchedWallet.label.label("source_label"),
            WalletScore.score.label("score"),
            ranked_scores.c.pool_rank.label("pool_rank"),
        )
        .outerjoin(WatchedWallet, WatchedWallet.address == WalletScore.wallet_address)
        .outerjoin(ranked_scores, ranked_scores.c.wallet_address == WalletScore.wallet_address)
        .where(func.lower(WalletScore.wallet_address).in_(normalized_sources))
    )

    metadata = {
        source: TradingSourceMetadataRead(source_wallet=source) for source in normalized_sources
    }
    for row in result.mappings().all():
        source_wallet = str(row["source_wallet"]).lower()
        pool_rank = int(row["pool_rank"]) if row["pool_rank"] is not None else None
        existing = metadata[source_wallet]
        metadata[source_wallet] = existing.model_copy(
            update={
                "source_label": str(row["source_label"]) if row["source_label"] else None,
                "rank": pool_rank,
                "pool_rank": pool_rank,
                "score": row["score"],
                "allocation_pct": source_allocation_pct_for_rank(pool_rank, settings=settings),
            }
        )

    missing_label_sources = [
        source
        for source in normalized_sources
        if source not in metadata or metadata[source].source_label is None
    ]
    if missing_label_sources:
        label_result = await session.execute(
            select(
                DiscoveryWalletCandidate.wallet_address,
                DiscoveryWalletCandidate.source_label,
            )
            .where(
                func.lower(DiscoveryWalletCandidate.wallet_address).in_(missing_label_sources),
                DiscoveryWalletCandidate.source_label.is_not(None),
            )
            .order_by(
                DiscoveryWalletCandidate.source_rank.asc().nulls_last(),
                DiscoveryWalletCandidate.updated_at.desc(),
            )
        )
        for wallet_address, source_label in label_result.all():
            source_wallet = str(wallet_address).lower()
            if not source_wallet or not source_label:
                continue
            existing = metadata[source_wallet]
            if existing.source_label is None:
                metadata[source_wallet] = existing.model_copy(
                    update={"source_label": str(source_label)}
                )

    monitoring_stats = await load_wallet_monitoring_stats(
        session,
        source_wallets=normalized_sources,
        settings=settings,
        now=datetime.now(UTC),
    )
    for source_wallet, monitoring in monitoring_stats.items():
        existing = metadata[source_wallet]
        metadata[source_wallet] = existing.model_copy(
            update={
                "monitored_seconds": monitoring.monitored_seconds,
                "first_monitored_at": monitoring.first_monitored_at,
                "current_monitoring_started_at": monitoring.current_monitoring_started_at,
                "last_monitored_at": monitoring.last_monitored_at,
            }
        )

    performance_result = await session.execute(
        select(
            TradingFill.source_wallet.label("source_wallet"),
            func.coalesce(func.sum(TradingFill.realized_pnl_usd), Decimal("0")).label(
                "live_realized_pnl_usd"
            ),
            func.count(TradingFill.id).label("live_fill_count"),
        )
        .where(
            TradingFill.account_type == "live",
            TradingFill.source_wallet.in_(normalized_sources),
        )
        .group_by(TradingFill.source_wallet)
    )
    for row in performance_result.mappings().all():
        source_wallet = str(row["source_wallet"]).lower()
        existing = metadata[source_wallet]
        metadata[source_wallet] = existing.model_copy(
            update={
                "live_realized_pnl_usd": row["live_realized_pnl_usd"] or Decimal("0"),
                "live_fill_count": int(row["live_fill_count"] or 0),
            }
        )

    return [metadata[source] for source in normalized_sources]


async def trading_account_read(
    session: AsyncSession,
    account: TradingAccount,
    settings: Settings,
) -> TradingAccountRead:
    await session.flush()
    await session.refresh(account)
    return enriched_trading_account_read(account, settings=settings)


def enriched_trading_account_read(
    account: TradingAccount,
    *,
    settings: Settings,
) -> TradingAccountRead:
    read = TradingAccountRead.model_validate(account)
    if account.account_type != "live":
        return read

    last_reconciliation = account_last_reconciliation(account)
    last_attempt = account_last_reconciliation_attempt(account)
    mode = live_capital_mode(settings)
    component_errors = last_attempt.get("componentErrors")
    reconciliation_errors = (
        {str(key): str(value) for key, value in component_errors.items()}
        if isinstance(component_errors, dict)
        else {}
    )
    incomplete_components = last_attempt.get("incompleteComponents")
    return read.model_copy(
        update={
            "capital_mode": mode,
            "user_abstraction": normalize_user_abstraction(
                last_reconciliation.get("userAbstraction")
                or last_reconciliation.get("userAbstractionRaw")
            ),
            "tradable_equity_usd": live_tradable_equity_usd(account, settings=settings),
            "perp_equity_usd": live_perp_equity_usd(account),
            "spot_usdc_balance_usd": live_spot_balance_usd(account),
            "spot_usdc_available_usd": live_spot_available_usd(account),
            "capital_balances": live_capital_balance_rows(account, settings=settings),
            "reconciliation_status": live_reconciliation_status(account),
            "reconciliation_attempted_at": parse_reconciliation_datetime(
                last_attempt.get("attemptedAt")
            ),
            "incomplete_reconciliation_components": (
                [str(value) for value in incomplete_components]
                if isinstance(incomplete_components, list)
                else []
            ),
            "reconciliation_errors": reconciliation_errors,
        }
    )


def parse_reconciliation_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def live_capital_balance_rows(
    account: TradingAccount,
    *,
    settings: Settings,
) -> list[TradingCapitalBalanceRead]:
    last_reconciliation = account_last_reconciliation(account)
    last_attempt = account_last_reconciliation_attempt(account)
    incomplete_components = {
        str(value) for value in last_attempt.get("incompleteComponents", []) if value is not None
    }
    component_errors = last_attempt.get("componentErrors")
    errors = component_errors if isinstance(component_errors, dict) else {}
    mode = live_capital_mode(settings)
    if mode == "unified":
        return [
            TradingCapitalBalanceRead(
                key="unified",
                label="Unified USDC",
                equity_usd=live_unified_equity_usd(account),
                available_usd=live_unified_available_usd(account),
                tradable=True,
                stale="spot" in incomplete_components,
                error=str(errors.get("spot")) if errors.get("spot") else None,
            )
        ]

    rows: list[TradingCapitalBalanceRead] = []
    states = last_reconciliation.get("perpStates")
    if isinstance(states, list):
        for state in states:
            if not isinstance(state, dict):
                continue
            key = str(state.get("dex") or "default")
            equity = decimal_or_none(state.get("accountValue")) or Decimal("0")
            available = decimal_or_none(state.get("withdrawable"))
            rows.append(
                TradingCapitalBalanceRead(
                    key=key,
                    label="Default perps" if key == "default" else f"{key} perps",
                    equity_usd=equity,
                    available_usd=available,
                    tradable=True,
                    stale=bool(state.get("stale")),
                    error=str(state.get("error")) if state.get("error") else None,
                )
            )
    spot_balance = live_spot_balance_usd(account)
    spot_available = live_spot_available_usd(account)
    if spot_balance > Decimal("0") or spot_available > Decimal("0"):
        rows.append(
            TradingCapitalBalanceRead(
                key="spot",
                label="Spot USDC",
                equity_usd=spot_balance,
                available_usd=spot_available,
                tradable=False,
                stale="spot" in incomplete_components,
                error=str(errors.get("spot")) if errors.get("spot") else None,
            )
        )
    return rows


LIVE_OPEN_ACTIONS = {"open", "add", "flip_open"}


def datetime_to_ms(value: datetime) -> int:
    resolved = value
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=UTC)
    return int(resolved.timestamp() * 1000)


def live_entry_delay_ms(*, source_timestamp_ms: int | None, filled_at: datetime) -> int | None:
    if source_timestamp_ms is None or source_timestamp_ms <= 0:
        return None
    return max(datetime_to_ms(filled_at) - source_timestamp_ms, 0)


async def load_live_position_entry_execution_delays(
    session: AsyncSession,
    positions: list[TradingPosition],
) -> dict[UUID, int]:
    live_positions = [position for position in positions if position.account_type == "live"]
    if not live_positions:
        return {}

    account_keys = sorted({position.account_key for position in live_positions})
    coins = sorted({position.coin for position in live_positions})
    source_result = await session.execute(
        select(
            TradingFill.account_key,
            TradingFill.source_wallet,
            TradingFill.coin,
            TradingFill.side,
            TradingFill.filled_at,
            WalletFill.timestamp_ms.label("source_timestamp_ms"),
        )
        .join(
            WalletFill,
            (func.lower(WalletFill.wallet_address) == func.lower(TradingFill.source_wallet))
            & (WalletFill.external_fill_id == TradingFill.source_fill_id),
        )
        .where(
            TradingFill.account_type == "live",
            TradingFill.account_key.in_(account_keys),
            TradingFill.coin.in_(coins),
            TradingFill.action.in_(LIVE_OPEN_ACTIONS),
            TradingFill.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
        .order_by(TradingFill.filled_at.asc(), TradingFill.created_at.asc())
    )

    by_source: dict[tuple[str, str, str, str], list[tuple[datetime, int]]] = {}
    by_market: dict[tuple[str, str, str], list[tuple[datetime, int]]] = {}
    for row in source_result.all():
        delay_ms = live_entry_delay_ms(
            source_timestamp_ms=int(row.source_timestamp_ms)
            if row.source_timestamp_ms is not None
            else None,
            filled_at=row.filled_at,
        )
        if delay_ms is None:
            continue
        source_key = (
            str(row.account_key),
            str(row.source_wallet).lower(),
            str(row.coin),
            str(row.side),
        )
        market_key = (str(row.account_key), str(row.coin), str(row.side))
        by_source.setdefault(source_key, []).append((row.filled_at, delay_ms))
        by_market.setdefault(market_key, []).append((row.filled_at, delay_ms))

    source_position_delays: dict[tuple[str, str, str, str], int] = {}
    for position in live_positions:
        if position.source_wallet == LIVE_EXCHANGE_SOURCE:
            continue
        source_key = (
            position.account_key,
            position.source_wallet.lower(),
            position.coin,
            position.side,
        )
        delay_ms = matching_live_entry_delay(
            by_source.get(source_key, []),
            opened_at=position.opened_at,
        )
        if delay_ms is not None:
            source_position_delays[source_key] = delay_ms

    delays: dict[UUID, int] = {}
    for position in live_positions:
        if position.source_wallet == LIVE_EXCHANGE_SOURCE:
            matching_source_delays = [
                delay_ms
                for (
                    account_key,
                    _source_wallet,
                    coin,
                    side,
                ), delay_ms in source_position_delays.items()
                if account_key == position.account_key
                and coin == position.coin
                and side == position.side
            ]
            if matching_source_delays:
                delays[position.id] = min(matching_source_delays)
                continue
            market_key = (position.account_key, position.coin, position.side)
            delay_ms = matching_live_entry_delay(
                by_market.get(market_key, []),
                opened_at=position.opened_at,
            )
        else:
            source_key = (
                position.account_key,
                position.source_wallet.lower(),
                position.coin,
                position.side,
            )
            delay_ms = source_position_delays.get(source_key)
        if delay_ms is not None:
            delays[position.id] = delay_ms
    return delays


async def load_live_position_fill_metrics(
    session: AsyncSession,
    positions: list[TradingPosition],
) -> dict[UUID, tuple[int, int, Decimal]]:
    live_positions = [position for position in positions if position.account_type == "live"]
    if not live_positions:
        return {}
    position_ids = [position.id for position in live_positions]
    result = await session.execute(
        select(
            TradingPosition.id,
            func.count(TradingFill.id)
            .filter(TradingFill.action.in_(POSITION_ADD_FILL_ACTIONS))
            .label("add_fill_count"),
            func.count(TradingFill.id)
            .filter(TradingFill.action.in_(POSITION_CLOSE_FILL_ACTIONS))
            .label("close_fill_count"),
            func.coalesce(
                func.sum(TradingFill.realized_pnl_usd).filter(
                    TradingFill.action.in_(POSITION_CLOSE_FILL_ACTIONS)
                ),
                Decimal("0"),
            ).label("realized_pnl_usd"),
        )
        .outerjoin(
            TradingFill,
            and_(
                TradingFill.account_key == TradingPosition.account_key,
                TradingFill.account_type == "live",
                TradingFill.coin == TradingPosition.coin,
                TradingFill.side == TradingPosition.side,
                TradingFill.filled_at >= TradingPosition.opened_at,
                or_(
                    TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
                    TradingFill.source_wallet == TradingPosition.source_wallet,
                ),
            ),
        )
        .where(TradingPosition.id.in_(position_ids))
        .group_by(TradingPosition.id)
    )
    return {
        row.id: (
            int(row.add_fill_count or 0),
            int(row.close_fill_count or 0),
            row.realized_pnl_usd or Decimal("0"),
        )
        for row in result.all()
    }


def matching_live_entry_delay(
    entries: list[tuple[datetime, int]],
    *,
    opened_at: datetime,
) -> int | None:
    if not entries:
        return None
    previous_entries = [entry for entry in entries if entry[0] <= opened_at]
    if previous_entries:
        return previous_entries[-1][1]
    return entries[0][1]


def trading_position_read(
    position: TradingPosition,
    *,
    entry_execution_delay_ms: int | None = None,
    fill_metrics: tuple[int, int, Decimal] | None = None,
) -> TradingPositionRead:
    add_fill_count, close_fill_count, realized_pnl_usd = (
        fill_metrics if fill_metrics is not None else (0, 0, position.realized_pnl_usd)
    )
    read = TradingPositionRead.model_validate(position)
    if position.account_type != "live":
        return read.model_copy(
            update={
                "realized_pnl_usd": realized_pnl_usd,
                "add_fill_count": add_fill_count,
                "close_fill_count": close_fill_count,
            }
        )
    return read.model_copy(
        update={
            "current_notional_usd": live_position_current_notional(position),
            "mark_price": live_position_mark_price(position),
            "unrealized_pnl_usd": live_position_unrealized_pnl(position),
            "unrealized_pnl_pct": live_position_unrealized_pnl_pct(position),
            "realized_pnl_usd": realized_pnl_usd,
            "price_updated_at": position.last_reconciled_at,
            "entry_execution_delay_ms": entry_execution_delay_ms,
            "add_fill_count": add_fill_count,
            "close_fill_count": close_fill_count,
        }
    )


@router.get("/accounts", response_model=TradingAccountsResponse)
async def list_trading_accounts_route(
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountsResponse:
    accounts = await list_trading_accounts(session)
    position_result = await session.scalars(
        select(TradingPosition)
        .where(TradingPosition.account_type == "live")
        .order_by(
            TradingPosition.account_key.asc(),
            TradingPosition.source_wallet.asc(),
            TradingPosition.coin.asc(),
        )
    )
    fill_result = await session.scalars(
        select(TradingFill)
        .where(TradingFill.account_type == "live")
        .order_by(TradingFill.filled_at.desc(), TradingFill.created_at.desc())
        .limit(TRADING_ACTIVITY_LIMIT)
    )
    order_result = await session.scalars(
        select(TradingOrder)
        .where(
            TradingOrder.account_type == "live",
            or_(
                TradingOrder.raw_payload["hiddenFromActivity"].as_boolean().is_not(true()),
                TradingOrder.error == "skip:live_source_fill_too_old",
            ),
        )
        .order_by(TradingOrder.updated_at.desc(), TradingOrder.created_at.desc())
        .limit(TRADING_ACTIVITY_LIMIT)
    )
    closed_trades = await load_live_closed_trades(
        session,
        limit=TRADING_CLOSED_TRADE_LIMIT,
        fill_scan_limit=TRADING_CLOSED_TRADE_FILL_SCAN_LIMIT,
    )
    positions = list(position_result.all())
    entry_execution_delays = await load_live_position_entry_execution_delays(
        session,
        positions,
    )
    position_fill_metrics = await load_live_position_fill_metrics(
        session,
        positions,
    )
    recent_fills = list(fill_result.all())
    recent_orders = list(order_result.all())
    historical_source_result = await session.scalars(
        select(TradingFill.source_wallet).where(TradingFill.account_type == "live").distinct()
    )
    source_wallets = set(
        collect_live_source_wallets(
            positions,
            recent_fills,
            recent_orders,
            closed_trades,
        )
    )
    source_wallets.update(
        source
        for source in (normalize_source_wallet(value) for value in historical_source_result.all())
        if source is not None
    )
    source_metadata = await load_trading_source_metadata(
        session,
        source_wallets=sorted(source_wallets),
        settings=settings,
    )
    return TradingAccountsResponse(
        accounts=[
            enriched_trading_account_read(account, settings=settings) for account in accounts
        ],
        live_trading_enabled=settings.live_trading_enabled,
        risk_limits=live_risk_limits_read(settings),
        positions=[
            trading_position_read(
                position,
                entry_execution_delay_ms=entry_execution_delays.get(position.id),
                fill_metrics=position_fill_metrics.get(
                    position.id,
                    (0, 0, position.realized_pnl_usd),
                ),
            )
            for position in positions
        ],
        recent_fills=[TradingFillRead.model_validate(fill) for fill in recent_fills],
        recent_orders=[TradingOrderRead.model_validate(order) for order in recent_orders],
        closed_trades=[TradingClosedTradeRead.model_validate(trade) for trade in closed_trades],
        source_metadata=source_metadata,
        updated_at=datetime.now(UTC),
    )


@router.post("/accounts/live", response_model=TradingAccountRead)
async def create_live_account_route(
    payload: LiveTradingAccountCreateRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        account = await create_live_trading_account(
            session,
            key=payload.key,
            label=payload.label,
            wallet_address=payload.wallet_address,
            vault_address=payload.vault_address,
            status=payload.status,
            settings=settings,
        )
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
        )
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.delete("/accounts/{account_key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_live_account_route(
    account_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        await delete_live_trading_account(
            session,
            account_key=account_key,
            settings=settings,
            actor=request_audit_actor(request),
        )
        await session.commit()
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch("/accounts/{account_key}/status", response_model=TradingAccountRead)
async def set_trading_account_status_route(
    account_key: str,
    payload: TradingAccountStatusRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        if payload.status == "enabled":
            account = await start_live_trading_account(
                session,
                account_key=account_key,
                settings=settings,
                actor=request_audit_actor(request),
            )
        elif payload.status == "exit_only":
            account = await stop_live_trading_account(
                session,
                account_key=account_key,
                actor=request_audit_actor(request),
            )
        else:
            account = await disable_live_trading_account(
                session,
                account_key=account_key,
                settings=settings,
                actor=request_audit_actor(request),
            )
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/start", response_model=TradingAccountRead)
async def start_live_account_route(
    account_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        account = await start_live_trading_account(
            session,
            account_key=account_key,
            settings=settings,
            actor=request_audit_actor(request),
        )
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/stop", response_model=TradingAccountRead)
async def stop_live_account_route(
    account_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        account = await stop_live_trading_account(
            session,
            account_key=account_key,
            actor=request_audit_actor(request),
        )
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/disable", response_model=TradingAccountRead)
async def disable_live_account_route(
    account_key: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TradingAccountRead:
    try:
        account = await disable_live_trading_account(
            session,
            account_key=account_key,
            settings=settings,
            actor=request_audit_actor(request),
        )
        response = await trading_account_read(session, account, settings)
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/close-all-and-stop", response_model=LiveCloseAllResponse)
async def close_all_and_stop_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveCloseAllResponse:
    try:
        account = await load_live_account(session, account_key=account_key)
        result = await close_all_live_account_positions(
            session,
            account=account,
            settings=settings,
        )
        await session.commit()
        return LiveCloseAllResponse(
            account_key=result.account_key,
            operation_id=result.operation_id,
            operation_status=result.operation_status,
            submitted_orders=result.submitted_orders,
            failed_orders=result.failed_orders,
            status=result.status,
            updated_at=datetime.now(UTC),
        )
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/accounts/{account_key}/reconcile", response_model=LiveReconciliationResponse)
async def reconcile_live_account_route(
    account_key: str,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    lookback_minutes: Annotated[int | None, Query(ge=1, le=10080)] = None,
) -> LiveReconciliationResponse:
    try:
        account = await load_live_account(session, account_key=account_key)
        await session.commit()
        result = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            lookback_minutes=lookback_minutes,
        )
        await session.commit()
        return LiveReconciliationResponse.model_validate(result)
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/positions/{position_id}/close", response_model=LiveOrderSubmitResponse)
async def close_live_position_route(
    position_id: UUID,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveOrderSubmitResponse:
    try:
        result = await close_live_account_position(
            session,
            position_id=position_id,
            settings=settings,
        )
        response = LiveOrderSubmitResponse(
            order=TradingOrderRead.model_validate(result.order),
            submitted=result.submitted,
            updated_at=datetime.now(UTC),
        )
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.post("/testnet/orders", response_model=LiveOrderSubmitResponse)
async def submit_testnet_live_order_route(
    payload: TestnetLiveOrderRequest,
    session: Annotated[AsyncSession, Depends(db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LiveOrderSubmitResponse:
    if settings.hyperliquid_network != "testnet":
        raise HTTPException(
            status_code=403,
            detail="Manual test orders are only available when hyperliquid_network is testnet.",
        )

    try:
        account = await load_live_account(session, account_key=payload.account_key)
        await session.commit()
        if account.network != "testnet":
            raise LiveTradingServiceError("Live account is not a testnet account.")
        intent = build_testnet_live_trade_intent(
            account=account,
            coin=payload.coin,
            side=payload.side,
            notional_usd=payload.notional_usd,
            limit_price=payload.limit_price,
            leverage=payload.leverage,
            margin_mode=payload.margin_mode,
            reduce_only=payload.reduce_only,
        )
        result = await submit_live_trade_intent(
            session,
            account=account,
            intent=intent,
            settings=settings,
        )
        response = LiveOrderSubmitResponse(
            order=TradingOrderRead.model_validate(result.order),
            submitted=result.submitted,
            updated_at=datetime.now(UTC),
        )
        await session.commit()
        return response
    except LiveTradingServiceError as exc:
        await session.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
