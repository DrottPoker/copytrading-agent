import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import blake2s
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import TradingAccount, TradingFill, TradingOrder, TradingPosition
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
    LiveOrderResult,
)
from app.services.trading_core import (
    TradeIntent,
    build_copy_trade_intent,
    margin_from_notional,
    trade_intent_payload,
)

ZERO = Decimal("0")
POSITION_EPSILON = Decimal("0.000000000001")
LIVE_EXCHANGE_SOURCE = "__exchange__"
LIVE_MANUAL_TEST_SOURCE = "__manual_testnet__"
TERMINAL_ORDER_STATUSES = {"filled", "rejected", "canceled", "failed"}
ACTIVE_ORDER_STATUSES = {"planned", "submitted", "accepted", "partially_filled"}
MAX_LIVE_FILL_RECONCILIATION_PAGES = 10
LIVE_ACCOUNT_KEY_MAX_LENGTH = 64
LIVE_CAPITAL_MODE_UNIFIED = "unified"
LIVE_CAPITAL_MODE_STANDARD_PER_DEX = "standard_per_dex"
LIVE_CAPITAL_MODES = {LIVE_CAPITAL_MODE_UNIFIED, LIVE_CAPITAL_MODE_STANDARD_PER_DEX}
UNIFIED_USER_ABSTRACTION_KEYS = {
    "portfolio",
    "portfolioaccount",
    "portfoliomargin",
    "unified",
    "unifiedaccount",
}
STANDARD_USER_ABSTRACTION_KEYS = {
    "classic",
    "default",
    "disabled",
    "none",
    "standard",
    "standardaccount",
}


class LiveTradingServiceError(Exception):
    status_code = 400
    detail = "Live trading request failed."

    def __init__(self, detail: str | None = None, *, status_code: int | None = None) -> None:
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail
        if status_code is not None:
            self.status_code = status_code


class LiveAccountNotFoundError(LiveTradingServiceError):
    status_code = 404
    detail = "Live account was not found."


class LiveAccountCreateError(LiveTradingServiceError):
    detail = "Live account could not be created."


class LiveAccountDeleteError(LiveTradingServiceError):
    detail = "Live account could not be deleted."


class LiveOrderSubmitError(LiveTradingServiceError):
    detail = "Live order could not be submitted."


class LiveReconciliationError(LiveTradingServiceError):
    detail = "Live reconciliation failed."


@dataclass(frozen=True)
class LiveReconciliationResult:
    account_key: str
    user_address: str
    fetched_fills: int = 0
    inserted_fills: int = 0
    updated_orders: int = 0
    open_positions: int = 0
    removed_positions: int = 0
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class LiveOrderLifecycleResult:
    order: TradingOrder
    exchange_result: LiveOrderResult | None
    submitted: bool


@dataclass(frozen=True)
class LiveClosedTrade:
    id: str
    account_key: str
    source_wallet: str
    source_label: str | None
    coin: str
    side: str
    entry_price: Decimal | None
    exit_price: Decimal | None
    size: Decimal
    entry_notional_usd: Decimal
    exit_notional_usd: Decimal
    fee_usd: Decimal
    realized_pnl_usd: Decimal
    net_pnl_usd: Decimal
    opened_at: datetime
    closed_at: datetime
    duration_ms: int | None
    open_fill_count: int
    close_fill_count: int


@dataclass
class LiveTradeAccumulator:
    account_key: str
    source_wallet: str
    source_label: str | None
    coin: str
    side: str
    opened_at: datetime
    closed_at: datetime
    last_close_fill_id: str
    opened_size: Decimal = ZERO
    remaining_size: Decimal = ZERO
    closed_size: Decimal = ZERO
    entry_notional_usd: Decimal = ZERO
    exit_notional_usd: Decimal = ZERO
    fee_usd: Decimal = ZERO
    realized_pnl_usd: Decimal = ZERO
    open_fill_count: int = 0
    close_fill_count: int = 0


@dataclass(frozen=True)
class LiveCloseAllResult:
    account_key: str
    submitted_orders: int
    failed_orders: int
    status: str


@dataclass(frozen=True)
class LivePositionSnapshot:
    coin: str
    side: str
    size: Decimal
    entry_price: Decimal
    notional_usd: Decimal
    leverage: Decimal
    margin_usd: Decimal
    raw_payload: dict[str, Any]


@dataclass(frozen=True)
class LivePerpState:
    dex: str
    payload: dict[str, Any]


def validate_live_trading_configuration(settings: Settings) -> None:
    try:
        HyperliquidLiveTradingClient(settings=settings).validate_live_configuration()
    except Exception as exc:
        raise LiveTradingServiceError(str(exc) or exc.__class__.__name__) from exc


def live_capital_mode(settings: Settings) -> str:
    value = str(settings.live_trading_capital_mode or "").strip()
    if value in LIVE_CAPITAL_MODES:
        return value
    return LIVE_CAPITAL_MODE_UNIFIED


def account_last_reconciliation(account: TradingAccount) -> dict[str, Any]:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    last_reconciliation = payload.get("lastReconciliation")
    return last_reconciliation if isinstance(last_reconciliation, dict) else {}


def normalize_user_abstraction(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, dict):
        for key in (
            "accountAbstraction",
            "abstraction",
            "mode",
            "type",
            "userAbstraction",
            "value",
        ):
            normalized = normalize_user_abstraction(value.get(key))
            if normalized is not None:
                return normalized
        return None
    normalized = str(value).strip()
    return normalized or None


def user_abstraction_key(value: Any) -> str | None:
    normalized = normalize_user_abstraction(value)
    if normalized is None:
        return None
    key = re.sub(r"[^a-z0-9]", "", normalized.lower())
    return key or None


def user_abstraction_is_unified(value: Any) -> bool:
    key = user_abstraction_key(value)
    return key in UNIFIED_USER_ABSTRACTION_KEYS if key is not None else False


def user_abstraction_is_standard(value: Any) -> bool:
    key = user_abstraction_key(value)
    return key in STANDARD_USER_ABSTRACTION_KEYS if key is not None else False


def live_perp_equity_usd(account: TradingAccount, *, dex: str | None = None) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    if dex is not None:
        normalized_dex = dex or "default"
        states = last_reconciliation.get("perpStates")
        if isinstance(states, list):
            for state in states:
                if not isinstance(state, dict):
                    continue
                if str(state.get("dex") or "default") == normalized_dex:
                    return decimal_or_none(state.get("accountValue")) or ZERO
        return ZERO
    if "perpEquityUsd" in last_reconciliation:
        return decimal_or_none(last_reconciliation.get("perpEquityUsd")) or ZERO
    return account.equity_usd or ZERO


def live_unified_equity_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("unifiedEquityUsd")) or ZERO


def live_unified_available_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("unifiedAvailableUsd")) or ZERO


