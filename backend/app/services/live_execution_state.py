from datetime import UTC, datetime
from decimal import Decimal
from hashlib import blake2s
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TradingOrder, TradingOrderDispatch
from app.services.trading_core import TradeIntent, trade_intent_payload

ZERO = Decimal("0")
MAX_LIVE_ORDER_SUBMIT_ATTEMPTS = 3
TERMINAL_ORDER_STATUSES = {"filled", "rejected", "canceled", "failed"}
RECOVERABLE_ORDER_STATUSES = {"ready", "submitting", "uncertain"}
RECONCILABLE_ORDER_STATUSES = {
    "submitting",
    "uncertain",
    "submitted",
    "accepted",
    "partially_filled",
}


async def prepare_live_order_dispatch(
    session: AsyncSession,
    *,
    intent: TradeIntent,
) -> tuple[TradingOrder, TradingOrderDispatch, bool]:
    order = await session.scalar(
        select(TradingOrder)
        .where(TradingOrder.client_order_id == intent.client_order_id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    created = order is None
    if order is None:
        order = trading_order_from_intent(intent)
        session.add(order)
        await session.flush()

    dispatch = await session.scalar(
        select(TradingOrderDispatch)
        .where(TradingOrderDispatch.order_id == order.id)
        .order_by(TradingOrderDispatch.attempt_number.desc())
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if dispatch is None:
        dispatch = TradingOrderDispatch(
            order_id=order.id,
            account_key=order.account_key,
            client_order_id=build_dispatch_client_order_id(
                order.client_order_id,
                attempt_number=1,
            ),
            attempt_number=1,
            status="pending",
            attempt_count=0,
        )
        session.add(dispatch)

    if created or order.status == "planned":
        order.status = "ready"
        order.error = None
        order.raw_payload = merge_payload(
            order.raw_payload,
            {"tradeIntent": trade_intent_payload(intent)},
        )
        dispatch.status = "pending"
        dispatch.available_at = datetime.now(UTC)
        dispatch.last_error = None

    await session.flush()
    await session.commit()
    return order, dispatch, created


async def mark_live_order_dispatching(
    session: AsyncSession,
    *,
    order: TradingOrder,
    dispatch: TradingOrderDispatch,
) -> None:
    now = datetime.now(UTC)
    order.status = "submitting"
    order.submitted_at = order.submitted_at or now
    order.error = None
    dispatch.status = "dispatching"
    dispatch.attempt_count += 1
    dispatch.dispatch_started_at = now
    dispatch.last_error = None
    dispatch.exchange_status = None
    dispatch.exchange_error_code = None
    dispatch.exchange_error_message = None
    dispatch.exchange_response = None
    await session.flush()
    await session.commit()


async def mark_live_order_uncertain(
    session: AsyncSession,
    *,
    order: TradingOrder,
    dispatch: TradingOrderDispatch,
    error: BaseException,
) -> None:
    message = str(error) or error.__class__.__name__
    order.status = "uncertain"
    order.error = message
    order.raw_payload = merge_payload(
        order.raw_payload,
        {
            "submitError": {
                "message": message,
                "type": error.__class__.__name__,
                "outcome": "uncertain",
            }
        },
    )
    dispatch.status = "uncertain"
    dispatch.last_error = message
    dispatch.exchange_status = "unknown"
    dispatch.exchange_error_code = "uncertain_submit"
    dispatch.exchange_error_message = message
    await session.flush()
    await session.commit()


async def mark_live_order_failed(
    session: AsyncSession,
    *,
    order: TradingOrder,
    dispatch: TradingOrderDispatch,
    error: BaseException,
) -> None:
    message = str(error) or error.__class__.__name__
    order.status = "failed"
    order.error = message
    order.raw_payload = merge_payload(
        order.raw_payload,
        {
            "submitError": {
                "message": message,
                "type": error.__class__.__name__,
                "outcome": "not_submitted",
            }
        },
    )
    dispatch.status = "completed"
    dispatch.completed_at = datetime.now(UTC)
    dispatch.last_error = message
    dispatch.exchange_status = "not_submitted"
    dispatch.exchange_error_code = "pre_submit_failure"
    dispatch.exchange_error_message = message
    await session.flush()
    await session.commit()


async def mark_live_order_dispatch_completed(
    session: AsyncSession,
    *,
    dispatch: TradingOrderDispatch,
) -> None:
    dispatch.status = "completed"
    dispatch.completed_at = datetime.now(UTC)
    await session.flush()
    await session.commit()


async def load_live_order_dispatch(
    session: AsyncSession,
    *,
    order_id: Any,
) -> TradingOrderDispatch | None:
    return await session.scalar(
        select(TradingOrderDispatch)
        .where(TradingOrderDispatch.order_id == order_id)
        .order_by(TradingOrderDispatch.attempt_number.desc())
    )


async def create_next_live_order_dispatch(
    session: AsyncSession,
    *,
    order: TradingOrder,
) -> TradingOrderDispatch:
    """Append the next deterministic exchange attempt for a logical order."""

    latest_attempt = await session.scalar(
        select(func.max(TradingOrderDispatch.attempt_number)).where(
            TradingOrderDispatch.order_id == order.id
        )
    )
    attempt_number = int(latest_attempt or 0) + 1
    if attempt_number > MAX_LIVE_ORDER_SUBMIT_ATTEMPTS:
        raise ValueError("Live order submit attempts are exhausted.")
    dispatch = TradingOrderDispatch(
        order_id=order.id,
        account_key=order.account_key,
        client_order_id=build_dispatch_client_order_id(
            order.client_order_id,
            attempt_number=attempt_number,
        ),
        attempt_number=attempt_number,
        status="pending",
        attempt_count=0,
    )
    session.add(dispatch)
    await session.flush()
    await session.commit()
    return dispatch


def build_dispatch_client_order_id(
    logical_client_order_id: str,
    *,
    attempt_number: int,
) -> str:
    """Build the 128-bit exchange CLOID for exactly one submit attempt."""

    if attempt_number <= 0:
        raise ValueError("attempt_number must be positive")
    raw_key = f"{logical_client_order_id}|{attempt_number}"
    return "0x" + blake2s(raw_key.encode("utf-8"), digest_size=16).hexdigest()


def record_live_order_exchange_result(
    dispatch: TradingOrderDispatch,
    *,
    exchange_status: str,
    error_code: str | None,
    error_message: str | None,
    raw_response: dict[str, Any],
) -> None:
    dispatch.exchange_status = exchange_status
    dispatch.exchange_error_code = error_code
    dispatch.exchange_error_message = error_message
    dispatch.exchange_response = raw_response
    dispatch.last_error = error_message


def record_live_order_status_lookup(
    dispatch: TradingOrderDispatch,
    *,
    response: dict[str, Any] | None = None,
    error: BaseException | None = None,
    now: datetime | None = None,
) -> None:
    dispatch.status_lookup_count = max(int(dispatch.status_lookup_count or 0), 0) + 1
    dispatch.last_status_lookup_at = now or datetime.now(UTC)
    dispatch.last_status_response = response
    dispatch.last_status_lookup_error = (
        (str(error) or error.__class__.__name__) if error is not None else None
    )


def trading_order_from_intent(intent: TradeIntent) -> TradingOrder:
    return TradingOrder(
        account_key=intent.account_key,
        account_type="live",
        source_wallet=intent.source_wallet,
        source_fill_id=intent.source_fill_id,
        sequence_index=intent.sequence_index,
        client_order_id=intent.client_order_id,
        coin=intent.coin,
        action=intent.action,
        side=intent.side,
        is_buy=intent.is_buy,
        reduce_only=intent.reduce_only,
        order_type="ioc",
        status="planned",
        requested_size=intent.size,
        requested_notional_usd=intent.notional_usd,
        margin_usd=intent.margin_usd,
        leverage=intent.leverage,
        margin_mode=intent.margin_mode,
        limit_price=intent.limit_price,
        filled_size=ZERO,
        filled_notional_usd=ZERO,
        fee_usd=ZERO,
        raw_payload={"tradeIntent": trade_intent_payload(intent)},
    )


def trade_intent_from_order(order: TradingOrder) -> TradeIntent:
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    raw_intent = payload.get("tradeIntent")
    if not isinstance(raw_intent, dict):
        raw_intent = {}

    created_at = parse_datetime(raw_intent.get("createdAt")) or order.created_at
    return TradeIntent(
        account_key=order.account_key,
        account_type="live",
        source_wallet=order.source_wallet,
        source_fill_id=order.source_fill_id,
        sequence_index=order.sequence_index,
        client_order_id=order.client_order_id,
        coin=order.coin,
        action=order.action,
        side=order.side,
        is_buy=order.is_buy,
        reduce_only=order.reduce_only,
        size=Decimal(order.requested_size),
        notional_usd=Decimal(order.requested_notional_usd),
        margin_usd=Decimal(order.margin_usd or ZERO),
        leverage=Decimal(order.leverage or Decimal("1")),
        margin_mode=(
            raw_intent.get("marginMode")
            if raw_intent.get("marginMode") in {"cross", "isolated"}
            else order.margin_mode
        ),
        limit_price=Decimal(order.limit_price or ZERO),
        source_price=optional_decimal(raw_intent.get("sourcePrice")),
        observed_price=optional_decimal(raw_intent.get("observedPrice")),
        price_drift_bps=optional_decimal(raw_intent.get("priceDriftBps")),
        price_source=optional_string(raw_intent.get("priceSource")),
        allocation_pct=optional_decimal(raw_intent.get("allocationPct")),
        allocation_usd=optional_decimal(raw_intent.get("allocationUsd")),
        source_perp_equity_usd=optional_decimal(raw_intent.get("sourcePerpEquityUsd")),
        source_exposure_pct=optional_decimal(raw_intent.get("sourceExposurePct")),
        created_at=created_at,
    )


def merge_payload(
    current: dict[str, Any] | None,
    update: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(current) if isinstance(current, dict) else {}
    payload.update(update)
    return payload


def optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
