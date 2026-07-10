import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import blake2s
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    TradingAccount,
    TradingCloseAllItem,
    TradingCloseAllOperation,
    TradingFill,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
    TradingReconciliationRun,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveOrderRejectedError,
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
    LiveOrderResult,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_execution_state import (
    RECONCILABLE_ORDER_STATUSES,
    RECOVERABLE_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    load_live_order_dispatch,
    mark_live_order_dispatch_completed,
    mark_live_order_dispatching,
    mark_live_order_failed,
    mark_live_order_uncertain,
    prepare_live_order_dispatch,
    trade_intent_from_order,
)
from app.services.trading_core import (
    TradeIntent,
    build_copy_trade_intent,
    margin_from_notional,
)
from app.services.trading_safety_service import (
    apply_live_account_status,
    cancel_unsent_live_entries,
    record_audit_log,
    record_risk_event,
    trip_live_account_risk,
)

ZERO = Decimal("0")
POSITION_EPSILON = Decimal("0.000000000001")
LIVE_EXCHANGE_SOURCE = "__exchange__"
LIVE_MANUAL_TEST_SOURCE = "__manual_testnet__"
ACTIVE_ORDER_STATUSES = RECONCILABLE_ORDER_STATUSES
MAX_LIVE_FILL_RECONCILIATION_PAGES = 10
MAX_LIVE_FILL_HISTORY = 10_000
LIVE_RECONCILIATION_RUN_RETENTION_DAYS = 30
LIVE_ACCOUNT_KEY_MAX_LENGTH = 64
LIVE_CAPITAL_MODE_UNIFIED = "unified"
LIVE_CAPITAL_MODE_STANDARD_PER_DEX = "standard_per_dex"
LIVE_CAPITAL_MODES = {LIVE_CAPITAL_MODE_UNIFIED, LIVE_CAPITAL_MODE_STANDARD_PER_DEX}
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-f0-9]{40}$")
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
    run_id: UUID | None = None
    fetched_fills: int = 0
    inserted_fills: int = 0
    updated_orders: int = 0
    open_positions: int = 0
    removed_positions: int = 0
    status: str = "complete"
    incomplete_components: tuple[str, ...] = ()
    component_errors: dict[str, str] = field(default_factory=dict)
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class LiveOrderLifecycleResult:
    order: TradingOrder
    exchange_result: LiveOrderResult | None
    submitted: bool


@dataclass(frozen=True)
class LiveDispatchRecoveryResult:
    inspected: int = 0
    recovered: int = 0
    dispatched: int = 0
    uncertain: int = 0
    failed: int = 0


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
    operation_id: UUID
    operation_status: str
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
    complete: bool = True
    error: str | None = None


@dataclass(frozen=True)
class LivePerpSnapshot:
    states: tuple[LivePerpState, ...]
    requested_dexes: tuple[str, ...]
    catalog_complete: bool = True
    catalog_error: str | None = None

    @property
    def complete(self) -> bool:
        return self.catalog_complete and all(state.complete for state in self.states)

    @property
    def incomplete_dexes(self) -> tuple[str, ...]:
        return tuple(state.dex for state in self.states if not state.complete)

    @property
    def component_errors(self) -> dict[str, str]:
        errors = {
            f"perp:{state.dex or 'default'}": state.error for state in self.states if state.error
        }
        if self.catalog_error:
            errors["perp_catalog"] = self.catalog_error
        return errors


@dataclass(frozen=True)
class LiveFillFetchResult:
    fills: tuple[dict[str, Any], ...]
    complete: bool
    pages: int
    next_start_time_ms: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class LiveOrderReconciliationResult:
    updated_orders: int = 0
    unresolved_order_ids: tuple[UUID, ...] = ()
    errors: dict[str, str] = field(default_factory=dict)


def validate_live_trading_configuration(settings: Settings) -> None:
    try:
        client = HyperliquidLiveTradingClient(settings=settings)
        client.validate_live_configuration()
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


def account_last_reconciliation_attempt(account: TradingAccount) -> dict[str, Any]:
    payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    attempt = payload.get("lastReconciliationAttempt")
    if isinstance(attempt, dict):
        return attempt
    return account_last_reconciliation(account)


def live_reconciliation_status(account: TradingAccount) -> str:
    attempt = account_last_reconciliation_attempt(account)
    status = str(attempt.get("status") or "").strip()
    if status in {"complete", "partial", "failed"}:
        return status
    return "complete" if account.last_reconciled_at is not None else "never"


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


