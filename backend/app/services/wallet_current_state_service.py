import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SourceTrade, WalletFill, WalletPosition
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.wallet_stats import (
    WalletCurrentStateStats,
    WalletPerpPositionStats,
    WalletSpotBalanceStats,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


@dataclass(frozen=True)
class WalletPerpClearinghouseState:
    dex: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class WalletPerpStateSummary:
    state_time_ms: int | None
    account_value_usd: Decimal
    withdrawable_usd: Decimal
    total_position_notional_usd: Decimal
    total_margin_used_usd: Decimal
    total_unrealized_pnl_usd: Decimal
    positions: list[WalletPerpPositionStats]
    raw_positions: list[dict[str, Any]]


@dataclass
class OpenPositionTradeStats:
    opened_at_ms: int | None = None
    realized_pnl_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    net_pnl_usd: Decimal = ZERO
    add_fill_count: int = 0
    reduce_fill_count: int = 0
    liquidation_fill_count: int = 0


async def get_wallet_current_state(
    session: AsyncSession,
    *,
    address: str,
    client: HyperliquidClient | None = None,
) -> WalletCurrentStateStats:
    hyperliquid_client = client or HyperliquidClient()
    known_dexes = await load_known_wallet_perp_dexes(session, address=address)
    perp_states, perp_errors = await load_wallet_perp_clearinghouse_states(
        client=hyperliquid_client,
        address=address,
        dexes=known_dexes,
    )
    all_dexes, dex_list_error = await load_all_perp_dex_names(client=hyperliquid_client)
    if dex_list_error is not None:
        perp_errors.append(dex_list_error)
    known_dex_set = set(known_dexes)
    missing_dexes = [
        dex
        for dex in all_dexes
        if dex not in known_dex_set
    ]
    if missing_dexes:
        fallback_states, fallback_errors = await load_wallet_perp_clearinghouse_states(
            client=hyperliquid_client,
            address=address,
            dexes=missing_dexes,
            include_default=False,
        )
        perp_states.extend(fallback_states)
        perp_errors.extend(fallback_errors)
    if not perp_states:
        return empty_current_state(error="; ".join(perp_errors) or "Perp state unavailable.")

    try:
        spot_state = await hyperliquid_client.spot_clearinghouse_state(user=address)
    except Exception as exc:
        logger.warning("wallet spot state fetch failed wallet=%s error=%s", address, exc)
        spot_state = {}

    perp_summary = summarize_perp_clearinghouse_states(perp_states)
    if perp_summary.positions:
        annotate_open_position_source_stats(
            perp_summary.positions,
            await load_open_source_trade_position_stats(session, address=address),
        )
    spot_balances = parse_spot_balances(spot_state)
    if not perp_errors:
        await sync_wallet_positions(
            session,
            wallet_address=address,
            positions=perp_summary.positions,
            raw_positions=perp_summary.raw_positions,
        )
    else:
        logger.warning(
            "wallet position sync skipped after partial perp state wallet=%s errors=%s",
            address,
            "; ".join(perp_errors),
        )

    return WalletCurrentStateStats(
        state_time_ms=perp_summary.state_time_ms,
        perp_equity_usd=perp_summary.account_value_usd,
        account_value_usd=perp_summary.account_value_usd,
        withdrawable_usd=perp_summary.withdrawable_usd,
        total_position_notional_usd=perp_summary.total_position_notional_usd,
        total_margin_used_usd=perp_summary.total_margin_used_usd,
        total_unrealized_pnl_usd=perp_summary.total_unrealized_pnl_usd,
        open_position_count=len(perp_summary.positions),
        spot_balance_count=len(spot_balances),
        spot_entry_notional_usd=sum_decimal(
            balance.entry_notional_usd
            for balance in spot_balances
            if balance.coin.upper() != "USDC"
        ),
        spot_usdc_balance=sum_decimal(
            balance.total for balance in spot_balances if balance.coin.upper() == "USDC"
        ),
        positions=perp_summary.positions,
        spot_balances=spot_balances,
        error="; ".join(perp_errors) or None,
    )


async def load_known_wallet_perp_dexes(
    session: AsyncSession,
    *,
    address: str,
) -> tuple[str, ...]:
    dexes_by_address = await load_known_wallet_perp_dexes_for_addresses(
        session,
        addresses=[address],
    )
    return dexes_by_address.get(address.lower(), ())


async def load_known_wallet_perp_dexes_for_addresses(
    session: AsyncSession,
    *,
    addresses: list[str],
) -> dict[str, tuple[str, ...]]:
    normalized_addresses = sorted({address.lower() for address in addresses if address})
    if not normalized_addresses:
        return {}

    dexes_by_address: dict[str, set[str]] = {
        address: set() for address in normalized_addresses
    }
    fill_result = await session.execute(
        select(WalletFill.wallet_address, WalletFill.coin)
        .where(
            WalletFill.wallet_address.in_(normalized_addresses),
            WalletFill.coin.contains(":"),
        )
        .distinct()
    )
    position_result = await session.execute(
        select(WalletPosition.wallet_address, WalletPosition.coin)
        .where(
            WalletPosition.wallet_address.in_(normalized_addresses),
            WalletPosition.coin.contains(":"),
        )
        .distinct()
    )
    for row in [*fill_result.mappings().all(), *position_result.mappings().all()]:
        wallet_address = str(row["wallet_address"]).lower()
        dex = dex_from_coin(row["coin"])
        if dex and wallet_address in dexes_by_address:
            dexes_by_address[wallet_address].add(dex)

    return {
        address: tuple(sorted(dexes))
        for address, dexes in dexes_by_address.items()
    }


async def load_wallet_perp_clearinghouse_states(
    *,
    client: HyperliquidClient,
    address: str,
    dexes: tuple[str, ...] | list[str] = (),
    include_default: bool = True,
) -> tuple[list[WalletPerpClearinghouseState], list[str]]:
    resolved_dexes = normalize_perp_dexes(dexes, include_default=include_default)
    results = await asyncio.gather(
        *(
            fetch_wallet_perp_clearinghouse_state(
                client=client,
                address=address,
                dex=dex,
            )
            for dex in resolved_dexes
        )
    )
    states: list[WalletPerpClearinghouseState] = []
    errors: list[str] = []
    for state, error in results:
        if state is not None:
            states.append(state)
        if error is not None:
            errors.append(error)
    return states, errors


async def load_all_perp_dex_names(
    *,
    client: HyperliquidClient,
) -> tuple[tuple[str, ...], str | None]:
    try:
        payload = await client.perp_dexs()
    except Exception as exc:
        logger.warning("wallet perp dex list fetch failed error=%s", exc)
        return (), f"perpDexs: {str(exc) or exc.__class__.__name__}"
    return perp_dex_names_from_payload(payload), None


async def fetch_wallet_perp_clearinghouse_state(
    *,
    client: HyperliquidClient,
    address: str,
    dex: str,
) -> tuple[WalletPerpClearinghouseState | None, str | None]:
    try:
        payload = await client.clearinghouse_state(user=address, dex=dex or None)
    except Exception as exc:
        logger.warning(
            "wallet perp state fetch failed wallet=%s dex=%s error=%s",
            address,
            dex or "default",
            exc,
        )
        return None, f"{dex or 'default'}: {str(exc) or exc.__class__.__name__}"
    return WalletPerpClearinghouseState(dex=dex, payload=payload), None


def summarize_perp_clearinghouse_states(
    states: list[WalletPerpClearinghouseState],
) -> WalletPerpStateSummary:
    perp_equity = ZERO
    withdrawable = ZERO
    total_position_notional = ZERO
    total_margin_used = ZERO
    state_time_ms: int | None = None
    raw_positions: list[dict[str, Any]] = []

    for state in states:
        payload = state.payload
        margin_summary = object_or_empty(payload.get("marginSummary"))
        perp_equity += decimal_value(margin_summary.get("accountValue"))
        withdrawable += decimal_value(payload.get("withdrawable"))
        total_position_notional += decimal_value(margin_summary.get("totalNtlPos"))
        total_margin_used += decimal_value(margin_summary.get("totalMarginUsed"))
        raw_positions.extend(
            normalized_asset_positions(payload.get("assetPositions"), dex=state.dex)
        )
        payload_time_ms = int_or_none(payload.get("time"))
        if payload_time_ms is not None:
            state_time_ms = (
                payload_time_ms if state_time_ms is None else max(state_time_ms, payload_time_ms)
            )

    positions = parse_perp_positions({"assetPositions": raw_positions})
    if total_position_notional == ZERO:
        total_position_notional = sum_decimal(
            position.position_value_usd for position in positions
        )
    if total_margin_used == ZERO:
        total_margin_used = sum_decimal(position.margin_used_usd for position in positions)

    return WalletPerpStateSummary(
        state_time_ms=state_time_ms,
        account_value_usd=perp_equity,
        withdrawable_usd=withdrawable,
        total_position_notional_usd=total_position_notional,
        total_margin_used_usd=total_margin_used,
        total_unrealized_pnl_usd=sum_decimal(
            position.unrealized_pnl_usd for position in positions
        ),
        positions=positions,
        raw_positions=raw_positions,
    )


def normalize_perp_dexes(
    dexes: tuple[str, ...] | list[str],
    *,
    include_default: bool = True,
) -> list[str]:
    cleaned = sorted({str(dex).strip() for dex in dexes if str(dex).strip()})
    return ["", *cleaned] if include_default else cleaned


def perp_dex_names_from_payload(payload: list[Any]) -> tuple[str, ...]:
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return tuple(unique_strings(names))


def unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


def parse_perp_positions(
    payload: dict[str, Any],
    *,
    dex: str = "",
) -> list[WalletPerpPositionStats]:
    raw_positions = normalized_asset_positions(payload.get("assetPositions"), dex=dex)

    positions: list[WalletPerpPositionStats] = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        raw_position = item.get("position")
        if not isinstance(raw_position, dict):
            continue
        size = decimal_value(raw_position.get("szi"))
        if size == ZERO:
            continue

        leverage = object_or_empty(raw_position.get("leverage"))
        positions.append(
            WalletPerpPositionStats(
                coin=str(raw_position.get("coin") or ""),
                side="long" if size > ZERO else "short",
                opened_at_ms=None,
                size=abs(size),
                entry_price=decimal_or_none(raw_position.get("entryPx")),
                position_value_usd=decimal_or_none(raw_position.get("positionValue")),
                unrealized_pnl_usd=decimal_or_none(raw_position.get("unrealizedPnl")),
                return_on_equity=decimal_or_none(raw_position.get("returnOnEquity")),
                margin_used_usd=decimal_or_none(raw_position.get("marginUsed")),
                liquidation_price=decimal_or_none(raw_position.get("liquidationPx")),
                leverage_type=string_or_none(leverage.get("type")),
                leverage_value=int_or_none(leverage.get("value")),
            )
        )

    return sorted(
        positions,
        key=lambda position: position.position_value_usd or ZERO,
        reverse=True,
    )


async def load_open_source_trade_position_stats(
    session: AsyncSession,
    *,
    address: str,
) -> dict[tuple[str, str], OpenPositionTradeStats]:
    result = await session.execute(
        select(
            SourceTrade.coin,
            SourceTrade.side,
            SourceTrade.opened_at_ms,
            SourceTrade.realized_pnl_usd,
            SourceTrade.fee_usd,
            SourceTrade.net_pnl_usd,
            SourceTrade.entry_fill_count,
            SourceTrade.close_fill_count,
            SourceTrade.liquidation_fill_count,
        ).where(
            SourceTrade.wallet_address == address.lower(),
            SourceTrade.status == "open",
        )
    )
    stats_by_position: dict[tuple[str, str], OpenPositionTradeStats] = {}
    for row in result.mappings().all():
        coin = str(row["coin"] or "")
        side = str(row["side"] or "")
        key = (coin, side)
        stats = stats_by_position.setdefault(key, OpenPositionTradeStats())
        opened_at_ms = int(row["opened_at_ms"])
        if stats.opened_at_ms is None or opened_at_ms < stats.opened_at_ms:
            stats.opened_at_ms = opened_at_ms
        stats.realized_pnl_usd += decimal_value(row["realized_pnl_usd"])
        stats.fee_usd += decimal_value(row["fee_usd"])
        stats.net_pnl_usd += decimal_value(row["net_pnl_usd"])
        stats.add_fill_count += max(int(row["entry_fill_count"] or 0) - 1, 0)
        stats.reduce_fill_count += int(row["close_fill_count"] or 0)
        stats.liquidation_fill_count += int(row["liquidation_fill_count"] or 0)
    return stats_by_position


def annotate_open_position_source_stats(
    positions: list[WalletPerpPositionStats],
    stats_by_position: dict[tuple[str, str], OpenPositionTradeStats],
) -> None:
    if not stats_by_position:
        return

    for position in positions:
        stats = open_trade_stats_for_position(position, stats_by_position)
        if stats is None:
            continue
        position.opened_at_ms = stats.opened_at_ms
        position.realized_pnl_usd = stats.realized_pnl_usd
        position.net_pnl_usd = stats.net_pnl_usd
        position.add_fill_count = stats.add_fill_count
        position.reduce_fill_count = stats.reduce_fill_count
        position.liquidation_fill_count = stats.liquidation_fill_count


def open_trade_stats_for_position(
    position: WalletPerpPositionStats,
    stats_by_position: dict[tuple[str, str], OpenPositionTradeStats],
) -> OpenPositionTradeStats | None:
    exact = stats_by_position.get((position.coin, position.side))
    if exact is not None:
        return exact

    for (coin, side), stats in stats_by_position.items():
        if side == position.side and coins_match(coin, position.coin):
            return stats
    return None


def normalized_asset_positions(raw_positions: Any, *, dex: str = "") -> list[dict[str, Any]]:
    if not isinstance(raw_positions, list):
        return []

    positions: list[dict[str, Any]] = []
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        raw_position = item.get("position")
        if not isinstance(raw_position, dict):
            positions.append(item)
            continue

        coin = str(raw_position.get("coin") or "")
        normalized_coin = coin_with_dex(coin=coin, dex=dex)
        if normalized_coin == coin and not dex:
            positions.append(item)
            continue

        normalized_position = {**raw_position, "coin": normalized_coin}
        normalized_item = {**item, "position": normalized_position}
        if dex:
            normalized_item["dex"] = dex
        positions.append(normalized_item)
    return positions


def coin_with_dex(*, coin: str, dex: str) -> str:
    resolved_coin = str(coin or "").strip()
    resolved_dex = str(dex or "").strip()
    if not resolved_coin or not resolved_dex or ":" in resolved_coin:
        return resolved_coin
    return f"{resolved_dex}:{resolved_coin}"


def parse_spot_balances(payload: dict[str, Any]) -> list[WalletSpotBalanceStats]:
    raw_balances = payload.get("balances")
    if not isinstance(raw_balances, list):
        return []

    balances: list[WalletSpotBalanceStats] = []
    for item in raw_balances:
        if not isinstance(item, dict):
            continue
        total = decimal_value(item.get("total"))
        hold = decimal_value(item.get("hold"))
        entry_notional = decimal_value(item.get("entryNtl"))
        if total == ZERO and hold == ZERO and entry_notional == ZERO:
            continue
        balances.append(
            WalletSpotBalanceStats(
                coin=str(item.get("coin") or ""),
                token=int_or_none(item.get("token")),
                total=total,
                hold=hold,
                entry_notional_usd=entry_notional,
            )
        )

    return sorted(
        balances,
        key=lambda balance: (
            balance.coin.upper() != "USDC",
            balance.entry_notional_usd,
            abs(balance.total),
        ),
        reverse=True,
    )


async def sync_wallet_positions(
    session: AsyncSession,
    *,
    wallet_address: str,
    positions: list[WalletPerpPositionStats],
    raw_positions: Any,
) -> None:
    active_coins = {position.coin for position in positions}
    now = datetime.now(UTC)

    if active_coins:
        await session.execute(
            delete(WalletPosition).where(
                WalletPosition.wallet_address == wallet_address,
                WalletPosition.coin.not_in(active_coins),
            )
        )
    else:
        await session.execute(
            delete(WalletPosition).where(WalletPosition.wallet_address == wallet_address)
        )

    records = [
        {
            "wallet_address": wallet_address,
            "coin": position.coin,
            "side": position.side,
            "size": position.size,
            "entry_price": position.entry_price,
            "position_value_usd": position.position_value_usd,
            "unrealized_pnl_usd": position.unrealized_pnl_usd,
            "liquidation_price": position.liquidation_price,
            "raw_json": raw_position_for_coin(raw_positions, position.coin),
            "updated_at": now,
        }
        for position in positions
    ]
    if records:
        stmt = insert(WalletPosition).values(records)
        update_values = {
            column: getattr(stmt.excluded, column)
            for column in (
                "side",
                "size",
                "entry_price",
                "position_value_usd",
                "unrealized_pnl_usd",
                "liquidation_price",
                "raw_json",
                "updated_at",
            )
        }
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["wallet_address", "coin"],
                set_=update_values,
            )
        )

    await session.commit()


