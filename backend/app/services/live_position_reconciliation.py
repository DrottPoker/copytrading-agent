"""Live positions: the exchange's view, each source's attributed share, and how fills move them."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LiveCopyFillState,
    TradingAccount,
    TradingOrder,
    TradingPosition,
)
from app.services.live_exchange_fetch import (
    LivePerpSnapshot,
    LivePerpState,
    live_dex_from_coin,
    normalize_live_perp_snapshot,
    normalized_live_asset_positions,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    merge_raw_payload,
    parse_live_position,
)
from app.services.live_trading_constants import (
    LIVE_EXCHANGE_SOURCE,
    POSITION_EPSILON,
)
from app.services.trading_core import (
    margin_from_notional,
)

ZERO = Decimal("0")


async def load_live_exchange_positions(
    session: AsyncSession,
    *,
    account_key: str,
) -> list[TradingPosition]:
    result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
        .order_by(TradingPosition.coin.asc())
    )
    return list(result.all())


async def load_live_source_position(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
) -> TradingPosition | None:
    return await session.scalar(
        select(TradingPosition).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.coin == coin,
        )
    )


async def apply_live_source_fill_to_position(
    session: AsyncSession,
    *,
    account: TradingAccount,
    order: TradingOrder | None,
    attributed_position: TradingPosition | None,
    parsed_fill: dict[str, Any],
) -> None:
    fee_usd = parsed_fill["fee_usd"]
    realized_pnl_usd = parsed_fill["realized_pnl_usd"]
    account.fee_usd += fee_usd
    account.realized_pnl_usd += realized_pnl_usd
    if order is None:
        if attributed_position is not None:
            await apply_live_close_fill_to_position(
                session,
                position=attributed_position,
                parsed_fill=parsed_fill,
            )
        return
    if order.source_wallet == LIVE_EXCHANGE_SOURCE:
        return

    position = await load_live_source_position(
        session,
        account_key=account.key,
        source_wallet=order.source_wallet,
        coin=order.coin,
    )

    if order.action in {"open", "add", "flip_open"}:
        await apply_live_open_fill_to_position(
            session,
            order=order,
            position=position,
            parsed_fill=parsed_fill,
        )
        return

    if position is None or position.side != order.side:
        return
    await apply_live_close_fill_to_position(
        session,
        position=position,
        parsed_fill=parsed_fill,
    )


async def apply_live_open_fill_to_position(
    session: AsyncSession,
    *,
    order: TradingOrder,
    position: TradingPosition | None,
    parsed_fill: dict[str, Any],
) -> None:
    fill_size = parsed_fill["size"]
    fill_notional = parsed_fill["notional_usd"]
    margin_delta = order_margin_delta(order, fill_notional=fill_notional)
    fee_usd = parsed_fill["fee_usd"]
    filled_at = parsed_fill["filled_at"]
    lifecycle_order = await load_live_copy_position_lifecycle_order(
        session,
        order=order,
    )

    if position is None:
        session.add(
            TradingPosition(
                account_key=order.account_key,
                account_type="live",
                source_wallet=order.source_wallet,
                coin=order.coin,
                side=order.side,
                size=fill_size,
                entry_price=parsed_fill["price"],
                notional_usd=fill_notional,
                leverage=order.leverage or Decimal("1"),
                margin_mode=order.margin_mode,
                margin_usd=margin_delta,
                realized_pnl_usd=ZERO,
                fee_usd=fee_usd,
                raw_payload={
                    "source": "live_fill",
                    **(
                        {"sourceLifecycleOrder": lifecycle_order["raw"]}
                        if lifecycle_order is not None
                        else {}
                    ),
                },
                **(lifecycle_order["columns"] if lifecycle_order is not None else {}),
                opened_at=filled_at,
                last_reconciled_at=filled_at,
            )
        )
        return

    if position.side != order.side:
        return
    previous_size = position.size
    next_size = previous_size + fill_size
    if next_size <= ZERO:
        return
    position.entry_price = (
        (position.entry_price * previous_size) + (parsed_fill["price"] * fill_size)
    ) / next_size
    position.size = next_size
    position.notional_usd += fill_notional
    position.leverage = order.leverage or Decimal("1")
    position.margin_mode = order.margin_mode
    position.margin_usd = margin_from_notional(
        position.notional_usd,
        position.leverage,
    )
    position.fee_usd += fee_usd
    position.last_reconciled_at = filled_at


async def load_live_copy_position_lifecycle_order(
    session: AsyncSession,
    *,
    order: TradingOrder,
) -> dict[str, Any] | None:
    state = await session.scalar(
        select(LiveCopyFillState).where(
            LiveCopyFillState.account_key == order.account_key,
            LiveCopyFillState.source_wallet == order.source_wallet,
            LiveCopyFillState.source_fill_id == order.source_fill_id,
            LiveCopyFillState.sequence_index == order.sequence_index,
        )
    )
    if state is None:
        return None
    numeric_fill_id = state.source_order_fill_id_numeric
    raw = {
        "timestampMs": int(state.source_timestamp_ms),
        "coin": state.coin,
        "directionRank": int(state.source_order_direction_rank),
        "positionRank": str(state.source_order_position),
        "fillIdNumeric": str(numeric_fill_id) if numeric_fill_id is not None else None,
        "fillId": state.source_fill_id,
    }
    return {
        "raw": raw,
        "columns": {
            "source_lifecycle_timestamp_ms": int(state.source_timestamp_ms),
            "source_lifecycle_direction_rank": int(state.source_order_direction_rank),
            "source_lifecycle_position": state.source_order_position,
            "source_lifecycle_fill_id_numeric": numeric_fill_id,
            "source_lifecycle_fill_id": state.source_fill_id,
        },
    }


async def apply_live_close_fill_to_position(
    session: AsyncSession,
    *,
    position: TradingPosition,
    parsed_fill: dict[str, Any],
) -> None:
    fill_size = min(parsed_fill["size"], position.size)
    if fill_size <= ZERO:
        return
    close_ratio = min(fill_size / position.size, Decimal("1"))
    position.size -= fill_size
    position.notional_usd = max(position.notional_usd * (Decimal("1") - close_ratio), ZERO)
    position.margin_usd = max(position.margin_usd * (Decimal("1") - close_ratio), ZERO)
    position.realized_pnl_usd += parsed_fill["realized_pnl_usd"]
    position.fee_usd += parsed_fill["fee_usd"]
    position.last_reconciled_at = parsed_fill["filled_at"]
    if position.size <= POSITION_EPSILON:
        await session.delete(position)


@dataclass(frozen=True)
class LivePositionReconciliationResult:
    open_positions: int
    removed_positions: int
    complete: bool = True
    authoritative_dexes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveSourcePositionSyncResult:
    updated_positions: int
    stale_positions: list[TradingPosition]


def source_unrealized_pnl_from_mark(position: TradingPosition, mark_price: Decimal) -> Decimal:
    if position.side == "short":
        return (position.entry_price - mark_price) * position.size
    return (mark_price - position.entry_price) * position.size


def sync_live_source_positions_from_exchange_positions(
    *,
    source_positions: list[TradingPosition],
    exchange_positions: list[TradingPosition],
    reconciled_at: datetime,
    authoritative_dexes: set[str] | None = None,
) -> LiveSourcePositionSyncResult:
    exchange_by_market = {
        (position.account_key, position.coin, position.side): position
        for position in exchange_positions
        if position.size > POSITION_EPSILON
    }
    source_positions_by_market: dict[tuple[str, str, str], list[TradingPosition]] = {}
    for position in source_positions:
        key = (position.account_key, position.coin, position.side)
        source_positions_by_market.setdefault(key, []).append(position)

    updated = 0
    stale_positions: list[TradingPosition] = []
    for key, positions in source_positions_by_market.items():
        dex = live_dex_from_coin(key[1])
        if authoritative_dexes is not None and dex not in authoritative_dexes:
            continue
        exchange_position = exchange_by_market.get(key)
        if exchange_position is None:
            stale_positions.extend(positions)
            continue

        total_source_size = sum(
            (position.size for position in positions if position.size > ZERO),
            ZERO,
        )
        if total_source_size <= POSITION_EPSILON:
            stale_positions.extend(positions)
            continue
        mark_price = live_position_mark_price(exchange_position)
        size_ratio = min(exchange_position.size / total_source_size, Decimal("1"))

        for position in positions:
            if position.size <= POSITION_EPSILON:
                stale_positions.append(position)
                continue
            if size_ratio < Decimal("1"):
                position.size *= size_ratio
                position.notional_usd *= size_ratio
                position.margin_usd *= size_ratio
            if position.size <= POSITION_EPSILON:
                stale_positions.append(position)
                continue
            position.leverage = exchange_position.leverage
            position.margin_mode = exchange_position.margin_mode
            position.margin_usd = margin_from_notional(
                position.notional_usd,
                position.leverage,
            )
            if mark_price is None or mark_price <= ZERO:
                position.last_reconciled_at = reconciled_at
                updated += 1
                continue

            current_notional = mark_price * position.size
            unrealized_pnl = source_unrealized_pnl_from_mark(position, mark_price)
            return_on_equity = (
                unrealized_pnl / position.margin_usd
                if position.margin_usd > ZERO
                else live_position_unrealized_pnl_pct(exchange_position)
            )
            position.raw_payload = merge_raw_payload(
                position.raw_payload,
                {
                    "position": {
                        "coin": position.coin,
                        "entryPx": str(position.entry_price),
                        "markPx": str(mark_price),
                        "positionValue": str(current_notional),
                        "returnOnEquity": str(return_on_equity)
                        if return_on_equity is not None
                        else None,
                        "szi": str(position.size if position.side == "long" else -position.size),
                        "source": "live_source_reconciliation",
                        "unrealizedPnl": str(unrealized_pnl),
                    },
                    "sourceReconciliation": {
                        "exchangePositionId": str(exchange_position.id),
                        "reconciledAt": reconciled_at.isoformat(),
                    },
                },
            )
            position.last_reconciled_at = reconciled_at
            updated += 1
    return LiveSourcePositionSyncResult(
        updated_positions=updated,
        stale_positions=stale_positions,
    )


async def reconcile_live_positions(
    session: AsyncSession,
    *,
    account: TradingAccount,
    perp_states: LivePerpSnapshot | list[LivePerpState],
    reconciled_at: datetime,
) -> LivePositionReconciliationResult:
    perp_snapshot = normalize_live_perp_snapshot(perp_states)
    snapshots = [
        snapshot
        for perp_state in perp_snapshot.states
        if perp_state.complete
        for payload in normalized_live_asset_positions(
            perp_state.payload.get("assetPositions"),
            dex=perp_state.dex,
        )
        if isinstance(payload, dict)
        for snapshot in [parse_live_position(payload)]
        if snapshot is not None
    ]
    existing_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    existing_positions = list(existing_result.all())
    existing = {position.coin: position for position in existing_positions}
    source_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    source_positions = list(source_result.all())
    authoritative_dexes = {state.dex for state in perp_snapshot.states if state.complete}
    requested_dexes = set(perp_snapshot.requested_dexes)
    exchange_positions: list[TradingPosition] = []
    for snapshot in snapshots:
        position = existing.get(snapshot.coin)
        opened_at = live_exchange_position_opened_at(
            coin=snapshot.coin,
            side=snapshot.side,
            existing_position=position,
            source_positions=source_positions,
            reconciled_at=reconciled_at,
        )
        if position is None:
            position = TradingPosition(
                account_key=account.key,
                account_type="live",
                source_wallet=LIVE_EXCHANGE_SOURCE,
                coin=snapshot.coin,
                side=snapshot.side,
                size=snapshot.size,
                entry_price=snapshot.entry_price,
                notional_usd=snapshot.notional_usd,
                leverage=snapshot.leverage,
                margin_mode=snapshot.margin_mode,
                margin_usd=snapshot.margin_usd,
                realized_pnl_usd=ZERO,
                fee_usd=ZERO,
                raw_payload=snapshot.raw_payload,
                opened_at=opened_at,
                last_reconciled_at=reconciled_at,
            )
            session.add(position)
            exchange_positions.append(position)
            continue
        position.side = snapshot.side
        position.size = snapshot.size
        position.entry_price = snapshot.entry_price
        position.notional_usd = snapshot.notional_usd
        position.leverage = snapshot.leverage
        position.margin_mode = snapshot.margin_mode
        position.margin_usd = snapshot.margin_usd
        position.raw_payload = snapshot.raw_payload
        position.opened_at = opened_at
        position.last_reconciled_at = reconciled_at
        exchange_positions.append(position)
    if perp_snapshot.catalog_complete:
        stored_dexes = {
            live_dex_from_coin(position.coin)
            for position in [*existing_positions, *source_positions]
        }
        authoritative_dexes.update(stored_dexes - requested_dexes)

    active_coins = {snapshot.coin for snapshot in snapshots}
    removed_exchange_positions = 0
    for position in existing_positions:
        if position.coin in active_coins:
            continue
        if live_dex_from_coin(position.coin) not in authoritative_dexes:
            continue
        await session.delete(position)
        removed_exchange_positions += 1

    await session.flush()
    source_sync_result = sync_live_source_positions_from_exchange_positions(
        source_positions=source_positions,
        exchange_positions=exchange_positions,
        reconciled_at=reconciled_at,
        authoritative_dexes=authoritative_dexes,
    )
    for stale_position in source_sync_result.stale_positions:
        await session.delete(stale_position)
    await session.flush()
    open_positions = await session.scalar(
        select(func.count(TradingPosition.id)).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    return LivePositionReconciliationResult(
        open_positions=int(open_positions or 0),
        removed_positions=removed_exchange_positions + len(source_sync_result.stale_positions),
        complete=perp_snapshot.complete,
        authoritative_dexes=tuple(sorted(authoritative_dexes)),
    )


def live_exchange_position_opened_at(
    *,
    coin: str,
    side: str,
    existing_position: TradingPosition | None,
    source_positions: list[TradingPosition],
    reconciled_at: datetime,
) -> datetime:
    source_opened_at = min(
        (
            position.opened_at
            for position in source_positions
            if position.coin == coin and position.side == side
        ),
        default=None,
    )
    if existing_position is None or existing_position.side != side:
        return source_opened_at or reconciled_at
    if source_opened_at is None:
        return existing_position.opened_at
    return min(existing_position.opened_at, source_opened_at)


def live_position_mark_price(position: TradingPosition) -> Decimal | None:
    raw_position = live_position_raw_position(position)
    position_value = (
        decimal_or_none(raw_position.get("positionValue")) if raw_position is not None else None
    )
    position_value = position_value or position.notional_usd
    if position.size <= ZERO or position_value <= ZERO:
        return None
    return position_value / position.size


def live_position_current_notional(position: TradingPosition) -> Decimal:
    raw_position = live_position_raw_position(position)
    if raw_position is None:
        return position.notional_usd
    return decimal_or_none(raw_position.get("positionValue")) or position.notional_usd


def live_position_unrealized_pnl(position: TradingPosition) -> Decimal | None:
    raw_position = live_position_raw_position(position)
    if raw_position is None:
        return None
    return decimal_or_none(raw_position.get("unrealizedPnl"))


def live_position_unrealized_pnl_pct(position: TradingPosition) -> Decimal | None:
    raw_position = live_position_raw_position(position)
    if raw_position is None:
        return None
    return_on_equity = decimal_or_none(raw_position.get("returnOnEquity"))
    if return_on_equity is not None:
        return return_on_equity
    unrealized = live_position_unrealized_pnl(position)
    if unrealized is None or position.margin_usd <= ZERO:
        return None
    return unrealized / position.margin_usd


def live_position_raw_position(position: TradingPosition) -> dict[str, Any] | None:
    payload = position.raw_payload if isinstance(position.raw_payload, dict) else {}
    raw_position = payload.get("position")
    if isinstance(raw_position, dict):
        return raw_position
    return payload if payload else None


def order_margin_delta(order: TradingOrder, *, fill_notional: Decimal) -> Decimal:
    if order.requested_notional_usd <= ZERO or order.margin_usd is None:
        return margin_from_notional(fill_notional, order.leverage or Decimal("1"))
    return order.margin_usd * min(fill_notional / order.requested_notional_usd, Decimal("1"))


def effective_leverage(
    *,
    notional_usd: Decimal,
    margin_usd: Decimal,
    fallback: Decimal,
) -> Decimal:
    if margin_usd <= ZERO:
        return fallback if fallback > ZERO else Decimal("1")
    return notional_usd / margin_usd