def validate_live_account_can_start(
    account: TradingAccount,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> None:
    reconciliation_status = live_reconciliation_status(account)
    if reconciliation_status != "complete":
        raise LiveTradingServiceError(
            "Live account requires a complete exchange reconciliation before it can start.",
            status_code=409,
        )
    if not live_reconciliation_is_fresh(account, settings=settings, now=now):
        raise LiveTradingServiceError(
            "Live account requires a fresh exchange reconciliation before it can start.",
            status_code=409,
        )
    last_reconciliation = account_last_reconciliation(account)
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED and user_abstraction_is_standard(
        last_reconciliation.get("userAbstraction")
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


def live_reconciliation_is_fresh(
    account: TradingAccount,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> bool:
    reconciled_at = account.last_reconciled_at
    if reconciled_at is None:
        return False
    if reconciled_at.tzinfo is None:
        reconciled_at = reconciled_at.replace(tzinfo=UTC)
    age = (now or datetime.now(UTC)) - reconciled_at.astimezone(UTC)
    return (
        timedelta(0)
        <= age
        <= timedelta(seconds=settings.live_trading_reconciliation_max_snapshot_age_seconds)
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
    if resolved_vault_address is not None:
        validate_evm_address(resolved_vault_address, field_name="vault address")
    existing_route = await find_existing_live_account_for_route(
        session,
        network=settings.hyperliquid_network,
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
    existing = await session.scalar(select(TradingAccount).where(TradingAccount.key == account_key))
    if existing is not None:
        if existing.account_type == "live" and existing.archived_at is not None:
            existing.label = account_label
            existing.network = settings.hyperliquid_network
            existing.wallet_address = resolved_wallet_address
            existing.vault_address = resolved_vault_address
            existing.archived_at = None
            apply_live_account_status(
                existing,
                status="disabled",
                reason="restored_archived_account",
            )
            await session.flush()
            return existing
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
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    actor: str = "dashboard",
) -> None:
    try:
        async with job_lock(
            session,
            key=f"live_close_all:{account_key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account_key}",
                ttl_seconds=120,
            ):
                account = await load_live_account_for_update(session, account_key=account_key)
                validate_live_account_identity(account, settings=settings)
                await run_live_trading_account_reconciliation(
                    session,
                    account=account,
                    settings=settings,
                    info_client=info_client,
                )
                account = await load_live_account_for_update(session, account_key=account_key)
                await validate_live_account_can_be_removed(
                    session,
                    account=account,
                    settings=settings,
                )
                account.archived_at = datetime.now(UTC)
                apply_live_account_status(
                    account,
                    status="disabled",
                    reason="archived_by_dashboard",
                )
                record_audit_log(
                    session,
                    actor=actor,
                    action="live_account.archive",
                    payload={
                        "accountKey": account.key,
                        "network": account.network,
                        "lifecycleVersion": account.lifecycle_version,
                    },
                )
                await session.flush()
    except JobLockAlreadyHeldError as exc:
        raise LiveAccountDeleteError(
            "Live execution or close-all is running for this account.",
            status_code=409,
        ) from exc


async def find_existing_live_account_for_route(
    session: AsyncSession,
    *,
    network: str,
    wallet_address: str,
    vault_address: str | None,
    include_config_wallet_fallback: bool,
) -> TradingAccount | None:
    wallet_conditions = [TradingAccount.wallet_address == wallet_address]
    if include_config_wallet_fallback:
        wallet_conditions.append(TradingAccount.wallet_address.is_(None))
    query = select(TradingAccount).where(
        TradingAccount.account_type == "live",
        TradingAccount.network == network,
        TradingAccount.archived_at.is_(None),
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
    validate_evm_address(resolved, field_name="wallet address")
    return resolved


def validate_evm_address(address: str, *, field_name: str) -> None:
    if not EVM_ADDRESS_PATTERN.fullmatch(address):
        raise LiveAccountCreateError(
            f"Live account {field_name} must be a 20-byte 0x-prefixed EVM address."
        )


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
    if status != "exit_only":
        raise LiveTradingServiceError(
            "Use the guarded start or disable lifecycle operation for this transition.",
            status_code=409,
        )
    account = await load_live_account_for_update(session, account_key=account_key)
    apply_live_account_status(
        account,
        status="exit_only",
        reason="legacy_status_transition",
    )
    await session.flush()
    return account


async def start_live_trading_account(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    actor: str = "dashboard",
) -> TradingAccount:
    validate_live_trading_configuration(settings)
    try:
        async with job_lock(
            session,
            key=f"live_close_all:{account_key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account_key}",
                ttl_seconds=300,
            ):
                account = await load_live_account_for_update(
                    session,
                    account_key=account_key,
                )
                validate_live_account_identity(account, settings=settings)
                result = await run_live_trading_account_reconciliation(
                    session,
                    account=account,
                    settings=settings,
                    info_client=info_client,
                )
                if result.status != "complete":
                    raise LiveTradingServiceError(
                        "Live account start requires complete exchange reconciliation.",
                        status_code=409,
                    )
                start_lifecycle_version = account.lifecycle_version

        async with job_lock(
            session,
            key=f"live_close_all:{account_key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account_key}",
                ttl_seconds=300,
            ):
                account = await load_live_account_for_update(
                    session,
                    account_key=account_key,
                )
                if account.lifecycle_version != start_lifecycle_version:
                    raise LiveTradingServiceError(
                        "Live account lifecycle changed during start reconciliation. "
                        "Review the current state before retrying Start.",
                        status_code=409,
                    )
                validate_live_account_identity(account, settings=settings)
                validate_live_account_can_start(account, settings=settings)
                if await live_account_has_incomplete_close_operation(
                    session,
                    account_key=account.key,
                ):
                    raise LiveTradingServiceError(
                        "Resolve the active close-all operation before starting this account.",
                        status_code=409,
                    )
                apply_live_account_status(
                    account,
                    status="enabled",
                    reason="start_after_complete_reconciliation",
                )
                record_audit_log(
                    session,
                    actor=actor,
                    action="live_account.start",
                    payload={
                        "accountKey": account.key,
                        "reconciliationRunId": str(result.run_id) if result.run_id else None,
                        "lifecycleVersion": account.lifecycle_version,
                    },
                )
                await session.flush()
                return account
    except JobLockAlreadyHeldError as exc:
        raise LiveTradingServiceError(
            "Live execution or close-all is already running for this account.",
            status_code=409,
        ) from exc


async def stop_live_trading_account(
    session: AsyncSession,
    *,
    account_key: str,
    reason: str = "stopped_by_dashboard",
    actor: str = "dashboard",
    force_exit_only: bool = False,
) -> TradingAccount:
    try:
        async with job_lock(
            session,
            key=f"live_execution:{account_key}",
            ttl_seconds=120,
        ):
            account = await load_live_account_for_update(session, account_key=account_key)
            previous_status = account.status
            if previous_status != "disabled" or force_exit_only:
                apply_live_account_status(account, status="exit_only", reason=reason)
            else:
                apply_live_account_status(account, status="disabled", reason=reason)
            canceled_orders = await cancel_unsent_live_entries(
                session,
                account_key=account.key,
                reason="Entry canceled because the live account was stopped.",
            )
            record_audit_log(
                session,
                actor=actor,
                action="live_account.stop",
                payload={
                    "accountKey": account.key,
                    "previousStatus": previous_status,
                    "status": account.status,
                    "canceledOrders": canceled_orders,
                    "lifecycleVersion": account.lifecycle_version,
                },
            )
            await session.flush()
            return account
    except JobLockAlreadyHeldError as exc:
        raise LiveTradingServiceError(
            "Live execution is already running for this account.",
            status_code=409,
        ) from exc


async def disable_live_trading_account(
    session: AsyncSession,
    *,
    account_key: str,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    actor: str = "dashboard",
) -> TradingAccount:
    try:
        async with job_lock(
            session,
            key=f"live_close_all:{account_key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account_key}",
                ttl_seconds=300,
            ):
                account = await load_live_account_for_update(session, account_key=account_key)
                if account.status == "enabled":
                    apply_live_account_status(
                        account,
                        status="exit_only",
                        reason="disable_requested",
                    )
                    canceled_orders = await cancel_unsent_live_entries(
                        session,
                        account_key=account.key,
                    )
                    record_audit_log(
                        session,
                        actor=actor,
                        action="live_account.disable_requested",
                        payload={
                            "accountKey": account.key,
                            "canceledOrders": canceled_orders,
                            "lifecycleVersion": account.lifecycle_version,
                        },
                    )
                    await session.commit()
                result = await run_live_trading_account_reconciliation(
                    session,
                    account=account,
                    settings=settings,
                    info_client=info_client,
                )
                if result.status != "complete" or result.open_positions != 0:
                    raise LiveTradingServiceError(
                        "Live account can only be disabled after a complete flat reconciliation.",
                        status_code=409,
                    )
                account = await load_live_account_for_update(session, account_key=account_key)
                await validate_live_account_has_no_pending_work(session, account_key=account.key)
                apply_live_account_status(
                    account,
                    status="disabled",
                    reason="disabled_after_complete_flat_reconciliation",
                )
                record_audit_log(
                    session,
                    actor=actor,
                    action="live_account.disable",
                    payload={
                        "accountKey": account.key,
                        "reconciliationRunId": str(result.run_id) if result.run_id else None,
                        "lifecycleVersion": account.lifecycle_version,
                    },
                )
                await session.flush()
                return account
    except JobLockAlreadyHeldError as exc:
        raise LiveTradingServiceError(
            "Live execution or close-all is already running for this account.",
            status_code=409,
        ) from exc


def validate_live_account_identity(account: TradingAccount, *, settings: Settings) -> None:
    if account.archived_at is not None:
        raise LiveTradingServiceError("Live account is archived.", status_code=409)
    if account.network != settings.hyperliquid_network:
        raise LiveTradingServiceError(
            "Live account network does not match the configured network.",
            status_code=409,
        )


async def validate_live_account_has_no_pending_work(
    session: AsyncSession,
    *,
    account_key: str,
) -> None:
    pending_orders = await live_account_nonterminal_order_count(
        session,
        account_key=account_key,
    )
    if pending_orders > 0:
        raise LiveTradingServiceError(
            "Live account still has non-terminal order work.",
            status_code=409,
        )
    if await live_account_has_incomplete_close_operation(session, account_key=account_key):
        raise LiveTradingServiceError(
            "Live account still has an incomplete close-all operation.",
            status_code=409,
        )


async def live_account_nonterminal_order_count(
    session: AsyncSession,
    *,
    account_key: str,
) -> int:
    value = await session.scalar(
        select(func.count(TradingOrder.id)).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.status.not_in(TERMINAL_ORDER_STATUSES),
        )
    )
    return int(value or 0)


async def live_account_has_incomplete_close_operation(
    session: AsyncSession,
    *,
    account_key: str,
) -> bool:
    value = await session.scalar(
        select(func.count(TradingCloseAllOperation.id)).where(
            TradingCloseAllOperation.account_key == account_key,
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            ),
        )
    )
    return int(value or 0) > 0


async def validate_live_account_can_be_removed(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
) -> None:
    if account.status != "disabled":
        raise LiveAccountDeleteError(
            "Disable the live account after a complete flat reconciliation before archiving it.",
            status_code=409,
        )
    if live_reconciliation_status(account) != "complete" or not live_reconciliation_is_fresh(
        account,
        settings=settings,
    ):
        raise LiveAccountDeleteError(
            "Archive requires a fresh complete exchange reconciliation.",
            status_code=409,
        )
    open_positions = await session.scalar(
        select(func.count(TradingPosition.id)).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
        )
    )
    if int(open_positions or 0) > 0:
        raise LiveAccountDeleteError(
            "Archive is blocked while live positions remain.",
            status_code=409,
        )
    try:
        await validate_live_account_has_no_pending_work(session, account_key=account.key)
    except LiveTradingServiceError as exc:
        raise LiveAccountDeleteError(exc.detail, status_code=exc.status_code) from exc


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
    try:
        async with job_lock(
            session,
            key=f"live_close_all:{account.key}",
            ttl_seconds=300,
        ):
            async with job_lock(
                session,
                key=f"live_execution:{account.key}",
                ttl_seconds=120,
            ):
                account = await load_live_account_for_update(
                    session,
                    account_key=account.key,
                )
                if account.status != "exit_only":
                    apply_live_account_status(
                        account,
                        status="exit_only",
                        reason="close_all_requested",
                    )
                await cancel_unsent_live_entries(
                    session,
                    account_key=account.key,
                    reason="Entry canceled because close-all was requested.",
                )
                await session.commit()
            return await run_live_close_all_operation(
                session,
                account=account,
                settings=settings,
                info_client=info_client,
                trading_client=trading_client,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveTradingServiceError(
            "A close-all operation is already running for this account.",
            status_code=409,
        ) from exc


async def run_live_close_all_operation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None,
    trading_client: HyperliquidLiveTradingClient | None,
) -> LiveCloseAllResult:
    operation = await get_or_create_live_close_all_operation(
        session,
        account_key=account.key,
    )
    if account.status != "exit_only":
        apply_live_account_status(
            account,
            status="exit_only",
            reason="close_all_running",
        )
    operation.status = "running"
    operation.last_error = None
    await session.commit()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    try:
        initial_reconciliation = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        await session.commit()
        await refresh_live_close_all_items(session, operation_id=operation.id)
        await session.commit()
        positions = await load_live_exchange_positions(session, account_key=account.key)
        await session.commit()
        if not positions and initial_reconciliation.status == "complete":
            return await complete_live_close_all_operation(
                session,
                account=account,
                operation=operation,
                submitted_orders=0,
            )
        if not positions:
            operation.status = "partially_completed"
            operation.last_error = (
                "Close-all could not prove the exchange account is flat because "
                "reconciliation was partial."
            )
            await session.commit()
            return LiveCloseAllResult(
                account_key=account.key,
                operation_id=operation.id,
                operation_status=operation.status,
                submitted_orders=0,
                failed_orders=1,
                status=account.status,
            )

        mids = await load_live_close_mids(client, positions=positions)
        live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
        submitted = 0
        for position in positions:
            item = await get_or_create_live_close_all_item(
                session,
                operation=operation,
                position=position,
            )
            if item.status == "uncertain":
                continue

            mid_price = decimal_or_none(mids.get(position.coin))
            if mid_price is None or mid_price <= ZERO:
                item.status = "failed"
                item.error = "Live close price is unavailable."
                await session.commit()
                continue

            item.attempt_count += 1
            item.status = "submitting"
            item.error = None
            await session.commit()
            intent = build_live_close_position_intent(
                account=account,
                position=position,
                mid_price=mid_price,
                settings=settings,
                source_fill_id=(f"close-all-{operation.id}-{item.id}-{item.attempt_count}"),
            )
            try:
                result = await submit_live_trade_intent(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                    client=live_client,
                )
            except LiveTradingServiceError as exc:
                order = await load_live_order_by_client_order_id(
                    session,
                    client_order_id=intent.client_order_id,
                )
                item = await session.get(TradingCloseAllItem, item.id)
                if item is not None:
                    item.order_id = order.id if order is not None else None
                    item.status = (
                        "uncertain"
                        if order is not None and order.status in {"submitting", "uncertain"}
                        else "failed"
                    )
                    item.error = str(exc) or exc.__class__.__name__
                    await session.commit()
                continue

            item = await session.get(TradingCloseAllItem, item.id)
            if item is not None:
                item.order_id = result.order.id
                item.status = close_all_item_status_for_order(result.order.status)
                item.error = result.order.error
                await session.commit()
            submitted += int(result.submitted)

        final_reconciliation = await reconcile_live_trading_account(
            session,
            account=account,
            settings=settings,
            info_client=client,
        )
        await session.commit()
        remaining_positions = await load_live_exchange_positions(session, account_key=account.key)
        await refresh_live_close_all_items(session, operation_id=operation.id)
        if not remaining_positions and final_reconciliation.status == "complete":
            return await complete_live_close_all_operation(
                session,
                account=account,
                operation=operation,
                submitted_orders=submitted,
            )

        failed = await live_close_all_incomplete_item_count(
            session,
            operation_id=operation.id,
        )
        operation.status = "partially_completed"
        operation.last_error = (
            f"{len(remaining_positions)} live positions remain open."
            if final_reconciliation.status == "complete"
            else "Close-all cannot confirm flat exposure because reconciliation is partial."
        )
        if account.status != "exit_only":
            apply_live_account_status(
                account,
                status="exit_only",
                reason="close_all_incomplete",
            )
        await session.commit()
        return LiveCloseAllResult(
            account_key=account.key,
            operation_id=operation.id,
            operation_status=operation.status,
            submitted_orders=submitted,
            failed_orders=max(failed, len(remaining_positions)),
            status=account.status,
        )
    except BaseException as exc:
        await session.rollback()
        persisted_operation = await session.get(TradingCloseAllOperation, operation.id)
        if persisted_operation is not None:
            persisted_operation.status = "failed"
            persisted_operation.last_error = str(exc) or exc.__class__.__name__
            await session.commit()
        raise
    finally:
        if client_created:
            await client.__aexit__(None, None, None)


async def get_or_create_live_close_all_operation(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingCloseAllOperation:
    operation = await session.scalar(
        select(TradingCloseAllOperation)
        .where(
            TradingCloseAllOperation.account_key == account_key,
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            ),
        )
        .order_by(TradingCloseAllOperation.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if operation is not None:
        return operation
    operation = TradingCloseAllOperation(
        account_key=account_key,
        status="pending",
        requested_at=datetime.now(UTC),
    )
    session.add(operation)
    await session.flush()
    return operation


async def get_or_create_live_close_all_item(
    session: AsyncSession,
    *,
    operation: TradingCloseAllOperation,
    position: TradingPosition,
) -> TradingCloseAllItem:
    item = await session.scalar(
        select(TradingCloseAllItem)
        .where(
            TradingCloseAllItem.operation_id == operation.id,
            TradingCloseAllItem.position_id == position.id,
        )
        .with_for_update()
    )
    if item is not None:
        return item
    item = TradingCloseAllItem(
        operation_id=operation.id,
        position_id=position.id,
        coin=position.coin,
        status="pending",
        attempt_count=0,
    )
    session.add(item)
    await session.flush()
    await session.commit()
    return item


def close_all_item_status_for_order(order_status: str) -> str:
    if order_status == "filled":
        return "completed"
    if order_status in {"submitting", "uncertain", "submitted", "accepted", "partially_filled"}:
        return "uncertain"
    if order_status in {"rejected", "canceled", "failed"}:
        return "failed"
    return "submitting"


async def refresh_live_close_all_items(
    session: AsyncSession,
    *,
    operation_id: UUID,
) -> None:
    items_result = await session.scalars(
        select(TradingCloseAllItem).where(
            TradingCloseAllItem.operation_id == operation_id,
        )
    )
    items = list(items_result.all())
    order_ids = [item.order_id for item in items if item.order_id is not None]
    orders: dict[UUID, TradingOrder] = {}
    if order_ids:
        orders_result = await session.scalars(
            select(TradingOrder).where(TradingOrder.id.in_(order_ids))
        )
        orders = {order.id: order for order in orders_result.all()}
    for item in items:
        if item.order_id is None:
            continue
        order = orders.get(item.order_id)
        if order is None:
            item.status = "failed"
            item.error = "Live order is missing."
            continue
        item.status = close_all_item_status_for_order(order.status)
        item.error = order.error
    await session.flush()


async def live_close_all_incomplete_item_count(
    session: AsyncSession,
    *,
    operation_id: UUID,
) -> int:
    value = await session.scalar(
        select(func.count(TradingCloseAllItem.id)).where(
            TradingCloseAllItem.operation_id == operation_id,
            TradingCloseAllItem.status.not_in(["completed", "skipped"]),
        )
    )
    return int(value or 0)


async def complete_live_close_all_operation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    operation: TradingCloseAllOperation,
    submitted_orders: int,
) -> LiveCloseAllResult:
    pending_orders = await live_account_nonterminal_order_count(
        session,
        account_key=account.key,
    )
    if pending_orders > 0:
        if account.status != "exit_only":
            apply_live_account_status(
                account,
                status="exit_only",
                reason="close_all_pending_order_work",
            )
        operation.status = "partially_completed"
        operation.last_error = f"{pending_orders} non-terminal live orders remain."
        await session.commit()
        return LiveCloseAllResult(
            account_key=account.key,
            operation_id=operation.id,
            operation_status=operation.status,
            submitted_orders=submitted_orders,
            failed_orders=pending_orders,
            status=account.status,
        )
    apply_live_account_status(
        account,
        status="disabled",
        reason="close_all_complete_and_flat",
    )
    operation.status = "completed"
    operation.completed_at = datetime.now(UTC)
    operation.last_error = None
    record_audit_log(
        session,
        actor="execution_engine",
        action="live_account.close_all_completed",
        payload={
            "accountKey": account.key,
            "operationId": str(operation.id),
            "submittedOrders": submitted_orders,
            "lifecycleVersion": account.lifecycle_version,
        },
    )
    await session.commit()
    return LiveCloseAllResult(
        account_key=account.key,
        operation_id=operation.id,
        operation_status=operation.status,
        submitted_orders=submitted_orders,
        failed_orders=0,
        status=account.status,
    )


async def resume_live_close_all_operations(
    session: AsyncSession,
    *,
    settings: Settings,
    limit: int = 10,
) -> list[LiveCloseAllResult]:
    account_keys_result = await session.scalars(
        select(TradingCloseAllOperation.account_key)
        .where(
            TradingCloseAllOperation.status.in_(
                ["pending", "running", "partially_completed", "failed"]
            )
        )
        .distinct()
        .limit(limit)
    )
    account_keys = list(account_keys_result.all())
    await session.commit()
    results: list[LiveCloseAllResult] = []
    for account_key in account_keys:
        try:
            account = await load_live_account(session, account_key=account_key)
            await session.commit()
            result = await close_all_live_account_positions(
                session,
                account=account,
                settings=settings,
            )
        except LiveTradingServiceError:
            await session.rollback()
            continue
        results.append(result)
    return results


async def close_live_account_position(
    session: AsyncSession,
    *,
    position_id: UUID,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
) -> LiveOrderLifecycleResult:
    position = await load_live_position(session, position_id=position_id)
    account = await load_live_account(session, account_key=position.account_key)
    if account.status == "disabled":
        account = await stop_live_trading_account(
            session,
            account_key=account.key,
            reason="manual_close_detected_exposure",
            actor="dashboard",
            force_exit_only=True,
        )
    else:
        await session.commit()

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
        await session.commit()
        position = await load_live_position(session, position_id=position_id)
        if position.account_key != account.key:
            raise LiveTradingServiceError("Live position account changed during reconcile.")
        close_size_before_submit = position.size
        await session.commit()

        mids = await load_live_close_mids(client, positions=[position])
        mid_price = decimal_or_none(mids.get(position.coin)) or live_position_mark_price(position)
        if mid_price is None or mid_price <= ZERO:
            raise LiveTradingServiceError("Live close price is unavailable.", status_code=409)

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
            result = await submit_live_trade_intent(
                session,
                account=account,
                intent=intent,
                settings=settings,
                client=live_client,
            )
            if result.submitted:
                await reconcile_live_trading_account(
                    session,
                    account=account,
                    settings=settings,
                    info_client=client,
                )
                await session.commit()
            return result
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

    if order.status in {"failed", "planned", "ready", "submitting", "uncertain", "submitted"}:
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


async def load_live_position(
    session: AsyncSession,
    *,
    position_id: UUID,
) -> TradingPosition:
    position = await get_live_position(session, position_id=position_id)
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
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if account is None:
        raise LiveAccountNotFoundError()
    return account


async def load_live_account(
    session: AsyncSession,
    *,
    account_key: str,
) -> TradingAccount:
    account = await session.scalar(
        select(TradingAccount).where(
            TradingAccount.key == account_key,
            TradingAccount.account_type == "live",
        )
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
    return await submit_live_trade_intent_under_account_lock(
        session,
        account_key=account.key,
        intent=intent,
        settings=settings,
        live_client=live_client,
    )


async def submit_live_trade_intent_under_account_lock(
    session: AsyncSession,
    *,
    account_key: str,
    intent: TradeIntent,
    settings: Settings,
    live_client: HyperliquidLiveTradingClient,
) -> LiveOrderLifecycleResult:
    try:
        async with job_lock(
            session,
            key=f"live_execution:{account_key}",
            ttl_seconds=120,
        ):
            account = await load_live_account_for_update(session, account_key=account_key)
            validate_live_account_identity(account, settings=settings)
            if not intent.reduce_only and account.status != "enabled":
                raise LiveOrderSubmitError(
                    "Live account is not enabled for entry execution.",
                    status_code=409,
                )
            existing_order = await load_live_order_by_client_order_id(
                session,
                client_order_id=intent.client_order_id,
            )
            will_dispatch = (
                existing_order is None
                or existing_order.status == "ready"
                or is_retryable_live_order_submit_failure(existing_order)
            )

            if not intent.reduce_only and will_dispatch:
                await ensure_live_entry_intent_is_fresh(
                    session,
                    intent=intent,
                    order=existing_order,
                    settings=settings,
                )
                await validate_live_entry_risk_guardrails(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                    exclude_order_id=existing_order.id if existing_order is not None else None,
                )

            if will_dispatch:
                try:
                    live_client.validate_account_order(account=account, intent=intent)
                except Exception as exc:
                    raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

            order, dispatch, _ = await prepare_live_order_dispatch(
                session,
                intent=intent,
            )
            if order.status in TERMINAL_ORDER_STATUSES:
                if is_retryable_live_order_submit_failure(order):
                    reset_live_order_for_retry(order, intent=intent)
                    order.status = "ready"
                    dispatch.status = "pending"
                    dispatch.completed_at = None
                    dispatch.last_error = None
                    await session.commit()
                else:
                    dispatch.status = "completed"
                    dispatch.completed_at = dispatch.completed_at or datetime.now(UTC)
                    await session.commit()
                    return LiveOrderLifecycleResult(
                        order=order,
                        exchange_result=None,
                        submitted=False,
                    )

            if order.status in {"submitting", "uncertain", "submitted"}:
                return LiveOrderLifecycleResult(
                    order=order,
                    exchange_result=None,
                    submitted=False,
                )
            if order.status not in RECOVERABLE_ORDER_STATUSES:
                return LiveOrderLifecycleResult(
                    order=order,
                    exchange_result=None,
                    submitted=False,
                )

            await mark_live_order_dispatching(
                session,
                order=order,
                dispatch=dispatch,
            )
            try:
                result = await live_client.submit_order(account=account, intent=intent)
            except (
                HyperliquidLiveOrderRejectedError,
                HyperliquidLiveTradingConfigurationError,
            ) as exc:
                await mark_live_order_failed(
                    session,
                    order=order,
                    dispatch=dispatch,
                    error=exc,
                )
                raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc
            except Exception as exc:
                await mark_live_order_uncertain(
                    session,
                    order=order,
                    dispatch=dispatch,
                    error=exc,
                )
                raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

            apply_live_order_result(order, result, updated_at=datetime.now(UTC))
            await mark_live_order_dispatch_completed(session, dispatch=dispatch)
            return LiveOrderLifecycleResult(
                order=order,
                exchange_result=result,
                submitted=True,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveOrderSubmitError(
            "Another live order is already being dispatched for this account.",
            status_code=409,
        ) from exc


async def ensure_live_entry_intent_is_fresh(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    order: TradingOrder | None,
    settings: Settings,
) -> None:
    created_at = intent.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - created_at.astimezone(UTC) <= timedelta(
        seconds=settings.live_trading_entry_intent_ttl_seconds
    ):
        return

    if order is not None and order.status in {"planned", "ready", "failed", "canceled"}:
        dispatch = await load_live_order_dispatch(session, order_id=order.id)
        order.status = "canceled"
        order.error = "Live entry intent expired before exchange submission."
        if dispatch is not None:
            dispatch.status = "canceled"
            dispatch.completed_at = datetime.now(UTC)
            dispatch.last_error = order.error
        record_audit_log(
            session,
            actor="execution_engine",
            action="live_entry.expired",
            payload={"accountKey": order.account_key, "orderId": str(order.id)},
        )
        await session.commit()
    raise LiveOrderSubmitError(
        "Live entry intent expired before exchange submission.",
        status_code=409,
    )


async def recover_live_order_dispatches(
    session: AsyncSession,
    *,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    limit: int = 100,
) -> LiveDispatchRecoveryResult:
    dispatch_ids_result = await session.scalars(
        select(TradingOrderDispatch.id)
        .where(
            TradingOrderDispatch.status.in_(["pending", "dispatching", "uncertain"]),
            TradingOrderDispatch.available_at <= datetime.now(UTC),
        )
        .order_by(TradingOrderDispatch.created_at.asc())
        .limit(limit)
    )
    dispatch_ids = list(dispatch_ids_result.all())
    await session.commit()
    if not dispatch_ids:
        return LiveDispatchRecoveryResult()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    if client_created:
        await client.__aenter__()
    live_client = trading_client or HyperliquidLiveTradingClient(settings=settings)
    inspected = recovered = dispatched_count = uncertain = failed = 0
    try:
        for dispatch_id in dispatch_ids:
            dispatch = await session.get(TradingOrderDispatch, dispatch_id)
            if dispatch is None or dispatch.status not in {
                "pending",
                "dispatching",
                "uncertain",
            }:
                await session.rollback()
                continue
            order = await session.get(TradingOrder, dispatch.order_id)
            account = (
                await session.get(TradingAccount, dispatch.account_key)
                if order is not None
                else None
            )
            await session.commit()
            if order is None or account is None or account.account_type != "live":
                dispatch = await session.get(TradingOrderDispatch, dispatch_id)
                if dispatch is not None:
                    dispatch.status = "canceled"
                    dispatch.last_error = "Live order or account is missing."
                    dispatch.completed_at = datetime.now(UTC)
                    await session.commit()
                failed += 1
                inspected += 1
                continue

            inspected += 1
            if dispatch.status == "pending" and order.status == "ready":
                if not order.reduce_only and not settings.live_trading_enabled:
                    refreshed_order = await session.get(TradingOrder, order.id)
                    refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
                    if refreshed_order is not None and refreshed_dispatch is not None:
                        refreshed_order.status = "canceled"
                        refreshed_order.error = (
                            "Entry canceled while live entry execution is stopped."
                        )
                        refreshed_dispatch.status = "canceled"
                        refreshed_dispatch.completed_at = datetime.now(UTC)
                        refreshed_dispatch.last_error = refreshed_order.error
                        record_audit_log(
                            session,
                            actor="recovery_engine",
                            action="live_entry.canceled_while_stopped",
                            payload={
                                "accountKey": refreshed_order.account_key,
                                "orderId": str(refreshed_order.id),
                                "liveTradingEnabled": False,
                            },
                        )
                        await session.commit()
                    failed += 1
                    continue
                try:
                    result = await submit_live_trade_intent(
                        session,
                        account=account,
                        intent=trade_intent_from_order(order),
                        settings=settings,
                        client=live_client,
                    )
                except LiveOrderSubmitError:
                    refreshed_order = await session.get(TradingOrder, order.id)
                    if refreshed_order is not None and refreshed_order.status == "uncertain":
                        uncertain += 1
                    else:
                        failed += 1
                else:
                    dispatched_count += int(result.submitted)
                continue

            try:
                status_response = await client.order_status(
                    user=live_account_user_address(account, settings=settings),
                    oid=order.client_order_id,
                )
            except Exception as exc:
                refreshed_order = await session.get(TradingOrder, order.id)
                refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
                if refreshed_order is not None and refreshed_dispatch is not None:
                    await mark_live_order_uncertain(
                        session,
                        order=refreshed_order,
                        dispatch=refreshed_dispatch,
                        error=exc,
                    )
                uncertain += 1
                continue

            refreshed_order = await session.get(TradingOrder, order.id)
            refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
            if refreshed_order is None or refreshed_dispatch is None:
                await session.rollback()
                failed += 1
                continue
            if mapped_exchange_order_status(status_response) is not None:
                apply_order_status_response(refreshed_order, status_response)
                refreshed_dispatch.status = "completed"
                refreshed_dispatch.completed_at = datetime.now(UTC)
                refreshed_dispatch.last_error = None
                await session.commit()
                recovered += 1
                continue

            await mark_live_order_uncertain(
                session,
                order=refreshed_order,
                dispatch=refreshed_dispatch,
                error=RuntimeError("Exchange order status is still unknown for this cloid."),
            )
            uncertain += 1
    finally:
        if client_created:
            await client.__aexit__(None, None, None)

    return LiveDispatchRecoveryResult(
        inspected=inspected,
        recovered=recovered,
        dispatched=dispatched_count,
        uncertain=uncertain,
        failed=failed,
    )


async def validate_live_entry_risk_guardrails(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
    exclude_order_id: UUID | None = None,
) -> None:
    now = datetime.now(UTC)

    async def reject(
        *,
        rule: str,
        message: str,
        observed: str | int | float | None = None,
        limit: str | int | float | None = None,
    ) -> None:
        await trip_live_account_risk(
            session,
            account=account,
            rule=rule,
            message=message,
            observed=observed,
            limit=limit,
        )
        await session.commit()
        raise LiveOrderSubmitError(message, status_code=409)

    if live_reconciliation_status(account) != "complete":
        await reject(
            rule="reconciliation_incomplete",
            message="Live entries require a complete exchange reconciliation snapshot.",
        )
    if not live_reconciliation_is_fresh(account, settings=settings, now=now):
        await reject(
            rule="reconciliation_stale",
            message="Live entries require a fresh exchange reconciliation snapshot.",
            observed=(
                account.last_reconciled_at.isoformat() if account.last_reconciled_at else None
            ),
            limit=settings.live_trading_reconciliation_max_snapshot_age_seconds,
        )
    if settings.live_trading_max_account_open_notional_usd > ZERO:
        open_notional = await live_account_open_notional(
            session,
            account_key=account.key,
            exclude_order_id=exclude_order_id,
            reconciled_at=account.last_reconciled_at,
        )
        reserved_notional = live_entry_reserved_notional(intent, settings=settings)
        if open_notional + reserved_notional > settings.live_trading_max_account_open_notional_usd:
            await reject(
                rule="max_account_open_notional",
                message="Live account open notional guard would be exceeded.",
                observed=str(open_notional + reserved_notional),
                limit=str(settings.live_trading_max_account_open_notional_usd),
            )

    if settings.live_trading_max_open_positions > 0:
        open_coins = await live_account_open_coins(
            session,
            account_key=account.key,
            exclude_order_id=exclude_order_id,
            reconciled_at=account.last_reconciled_at,
        )
        if (
            intent.coin not in open_coins
            and len(open_coins) >= settings.live_trading_max_open_positions
        ):
            await reject(
                rule="max_open_positions",
                message="Live account open position guard would be exceeded.",
                observed=len(open_coins) + 1,
                limit=settings.live_trading_max_open_positions,
            )

    current_unrealized_pnl = ZERO
    if (
        settings.live_trading_max_daily_loss_usd > ZERO
        or settings.live_trading_max_weekly_loss_usd > ZERO
    ):
        current_unrealized_pnl = await live_account_current_unrealized_pnl(
            session,
            account_key=account.key,
        )

    if settings.live_trading_max_daily_loss_usd > ZERO:
        daily_net_pnl = await live_account_daily_net_pnl(
            session,
            account_key=account.key,
            now=now,
        )
        daily_net_pnl += current_unrealized_pnl
        if daily_net_pnl <= -settings.live_trading_max_daily_loss_usd:
            await reject(
                rule="max_daily_loss",
                message="Live account daily loss guard is active.",
                observed=str(daily_net_pnl),
                limit=str(settings.live_trading_max_daily_loss_usd),
            )

    if settings.live_trading_max_weekly_loss_usd > ZERO:
        weekly_net_pnl = await live_account_weekly_net_pnl(
            session,
            account_key=account.key,
            now=now,
        )
        weekly_net_pnl += current_unrealized_pnl
        if weekly_net_pnl <= -settings.live_trading_max_weekly_loss_usd:
            await reject(
                rule="max_weekly_loss",
                message="Live account weekly loss guard is active.",
                observed=str(weekly_net_pnl),
                limit=str(settings.live_trading_max_weekly_loss_usd),
            )

    if settings.live_trading_max_orders_per_minute > 0:
        recent_orders = await live_account_recent_order_count(
            session,
            account_key=account.key,
            now=now,
        )
        if recent_orders >= settings.live_trading_max_orders_per_minute:
            await reject(
                rule="max_orders_per_minute",
                message="Live account order rate guard is active.",
                observed=recent_orders,
                limit=settings.live_trading_max_orders_per_minute,
            )


def live_entry_reserved_notional(intent: TradeIntent, *, settings: Settings) -> Decimal:
    minimum_wire_notional = (
        max(
            settings.trading_copy_min_order_notional_usd,
            settings.live_trading_min_order_notional_usd,
        )
        + settings.live_trading_min_order_notional_buffer_usd
    )
    return max(intent.notional_usd, minimum_wire_notional)


async def live_account_open_notional(
    session: AsyncSession,
    *,
    account_key: str,
    exclude_order_id: UUID | None = None,
    reconciled_at: datetime | None = None,
) -> Decimal:
    aggregate_value = await session.scalar(
        select(func.coalesce(func.sum(TradingPosition.notional_usd), ZERO)).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    aggregate_notional = decimal_or_none(aggregate_value) or ZERO
    position_notional = aggregate_notional
    if position_notional <= ZERO:
        source_value = await session.scalar(
            select(func.coalesce(func.sum(TradingPosition.notional_usd), ZERO)).where(
                TradingPosition.account_key == account_key,
                TradingPosition.account_type == "live",
                TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
            )
        )
        position_notional = decimal_or_none(source_value) or ZERO
    pending_statement = select(
        func.coalesce(func.sum(TradingOrder.requested_notional_usd), ZERO)
    ).where(
        TradingOrder.account_key == account_key,
        TradingOrder.account_type == "live",
        TradingOrder.reduce_only.is_(False),
        TradingOrder.status.in_(
            ["ready", "submitting", "uncertain", "submitted", "accepted", "partially_filled"]
        ),
    )
    if exclude_order_id is not None:
        pending_statement = pending_statement.where(TradingOrder.id != exclude_order_id)
    pending_value = await session.scalar(pending_statement)
    recent_filled_notional = ZERO
    if reconciled_at is not None:
        recent_statement = select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            TradingOrder.filled_notional_usd > ZERO,
                            TradingOrder.filled_notional_usd,
                        ),
                        else_=TradingOrder.requested_notional_usd,
                    )
                ),
                ZERO,
            )
        ).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.reduce_only.is_(False),
            TradingOrder.status == "filled",
            TradingOrder.filled_at > reconciled_at,
        )
        if exclude_order_id is not None:
            recent_statement = recent_statement.where(TradingOrder.id != exclude_order_id)
        recent_filled_notional = decimal_or_none(await session.scalar(recent_statement)) or ZERO
    return position_notional + (decimal_or_none(pending_value) or ZERO) + recent_filled_notional