def live_spot_balance_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("spotUsdcTotalUsd")) or ZERO


def live_spot_available_usd(account: TradingAccount) -> Decimal:
    last_reconciliation = account_last_reconciliation(account)
    return decimal_or_none(last_reconciliation.get("spotUsdcAvailableUsd")) or ZERO


def live_tradable_equity_usd(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None = None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        return live_unified_available_usd(account)
    return live_perp_equity_usd(account, dex=dex)


def validate_live_account_can_start(account: TradingAccount, *, settings: Settings) -> None:
    last_reconciliation = account_last_reconciliation(account)
    if (
        live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED
        and user_abstraction_is_standard(last_reconciliation.get("userAbstraction"))
    ):
        raise LiveTradingServiceError(
            "Hyperliquid account is not in Unified account mode.",
            status_code=409,
        )
    if live_tradable_equity_usd(account, settings=settings) <= ZERO:
        raise LiveTradingServiceError(
            "Transfer USDC to the configured Hyperliquid trading account before "
            "starting live trading.",
            status_code=409,
        )


async def create_live_trading_account(
    session: AsyncSession,
    *,
    key: str | None,
    label: str,
    wallet_address: str | None,
    vault_address: str | None,
    status: str,
    settings: Settings,
) -> TradingAccount:
    account_label = label.strip()
    if not account_label:
        raise LiveAccountCreateError("Live account wallet name is required.")
    if status != "disabled":
        raise LiveAccountCreateError("Live accounts must be created disabled.")
    resolved_wallet_address = resolve_live_account_wallet_address(
        wallet_address=wallet_address,
        settings=settings,
    )
    resolved_vault_address = normalize_optional_address(vault_address)
    existing_route = await find_existing_live_account_for_route(
        session,
        wallet_address=resolved_wallet_address,
        vault_address=resolved_vault_address,
        include_config_wallet_fallback=not normalize_optional_address(wallet_address),
    )
    if existing_route is not None:
        existing_route.wallet_address = existing_route.wallet_address or resolved_wallet_address
        existing_route.vault_address = existing_route.vault_address or resolved_vault_address
        return existing_route
    account_key = live_account_key_for_route(
        wallet_address=resolved_wallet_address,
        vault_address=resolved_vault_address,
    )
    existing = await session.scalar(
        select(TradingAccount).where(TradingAccount.key == account_key)
    )
    if existing is not None:
        raise LiveAccountCreateError("Trading account key already exists.", status_code=409)

    account = TradingAccount(
        key=account_key,
        account_type="live",
        label=account_label,
        status="disabled",
        network=settings.hyperliquid_network,
        wallet_address=resolved_wallet_address,
        vault_address=resolved_vault_address,
        realized_pnl_usd=ZERO,
        fee_usd=ZERO,
        config_payload={
            "source": "dashboard",
            "keySource": "wallet_route",
            "legacyRequestKey": key,
        },
    )
    session.add(account)
    await session.flush()
    return account


async def delete_live_trading_account(
    session: AsyncSession,
    *,
    account_key: str,
) -> None:
    account = await load_live_account_for_update(session, account_key=account_key)
    if account.status == "enabled":
        raise LiveAccountDeleteError(
            "Stop live trading before deleting this account.",
            status_code=409,
        )

    await session.execute(
        delete(TradingFill).where(
            TradingFill.account_key == account.key,
            TradingFill.account_type == "live",
        )
    )
    await session.execute(
        delete(TradingOrder).where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
        )
    )
    await session.execute(
        delete(TradingPosition).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
        )
    )
    await session.delete(account)
    await session.flush()


async def find_existing_live_account_for_route(
    session: AsyncSession,
    *,
    wallet_address: str,
    vault_address: str | None,
    include_config_wallet_fallback: bool,
) -> TradingAccount | None:
    wallet_conditions = [TradingAccount.wallet_address == wallet_address]
    if include_config_wallet_fallback:
        wallet_conditions.append(TradingAccount.wallet_address.is_(None))
    query = select(TradingAccount).where(
        TradingAccount.account_type == "live",
        or_(*wallet_conditions),
    )
    if vault_address is None:
        query = query.where(TradingAccount.vault_address.is_(None))
    else:
        query = query.where(TradingAccount.vault_address == vault_address)
    return await session.scalar(query.limit(1))


def resolve_live_account_wallet_address(
    *,
    wallet_address: str | None,
    settings: Settings,
) -> str:
    resolved = normalize_optional_address(wallet_address) or normalize_optional_address(
        settings.hyperliquid_wallet_address
    )
    if not resolved:
        raise LiveAccountCreateError(
            "Live account requires wallet address or HYPERLIQUID_WALLET_ADDRESS.",
        )
    return resolved


def live_account_key_for_route(
    *,
    wallet_address: str,
    vault_address: str | None = None,
) -> str:
    wallet_part = live_account_address_key_part(wallet_address)
    if vault_address:
        route_key = (
            f"{normalize_optional_address(wallet_address)}:"
            f"{normalize_optional_address(vault_address)}"
        )
        route_hash = blake2s(
            route_key.encode(),
            digest_size=6,
        ).hexdigest()
        suffix = f"_{route_hash}"
        wallet_limit = LIVE_ACCOUNT_KEY_MAX_LENGTH - len("live_") - len(suffix)
        wallet_part = wallet_part[:wallet_limit].rstrip("_") or "wallet"
        return f"live_{wallet_part}{suffix}"
    wallet_limit = LIVE_ACCOUNT_KEY_MAX_LENGTH - len("live_")
    wallet_part = wallet_part[:wallet_limit].rstrip("_") or "wallet"
    return f"live_{wallet_part}"


def live_account_address_key_part(address: str) -> str:
    key_part = re.sub(r"[^a-zA-Z0-9]+", "", normalize_optional_address(address) or "")
    return key_part or "wallet"


async def set_live_trading_account_status(
    session: AsyncSession,
    *,
    account_key: str,
    status: str,
) -> TradingAccount:
    if status not in {"disabled", "enabled", "exit_only"}:
        raise LiveTradingServiceError("Unsupported live account status.")
    account = await load_live_account_for_update(session, account_key=account_key)
    account.status = status
    await session.flush()
    return account