def raw_position_for_coin(raw_positions: Any, coin: str) -> dict[str, Any] | None:
    if not isinstance(raw_positions, list):
        return None
    for item in raw_positions:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        if not isinstance(position, dict):
            continue
        position_coin = str(position.get("coin") or "")
        if coins_match(position_coin, coin):
            return item
    return None


def coins_match(position_coin: str, requested_coin: str) -> bool:
    if position_coin == requested_coin:
        return True
    if ":" in requested_coin:
        return requested_coin.rsplit(":", maxsplit=1)[-1] == position_coin
    if ":" in position_coin:
        return position_coin.rsplit(":", maxsplit=1)[-1] == requested_coin
    return False


def dex_from_coin(value: Any) -> str:
    coin = str(value or "").strip()
    if ":" not in coin:
        return ""
    return coin.split(":", maxsplit=1)[0].strip()


def empty_current_state(*, error: str | None = None) -> WalletCurrentStateStats:
    return WalletCurrentStateStats(
        state_time_ms=None,
        perp_equity_usd=ZERO,
        account_value_usd=ZERO,
        withdrawable_usd=ZERO,
        total_position_notional_usd=ZERO,
        total_margin_used_usd=ZERO,
        total_unrealized_pnl_usd=ZERO,
        open_position_count=0,
        spot_balance_count=0,
        spot_entry_notional_usd=ZERO,
        spot_usdc_balance=ZERO,
        positions=[],
        spot_balances=[],
        error=error,
    )


def object_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def decimal_value(value: Any) -> Decimal:
    parsed = decimal_or_none(value)
    return parsed if parsed is not None else ZERO


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def sum_decimal(values: Any) -> Decimal:
    total = ZERO
    for value in values:
        if value is not None:
            total += value
    return total


def int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