async def live_account_open_coins(
    session: AsyncSession,
    *,
    account_key: str,
    exclude_order_id: UUID | None = None,
    reconciled_at: datetime | None = None,
) -> set[str]:
    aggregate_result = await session.scalars(
        select(TradingPosition.coin).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == LIVE_EXCHANGE_SOURCE,
        )
    )
    aggregate_coins = {coin for coin in aggregate_result.all() if coin}
    position_coins = aggregate_coins
    if not position_coins:
        source_result = await session.scalars(
            select(TradingPosition.coin).where(
                TradingPosition.account_key == account_key,
                TradingPosition.account_type == "live",
                TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
            )
        )
        position_coins = {coin for coin in source_result.all() if coin}
    pending_statement = select(TradingOrder.coin).where(
        TradingOrder.account_key == account_key,
        TradingOrder.account_type == "live",
        TradingOrder.reduce_only.is_(False),
        TradingOrder.status.in_(
            ["ready", "submitting", "uncertain", "submitted", "accepted", "partially_filled"]
        ),
    )
    if exclude_order_id is not None:
        pending_statement = pending_statement.where(TradingOrder.id != exclude_order_id)
    pending_result = await session.scalars(pending_statement)
    recent_filled_coins: set[str] = set()
    if reconciled_at is not None:
        recent_statement = select(TradingOrder.coin).where(
            TradingOrder.account_key == account_key,
            TradingOrder.account_type == "live",
            TradingOrder.reduce_only.is_(False),
            TradingOrder.status == "filled",
            TradingOrder.filled_at > reconciled_at,
        )
        if exclude_order_id is not None:
            recent_statement = recent_statement.where(TradingOrder.id != exclude_order_id)
        recent_result = await session.scalars(recent_statement)
        recent_filled_coins = {coin for coin in recent_result.all() if coin}
    return position_coins | {coin for coin in pending_result.all() if coin} | recent_filled_coins