async def close_all_live_account_positions(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
) -> LiveCloseAllResult:
    if account.account_type != "live":
        raise LiveTradingServiceError("Only live accounts can close live positions.")

    account.status = "exit_only"
    await session.flush()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    try:
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        positions = await load_live_exchange_positions(session, account_key=account.key)
        if not positions:
            account.status = "disabled"
            await session.flush()
            return LiveCloseAllResult(
                account_key=account.key,
                submitted_orders=0,
                failed_orders=0,
                status=account.status,
            )

        mids = await load_live_close_mids(client, positions=positions)
        submitted = 0
        failed = 0
        live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
        for position in positions:
            mid_price = decimal_or_none(mids.get(position.coin))
            if mid_price is None or mid_price <= ZERO:
                failed += 1
                continue
            intent = build_live_close_position_intent(
                account=account,
                position=position,
                mid_price=mid_price,
                settings=settings,
            )
            try:
                result = await submit_live_trade_intent(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                    client=live_client,
                )
                if result.order.status in {"rejected", "failed", "canceled"}:
                    failed += 1
                else:
                    submitted += 1
            except LiveTradingServiceError:
                failed += 1
        if failed > 0:
            await session.flush()
            return LiveCloseAllResult(
                account_key=account.key,
                submitted_orders=submitted,
                failed_orders=failed,
                status=account.status,
            )

        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        account.status = "disabled"
        await session.flush()
        return LiveCloseAllResult(
            account_key=account.key,
            submitted_orders=submitted,
            failed_orders=0,
            status=account.status,
        )
    finally:
        if client_created:
            await client.__aexit__(None, None, None)


async def close_live_account_position(
    session: AsyncSession,
    *,
    position_id: UUID,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
) -> LiveOrderLifecycleResult:
    position = await load_live_position_for_update(session, position_id=position_id)
    account = await load_live_account_for_update(session, account_key=position.account_key)

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    try:
        await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        position = await load_live_position_for_update(session, position_id=position_id)
        if position.account_key != account.key:
            raise LiveTradingServiceError("Live position account changed during reconcile.")
        close_size_before_submit = position.size

        mids = await load_live_close_mids(client, positions=[position])
        mid_price = decimal_or_none(mids.get(position.coin)) or live_position_mark_price(
            position
        )
        if mid_price is None or mid_price <= ZERO:
            raise LiveTradingServiceError("Live close price is unavailable.", status_code=409)

        previous_status = account.status
        if account.status == "disabled":
            account.status = "exit_only"
            await session.flush()

        intent = build_live_close_position_intent(
            account=account,
            position=position,
            mid_price=mid_price,
            settings=settings,
            source_fill_prefix="manual-close",
            price_source="manual_live_close",
        )
        try:
            live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
            return await submit_live_trade_intent(
                session,
                account=account,
                intent=intent,
                settings=settings,
                client=live_client,
            )
        except LiveOrderSubmitError as exc:
            recovered = await recover_manual_live_close_after_submit_error(
                session,
                account=account,
                close_size_before_submit=close_size_before_submit,
                error=exc,
                info_client=client,
                intent=intent,
                position_id=position_id,
                settings=settings,
            )
            if recovered is not None:
                return recovered
            raise
        finally:
            if previous_status == "disabled":
                account.status = "disabled"
                await session.flush()
    finally:
        if client_created:
            await client.__aexit__(None, None, None)


async def recover_manual_live_close_after_submit_error(
    session: AsyncSession,
    *,
    account: TradingAccount,
    close_size_before_submit: Decimal,
    error: LiveOrderSubmitError,
    info_client: HyperliquidClient,
    intent: TradeIntent,
    position_id: UUID,
    settings: Settings,
) -> LiveOrderLifecycleResult | None:
    await reconcile_live_trading_account(
        session,
        account=account,
        settings=settings,
        info_client=info_client,
    )
    order = await load_live_order_by_client_order_id(
        session,
        client_order_id=intent.client_order_id,
    )
    current_position = await get_live_position(session, position_id=position_id)
    recovery_status = manual_live_close_recovery_status(
        close_size_before_submit=close_size_before_submit,
        current_position=current_position,
        order=order,
    )
    if order is None or recovery_status is None:
        return None

    if order.status in {"failed", "planned", "submitted"}:
        order.status = recovery_status
        order.error = None
        if recovery_status == "filled":
            order.filled_at = order.filled_at or datetime.now(UTC)
        order.raw_payload = merge_raw_payload(
            order.raw_payload,
            {
                "manualCloseRecovery": {
                    "message": str(error),
                    "status": recovery_status,
                }
            },
        )
        await session.flush()
    return LiveOrderLifecycleResult(order=order, exchange_result=None, submitted=True)


def manual_live_close_recovery_status(
    *,
    close_size_before_submit: Decimal,
    current_position: TradingPosition | None,
    order: TradingOrder | None,
) -> str | None:
    if order is None:
        return None
    if order.status in {"accepted", "filled", "partially_filled"}:
        return order.status
    if current_position is None:
        return "filled"
    if close_size_before_submit - current_position.size > POSITION_EPSILON:
        return "partially_filled"
    return None


async def load_live_order_by_client_order_id(
    session: AsyncSession,
    *,
    client_order_id: str,
) -> TradingOrder | None:
    return await session.scalar(
        select(TradingOrder).where(
            TradingOrder.account_type == "live",
            TradingOrder.client_order_id == client_order_id,
        )
    )


async def get_live_position(
    session: AsyncSession,
    *,
    position_id: UUID,
) -> TradingPosition | None:
    return await session.scalar(
        select(TradingPosition).where(
            TradingPosition.id == position_id,
            TradingPosition.account_type == "live",
        )
    )


async def load_live_close_mids(
    client: HyperliquidClient,
    *,
    positions: list[TradingPosition],
) -> dict[str, Any]:
    dexes = sorted({live_dex_from_coin(position.coin) for position in positions})
    mids_by_coin: dict[str, Any] = {}
    for dex in dexes:
        mids = await client.all_mids(dex=dex or None)
        for raw_coin, raw_price in mids.items():
            coin = str(raw_coin or "").strip()
            if not coin:
                continue
            mids_by_coin[live_coin_with_dex(coin=coin, dex=dex)] = raw_price
            if not dex:
                mids_by_coin[coin] = raw_price
    return mids_by_coin


async def load_live_position_for_update(
    session: AsyncSession,
    *,
    position_id: UUID,
) -> TradingPosition:
    position = await session.scalar(
        select(TradingPosition)
        .where(
            TradingPosition.id == position_id,
            TradingPosition.account_type == "live",
        )
        .with_for_update()
    )
    if position is None:
        raise LiveTradingServiceError("Live position was not found.", status_code=404)
    return position


