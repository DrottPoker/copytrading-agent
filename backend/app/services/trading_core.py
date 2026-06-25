from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import blake2s
from typing import Any, Literal

from app.core.config import Settings

ZERO = Decimal("0")

TradeAction = Literal["open", "add", "reduce", "close", "flip_close", "flip_open"]
TradeSide = Literal["long", "short"]
TradingAccountType = Literal["paper", "live"]
TradingAccountStatus = Literal["disabled", "enabled", "exit_only"]


@dataclass(frozen=True)
class MinOrderAdjustment:
    original_notional_usd: Decimal
    adjusted_notional_usd: Decimal
    min_order_notional_usd: Decimal


@dataclass(frozen=True)
class TradeIntent:
    account_key: str
    account_type: TradingAccountType
    source_wallet: str
    source_fill_id: str
    sequence_index: int
    client_order_id: str
    coin: str
    action: TradeAction
    side: TradeSide
    is_buy: bool
    reduce_only: bool
    size: Decimal
    notional_usd: Decimal
    margin_usd: Decimal
    leverage: Decimal
    limit_price: Decimal
    source_price: Decimal | None
    observed_price: Decimal | None
    price_drift_bps: Decimal | None
    price_source: str | None
    allocation_pct: Decimal | None
    allocation_usd: Decimal | None
    source_perp_equity_usd: Decimal | None
    source_exposure_pct: Decimal | None
    created_at: datetime


def build_client_order_id(
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
    action: str,
) -> str:
    raw_key = "|".join(
        (
            account_key.lower(),
            source_wallet.lower(),
            source_fill_id,
            str(sequence_index),
            action,
        )
    )
    return "0x" + blake2s(raw_key.encode("utf-8"), digest_size=16).hexdigest()


def build_copy_trade_intent(
    *,
    account_key: str,
    account_type: TradingAccountType,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
    coin: str,
    action: TradeAction,
    side: TradeSide,
    size: Decimal,
    notional_usd: Decimal,
    margin_usd: Decimal,
    leverage: Decimal,
    limit_price: Decimal,
    source_price: Decimal | None,
    observed_price: Decimal | None,
    price_drift_bps: Decimal | None,
    price_source: str | None,
    allocation_pct: Decimal | None,
    allocation_usd: Decimal | None,
    source_perp_equity_usd: Decimal | None,
    source_exposure_pct: Decimal | None,
    created_at: datetime | None = None,
) -> TradeIntent:
    reduce_only = action in {"reduce", "close", "flip_close"}
    is_buy = trade_is_buy(side=side, reduce_only=reduce_only)
    return TradeIntent(
        account_key=account_key,
        account_type=account_type,
        source_wallet=source_wallet.lower(),
        source_fill_id=source_fill_id,
        sequence_index=sequence_index,
        client_order_id=build_client_order_id(
            account_key=account_key,
            source_wallet=source_wallet,
            source_fill_id=source_fill_id,
            sequence_index=sequence_index,
            action=action,
        ),
        coin=coin,
        action=action,
        side=side,
        is_buy=is_buy,
        reduce_only=reduce_only,
        size=size,
        notional_usd=notional_usd,
        margin_usd=margin_usd,
        leverage=leverage,
        limit_price=limit_price,
        source_price=source_price,
        observed_price=observed_price,
        price_drift_bps=price_drift_bps,
        price_source=price_source,
        allocation_pct=allocation_pct,
        allocation_usd=allocation_usd,
        source_perp_equity_usd=source_perp_equity_usd,
        source_exposure_pct=source_exposure_pct,
        created_at=created_at or datetime.now(UTC),
    )


def trade_is_buy(*, side: TradeSide, reduce_only: bool) -> bool:
    if side == "long":
        return not reduce_only
    return reduce_only


def safe_leverage(leverage: Decimal | None) -> Decimal:
    return leverage if leverage is not None and leverage > ZERO else Decimal("1")


def margin_from_notional(notional_usd: Decimal, leverage: Decimal) -> Decimal:
    resolved_leverage = safe_leverage(leverage)
    if notional_usd <= ZERO:
        return ZERO
    return notional_usd / resolved_leverage


def adjust_open_sizing_to_min_order(
    *,
    target_notional: Decimal,
    margin_usd: Decimal,
    notional_usd: Decimal,
    source_remaining: Decimal,
    global_remaining: Decimal,
    source_leverage: Decimal,
    settings: Settings,
) -> tuple[Decimal, Decimal, MinOrderAdjustment | None]:
    min_order_notional = settings.trading_copy_min_order_notional_usd
    if notional_usd >= min_order_notional:
        return margin_usd, notional_usd, None

    min_order_margin = margin_from_notional(min_order_notional, source_leverage)
    can_adjust_to_min_order = (
        settings.trading_copy_adjust_small_orders_to_min_order
        and target_notional < min_order_notional
        and source_remaining >= min_order_margin
        and global_remaining >= min_order_margin
    )
    if not can_adjust_to_min_order:
        return margin_usd, notional_usd, None

    return (
        min_order_margin,
        min_order_notional,
        MinOrderAdjustment(
            original_notional_usd=notional_usd,
            adjusted_notional_usd=min_order_notional,
            min_order_notional_usd=min_order_notional,
        ),
    )


def open_notional_skip_reason(
    *,
    target_notional: Decimal,
    source_remaining: Decimal,
    global_remaining: Decimal,
    min_order_notional: Decimal,
) -> str:
    source_cap_blocked = source_remaining < min_order_notional
    total_cap_blocked = global_remaining < min_order_notional
    if source_cap_blocked and total_cap_blocked:
        return "source_and_total_allocation_caps_reached"
    if source_cap_blocked:
        return "source_allocation_cap_reached"
    if total_cap_blocked:
        return "total_allocation_cap_reached"
    if target_notional < min_order_notional:
        return "below_min_order_notional"
    return "below_min_order_notional"


def trade_intent_payload(intent: TradeIntent | None) -> dict[str, Any] | None:
    if intent is None:
        return None
    return {
        "accountKey": intent.account_key,
        "accountType": intent.account_type,
        "sourceWallet": intent.source_wallet,
        "sourceFillId": intent.source_fill_id,
        "sequenceIndex": intent.sequence_index,
        "clientOrderId": intent.client_order_id,
        "coin": intent.coin,
        "action": intent.action,
        "side": intent.side,
        "isBuy": intent.is_buy,
        "reduceOnly": intent.reduce_only,
        "size": str(intent.size),
        "notionalUsd": str(intent.notional_usd),
        "marginUsd": str(intent.margin_usd),
        "leverage": str(intent.leverage),
        "limitPrice": str(intent.limit_price),
        "sourcePrice": str(intent.source_price) if intent.source_price is not None else None,
        "observedPrice": (
            str(intent.observed_price) if intent.observed_price is not None else None
        ),
        "priceDriftBps": (
            str(intent.price_drift_bps) if intent.price_drift_bps is not None else None
        ),
        "priceSource": intent.price_source,
        "allocationPct": (
            str(intent.allocation_pct) if intent.allocation_pct is not None else None
        ),
        "allocationUsd": (
            str(intent.allocation_usd) if intent.allocation_usd is not None else None
        ),
        "sourcePerpEquityUsd": (
            str(intent.source_perp_equity_usd)
            if intent.source_perp_equity_usd is not None
            else None
        ),
        "sourceExposurePct": (
            str(intent.source_exposure_pct)
            if intent.source_exposure_pct is not None
            else None
        ),
        "createdAt": intent.created_at.isoformat(),
    }