async def live_account_daily_net_pnl(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> Decimal:
    utc_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
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


async def live_account_current_unrealized_pnl(
    session: AsyncSession,
    *,
    account_key: str,
) -> Decimal:
    result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
        )
    )
    positions = list(result.all())
    exchange_positions = [
        position for position in positions if position.source_wallet == LIVE_EXCHANGE_SOURCE
    ]
    effective_positions = exchange_positions or positions
    return sum(
        (live_position_unrealized_pnl(position) or ZERO for position in effective_positions),
        ZERO,
    )


async def live_account_weekly_net_pnl(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime,
) -> Decimal:
    utc_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    day_start = utc_now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = day_start - timedelta(days=day_start.weekday())
    value = await session.scalar(
        select(
            func.coalesce(
                func.sum(TradingFill.realized_pnl_usd - TradingFill.fee_usd),
                ZERO,
            )
        ).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.filled_at >= week_start,
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
            TradingOrder.submitted_at >= now - timedelta(minutes=1),
        )
    )
    return int(value or 0)


def is_retryable_live_order_submit_failure(order: TradingOrder) -> bool:
    if order.status != "failed":
        return False
    if order.exchange_order_id or order.filled_size > ZERO or order.filled_notional_usd > ZERO:
        return False
    if is_retryable_live_copy_skip(order):
        return True
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    submit_error = payload.get("submitError")
    if not isinstance(submit_error, dict):
        return False
    if submit_error.get("type") != "HyperliquidLiveOrderRejectedError":
        return False
    message = str(submit_error.get("message") or order.error or "")
    return message.startswith("Live order market is not available for exchange submission:")