async def load_live_account_for_update(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingAccount:
    account = await session.scalar(
        select(TradingAccount)
        .where(
            TradingAccount.key == account_key,
            TradingAccount.account_type == "live",
        )
        .with_for_update()
    )
    if account is None:
        raise LiveAccountNotFoundError()
    return account


async def submit_live_trade_intent(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
    client: HyperliquidLiveTradingClient | None = None,
) -> LiveOrderLifecycleResult:
    if account.account_type != "live":
        raise LiveOrderSubmitError("Only live accounts can submit live trade intents.")
    if intent.account_type != "live":
        raise LiveOrderSubmitError("Trade intent must target a live account.")

    live_client = client or HyperliquidLiveTradingClient(settings=settings)
    try:
        live_client.validate_account_order(account=account, intent=intent)
        if not intent.reduce_only:
            await validate_live_entry_state_guardrails(
                session,
                account=account,
                intent=intent,
                settings=settings,
            )
    except Exception as exc:
        raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

    order = await get_or_create_live_order(session, intent=intent)
    if order.status in TERMINAL_ORDER_STATUSES:
        return LiveOrderLifecycleResult(order=order, exchange_result=None, submitted=False)

    order.status = "submitted"
    order.submitted_at = datetime.now(UTC)
    order.error = None
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {"tradeIntent": trade_intent_payload(intent)},
    )
    await session.flush()

    try:
        result = await live_client.submit_order(account=account, intent=intent)
    except Exception as exc:
        order.status = "failed"
        order.error = str(exc) or exc.__class__.__name__
        order.raw_payload = merge_raw_payload(
            order.raw_payload,
            {"submitError": {"message": order.error, "type": exc.__class__.__name__}},
        )
        await session.flush()
        raise LiveOrderSubmitError(order.error) from exc

    apply_live_order_result(order, result, updated_at=datetime.now(UTC))
    await session.flush()
    return LiveOrderLifecycleResult(order=order, exchange_result=result, submitted=True)


