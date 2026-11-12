"""Pure parsing of Hyperliquid fills, positions, funding, and ledger rows for live accounts."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import blake2s
from typing import Any

from app.db.models import (
    TradingAccount,
    TradingOrder,
    TradingPosition,
)
from app.services.live_trading_constants import LIVE_EXCHANGE_SOURCE
from app.services.trading_core import (
    MarginMode,
    margin_from_notional,
)

ZERO = Decimal("0")


@dataclass(frozen=True)
class LivePositionSnapshot:
    coin: str
    side: str
    size: Decimal
    entry_price: Decimal
    notional_usd: Decimal
    leverage: Decimal
    margin_mode: MarginMode
    margin_usd: Decimal
    raw_payload: dict[str, Any]


def live_fill_pagination_key(fill: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(fill.get(key) or "")
        for key in (
            "tid",
            "hash",
            "oid",
            "cloid",
            "time",
            "timestamp",
            "coin",
            "px",
            "price",
            "sz",
            "size",
            "side",
            "dir",
        )
    )


def live_funding_pagination_key(payment: dict[str, Any]) -> tuple[str, ...]:
    delta = payment.get("delta")
    resolved_delta = delta if isinstance(delta, dict) else {}
    return tuple(
        str(value or "")
        for value in (
            payment.get("hash"),
            payment.get("time"),
            resolved_delta.get("coin"),
            resolved_delta.get("usdc"),
            resolved_delta.get("szi"),
            resolved_delta.get("fundingRate"),
        )
    )


def live_cash_flow_pagination_key(update: dict[str, Any]) -> tuple[str, ...]:
    delta = update.get("delta")
    resolved_delta = delta if isinstance(delta, dict) else {}
    return tuple(
        str(value or "")
        for value in (
            update.get("hash"),
            update.get("time"),
            resolved_delta.get("type"),
            resolved_delta.get("usdc"),
            resolved_delta.get("amount"),
            resolved_delta.get("usdcValue"),
            resolved_delta.get("token"),
            resolved_delta.get("user"),
            resolved_delta.get("destination"),
            resolved_delta.get("nonce"),
        )
    )


def parse_live_fill(fill: dict[str, Any], *, account_key: str) -> dict[str, Any] | None:
    price = decimal_or_none(fill.get("px") or fill.get("price"))
    size = decimal_or_none(fill.get("sz") or fill.get("size"))
    timestamp_ms = decimal_or_none(fill.get("time") or fill.get("timestamp"))
    coin = string_or_none(fill.get("coin"))
    if price is None or size is None or timestamp_ms is None or not coin:
        return None
    side = infer_position_side(fill)
    if side is None:
        return None
    action = infer_fill_action(fill)
    exchange_fill_id = build_exchange_fill_id(fill, account_key=account_key)
    return {
        "exchange_fill_id": exchange_fill_id,
        "exchange_order_id": string_or_none(fill.get("oid")),
        "client_order_id": string_or_none(fill.get("cloid")),
        "coin": coin,
        "action": action,
        "side": side,
        "price": price,
        "size": size,
        "notional_usd": price * size,
        "fee_usd": decimal_or_none(fill.get("fee")) or ZERO,
        "realized_pnl_usd": decimal_or_none(fill.get("closedPnl")) or ZERO,
        "filled_at": ms_to_datetime(timestamp_ms) or datetime.now(UTC),
        "raw_payload": fill,
    }


def parse_live_funding_payment(
    payment: dict[str, Any],
    *,
    account_key: str,
) -> dict[str, Any] | None:
    delta = payment.get("delta")
    if not isinstance(delta, dict):
        return None
    coin = string_or_none(delta.get("coin"))
    amount_usd = decimal_or_none(delta.get("usdc"))
    timestamp_ms = decimal_or_none(payment.get("time"))
    if not coin or amount_usd is None or timestamp_ms is None:
        return None
    identity = "|".join(live_funding_pagination_key(payment))
    exchange_event_id = blake2s(
        f"{account_key}|{identity}".encode(),
        digest_size=16,
    ).hexdigest()
    return {
        "exchange_event_id": exchange_event_id,
        "coin": coin,
        "amount_usd": amount_usd,
        "funding_rate": decimal_or_none(delta.get("fundingRate")),
        "position_size": decimal_or_none(delta.get("szi")),
        "occurred_at": ms_to_datetime(timestamp_ms) or datetime.now(UTC),
        "raw_payload": payment,
    }


def parse_live_account_cash_flow(
    update: dict[str, Any],
    *,
    account_key: str,
    user_address: str,
) -> dict[str, Any] | None:
    delta = update.get("delta")
    if not isinstance(delta, dict):
        return None
    event_type = string_or_none(delta.get("type"))
    timestamp_ms = decimal_or_none(update.get("time"))
    if event_type is None or timestamp_ms is None:
        return None
    if event_type in {"send", "spotTransfer"}:
        token = string_or_none(delta.get("token"))
        if token is None or token.upper() != "USDC":
            return None
        amount = decimal_or_none(delta.get("amount") or delta.get("usdcValue"))
    else:
        amount = decimal_or_none(delta.get("usdc"))
    if amount is None or amount == ZERO:
        return None

    fee = abs(decimal_or_none(delta.get("fee")) or ZERO)
    absolute_amount = abs(amount)
    normalized_user = normalize_optional_address(string_or_none(delta.get("user")))
    normalized_destination = normalize_optional_address(string_or_none(delta.get("destination")))
    normalized_account = normalize_optional_address(user_address)

    if event_type == "deposit":
        signed_amount = absolute_amount
        flow_type = "deposit"
    elif event_type == "withdraw":
        signed_amount = -(absolute_amount + fee)
        flow_type = "withdrawal"
    elif event_type == "send":
        if normalized_account is None:
            return None
        if normalized_destination == normalized_account and normalized_user != normalized_account:
            signed_amount = absolute_amount
            flow_type = "send_in"
        elif normalized_user == normalized_account and normalized_destination != normalized_account:
            signed_amount = -(absolute_amount + fee)
            flow_type = "send_out"
        else:
            return None
    elif event_type in {"internalTransfer", "spotTransfer", "subAccountTransfer"}:
        if normalized_account is None:
            return None
        if normalized_destination == normalized_account and normalized_user != normalized_account:
            signed_amount = absolute_amount
            flow_type = {
                "internalTransfer": "internal_transfer_in",
                "spotTransfer": "spot_transfer_in",
                "subAccountTransfer": "sub_account_transfer_in",
            }[event_type]
        elif normalized_user == normalized_account and normalized_destination != normalized_account:
            signed_amount = -(absolute_amount + fee)
            flow_type = {
                "internalTransfer": "internal_transfer_out",
                "spotTransfer": "spot_transfer_out",
                "subAccountTransfer": "sub_account_transfer_out",
            }[event_type]
        else:
            return None
    else:
        return None

    identity = "|".join(live_cash_flow_pagination_key(update))
    exchange_event_id = blake2s(
        f"{account_key}|{identity}".encode(),
        digest_size=16,
    ).hexdigest()
    return {
        "exchange_event_id": exchange_event_id,
        "flow_type": flow_type,
        "amount_usd": signed_amount,
        "fee_usd": fee,
        "occurred_at": ms_to_datetime(timestamp_ms) or datetime.now(UTC),
        "raw_payload": update,
    }


def live_fill_row(
    parsed_fill: dict[str, Any],
    *,
    account: TradingAccount,
    order: TradingOrder | None,
    attributed_position: TradingPosition | None,
) -> dict[str, Any]:
    attributed_source_wallet = (
        attributed_position.source_wallet if attributed_position is not None else None
    )
    raw_payload = parsed_fill["raw_payload"]
    if attributed_source_wallet is not None:
        raw_payload = merge_raw_payload(
            raw_payload,
            {
                "sourceAttribution": {
                    "method": "unique_open_source_position",
                    "sourceWallet": attributed_source_wallet,
                }
            },
        )
    return {
        "order_id": order.id if order is not None else None,
        "account_key": account.key,
        "account_type": "live",
        "source_wallet": (
            order.source_wallet
            if order is not None
            else attributed_source_wallet or LIVE_EXCHANGE_SOURCE
        ),
        "source_fill_id": order.source_fill_id
        if order is not None
        else parsed_fill["exchange_fill_id"],
        "sequence_index": order.sequence_index if order is not None else None,
        "exchange_fill_id": parsed_fill["exchange_fill_id"],
        "coin": order.coin if order is not None else parsed_fill["coin"],
        "action": order.action if order is not None else parsed_fill["action"],
        "side": order.side if order is not None else parsed_fill["side"],
        "price": parsed_fill["price"],
        "size": parsed_fill["size"],
        "notional_usd": parsed_fill["notional_usd"],
        "fee_usd": parsed_fill["fee_usd"],
        "realized_pnl_usd": parsed_fill["realized_pnl_usd"],
        "raw_payload": raw_payload,
        "filled_at": parsed_fill["filled_at"],
    }


def match_live_fill_order(
    parsed_fill: dict[str, Any],
    *,
    orders_by_oid: dict[str, TradingOrder],
    orders_by_cloid: dict[str, TradingOrder],
) -> TradingOrder | None:
    client_order_id = parsed_fill.get("client_order_id")
    if client_order_id and client_order_id in orders_by_cloid:
        return orders_by_cloid[client_order_id]
    exchange_order_id = parsed_fill.get("exchange_order_id")
    if exchange_order_id and exchange_order_id in orders_by_oid:
        return orders_by_oid[exchange_order_id]
    return None


def parse_live_position(payload: dict[str, Any]) -> LivePositionSnapshot | None:
    position = payload.get("position")
    if not isinstance(position, dict):
        position = payload
    coin = string_or_none(position.get("coin"))
    signed_size = decimal_or_none(position.get("szi"))
    if not coin or signed_size is None or signed_size == ZERO:
        return None
    side = "long" if signed_size > ZERO else "short"
    size = abs(signed_size)
    entry_price = decimal_or_none(position.get("entryPx")) or ZERO
    notional = decimal_or_none(position.get("positionValue")) or (size * entry_price)
    raw_leverage = position.get("leverage")
    leverage = parse_position_leverage(raw_leverage)
    margin_mode = parse_position_margin_mode(raw_leverage)
    margin = decimal_or_none(position.get("marginUsed")) or margin_from_notional(
        notional,
        leverage,
    )
    return LivePositionSnapshot(
        coin=coin,
        side=side,
        size=size,
        entry_price=entry_price,
        notional_usd=notional,
        leverage=leverage,
        margin_mode=margin_mode,
        margin_usd=margin,
        raw_payload=payload,
    )


def parse_position_leverage(value: Any) -> Decimal:
    if isinstance(value, dict):
        parsed = decimal_or_none(value.get("value"))
        return parsed if parsed is not None and parsed > ZERO else Decimal("1")
    parsed = decimal_or_none(value)
    return parsed if parsed is not None and parsed > ZERO else Decimal("1")


def parse_position_margin_mode(value: Any) -> MarginMode:
    if isinstance(value, dict):
        margin_mode = str(value.get("type") or "").strip().casefold()
        if margin_mode == "isolated":
            return "isolated"
    return "cross"


def infer_position_side(fill: dict[str, Any]) -> str | None:
    direction = str(fill.get("dir") or "").casefold()
    if "long" in direction:
        return "long"
    if "short" in direction:
        return "short"
    fill_side = str(fill.get("side") or "").casefold()
    if fill_is_auto_deleveraging(fill):
        # Auto-deleveraging closes the position, so a buy closes a short.
        if fill_side in {"b", "buy"}:
            return "short"
        if fill_side in {"a", "sell"}:
            return "long"
        return None
    if fill_side in {"b", "buy"}:
        return "long"
    if fill_side in {"a", "sell"}:
        return "short"
    return None


def infer_fill_action(fill: dict[str, Any]) -> str:
    direction = str(fill.get("dir") or "").casefold()
    if "close" in direction or "liquidated" in direction or fill_is_auto_deleveraging(fill):
        return "close"
    return "open"


def fill_is_auto_deleveraging(fill: dict[str, Any]) -> bool:
    return str(fill.get("dir") or "").strip().casefold() == "auto-deleveraging"


def map_exchange_order_status(status: str | None) -> str | None:
    if status is None:
        return None
    normalized = status.strip().casefold()
    if normalized in {"open", "triggered"}:
        return "accepted"
    if normalized == "filled":
        return "filled"
    if normalized.endswith("rejected") or normalized == "rejected":
        return "rejected"
    if normalized.endswith("canceled") or normalized in {"canceled", "scheduledcancel"}:
        return "canceled"
    return None


def build_exchange_fill_id(fill: dict[str, Any], *, account_key: str) -> str:
    tid = string_or_none(fill.get("tid"))
    if tid:
        return f"hl:{account_key}:tid:{tid}"
    fill_hash = string_or_none(fill.get("hash"))
    if fill_hash and fill_hash != "0x" + "0" * 64:
        return f"hl:{account_key}:hash:{fill_hash}"
    coin = string_or_none(fill.get("coin")) or "unknown"
    time_value = string_or_none(fill.get("time") or fill.get("timestamp")) or "0"
    oid = string_or_none(fill.get("oid")) or "unknown"
    px = string_or_none(fill.get("px") or fill.get("price")) or "0"
    size = string_or_none(fill.get("sz") or fill.get("size")) or "0"
    return f"hl:{account_key}:fallback:{coin}:{time_value}:{oid}:{px}:{size}"


def normalize_optional_address(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized.lower() if normalized else None


def merge_raw_payload(
    existing: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(existing or {})
    merged.update(patch)
    return merged


def parse_exchange_order_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation, TypeError, ValueError:
        return None


def max_optional_numeric(current: Any, candidate: Any) -> Any:
    current_value = decimal_or_none(current)
    candidate_value = decimal_or_none(candidate)
    if candidate_value is None:
        return current
    if current_value is None or candidate_value > current_value:
        return candidate
    return current


def string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def ms_to_datetime(value: Decimal | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