def is_retryable_live_copy_skip(order: TradingOrder) -> bool:
    return (
        order.error == "skip:live_close_below_min_order_notional"
        and order.reduce_only
        and order.action in {"reduce", "close", "flip_close"}
    )


def reset_live_order_for_retry(order: TradingOrder, *, intent: TradeIntent) -> None:
    order.status = "planned"
    order.error = None
    order.submitted_at = None
    order.coin = intent.coin
    order.action = intent.action
    order.side = intent.side
    order.is_buy = intent.is_buy
    order.reduce_only = intent.reduce_only
    order.order_type = "ioc"
    order.requested_size = intent.size
    order.requested_notional_usd = intent.notional_usd
    order.margin_usd = intent.margin_usd
    order.leverage = intent.leverage
    order.limit_price = intent.limit_price
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {
            "retry": {
                "reason": "market_metadata_available_after_previous_submit_failure",
                "requestedAt": datetime.now(UTC).isoformat(),
            }
        },
    )


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
    lookback_minutes: int | None = None,
) -> LiveReconciliationResult:
    try:
        async with job_lock(
            session,
            key=f"live_execution:{account.key}",
            ttl_seconds=300,
        ):
            return await run_live_trading_account_reconciliation(
                session,
                account=account,
                settings=settings,
                info_client=info_client,
                lookback_minutes=lookback_minutes,
            )
    except JobLockAlreadyHeldError as exc:
        raise LiveReconciliationError(
            "Live execution or reconciliation is already running for this account.",
            status_code=409,
        ) from exc