async def validate_live_entry_state_guardrails(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    if settings.live_trading_max_account_open_notional_usd > ZERO:
        open_notional = await live_account_open_notional(session, account_key=account.key)
        if (
            open_notional + intent.notional_usd
            > settings.live_trading_max_account_open_notional_usd
        ):
            raise LiveOrderSubmitError("Live account open notional guard would be exceeded.")

    if settings.live_trading_max_open_positions > 0:
        open_coins = await live_account_open_coins(session, account_key=account.key)
        if (
            intent.coin not in open_coins
            and len(open_coins) >= settings.live_trading_max_open_positions
        ):
            raise LiveOrderSubmitError("Live account open position guard would be exceeded.")

    if settings.live_trading_max_daily_loss_usd > ZERO:
        daily_net_pnl = await live_account_daily_net_pnl(
            session,
            account_key=account.key,
            now=now,
        )
        if daily_net_pnl <= -settings.live_trading_max_daily_loss_usd:
            raise LiveOrderSubmitError("Live account daily loss guard is active.")

    if settings.live_trading_max_orders_per_minute > 0:
        recent_orders = await live_account_recent_order_count(
            session,
            account_key=account.key,
            now=now,
        )
        if recent_orders >= settings.live_trading_max_orders_per_minute:
            raise LiveOrderSubmitError("Live account order rate guard is active.")


async def live_account_open_notional(
    session: AsyncSession,
    *,
    account_key: str,
) -> Decimal:
    aggregate_value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.notional_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    aggregate_notional = decimal_or_none(aggregate_value) or ZERO
    if aggregate_notional > ZERO:
        return aggregate_notional
    source_value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.notional_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    return decimal_or_none(source_value) or ZERO


async def live_account_open_coins(
    session: AsyncSession,
    *,
    account_key: str,
) -> set[str]:
    aggregate_result = await session.scalars(
        select(TradingPosition.coin).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    aggregate_coins = {coin for coin in aggregate_result.all() if coin}
    if aggregate_coins:
        return aggregate_coins
    source_result = await session.scalars(
        select(TradingPosition.coin).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    return {coin for coin in source_result.all() if coin}


async def live_account_daily_net_pnl(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> Decimal:
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    value = await session.scalar(
        select(
            func.coalesce(
                func.sum(TradingFill.realized_pnl_usd - TradingFill.fee_usd),
                ZERO,
            )
        ).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.filled_at >= day_start,
        )
    )
    return decimal_or_none(value) or ZERO


async def live_account_recent_order_count(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> int:
    value = await session.scalar(
        select(func.count(TradingOrder.id)).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.submitted_at.is_not(None),
            TradingOrder.created_at >= now - timedelta(minutes=1),
        )
    )
    return int(value or 0)


async def get_or_create_live_order(
    session: AsyncSession,
    *,
    intent: TradeIntent,
) -> TradingOrder:
    existing = await session.scalar(
        select(TradingOrder)
        .where(TradingOrder.client_order_id == intent.client_order_id)
        .with_for_update()
    )
    if existing is not None:
        return existing

    order = TradingOrder(
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
        limit_price=intent.limit_price,
        filled_size=ZERO,
        filled_notional_usd=ZERO,
        fee_usd=ZERO,
        raw_payload={"tradeIntent": trade_intent_payload(intent)},
    )
    session.add(order)
    await session.flush()
    return order


def apply_live_order_result(
    order: TradingOrder,
    result: LiveOrderResult,
    *,
    updated_at: datetime,
) -> None:
    order.status = result.status
    order.exchange_order_id = result.exchange_order_id or order.exchange_order_id
    order.error = result.error
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {"exchangeResponse": result.raw_response},
    )
    if result.submitted_size is not None:
        order.requested_size = result.submitted_size
    if result.submitted_limit_price is not None:
        order.limit_price = result.submitted_limit_price
    if result.submitted_notional_usd is not None:
        order.requested_notional_usd = result.submitted_notional_usd
        order.margin_usd = margin_from_notional(result.submitted_notional_usd, order.leverage)
    if result.status in {"accepted", "filled"}:
        order.accepted_at = order.accepted_at or updated_at
    if result.status == "filled":
        order.filled_at = order.filled_at or updated_at
        if result.filled_size is not None:
            order.filled_size = result.filled_size
        if result.average_fill_price is not None:
            order.average_fill_price = result.average_fill_price
        if order.average_fill_price is not None and order.filled_size > ZERO:
            order.filled_notional_usd = order.average_fill_price * order.filled_size


async def reconcile_live_trading_account(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
) -> LiveReconciliationResult:
    if account.account_type != "live":
        raise LiveReconciliationError("Only live accounts can be reconciled.")
    user_address = live_account_user_address(account, settings=settings)
    reconciled_at = datetime.now(UTC)

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    try:
        updated_orders_from_status = await reconcile_live_order_statuses(
            session,
            account=account,
            user_address=user_address,
            client=client,
        )
        start_time_ms = await live_fill_reconciliation_start_time_ms(
            session,
            account_key=account.key,
            settings=settings,
            now=reconciled_at,
        )
        fills = await fetch_live_fills_by_time(
            client,
            user=user_address,
            start_time_ms=start_time_ms,
        )
        inserted_fills = await reconcile_live_fills(
            session,
            account=account,
            fills=fills,
        )
        updated_orders_from_fills = await update_live_orders_from_reconciled_fills(
            session,
            account_key=account.key,
        )
        perp_states = await fetch_live_perp_states(client, user_address=user_address)
        spot_state = await fetch_live_spot_state(client, user_address=user_address)
        user_abstraction = await fetch_live_user_abstraction(
            client,
            user_address=user_address,
        )
        position_result = await reconcile_live_positions(
            session,
            account=account,
            perp_states=perp_states,
            reconciled_at=reconciled_at,
        )
    except LiveTradingServiceError:
        raise
    except Exception as exc:
        raise LiveReconciliationError(
            f"Live reconciliation failed: {exc.__class__.__name__}.",
            status_code=502,
        ) from exc
    finally:
        if client_created:
            await client.__aexit__(None, None, None)

    update_live_account_from_state(
        account,
        perp_states=perp_states,
        spot_state=spot_state,
        user_abstraction=user_abstraction,
        reconciled_at=reconciled_at,
        settings=settings,
    )
    await session.flush()
    return LiveReconciliationResult(
        account_key=account.key,
        user_address=user_address,
        fetched_fills=len(fills),
        inserted_fills=inserted_fills,
        updated_orders=updated_orders_from_status + updated_orders_from_fills,
        open_positions=position_result.open_positions,
        removed_positions=position_result.removed_positions,
        reconciled_at=reconciled_at,
    )


async def reconcile_live_order_statuses(
    session: AsyncSession,
    *,
    account: TradingAccount,
    user_address: str,
    client: HyperliquidClient,
) -> int:
    result = await session.scalars(
        select(TradingOrder)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
        .order_by(TradingOrder.created_at.asc())
    )
    orders = list(result.all())
    updated = 0
    for order in orders:
        lookup_id: int | str | None = parse_exchange_order_id(order.exchange_order_id)
        if lookup_id is None:
            lookup_id = order.client_order_id
        try:
            status_response = await client.order_status(user=user_address, oid=lookup_id)
        except Exception as exc:
            order.raw_payload = merge_raw_payload(
                order.raw_payload,
                {"orderStatusError": {"message": str(exc), "type": exc.__class__.__name__}},
            )
            updated += 1
            continue
        if apply_order_status_response(order, status_response):
            updated += 1
    await session.flush()
    return updated


async def fetch_live_fills_by_time(
    client: HyperliquidClient,
    *,
    user: str,
    start_time_ms: int,
    max_pages: int = MAX_LIVE_FILL_RECONCILIATION_PAGES,
) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    next_start_time_ms = start_time_ms
    for _ in range(max_pages):
        batch = await client.user_fills_by_time(
            user=user,
            start_time_ms=next_start_time_ms,
            aggregate_by_time=False,
        )
        if not batch:
            break
        fills.extend(batch)
        timestamps = [
            int(timestamp)
            for fill in batch
            for timestamp in [decimal_or_none(fill.get("time") or fill.get("timestamp"))]
            if timestamp is not None
        ]
        if len(batch) < 500 or not timestamps:
            break
        next_start_time_ms = max(timestamps) + 1
    return fills


async def fetch_live_perp_states(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> list[LivePerpState]:
    states = [
        LivePerpState(
            dex="",
            payload=await client.clearinghouse_state(user=user_address),
        )
    ]
    for dex in await fetch_live_perp_dex_names(client):
        try:
            payload = await client.clearinghouse_state(user=user_address, dex=dex)
        except Exception:
            continue
        states.append(LivePerpState(dex=dex, payload=payload))
    return states


async def fetch_live_perp_dex_names(client: HyperliquidClient) -> list[str]:
    try:
        payload = await client.perp_dexs()
    except Exception:
        return []
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names))


async def fetch_live_spot_state(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> dict[str, Any]:
    try:
        return await client.spot_clearinghouse_state(user=user_address)
    except Exception as exc:
        return {"error": {"message": str(exc), "type": exc.__class__.__name__}}


async def fetch_live_user_abstraction(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> Any:
    try:
        return await client.user_abstraction(user=user_address)
    except Exception as exc:
        return {"error": {"message": str(exc), "type": exc.__class__.__name__}}


def apply_order_status_response(order: TradingOrder, response: dict[str, Any]) -> bool:
    before_status = order.status
    before_exchange_id = order.exchange_order_id
    order.raw_payload = merge_raw_payload(order.raw_payload, {"orderStatusResponse": response})
    if response.get("status") != "order":
        return before_status != order.status or before_exchange_id != order.exchange_order_id

    payload = response.get("order")
    if not isinstance(payload, dict):
        return False
    exchange_order = payload.get("order")
    if isinstance(exchange_order, dict):
        order.exchange_order_id = (
            string_or_none(exchange_order.get("oid")) or order.exchange_order_id
        )
    mapped_status = map_exchange_order_status(string_or_none(payload.get("status")))
    status_time = ms_to_datetime(decimal_or_none(payload.get("statusTimestamp")))
    if mapped_status is not None:
        order.status = mapped_status
    if mapped_status in {"accepted", "filled"} and status_time is not None:
        order.accepted_at = order.accepted_at or status_time
    if mapped_status == "filled" and status_time is not None:
        order.filled_at = order.filled_at or status_time
    return before_status != order.status or before_exchange_id != order.exchange_order_id


async def reconcile_live_fills(
    session: AsyncSession,
    *,
    account: TradingAccount,
    fills: list[dict[str, Any]],
) -> int:
    orders = await load_live_orders_for_fill_matching(session, account_key=account.key)
    orders_by_oid = {order.exchange_order_id: order for order in orders if order.exchange_order_id}
    orders_by_cloid = {order.client_order_id: order for order in orders}
    inserted = 0
    for fill in fills:
        parsed = parse_live_fill(fill, account_key=account.key)
        if parsed is None:
            continue
        matched_order = match_live_fill_order(
            parsed,
            orders_by_oid=orders_by_oid,
            orders_by_cloid=orders_by_cloid,
        )
        row = live_fill_row(
            parsed,
            account=account,
            order=matched_order,
        )
        stmt = insert(TradingFill).values(**row)
        stmt = stmt.on_conflict_do_nothing(constraint="ux_trading_fills_exchange_fill_id")
        result = await session.execute(stmt)
        inserted_row = int(result.rowcount or 0) > 0
        inserted += int(inserted_row)
        if inserted_row:
            await apply_live_source_fill_to_position(
                session,
                account=account,
                order=matched_order,
                parsed_fill=parsed,
            )
    await session.flush()
    return inserted


async def load_live_orders_for_fill_matching(
    session: AsyncSession,
    *,
    account_key: str,
) -> list[TradingOrder]:
    result = await session.scalars(
        select(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
        )
    )
    return list(result.all())


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


async def load_live_closed_trades(
    session: AsyncSession,
    *,
    limit: int = 100,
    fill_scan_limit: int = 5000,
) -> list[LiveClosedTrade]:
    fill_result = await session.scalars(
        select(TradingFill)
        .where(
            TradingFill.account_type == "live",
            TradingFill.source_wallet != "",
            TradingFill.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
        .order_by(TradingFill.filled_at.desc(), TradingFill.created_at.desc())
        .limit(fill_scan_limit)
    )
    fills = list(fill_result.all())
    return live_closed_trades_from_fills(fills, limit=limit)


def live_closed_trades_from_fills(
    fills: list[TradingFill],
    *,
    limit: int = 100,
) -> list[LiveClosedTrade]:
    open_trades: dict[tuple[str, str, str, str], LiveTradeAccumulator] = {}
    closed_trades: list[LiveClosedTrade] = []
    for fill in sorted(fills, key=live_fill_chronology_key):
        if fill.source_wallet == LIVE_EXCHANGE_SOURCE:
            continue
        key = live_fill_trade_key(fill)
        if live_fill_is_open(fill):
            trade = open_trades.get(key) or live_trade_accumulator(fill)
            fill_size = decimal_or_none(fill.size) or ZERO
            trade.opened_size += fill_size
            trade.remaining_size += fill_size
            trade.entry_notional_usd += decimal_or_none(fill.notional_usd) or ZERO
            trade.fee_usd += decimal_or_none(fill.fee_usd) or ZERO
            trade.open_fill_count += 1
            trade.opened_at = min(trade.opened_at, fill.filled_at)
            open_trades[key] = trade
            continue
        if not live_fill_is_close(fill):
            continue
        trade = open_trades.get(key)
        if trade is None:
            continue
        fill_size = decimal_or_none(fill.size) or ZERO
        close_size = min(fill_size, trade.remaining_size)
        if close_size <= ZERO:
            continue
        fill_ratio = close_size / fill_size if fill_size > ZERO else ZERO
        trade.remaining_size = max(trade.remaining_size - close_size, ZERO)
        trade.closed_size += close_size
        trade.exit_notional_usd += (decimal_or_none(fill.notional_usd) or ZERO) * fill_ratio
        trade.realized_pnl_usd += (decimal_or_none(fill.realized_pnl_usd) or ZERO) * fill_ratio
        trade.fee_usd += (decimal_or_none(fill.fee_usd) or ZERO) * fill_ratio
        trade.close_fill_count += 1
        trade.closed_at = fill.filled_at
        trade.last_close_fill_id = str(fill.id)
        if trade.remaining_size <= POSITION_EPSILON:
            closed_trades.append(finish_live_closed_trade(trade))
            del open_trades[key]
    return sorted(closed_trades, key=lambda trade: trade.closed_at, reverse=True)[:limit]


def live_trade_accumulator(fill: TradingFill) -> LiveTradeAccumulator:
    return LiveTradeAccumulator(
        account_key=fill.account_key,
        source_wallet=fill.source_wallet,
        source_label=None,
        coin=fill.coin,
        side=fill.side,
        opened_at=fill.filled_at,
        closed_at=fill.filled_at,
        last_close_fill_id=str(fill.id),
    )


def finish_live_closed_trade(trade: LiveTradeAccumulator) -> LiveClosedTrade:
    duration_ms = int((trade.closed_at - trade.opened_at).total_seconds() * 1000)
    return LiveClosedTrade(
        id=(
            f"live-closed:{trade.account_key}:{trade.source_wallet}:"
            f"{trade.coin}:{trade.side}:{trade.last_close_fill_id}"
        ),
        account_key=trade.account_key,
        source_wallet=trade.source_wallet,
        source_label=trade.source_label,
        coin=trade.coin,
        side=trade.side,
        entry_price=(
            trade.entry_notional_usd / trade.opened_size
            if trade.opened_size > ZERO
            else None
        ),
        exit_price=(
            trade.exit_notional_usd / trade.closed_size
            if trade.closed_size > ZERO
            else None
        ),
        size=trade.closed_size,
        entry_notional_usd=trade.entry_notional_usd,
        exit_notional_usd=trade.exit_notional_usd,
        fee_usd=trade.fee_usd,
        realized_pnl_usd=trade.realized_pnl_usd,
        net_pnl_usd=trade.realized_pnl_usd - trade.fee_usd,
        opened_at=trade.opened_at,
        closed_at=trade.closed_at,
        duration_ms=max(duration_ms, 0),
        open_fill_count=trade.open_fill_count,
        close_fill_count=trade.close_fill_count,
    )


def live_fill_chronology_key(fill: TradingFill) -> tuple[datetime, datetime, str, int]:
    return (
        fill.filled_at,
        fill.created_at or fill.filled_at,
        fill.source_fill_id or "",
        fill.sequence_index or 0,
    )


def live_fill_trade_key(fill: TradingFill) -> tuple[str, str, str, str]:
    return (fill.account_key, fill.source_wallet.lower(), fill.coin, fill.side)


def live_fill_is_open(fill: TradingFill) -> bool:
    return fill.action in {"open", "add", "flip_open"}


def live_fill_is_close(fill: TradingFill) -> bool:
    return "close" in fill.action or "reduce" in fill.action


async def apply_live_source_fill_to_position(
    session: AsyncSession,
    *,
    account: TradingAccount,
    order: TradingOrder | None,
    parsed_fill: dict[str, Any],
) -> None:
    fee_usd = parsed_fill["fee_usd"]
    realized_pnl_usd = parsed_fill["realized_pnl_usd"]
    account.fee_usd += fee_usd
    account.realized_pnl_usd += realized_pnl_usd
    if order is None or order.source_wallet == LIVE_EXCHANGE_SOURCE:
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
                margin_usd=margin_delta,
                realized_pnl_usd=ZERO,
                fee_usd=fee_usd,
                raw_payload={"source": "live_fill"},
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
    position.margin_usd += margin_delta
    position.leverage = effective_leverage(
        notional_usd=position.notional_usd,
        margin_usd=position.margin_usd,
        fallback=order.leverage or Decimal("1"),
    )
    position.fee_usd += fee_usd
    position.last_reconciled_at = filled_at


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


async def update_live_orders_from_reconciled_fills(
    session: AsyncSession,
    *,
    account_key: str,
) -> int:
    order_result = await session.scalars(
        select(TradingOrder).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
        )
    )
    orders = {order.id: order for order in order_result.all()}
    if not orders:
        return 0

    fill_result = await session.execute(
        select(
            TradingFill.order_id,
            func.sum(TradingFill.size),
            func.sum(TradingFill.notional_usd),
            func.sum(TradingFill.fee_usd),
            func.max(TradingFill.filled_at),
        )
        .where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.order_id.in_(list(orders)),
        )
        .group_by(TradingFill.order_id)
    )
    updated = 0
    for order_id, filled_size, filled_notional, fee_usd, filled_at in fill_result.all():
        order = orders.get(order_id)
        if order is None:
            continue
        size = filled_size or ZERO
        notional = filled_notional or ZERO
        before_status = order.status
        order.filled_size = size
        order.filled_notional_usd = notional
        order.fee_usd = fee_usd or ZERO
        if size > ZERO:
            order.average_fill_price = notional / size
            order.filled_at = filled_at or order.filled_at
            if size >= order.requested_size:
                order.status = "filled"
            elif order.status != "filled":
                order.status = "partially_filled"
        if order.status != before_status or size > ZERO:
            updated += 1
    await session.flush()
    return updated


@dataclass(frozen=True)
class LivePositionReconciliationResult:
    open_positions: int
    removed_positions: int


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
    perp_states: list[LivePerpState],
    reconciled_at: datetime,
) -> LivePositionReconciliationResult:
    snapshots = [
        snapshot
        for perp_state in perp_states
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
    existing = {position.coin: position for position in existing_result.all()}
    exchange_positions: list[TradingPosition] = []
    for snapshot in snapshots:
        position = existing.get(snapshot.coin)
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
                margin_usd=snapshot.margin_usd,
                realized_pnl_usd=ZERO,
                fee_usd=ZERO,
                raw_payload=snapshot.raw_payload,
                opened_at=reconciled_at,
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
        position.margin_usd = snapshot.margin_usd
        position.raw_payload = snapshot.raw_payload
        position.last_reconciled_at = reconciled_at
        exchange_positions.append(position)

    active_coins = {snapshot.coin for snapshot in snapshots}
    delete_stmt = delete(TradingPosition).where(
        TradingPosition.account_key == account.key,
        TradingPosition.account_type == "live",
        TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
    )
    if active_coins:
        delete_stmt = delete_stmt.where(~TradingPosition.coin.in_(active_coins))
    delete_result = await session.execute(delete_stmt)

    source_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    source_sync_result = sync_live_source_positions_from_exchange_positions(
        source_positions=list(source_result.all()),
        exchange_positions=exchange_positions,
        reconciled_at=reconciled_at,
    )
    for stale_position in source_sync_result.stale_positions:
        await session.delete(stale_position)
    await session.flush()
    return LivePositionReconciliationResult(
        open_positions=len(active_coins),
        removed_positions=int(delete_result.rowcount or 0)
        + len(source_sync_result.stale_positions),
    )


def normalized_live_asset_positions(raw_positions: Any, *, dex: str) -> list[dict[str, Any]]:
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
        normalized_coin = live_coin_with_dex(coin=coin, dex=dex)
        if normalized_coin == coin and not dex:
            positions.append(item)
            continue

        normalized_position = {**raw_position, "coin": normalized_coin}
        normalized_item = {**item, "position": normalized_position}
        if dex:
            normalized_item["dex"] = dex
        positions.append(normalized_item)
    return positions


def live_coin_with_dex(*, coin: str, dex: str) -> str:
    resolved_coin = str(coin or "").strip()
    resolved_dex = str(dex or "").strip()
    if not resolved_coin or not resolved_dex or ":" in resolved_coin:
        return resolved_coin
    return f"{resolved_dex}:{resolved_coin}"


def live_dex_from_coin(coin: str) -> str:
    value = str(coin or "").strip()
    if ":" not in value:
        return ""
    return value.split(":", maxsplit=1)[0].strip()


async def live_fill_reconciliation_start_time_ms(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
    now: datetime,
) -> int:
    latest_fill_at = await session.scalar(
        select(func.max(TradingFill.filled_at)).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
        )
    )
    if latest_fill_at is not None:
        start_at = latest_fill_at - timedelta(minutes=5)
    else:
        start_at = now - timedelta(minutes=settings.live_trading_reconciliation_lookback_minutes)
    return int(start_at.timestamp() * 1000)


def build_testnet_live_trade_intent(
    *,
    account: TradingAccount,
    coin: str,
    side: str,
    notional_usd: Decimal,
    limit_price: Decimal,
    leverage: Decimal,
    reduce_only: bool,
    source_fill_id: str | None = None,
    created_at: datetime | None = None,
) -> TradeIntent:
    if side not in {"long", "short"}:
        raise LiveOrderSubmitError("Side must be long or short.")
    if notional_usd <= ZERO:
        raise LiveOrderSubmitError("Order notional must be positive.")
    if limit_price <= ZERO:
        raise LiveOrderSubmitError("Limit price must be positive.")
    if leverage <= ZERO:
        raise LiveOrderSubmitError("Leverage must be positive.")
    now = created_at or datetime.now(UTC)
    size = notional_usd / limit_price
    return build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=LIVE_MANUAL_TEST_SOURCE,
        source_fill_id=source_fill_id or f"testnet-manual-{uuid4().hex}",
        sequence_index=0,
        coin=coin,
        action="close" if reduce_only else "open",
        side=side,
        size=size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        limit_price=limit_price,
        source_price=limit_price,
        observed_price=limit_price,
        price_drift_bps=ZERO,
        price_source="manual_testnet",
        allocation_pct=None,
        allocation_usd=None,
        source_perp_equity_usd=None,
        source_exposure_pct=None,
        created_at=now,
    )


