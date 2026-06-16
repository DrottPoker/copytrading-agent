import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WalletPosition
from app.integrations.hyperliquid_client import HyperliquidClient
from app.schemas.wallet_stats import (
    WalletCurrentStateStats,
    WalletPerpPositionStats,
    WalletSpotBalanceStats,
)

logger = logging.getLogger(__name__)
ZERO = Decimal("0")


async def get_wallet_current_state(
    session: AsyncSession,
    *,
    address: str,
    client: HyperliquidClient | None = None,
) -> WalletCurrentStateStats:
    hyperliquid_client = client or HyperliquidClient()
    try:
        clearinghouse_state = await hyperliquid_client.clearinghouse_state(user=address)
        spot_state = await hyperliquid_client.spot_clearinghouse_state(user=address)
    except Exception as exc:
        logger.warning("wallet current state fetch failed wallet=%s error=%s", address, exc)
        return empty_current_state(error=str(exc) or exc.__class__.__name__)

    positions = parse_perp_positions(clearinghouse_state)
    spot_balances = parse_spot_balances(spot_state)
    await sync_wallet_positions(
        session,
        wallet_address=address,
        positions=positions,
        raw_positions=clearinghouse_state.get("assetPositions"),
    )

    margin_summary = object_or_empty(clearinghouse_state.get("marginSummary"))
    state_time_ms = int_or_none(clearinghouse_state.get("time"))

    return WalletCurrentStateStats(
        state_time_ms=state_time_ms,
        account_value_usd=decimal_value(margin_summary.get("accountValue")),
        withdrawable_usd=decimal_value(clearinghouse_state.get("withdrawable")),
        total_position_notional_usd=decimal_value(margin_summary.get("totalNtlPos")),
        total_margin_used_usd=decimal_value(margin_summary.get("totalMarginUsed")),
        total_unrealized_pnl_usd=sum_decimal(
            position.unrealized_pnl_usd for position in positions
        ),
        open_position_count=len(positions),
        spot_balance_count=len(spot_balances),
        spot_entry_notional_usd=sum_decimal(
            balance.entry_notional_usd
            for balance in spot_balances
            if balance.coin.upper() != "USDC"
        ),
        spot_usdc_balance=sum_decimal(
            balance.total for balance in spot_balances if balance.coin.upper() == "USDC"
        ),
        positions=positions,
        spot_balances=spot_balances,
    )


def parse_perp_positions(payload: dict[str, Any]) -> list[WalletPerpPositionStats]:
    raw_positions = payload.get("assetPositions")
    if not isinstance(raw_positions, list):
        return []

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
            "notional_usd": position.position_value_usd,
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
                "notional_usd",
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
        if isinstance(position, dict) and position.get("coin") == coin:
            return item
    return None


def empty_current_state(*, error: str | None = None) -> WalletCurrentStateStats:
    return WalletCurrentStateStats(
        state_time_ms=None,
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