async def run_live_trading_account_reconciliation(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    lookback_minutes: int | None = None,
) -> LiveReconciliationResult:
    if account.account_type != "live":
        raise LiveReconciliationError("Only live accounts can be reconciled.")
    user_address = live_account_user_address(account, settings=settings)
    reconciled_at = datetime.now(UTC)
    run = TradingReconciliationRun(
        account_key=account.key,
        status="running",
        started_at=reconciled_at,
        components={},
    )
    session.add(run)
    await session.commit()

    client_created = info_client is None
    client = info_client or HyperliquidClient(settings)
    client_entered = False
    try:
        if client_created:
            await client.__aenter__()
            client_entered = True
        order_result = await reconcile_live_order_statuses(
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
            lookback_minutes=lookback_minutes,
        )
        fill_result = await fetch_live_fills_by_time(
            client,
            user=user_address,
            start_time_ms=start_time_ms,
        )
        inserted_fills = await reconcile_live_fills(
            session,
            account=account,
            fills=list(fill_result.fills),
        )
        updated_orders_from_fills = await update_live_orders_from_reconciled_fills(
            session,
            account_key=account.key,
        )
        await recompute_live_account_fill_totals(session, account=account)
        perp_snapshot = await fetch_live_perp_states(client, user_address=user_address)
        spot_state = await fetch_live_spot_state(client, user_address=user_address)
        user_abstraction = await fetch_live_user_abstraction(
            client,
            user_address=user_address,
        )
        position_result = await reconcile_live_positions(
            session,
            account=account,
            perp_states=perp_snapshot,
            reconciled_at=reconciled_at,
        )
        unresolved_order_ids = await still_unresolved_live_order_ids(
            session,
            order_ids=order_result.unresolved_order_ids,
        )

        component_errors = reconciliation_component_errors(
            order_result=order_result,
            unresolved_order_ids=unresolved_order_ids,
            fill_result=fill_result,
            perp_snapshot=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
        )
        incomplete_components = tuple(sorted(component_errors))
        reconciliation_status = "partial" if incomplete_components else "complete"
        updated_orders = order_result.updated_orders + updated_orders_from_fills
        components = reconciliation_components_payload(
            order_result=order_result,
            unresolved_order_ids=unresolved_order_ids,
            fill_result=fill_result,
            perp_snapshot=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
            position_result=position_result,
        )
        await session.refresh(
            account,
            attribute_names=[
                "status",
                "lifecycle_version",
                "status_changed_at",
                "status_reason",
            ],
            with_for_update=True,
        )
        was_enabled = account.status == "enabled"
        update_live_account_from_state(
            account,
            perp_states=perp_snapshot,
            spot_state=spot_state,
            user_abstraction=user_abstraction,
            reconciled_at=reconciled_at,
            settings=settings,
            reconciliation_status=reconciliation_status,
            incomplete_components=incomplete_components,
            component_errors=component_errors,
        )
        if reconciliation_status != "complete" and was_enabled:
            apply_live_account_status(
                account,
                status="exit_only",
                reason="reconciliation_partial",
            )
            payload = {
                "accountKey": account.key,
                "incompleteComponents": list(incomplete_components),
                "componentErrors": component_errors,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_partial",
                severity="critical",
                message="Live account entered exit-only after partial reconciliation.",
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.reconciliation_partial",
                payload=payload,
            )
        elif position_result.open_positions > 0 and account.status == "disabled":
            apply_live_account_status(
                account,
                status="exit_only",
                reason=(
                    "external_exposure_detected"
                    if reconciliation_status == "complete"
                    else "external_exposure_detected_during_partial_reconciliation"
                ),
            )
            payload = {
                "accountKey": account.key,
                "openPositions": position_result.open_positions,
                "reconciliationStatus": reconciliation_status,
                "incompleteComponents": list(incomplete_components),
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_external_exposure_detected",
                severity="critical",
                message="Disabled live account entered exit-only after exposure was detected.",
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.external_exposure_detected",
                payload=payload,
            )
        run.status = reconciliation_status
        run.completed_at = datetime.now(UTC)
        run.components = components
        run.fetched_fills = len(fill_result.fills)
        run.inserted_fills = inserted_fills
        run.updated_orders = updated_orders
        run.open_positions = position_result.open_positions
        run.removed_positions = position_result.removed_positions
        run.error = None
        await prune_live_reconciliation_runs(session, account_key=account.key)
        await session.commit()
    except LiveTradingServiceError as exc:
        await mark_live_reconciliation_run_failed(
            session,
            run_id=run.id,
            account_key=account.key,
            attempted_at=reconciled_at,
            error=str(exc) or exc.__class__.__name__,
        )
        raise
    except Exception as exc:
        await mark_live_reconciliation_run_failed(
            session,
            run_id=run.id,
            account_key=account.key,
            attempted_at=reconciled_at,
            error=str(exc) or exc.__class__.__name__,
        )
        raise LiveReconciliationError(
            f"Live reconciliation failed: {exc.__class__.__name__}.",
            status_code=502,
        ) from exc
    finally:
        if client_entered:
            await client.__aexit__(None, None, None)
    return LiveReconciliationResult(
        account_key=account.key,
        user_address=user_address,
        run_id=run.id,
        fetched_fills=len(fill_result.fills),
        inserted_fills=inserted_fills,
        updated_orders=updated_orders,
        open_positions=position_result.open_positions,
        removed_positions=position_result.removed_positions,
        status=reconciliation_status,
        incomplete_components=incomplete_components,
        component_errors=component_errors,
        reconciled_at=reconciled_at,
    )


async def recompute_live_account_fill_totals(
    session: AsyncSession,
    *,
    account: TradingAccount,
) -> None:
    totals = await session.execute(
        select(
            func.coalesce(func.sum(TradingFill.realized_pnl_usd), ZERO),
            func.coalesce(func.sum(TradingFill.fee_usd), ZERO),
        ).where(
            TradingFill.account_key == account.key,
            TradingFill.account_type == "live",
        )
    )
    realized_pnl_usd, fee_usd = totals.one()
    account.realized_pnl_usd = decimal_or_none(realized_pnl_usd) or ZERO
    account.fee_usd = decimal_or_none(fee_usd) or ZERO
    await session.flush()


async def still_unresolved_live_order_ids(
    session: AsyncSession,
    *,
    order_ids: tuple[UUID, ...],
) -> tuple[UUID, ...]:
    if not order_ids:
        return ()
    result = await session.scalars(
        select(TradingOrder.id).where(
            TradingOrder.id.in_(order_ids),
            TradingOrder.status.in_(ACTIVE_ORDER_STATUSES),
        )
    )
    return tuple(result.all())