def build_live_close_position_intent(
    *,
    account: TradingAccount,
    position: TradingPosition,
    mid_price: Decimal,
    settings: Settings,
    source_fill_prefix: str = "close-all",
    price_source: str = "live_close_all",
) -> TradeIntent:
    now = datetime.now(UTC)
    limit_price = close_limit_price(
        mid_price=mid_price,
        side=position.side,
        max_slippage_bps=settings.live_trading_max_slippage_bps,
    )
    notional_usd = limit_price * position.size
    leverage = position.leverage if position.leverage > ZERO else Decimal("1")
    return build_copy_trade_intent(
        account_key=account.key,
        account_type="live",
        source_wallet=position.source_wallet,
        source_fill_id=f"{source_fill_prefix}-{position.coin}-{uuid4().hex}",
        sequence_index=0,
        coin=position.coin,
        action="close",
        side=position.side,
        size=position.size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        limit_price=limit_price,
        source_price=mid_price,
        observed_price=mid_price,
        price_drift_bps=ZERO,
        price_source=price_source,
        allocation_pct=None,
        allocation_usd=None,
        source_perp_equity_usd=None,
        source_exposure_pct=None,
        created_at=now,
    )


def close_limit_price(
    *,
    mid_price: Decimal,
    side: str,
    max_slippage_bps: Decimal,
) -> Decimal:
    slippage_ratio = max_slippage_bps / Decimal("10000")
    if side == "long":
        return mid_price * (Decimal("1") - slippage_ratio)
    return mid_price * (Decimal("1") + slippage_ratio)