def reconciliation_component_errors(
    *,
    order_result: LiveOrderReconciliationResult,
    unresolved_order_ids: tuple[UUID, ...],
    fill_result: LiveFillFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if unresolved_order_ids:
        errors["orders"] = f"{len(unresolved_order_ids)} live order statuses remain unresolved."
    if not fill_result.complete:
        errors["fills"] = fill_result.error or "Live fill history is incomplete."
    errors.update(perp_snapshot.component_errors)
    if not perp_snapshot.complete and not any(key.startswith("perp") for key in errors):
        errors["positions"] = "One or more perp position scopes are incomplete."
    spot_error = remote_state_error(spot_state)
    if spot_error:
        errors["spot"] = spot_error
    user_abstraction_error = remote_state_error(user_abstraction)
    if user_abstraction_error:
        errors["user_abstraction"] = user_abstraction_error
    return errors


def reconciliation_components_payload(
    *,
    order_result: LiveOrderReconciliationResult,
    unresolved_order_ids: tuple[UUID, ...],
    fill_result: LiveFillFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
    position_result: "LivePositionReconciliationResult",
) -> dict[str, Any]:
    return {
        "orders": {
            "status": "partial" if unresolved_order_ids else "complete",
            "updated": order_result.updated_orders,
            "unresolved": len(unresolved_order_ids),
            "errors": order_result.errors,
        },
        "fills": {
            "status": "complete" if fill_result.complete else "partial",
            "fetched": len(fill_result.fills),
            "pages": fill_result.pages,
            "nextStartTimeMs": fill_result.next_start_time_ms,
            "error": fill_result.error,
        },
        "perpCatalog": {
            "status": "complete" if perp_snapshot.catalog_complete else "partial",
            "requestedDexes": [dex or "default" for dex in perp_snapshot.requested_dexes],
            "error": perp_snapshot.catalog_error,
        },
        "perpStates": {
            state.dex or "default": {
                "status": "complete" if state.complete else "partial",
                "error": state.error,
            }
            for state in perp_snapshot.states
        },
        "positions": {
            "status": "complete" if position_result.complete else "partial",
            "authoritativeDexes": [dex or "default" for dex in position_result.authoritative_dexes],
            "open": position_result.open_positions,
            "removed": position_result.removed_positions,
        },
        "spot": {
            "status": "partial" if remote_state_error(spot_state) else "complete",
            "error": remote_state_error(spot_state),
        },
        "userAbstraction": {
            "status": "partial" if remote_state_error(user_abstraction) else "complete",
            "error": remote_state_error(user_abstraction),
        },
    }


def remote_state_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    return str(error.get("message") or error.get("type") or "Remote state request failed.")


async def mark_live_reconciliation_run_failed(
    session: AsyncSession,
    *,
    run_id: UUID,
    account_key: str,
    attempted_at: datetime,
    error: str,
) -> None:
    await session.rollback()
    run = await session.get(TradingReconciliationRun, run_id)
    account = await session.scalar(
        select(TradingAccount).where(TradingAccount.key == account_key).with_for_update()
    )
    if run is not None:
        run.status = "failed"
        run.completed_at = datetime.now(UTC)
        run.error = error
        run.components = {
            "reconciliation": {
                "status": "failed",
                "error": error,
            }
        }
    if account is not None:
        account.config_payload = merge_raw_payload(
            account.config_payload,
            {
                "lastReconciliationAttempt": {
                    "attemptedAt": attempted_at.isoformat(),
                    "componentErrors": {"reconciliation": error},
                    "incompleteComponents": ["reconciliation"],
                    "status": "failed",
                }
            },
        )
        if account.status == "enabled":
            apply_live_account_status(
                account,
                status="exit_only",
                reason="reconciliation_failed",
            )
            payload = {
                "accountKey": account.key,
                "error": error,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_failed",
                severity="critical",
                message="Live account entered exit-only after reconciliation failed.",
                payload=payload,
            )
            record_audit_log(
                session,
                actor="reconciliation_engine",
                action="live_account.reconciliation_failed",
                payload=payload,
            )
    await prune_live_reconciliation_runs(session, account_key=account_key)
    await session.commit()


async def prune_live_reconciliation_runs(
    session: AsyncSession,
    *,
    account_key: str,
    now: datetime | None = None,
) -> None:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=LIVE_RECONCILIATION_RUN_RETENTION_DAYS)
    await session.execute(
        delete(TradingReconciliationRun).where(
            TradingReconciliationRun.account_key == account_key,
            TradingReconciliationRun.started_at < cutoff,
        )
    )


async def reconcile_live_order_statuses(
    session: AsyncSession,
    *,
    account: TradingAccount,
    user_address: str,
    client: HyperliquidClient,
) -> LiveOrderReconciliationResult:
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
    unresolved_order_ids: list[UUID] = []
    errors: dict[str, str] = {}
    for order in orders:
        lookup_id: int | str | None = parse_exchange_order_id(order.exchange_order_id)
        if lookup_id is None:
            lookup_id = order.client_order_id
        try:
            status_response = await client.order_status(user=user_address, oid=lookup_id)
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            order.raw_payload = merge_raw_payload(
                order.raw_payload,
                {"orderStatusError": {"message": message, "type": exc.__class__.__name__}},
            )
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = message
            updated += 1
            continue
        mapped_status = mapped_exchange_order_status(status_response)
        changed = apply_order_status_response(order, status_response)
        if mapped_status is not None:
            dispatch = await load_live_order_dispatch(session, order_id=order.id)
            if dispatch is not None and dispatch.status != "completed":
                dispatch.status = "completed"
                dispatch.completed_at = datetime.now(UTC)
                dispatch.last_error = None
                changed = True
        else:
            unresolved_order_ids.append(order.id)
            errors[order.client_order_id] = "Exchange order status is missing or unrecognized."
        if changed:
            updated += 1
    await session.flush()
    return LiveOrderReconciliationResult(
        updated_orders=updated,
        unresolved_order_ids=tuple(unresolved_order_ids),
        errors=errors,
    )


async def fetch_live_fills_by_time(
    client: HyperliquidClient,
    *,
    user: str,
    start_time_ms: int,
    max_pages: int = MAX_LIVE_FILL_RECONCILIATION_PAGES,
) -> LiveFillFetchResult:
    fills: list[dict[str, Any]] = []
    seen_fill_keys: set[tuple[str, ...]] = set()
    next_start_time_ms = start_time_ms
    pages = 0
    for _ in range(max_pages):
        try:
            batch = await client.user_fills_by_time(
                user=user,
                start_time_ms=next_start_time_ms,
                aggregate_by_time=False,
            )
        except Exception as exc:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error=str(exc) or exc.__class__.__name__,
            )
        pages += 1
        if not batch:
            return LiveFillFetchResult(fills=tuple(fills), complete=True, pages=pages)
        new_fills = []
        for fill in batch:
            fill_key = live_fill_pagination_key(fill)
            if fill_key in seen_fill_keys:
                continue
            seen_fill_keys.add(fill_key)
            new_fills.append(fill)
        fills.extend(new_fills)
        timestamps = [
            int(timestamp)
            for fill in batch
            for timestamp in [decimal_or_none(fill.get("time") or fill.get("timestamp"))]
            if timestamp is not None
        ]
        if len(fills) >= MAX_LIVE_FILL_HISTORY:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=max(timestamps) if timestamps else next_start_time_ms,
                error="Hyperliquid live fill history reached the 10000-fill availability limit.",
            )
        if len(batch) < 500 or not timestamps:
            return LiveFillFetchResult(fills=tuple(fills), complete=True, pages=pages)
        next_page_start_time_ms = max(timestamps)
        if next_page_start_time_ms < next_start_time_ms or not new_fills:
            return LiveFillFetchResult(
                fills=tuple(fills),
                complete=False,
                pages=pages,
                next_start_time_ms=next_start_time_ms,
                error="Fill reconciliation pagination did not advance.",
            )
        next_start_time_ms = next_page_start_time_ms
    return LiveFillFetchResult(
        fills=tuple(fills),
        complete=False,
        pages=pages,
        next_start_time_ms=next_start_time_ms,
        error=f"Fill reconciliation reached the {max_pages}-page safety limit.",
    )


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


async def fetch_live_perp_states(
    client: HyperliquidClient,
    *,
    user_address: str,
) -> LivePerpSnapshot:
    states: list[LivePerpState] = []
    try:
        default_payload = await client.clearinghouse_state(user=user_address)
    except Exception as exc:
        states.append(
            LivePerpState(
                dex="",
                payload={},
                complete=False,
                error=str(exc) or exc.__class__.__name__,
            )
        )
    else:
        states.append(LivePerpState(dex="", payload=default_payload))

    dex_names, catalog_complete, catalog_error = await fetch_live_perp_dex_catalog(client)
    for dex in dex_names:
        try:
            payload = await client.clearinghouse_state(user=user_address, dex=dex)
        except Exception as exc:
            states.append(
                LivePerpState(
                    dex=dex,
                    payload={},
                    complete=False,
                    error=str(exc) or exc.__class__.__name__,
                )
            )
        else:
            states.append(LivePerpState(dex=dex, payload=payload))
    return LivePerpSnapshot(
        states=tuple(states),
        requested_dexes=("", *dex_names),
        catalog_complete=catalog_complete,
        catalog_error=catalog_error,
    )


async def fetch_live_perp_dex_catalog(
    client: HyperliquidClient,
) -> tuple[list[str], bool, str | None]:
    try:
        payload = await client.perp_dexs()
    except Exception as exc:
        return [], False, str(exc) or exc.__class__.__name__
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names)), True, None


async def fetch_live_perp_dex_names(client: HyperliquidClient) -> list[str]:
    names, _, _ = await fetch_live_perp_dex_catalog(client)
    return names


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


def mapped_exchange_order_status(response: dict[str, Any]) -> str | None:
    if response.get("status") != "order":
        return None
    payload = response.get("order")
    if not isinstance(payload, dict):
        return None
    return map_exchange_order_status(string_or_none(payload.get("status")))


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
        if matched_order is not None and parsed.get("exchange_order_id"):
            matched_order.exchange_order_id = (
                matched_order.exchange_order_id or parsed["exchange_order_id"]
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
            close_only_trade = live_exchange_close_only_trade(fill)
            if close_only_trade is not None:
                closed_trades.append(close_only_trade)
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


def live_exchange_close_only_trade(fill: TradingFill) -> LiveClosedTrade | None:
    if fill.source_wallet != LIVE_EXCHANGE_SOURCE or not live_fill_is_close(fill):
        return None
    fill_size = decimal_or_none(fill.size) or ZERO
    if fill_size <= ZERO:
        return None
    exit_notional_usd = decimal_or_none(fill.notional_usd) or ZERO
    realized_pnl_usd = decimal_or_none(fill.realized_pnl_usd) or ZERO
    fee_usd = decimal_or_none(fill.fee_usd) or ZERO
    exit_price = (
        exit_notional_usd / fill_size if exit_notional_usd > ZERO else decimal_or_none(fill.price)
    )
    entry_price = live_close_only_entry_price(
        side=fill.side,
        exit_price=exit_price,
        realized_pnl_usd=realized_pnl_usd,
        size=fill_size,
    )
    entry_notional_usd = entry_price * fill_size if entry_price is not None else exit_notional_usd
    return LiveClosedTrade(
        id=f"live-closed:{fill.account_key}:{fill.source_wallet}:{fill.coin}:{fill.side}:{fill.id}",
        account_key=fill.account_key,
        source_wallet=fill.source_wallet,
        source_label="Exchange position",
        coin=fill.coin,
        side=fill.side,
        entry_price=entry_price,
        exit_price=exit_price,
        size=fill_size,
        entry_notional_usd=entry_notional_usd,
        exit_notional_usd=exit_notional_usd,
        fee_usd=fee_usd,
        realized_pnl_usd=realized_pnl_usd,
        net_pnl_usd=realized_pnl_usd - fee_usd,
        opened_at=fill.filled_at,
        closed_at=fill.filled_at,
        duration_ms=0,
        open_fill_count=0,
        close_fill_count=1,
    )


def live_close_only_entry_price(
    *,
    side: str,
    exit_price: Decimal | None,
    realized_pnl_usd: Decimal,
    size: Decimal,
) -> Decimal | None:
    if exit_price is None or exit_price <= ZERO or size <= ZERO:
        return None
    pnl_per_unit = realized_pnl_usd / size
    if side == "short":
        entry_price = exit_price + pnl_per_unit
    elif side == "long":
        entry_price = exit_price - pnl_per_unit
    else:
        return None
    return entry_price if entry_price > ZERO else None


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
            trade.entry_notional_usd / trade.opened_size if trade.opened_size > ZERO else None
        ),
        exit_price=(
            trade.exit_notional_usd / trade.closed_size if trade.closed_size > ZERO else None
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
    authoritative_dexes = {state.dex for state in perp_snapshot.states if state.complete}
    requested_dexes = set(perp_snapshot.requested_dexes)
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

    source_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet != LIVE_EXCHANGE_SOURCE,
        )
    )
    source_positions = list(source_result.all())
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


def normalize_live_perp_snapshot(
    perp_states: LivePerpSnapshot | list[LivePerpState],
) -> LivePerpSnapshot:
    if isinstance(perp_states, LivePerpSnapshot):
        return perp_states
    states = tuple(perp_states)
    return LivePerpSnapshot(
        states=states,
        requested_dexes=tuple(state.dex for state in states),
        catalog_complete=True,
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
    lookback_minutes: int | None = None,
) -> int:
    if lookback_minutes is not None:
        start_at = now - timedelta(minutes=max(lookback_minutes, 1))
        return int(start_at.timestamp() * 1000)
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
    source_fill_id: str | None = None,
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
        source_fill_id=(
            source_fill_id
            if source_fill_id is not None
            else f"{source_fill_prefix}-{position.coin}-{uuid4().hex}"
        ),
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


def update_live_account_from_state(
    account: TradingAccount,
    *,
    perp_states: LivePerpSnapshot | list[LivePerpState],
    spot_state: dict[str, Any] | None = None,
    user_abstraction: Any = None,
    reconciled_at: datetime,
    settings: Settings,
    reconciliation_status: str = "complete",
    incomplete_components: tuple[str, ...] = (),
    component_errors: dict[str, str] | None = None,
) -> None:
    perp_snapshot = normalize_live_perp_snapshot(perp_states)
    previous = account_last_reconciliation(account)
    previous_states = {
        str(state.get("dex") or "default"): dict(state)
        for state in previous.get("perpStates", [])
        if isinstance(state, dict)
    }
    state_by_dex = {state.dex: state for state in perp_snapshot.states}
    requested_dexes = set(perp_snapshot.requested_dexes)
    state_keys = {dex or "default" for dex in requested_dexes}
    if not perp_snapshot.catalog_complete:
        state_keys.update(previous_states)

    state_summaries: list[dict[str, Any]] = []
    for key in sorted(state_keys, key=lambda value: (value != "default", value)):
        dex = "" if key == "default" else key
        state = state_by_dex.get(dex)
        if state is not None and state.complete:
            state_summaries.append(
                live_perp_state_summary(
                    state,
                    reconciled_at=reconciled_at,
                )
            )
            continue
        previous_summary = previous_states.get(key, {})
        error = (
            state.error
            if state is not None
            else perp_snapshot.catalog_error or "Perp dex was not refreshed."
        )
        state_summaries.append(
            {
                **previous_summary,
                "accountValue": str(previous_summary.get("accountValue") or ZERO),
                "dex": key,
                "error": error,
                "stale": True,
                "status": "partial",
                "withdrawable": str(previous_summary.get("withdrawable") or ZERO),
            }
        )

    perp_equity = sum(
        (decimal_or_none(state.get("accountValue")) or ZERO for state in state_summaries),
        ZERO,
    )
    perp_withdrawable = sum(
        (decimal_or_none(state.get("withdrawable")) or ZERO for state in state_summaries),
        ZERO,
    )
    state_time: Any = None
    for state in state_summaries:
        state_time = max_optional_numeric(state_time, state.get("time"))

    capital_mode = live_capital_mode(settings)
    resolved_spot_state = spot_state or {}
    spot_error = remote_state_error(resolved_spot_state)
    if spot_error:
        previous_spot_total = decimal_or_none(previous.get("spotUsdcTotalUsd"))
        previous_spot_available = decimal_or_none(previous.get("spotUsdcAvailableUsd"))
        spot_usdc_total = (
            previous_spot_total
            if previous_spot_total is not None
            else (
                decimal_or_none(account.equity_usd)
                if capital_mode == LIVE_CAPITAL_MODE_UNIFIED
                else ZERO
            )
        )
        spot_usdc_available = (
            previous_spot_available
            if previous_spot_available is not None
            else (
                decimal_or_none(account.cash_balance_usd)
                if capital_mode == LIVE_CAPITAL_MODE_UNIFIED
                else ZERO
            )
        )
        spot_usdc_total = spot_usdc_total or ZERO
        spot_usdc_available = spot_usdc_available or ZERO
        stored_spot_state = previous.get("spotState")
    else:
        spot_usdc_total = live_spot_usdc_total(resolved_spot_state)
        spot_usdc_available = live_spot_usdc_available(resolved_spot_state)
        stored_spot_state = resolved_spot_state

    user_abstraction_error = remote_state_error(user_abstraction)
    if user_abstraction_error:
        stored_user_abstraction = previous.get("userAbstraction")
        stored_user_abstraction_raw = previous.get("userAbstractionRaw")
    else:
        stored_user_abstraction = normalize_user_abstraction(user_abstraction)
        stored_user_abstraction_raw = user_abstraction

    if capital_mode == LIVE_CAPITAL_MODE_UNIFIED:
        account.equity_usd = spot_usdc_total
        account.cash_balance_usd = spot_usdc_available
        tradable_equity = spot_usdc_available
    else:
        account.equity_usd = perp_equity + spot_usdc_total
        account.cash_balance_usd = perp_withdrawable + spot_usdc_available
        tradable_equity = perp_equity
    if reconciliation_status == "complete":
        account.last_reconciled_at = reconciled_at
    errors = component_errors or {}
    attempt_payload = {
        "attemptedAt": reconciled_at.isoformat(),
        "componentErrors": errors,
        "incompleteComponents": list(incomplete_components),
        "status": reconciliation_status,
    }
    account.config_payload = merge_raw_payload(
        account.config_payload,
        {
            "lastReconciliation": {
                "attemptedAt": reconciled_at.isoformat(),
                "capitalMode": capital_mode,
                "componentErrors": errors,
                "incompleteComponents": list(incomplete_components),
                "perpEquityUsd": str(perp_equity),
                "perpStates": state_summaries,
                "perpWithdrawableUsd": str(perp_withdrawable),
                "spotState": stored_spot_state,
                "spotStateError": spot_error,
                "spotUsdcAvailableUsd": str(spot_usdc_available),
                "spotUsdcTotalUsd": str(spot_usdc_total),
                "status": reconciliation_status,
                "tradableEquityUsd": str(tradable_equity),
                "time": state_time,
                "unifiedAvailableUsd": str(spot_usdc_available),
                "unifiedEquityUsd": str(spot_usdc_total),
                "userAbstraction": stored_user_abstraction,
                "userAbstractionError": user_abstraction_error,
                "userAbstractionRaw": stored_user_abstraction_raw,
            },
            "lastReconciliationAttempt": attempt_payload,
        },
    )


def live_perp_state_summary(
    state: LivePerpState,
    *,
    reconciled_at: datetime,
) -> dict[str, Any]:
    payload = state.payload
    margin_summary = payload.get("marginSummary")
    if not isinstance(margin_summary, dict):
        margin_summary = payload.get("crossMarginSummary")
    state_equity = (
        decimal_or_none(margin_summary.get("accountValue"))
        if isinstance(margin_summary, dict)
        else ZERO
    )
    state_withdrawable = decimal_or_none(payload.get("withdrawable")) or ZERO
    return {
        "accountValue": str(state_equity or ZERO),
        "dex": state.dex or "default",
        "error": None,
        "marginSummary": margin_summary if isinstance(margin_summary, dict) else None,
        "reconciledAt": reconciled_at.isoformat(),
        "stale": False,
        "status": "complete",
        "time": payload.get("time"),
        "withdrawable": str(state_withdrawable),
    }


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
        "coin": order.coin if order is not None else parsed_fill["coin"],
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