def live_position_mark_price(position: TradingPosition) -> Decimal | None:
    raw_position = live_position_raw_position(position)
    position_value = (
        decimal_or_none(raw_position.get("positionValue"))
        if raw_position is not None
        else None
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


def update_live_account_from_state(
    account: TradingAccount,
    *,
    perp_states: list[LivePerpState],
    spot_state: dict[str, Any] | None = None,
    user_abstraction: Any = None,
    reconciled_at: datetime,
    settings: Settings,
) -> None:
    perp_equity = ZERO
    perp_withdrawable = ZERO
    state_time: Any = None
    state_summaries: list[dict[str, Any]] = []
    for perp_state in perp_states:
        payload = perp_state.payload
        margin_summary = payload.get("marginSummary")
        if not isinstance(margin_summary, dict):
            margin_summary = payload.get("crossMarginSummary")
        state_equity = (
            decimal_or_none(margin_summary.get("accountValue"))
            if isinstance(margin_summary, dict)
            else ZERO
        )
        state_withdrawable = decimal_or_none(payload.get("withdrawable")) or ZERO
        perp_equity += state_equity or ZERO
        perp_withdrawable += state_withdrawable
        state_time = max_optional_numeric(state_time, payload.get("time"))
        state_summaries.append(
            {
                "accountValue": str(state_equity or ZERO),
                "dex": perp_state.dex or "default",
                "marginSummary": margin_summary if isinstance(margin_summary, dict) else None,
                "withdrawable": str(state_withdrawable),
            }
        )

    spot_usdc_total = live_spot_usdc_total(spot_state or {})
    spot_usdc_available = live_spot_usdc_available(spot_state or {})
    capital_mode = live_capital_mode(settings)
    if capital_mode == LIVE_CAPITAL_MODE_UNIFIED:
        account.equity_usd = spot_usdc_total
        account.cash_balance_usd = spot_usdc_available
        tradable_equity = spot_usdc_available
    else:
        account.equity_usd = perp_equity + spot_usdc_total
        account.cash_balance_usd = perp_withdrawable + spot_usdc_available
        tradable_equity = perp_equity
    account.last_reconciled_at = reconciled_at
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {
            "lastReconciliation": {
                "capitalMode": capital_mode,
                "perpEquityUsd": str(perp_equity),
                "perpStates": state_summaries,
                "perpWithdrawableUsd": str(perp_withdrawable),
                "spotState": spot_state,
                "spotUsdcAvailableUsd": str(spot_usdc_available),
                "spotUsdcTotalUsd": str(spot_usdc_total),
                "tradableEquityUsd": str(tradable_equity),
                "time": state_time,
                "unifiedAvailableUsd": str(spot_usdc_available),
                "unifiedEquityUsd": str(spot_usdc_total),
                "userAbstraction": normalize_user_abstraction(user_abstraction),
                "userAbstractionRaw": user_abstraction,
            }
        },
    )


def live_spot_usdc_total(spot_state: dict[str, Any]) -> Decimal:
    balances = spot_state.get("balances")
    if not isinstance(balances, list):
        return ZERO
    total = ZERO
    for item in balances:
        if not isinstance(item, dict):
            continue
        if str(item.get("coin") or "").upper() == "USDC":
            total += decimal_or_none(item.get("total")) or ZERO
    return total


def live_spot_usdc_available(spot_state: dict[str, Any]) -> Decimal:
    values = spot_state.get("tokenToAvailableAfterMaintenance")
    if isinstance(values, list):
        total = ZERO
        matched_usdc = False
        for item in values:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            token_id = decimal_or_none(item[0])
            if token_id == ZERO:
                matched_usdc = True
                total += decimal_or_none(item[1]) or ZERO
        if matched_usdc:
            return total

    balances = spot_state.get("balances")
    if not isinstance(balances, list):
        return ZERO
    total = ZERO
    for item in balances:
        if not isinstance(item, dict):
            continue
        if str(item.get("coin") or "").upper() != "USDC":
            continue
        balance_total = decimal_or_none(item.get("total")) or ZERO
        hold = decimal_or_none(item.get("hold")) or ZERO
        total += max(balance_total - hold, ZERO)
    return total


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


def live_fill_row(
    parsed_fill: dict[str, Any],
    *,
    account: TradingAccount,
    order: TradingOrder | None,
) -> dict[str, Any]:
    return {
        "order_id": order.id if order is not None else None,
        "account_key": account.key,
        "account_type": "live",
        "source_wallet": order.source_wallet if order is not None else LIVE_EXCHANGE_SOURCE,
        "source_fill_id": order.source_fill_id
        if order is not None
        else parsed_fill["exchange_fill_id"],
        "sequence_index": order.sequence_index if order is not None else None,
        "exchange_fill_id": parsed_fill["exchange_fill_id"],
        "coin": parsed_fill["coin"],
        "action": order.action if order is not None else parsed_fill["action"],
        "side": order.side if order is not None else parsed_fill["side"],
        "price": parsed_fill["price"],
        "size": parsed_fill["size"],
        "notional_usd": parsed_fill["notional_usd"],
        "fee_usd": parsed_fill["fee_usd"],
        "realized_pnl_usd": parsed_fill["realized_pnl_usd"],
        "raw_payload": parsed_fill["raw_payload"],
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
    leverage = parse_position_leverage(position.get("leverage"))
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
        margin_usd=margin,
        raw_payload=payload,
    )


def parse_position_leverage(value: Any) -> Decimal:
    if isinstance(value, dict):
        parsed = decimal_or_none(value.get("value"))
        return parsed if parsed is not None and parsed > ZERO else Decimal("1")
    parsed = decimal_or_none(value)
    return parsed if parsed is not None and parsed > ZERO else Decimal("1")


def infer_position_side(fill: dict[str, Any]) -> str | None:
    direction = str(fill.get("dir") or "").casefold()
    if "long" in direction:
        return "long"
    if "short" in direction:
        return "short"
    fill_side = str(fill.get("side") or "").casefold()
    if fill_side in {"b", "buy"}:
        return "long"
    if fill_side in {"a", "sell"}:
        return "short"
    return None


def infer_fill_action(fill: dict[str, Any]) -> str:
    direction = str(fill.get("dir") or "").casefold()
    if "close" in direction:
        return "close"
    return "open"


def map_exchange_order_status(status: str | None) -> str | None:
    if status is None:
        return None
    normalized = status.strip().casefold()
    if normalized == "open":
        return "accepted"
    if normalized == "filled":
        return "filled"
    if normalized.endswith("rejected") or normalized == "rejected":
        return "rejected"
    if normalized.endswith("canceled") or normalized == "canceled":
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


def live_account_user_address(account: TradingAccount, *, settings: Settings) -> str:
    user_address = account.wallet_address or settings.hyperliquid_wallet_address
    if not user_address:
        raise LiveReconciliationError(
            "Live account requires wallet_address or HYPERLIQUID_WALLET_ADDRESS."
        )
    return user_address.lower()


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
    except (InvalidOperation, TypeError, ValueError):
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
