import logging
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import blake2s
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    LiveCopyFillState,
    PaperCopyAllocation,
    TradingAccount,
    TradingCloseAllItem,
    TradingCloseAllOperation,
    TradingFill,
    TradingOrder,
    TradingOrderDispatch,
    TradingPosition,
    TradingReconciliationRun,
    WalletFill,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveOrderRejectedError,
    HyperliquidLiveTradingClient,
    HyperliquidLiveTradingConfigurationError,
    LiveOrderResult,
)
from app.services.copy_risk_rules import (
    HARD_STOP_RULE,
    account_loss_breach,
    hard_stop_breached,
)
from app.services.hyperliquid_account_mode import (
    live_spot_usdc_available,
    live_spot_usdc_total,
    normalize_user_abstraction,
    user_abstraction_is_standard,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_account_state import (
    LIVE_CAPITAL_MODE_UNIFIED,
    LIVE_PERFORMANCE_BACKFILL_VERSION,
    account_last_reconciliation,
    live_capital_mode,
    live_performance_backfill_version,
    live_perp_equity_usd,
    live_reconciliation_is_fresh,
    live_reconciliation_status,
    live_tradable_equity_usd,
    live_unified_equity_usd,
)
from app.services.live_copy_state_service import (
    LIVE_COPY_FILL_PLAN_VERSION,
    LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    LIVE_COPY_OUTCOME_ORDER,
    LIVE_COPY_OUTCOME_PENDING,
    LIVE_COPY_OUTCOME_RETRYABLE,
    LIVE_COPY_OUTCOME_TERMINAL_SKIP,
    acquire_live_copy_lifecycle_lock,
    activate_live_copy_account_sources,
    get_live_copy_source_state,
    get_prior_live_copy_fill_blocker,
    is_live_copy_fill_post_baseline,
    live_copy_entry_follows_owned_position_lifecycle,
    live_copy_unresolved_order_predicate,
    load_owned_live_copy_account_source_pairs,
    synchronize_live_copy_account_source_activity,
)
from app.services.live_exchange_fetch import (
    LiveCashFlowFetchResult,
    LiveFillFetchResult,
    LiveFundingFetchResult,
    LivePerpSnapshot,
    LivePerpState,
    fetch_live_cash_flows_by_time,
    fetch_live_fills_by_time,
    fetch_live_funding_by_time,
    fetch_live_perp_states,
    fetch_live_portfolio_history,
    fetch_live_spot_state,
    fetch_live_user_abstraction,
    live_coin_with_dex,
    live_dex_from_coin,
    normalize_live_perp_snapshot,
    remote_state_error,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    max_optional_numeric,
    merge_raw_payload,
    normalize_optional_address,
)
from app.services.live_execution_state import (
    MAX_LIVE_ORDER_SUBMIT_ATTEMPTS,
    RECOVERABLE_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES,
    create_next_live_order_dispatch,
    load_live_order_dispatch,
    mark_live_order_dispatch_completed,
    mark_live_order_dispatching,
    mark_live_order_failed,
    mark_live_order_uncertain,
    prepare_live_order_dispatch,
    record_live_order_exchange_result,
    record_live_order_status_lookup,
    trade_intent_from_order,
)
from app.services.live_fill_reconciliation import (
    LiveOrderReconciliationResult,
    apply_order_status_response,
    live_cash_flow_reconciliation_start_time_ms,
    live_fill_reconciliation_start_time_ms,
    live_funding_reconciliation_start_time_ms,
    mapped_exchange_order_status,
    mark_live_cash_flow_backfill_complete,
    reconcile_live_account_cash_flows,
    reconcile_live_fills,
    reconcile_live_funding_payments,
    reconcile_live_order_statuses,
    still_unresolved_live_order_ids,
    update_live_orders_from_reconciled_fills,
)
from app.services.live_order_reject_policy import (
    OPEN_INTEREST_CAP,
    OPEN_INTEREST_CAP_COOLDOWN_REASON,
    classify_live_order_reject,
)
from app.services.live_performance_service import (
    backfill_live_account_performance_history,
    ensure_live_account_performance_baseline,
    live_account_loss_state,
    record_live_account_performance_snapshot,
    restart_breached_live_loss_limits,
    thin_live_account_performance_snapshots,
)
from app.services.live_position_reconciliation import (
    LivePositionReconciliationResult,
    live_position_current_notional,
    live_position_mark_price,
    live_position_unrealized_pnl,
    load_live_exchange_positions,
    reconcile_live_positions,
)
from app.services.live_signer_service import (
    live_signer_entry_block,
    refresh_live_account_signer,
)
from app.services.live_trading_constants import (
    LIVE_EXCHANGE_SOURCE,
    LIVE_MANUAL_TEST_SOURCE,
    POSITION_EPSILON,
)
from app.services.source_fill_ordering import source_fill_order_components
from app.services.trading_core import (
    MARGIN_MODES,
    MarginMode,
    TradeIntent,
    build_copy_trade_intent,
    margin_from_notional,
    parse_margin_mode,
    parse_trade_side,
)
from app.services.trading_safety_service import (
    apply_live_account_status,
    cancel_unsent_live_entries,
    record_audit_log,
    record_risk_event,
    trip_live_account_risk,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
LIVE_RECONCILIATION_RUN_RETENTION_DAYS = 30
LIVE_ACCOUNT_KEY_MAX_LENGTH = 64
LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR = "Live entry intent expired before exchange submission."
LIVE_ENTRY_RETRY_EXPIRED_AFTER_REJECTION_ERROR = (
    "Live entry retry window expired after Hyperliquid rejected an earlier exchange attempt."
)
LIVE_ENTRY_RETRY_EXPIRED_AFTER_ATTEMPT_ERROR = (
    "Live entry retry window expired after an earlier exchange submission attempt."
)
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-f0-9]{40}$")


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


class LiveCopyEntryLifecycleDeferred(LiveOrderSubmitError):
    """A copied entry changed lifecycle state before its order was materialized."""

    def __init__(self, reason: str, *, state_reclassified: bool = False) -> None:
        super().__init__(reason, status_code=409)
        self.reason = reason
        self.state_reclassified = state_reclassified


class LiveReconciliationError(LiveTradingServiceError):
    detail = "Live reconciliation failed."


@dataclass(frozen=True)
class LiveReconciliationResult:
    account_key: str
    user_address: str
    run_id: UUID | None = None
    fetched_fills: int = 0
    inserted_fills: int = 0
    fetched_funding_payments: int = 0
    inserted_funding_payments: int = 0
    fetched_cash_flows: int = 0
    inserted_cash_flows: int = 0
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
class LiveCloseAllResult:
    account_key: str
    operation_id: UUID
    operation_status: str
    submitted_orders: int
    failed_orders: int
    status: str


def validate_live_trading_configuration(settings: Settings) -> None:
    try:
        client = HyperliquidLiveTradingClient(settings=settings)
        client.validate_live_configuration()
    except Exception as exc:
        raise LiveTradingServiceError(str(exc) or exc.__class__.__name__) from exc


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
    signer_block = live_signer_entry_block(account, settings=settings)
    if signer_block is not None:
        raise LiveTradingServiceError(signer_block.message, status_code=409)


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


async def load_active_live_copy_source_wallets(session: AsyncSession) -> set[str]:
    """Return the current allocation sources that may open live-copy exposure."""

    result = await session.scalars(
        select(PaperCopyAllocation.source_wallet)
        .where(
            PaperCopyAllocation.active.is_(True),
            PaperCopyAllocation.source_wallet != "",
        )
        .distinct()
    )
    return {str(source_wallet).lower() for source_wallet in result.all() if source_wallet}


async def load_owned_live_copy_source_wallets(
    session: AsyncSession,
    *,
    account_key: str,
) -> set[str]:
    """Return sources whose existing exposure or order lifecycle must be retained."""

    owned_pairs = await load_owned_live_copy_account_source_pairs(
        session,
        account_keys={account_key},
    )
    return {str(source_wallet).lower() for _, source_wallet in owned_pairs if source_wallet}


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
                    force_signer_check=True,
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
                if account.status == "enabled":
                    return account
                if await live_account_has_incomplete_close_operation(
                    session,
                    account_key=account.key,
                ):
                    raise LiveTradingServiceError(
                        "Resolve the active close-all operation before starting this account.",
                        status_code=409,
                    )
                active_source_wallets = await load_active_live_copy_source_wallets(session)
                owned_source_wallets = await load_owned_live_copy_source_wallets(
                    session,
                    account_key=account.key,
                )
                await synchronize_live_copy_account_source_activity(
                    session,
                    account_key=account.key,
                    eligible_source_wallets=active_source_wallets,
                )
                start_epoch = datetime.now(UTC)
                loss_limits_restart = await restart_breached_live_loss_limits(
                    session,
                    account=account,
                    settings=settings,
                    now=start_epoch,
                )
                if isinstance(account.config_payload, dict) and account.config_payload.get(
                    LIVE_HARD_STOP_MARKER
                ):
                    # A later hard stop is a new event.
                    account.config_payload = merge_raw_payload(
                        account.config_payload,
                        {LIVE_HARD_STOP_MARKER: None},
                    )
                await activate_live_copy_account_sources(
                    session,
                    account_key=account.key,
                    source_wallets=active_source_wallets | owned_source_wallets,
                    entry_eligible_source_wallets=active_source_wallets,
                    now=start_epoch,
                )
                apply_live_account_status(
                    account,
                    status="enabled",
                    reason="start_after_complete_reconciliation",
                    changed_at=start_epoch,
                )
                record_audit_log(
                    session,
                    actor=actor,
                    action="live_account.start",
                    payload={
                        "accountKey": account.key,
                        "reconciliationRunId": str(result.run_id) if result.run_id else None,
                        "lifecycleVersion": account.lifecycle_version,
                        "lossLimitsRestart": loss_limits_restart,
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
            await synchronize_live_copy_account_source_activity(
                session,
                account_key=account.key,
                eligible_source_wallets=set(),
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
                await synchronize_live_copy_account_source_activity(
                    session,
                    account_key=account.key,
                    eligible_source_wallets=set(),
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
                refreshed_item = await session.get(TradingCloseAllItem, item.id)
                if refreshed_item is not None:
                    refreshed_item.order_id = order.id if order is not None else None
                    refreshed_item.status = (
                        "uncertain"
                        if order is not None and order.status in {"submitting", "uncertain"}
                        else "failed"
                    )
                    refreshed_item.error = str(exc) or exc.__class__.__name__
                    await session.commit()
                continue

            refreshed_item = await session.get(TradingCloseAllItem, item.id)
            if refreshed_item is not None:
                refreshed_item.order_id = result.order.id
                refreshed_item.status = close_all_item_status_for_order(result.order.status)
                refreshed_item.error = result.order.error
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


LIVE_HARD_STOP_MARKER = "hardStop"
LIVE_HARD_STOP_ACCOUNT_STATUSES = frozenset({"enabled", "exit_only"})


async def enforce_live_hard_stop(
    session: AsyncSession,
    *,
    account: TradingAccount,
    settings: Settings,
    info_client: HyperliquidClient | None = None,
    trading_client: HyperliquidLiveTradingClient | None = None,
    now: datetime | None = None,
) -> LiveCloseAllResult | None:
    """Close every position and stop the account once drawdown reaches the hard stop.

    Runs after each reconciliation, so it acts on the equity the exchange just
    reported. The close-all operation it starts is durable and resumed by the
    reconciliation loop until the account is flat. Returns None when the hard
    stop is off, not reached, or already being carried out.
    """

    if (
        settings.live_trading_hard_stop_drawdown_pct <= ZERO
        or account.account_type != "live"
        or account.status not in LIVE_HARD_STOP_ACCOUNT_STATUSES
    ):
        return None
    resolved_now = now or datetime.now(UTC)
    loss_state = await live_account_loss_state(
        session,
        account_key=account.key,
        now=resolved_now,
        since=account.loss_limits_reset_at,
    )
    if not hard_stop_breached(loss_state, settings=settings) or loss_state is None:
        return None
    positions = await load_live_exchange_positions(session, account_key=account.key)
    if not positions or await live_account_has_incomplete_close_operation(
        session,
        account_key=account.key,
    ):
        await session.commit()
        return None

    existing_payload = account.config_payload if isinstance(account.config_payload, dict) else {}
    if not isinstance(existing_payload.get(LIVE_HARD_STOP_MARKER), dict):
        # One event per hard stop, not one per retried close-all.
        payload = {
            "accountKey": account.key,
            "rule": HARD_STOP_RULE,
            "observed": str(loss_state.drawdown_pct),
            "limit": str(settings.live_trading_hard_stop_drawdown_pct),
            "openPositions": len(positions),
        }
        account.config_payload = merge_raw_payload(
            account.config_payload,
            {LIVE_HARD_STOP_MARKER: {**payload, "trippedAt": resolved_now.isoformat()}},
        )
        record_risk_event(
            session,
            event_type="live_hard_stop",
            severity="critical",
            message="Hard stop drawdown reached; closing every position and stopping the account.",
            payload=payload,
        )
        record_audit_log(
            session,
            actor="risk_engine",
            action="live_account.hard_stop",
            payload=payload,
        )
    await session.commit()
    return await close_all_live_account_positions(
        session,
        account=account,
        settings=settings,
        info_client=info_client,
        trading_client=trading_client,
    )


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
        select(TradingOrder)
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.client_order_id == client_order_id,
        )
        .execution_options(populate_existing=True)
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


async def validate_live_copy_entry_lifecycle_gate(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
) -> None:
    """Fence a copied entry against a concurrent source-lane deactivation.

    Callers hold the live execution lock and the locked account row before this
    function acquires the lifecycle advisory lock.  Copy workers commit their
    lifecycle lease before submission, so this order has no inverse holder.
    """

    if intent.reduce_only or intent.source_wallet in {
        LIVE_EXCHANGE_SOURCE,
        LIVE_MANUAL_TEST_SOURCE,
    }:
        return
    await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
    source_state = await get_live_copy_source_state(
        session,
        account_key=account.key,
        source_wallet=intent.source_wallet,
        for_update=True,
    )
    if source_state is None:
        raise LiveCopyEntryLifecycleDeferred("live_source_lifecycle_inactive")
    wallet_fill = await session.scalar(
        select(WalletFill)
        .where(
            WalletFill.wallet_address == intent.source_wallet,
            WalletFill.external_fill_id == intent.source_fill_id,
            WalletFill.coin == intent.coin,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if wallet_fill is None:
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_missing")
    plan_result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == account.key,
            LiveCopyFillState.account_type == "live",
            LiveCopyFillState.source_wallet == intent.source_wallet,
            LiveCopyFillState.source_fill_id == intent.source_fill_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    plan_states = list(plan_result.all())
    target_state = next(
        (state for state in plan_states if state.sequence_index == intent.sequence_index),
        None,
    )
    expected_part_count = len(plan_states)
    source_fill = {
        "externalFillId": wallet_fill.external_fill_id,
        "coin": wallet_fill.coin,
        "timestampMs": wallet_fill.timestamp_ms,
        "rawJson": wallet_fill.raw_json,
    }
    expected_direction_rank, expected_position, expected_numeric_fill_id = (
        source_fill_order_components(source_fill)
    )
    if target_state is not None and (
        target_state.fill_complete
        or target_state.trading_order_id is not None
        or target_state.outcome not in {LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE}
    ):
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    if (
        target_state is None
        or expected_part_count == 0
        or {state.sequence_index for state in plan_states} != set(range(expected_part_count))
        or any(
            state.expected_part_count != expected_part_count
            or state.plan_version != LIVE_COPY_FILL_PLAN_VERSION
            for state in plan_states
        )
        or target_state.coin != intent.coin
        or target_state.side != intent.side
        or int(target_state.source_timestamp_ms) != int(wallet_fill.timestamp_ms)
        or int(target_state.source_order_direction_rank) != expected_direction_rank
        or target_state.source_order_position != expected_position
        or target_state.source_order_fill_id_numeric != expected_numeric_fill_id
        or target_state.outcome not in {LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE}
        or (
            target_state.outcome == LIVE_COPY_OUTCOME_RETRYABLE
            and target_state.reason != "processing"
        )
    ):
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_reclassified")
    if account.status != "enabled":
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_TERMINAL_SKIP,
            reason="live_account_not_enabled",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_account_not_enabled",
            state_reclassified=True,
        )
    if source_state.status != "active":
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_source_deactivated",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    unresolved_lower_sequence = await session.scalar(
        select(LiveCopyFillState.sequence_index)
        .where(
            LiveCopyFillState.account_key == account.key,
            LiveCopyFillState.source_wallet == intent.source_wallet,
            LiveCopyFillState.source_fill_id == intent.source_fill_id,
            LiveCopyFillState.sequence_index < intent.sequence_index,
            exists(
                select(TradingOrder.id).where(
                    TradingOrder.id == LiveCopyFillState.trading_order_id,
                    live_copy_unresolved_order_predicate(),
                )
            ),
        )
        .order_by(LiveCopyFillState.sequence_index.asc())
        .limit(1)
        .with_for_update()
    )
    if any(
        state.sequence_index < intent.sequence_index
        and (
            state.outcome
            not in {
                LIVE_COPY_OUTCOME_TERMINAL_SKIP,
                LIVE_COPY_OUTCOME_BASELINE_IGNORED,
                LIVE_COPY_OUTCOME_ORDER,
            }
            or state.sequence_index == unresolved_lower_sequence
        )
        for state in plan_states
    ):
        raise LiveCopyEntryLifecycleDeferred("live_prior_source_fill_pending")
    predecessor = await get_prior_live_copy_fill_blocker(
        session,
        account_key=account.key,
        source_wallet=intent.source_wallet,
        fill=source_fill,
    )
    if predecessor is not None:
        raise LiveCopyEntryLifecycleDeferred("live_prior_source_fill_pending")

    competing_position = await session.scalar(
        select(TradingPosition.id)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == intent.coin,
            TradingPosition.source_wallet.not_in((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
            TradingPosition.size > POSITION_EPSILON,
        )
        .with_for_update()
        .limit(1)
    )
    competing_order = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.source_wallet.not_in((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
            live_copy_unresolved_order_predicate(),
        )
        .with_for_update()
        .limit(1)
    )
    same_source_unresolved_order = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_key == account.key,
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.source_wallet == intent.source_wallet,
            or_(
                TradingOrder.source_fill_id != intent.source_fill_id,
                TradingOrder.sequence_index != intent.sequence_index,
            ),
            live_copy_unresolved_order_predicate(),
        )
        .with_for_update()
        .limit(1)
    )
    position_result = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account.key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == intent.coin,
            TradingPosition.source_wallet.in_((intent.source_wallet, LIVE_EXCHANGE_SOURCE)),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    positions = list(position_result.all())
    source_position = next(
        (position for position in positions if position.source_wallet == intent.source_wallet),
        None,
    )
    exchange_position = next(
        (position for position in positions if position.source_wallet == LIVE_EXCHANGE_SOURCE),
        None,
    )
    source_has_matching_position = (
        source_position is not None
        and source_position.size > POSITION_EPSILON
        and source_position.side == intent.side
    )
    normal_entry_is_post_activation = is_live_copy_fill_post_baseline(
        source_state,
        fill=source_fill,
        origin=target_state.origin,
        observed_at=target_state.observed_at,
        first_observed_at=target_state.first_observed_at,
    )
    raw_json = wallet_fill.raw_json if isinstance(wallet_fill.raw_json, dict) else {}
    owned_continuation = await live_copy_entry_follows_owned_position_lifecycle(
        session,
        source_state=source_state,
        fill=source_fill,
        action=target_state.action,
        side=target_state.side,
        start_position=raw_json.get("startPosition"),
    )
    if not source_state.entry_eligible and not owned_continuation:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_retained_source_new_market",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    if not normal_entry_is_post_activation and not owned_continuation:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_BASELINE_IGNORED,
            reason="live_copy_baseline_entry",
        )
        raise LiveCopyEntryLifecycleDeferred(
            "live_source_lifecycle_reclassified",
            state_reclassified=True,
        )
    planned_open_to_add = (
        target_state.action == "open" and intent.action == "add" and source_has_matching_position
    )
    if (target_state.action != intent.action and not planned_open_to_add) or (
        intent.action == "add" and not source_has_matching_position
    ):
        raise LiveCopyEntryLifecycleDeferred("live_copy_plan_reclassified")
    conflict_reason: str | None = None
    if competing_position is not None or competing_order is not None:
        conflict_reason = "live_market_reserved_by_other_source"
    elif (
        source_position is not None
        and source_position.size > POSITION_EPSILON
        and source_position.side != intent.side
    ):
        conflict_reason = "live_market_reserved_by_other_source"
    elif exchange_position is not None and exchange_position.size > POSITION_EPSILON:
        if source_position is None or exchange_position.side != intent.side:
            conflict_reason = "live_exchange_position_conflict"
        elif (
            exchange_position.size > source_position.size + POSITION_EPSILON
            and same_source_unresolved_order is None
        ):
            conflict_reason = "live_exchange_position_conflict"
    if conflict_reason is not None:
        await mark_live_copy_entry_state_terminal(
            session,
            state=target_state,
            outcome=LIVE_COPY_OUTCOME_TERMINAL_SKIP,
            reason=conflict_reason,
        )
        raise LiveCopyEntryLifecycleDeferred(
            conflict_reason,
            state_reclassified=True,
        )


async def mark_live_copy_entry_state_terminal(
    session: AsyncSession,
    *,
    state: LiveCopyFillState,
    outcome: str,
    reason: str,
) -> None:
    """Finish a stale copied-entry decision without manufacturing an order row."""

    state.outcome = outcome
    state.reason = reason
    state.next_attempt_at = None
    state.fill_complete = False
    state.trading_order_id = None
    state.decision_at = datetime.now(UTC)
    await session.flush()
    await session.commit()


@dataclass(frozen=True)
class LiveCopyExposure:
    """Notional exposure that copy sizing and the entry gate count against caps."""

    source_usd: Decimal
    account_usd: Decimal


async def load_live_copy_exposure(
    session: AsyncSession,
    *,
    account: TradingAccount,
    source_wallet: str,
    coin: str,
    settings: Settings,
    exclude_client_order_id: str | None = None,
    lock: bool = False,
) -> LiveCopyExposure:
    """Sum current position notional and unfilled entry orders.

    Account exposure takes the larger of the attributed source positions and the
    authoritative exchange positions: source positions move immediately on fills,
    while exchange positions also include unattributed exposure.
    """

    capacity_dex = live_dex_from_coin(coin)
    unified_mode = live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED
    position_query = select(TradingPosition).where(
        TradingPosition.account_key == account.key,
        TradingPosition.account_type == "live",
        TradingPosition.size > POSITION_EPSILON,
    )
    order_query = select(TradingOrder).where(
        TradingOrder.account_key == account.key,
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet != LIVE_EXCHANGE_SOURCE,
        TradingOrder.reduce_only.is_(False),
        live_copy_unresolved_order_predicate(),
    )
    if exclude_client_order_id is not None:
        order_query = order_query.where(
            or_(
                TradingOrder.client_order_id.is_(None),
                TradingOrder.client_order_id != exclude_client_order_id,
            )
        )
    if lock:
        position_query = position_query.execution_options(populate_existing=True).with_for_update()
        order_query = order_query.execution_options(populate_existing=True).with_for_update()
    positions = [
        position
        for position in (await session.scalars(position_query)).all()
        if unified_mode or live_dex_from_coin(position.coin) == capacity_dex
    ]
    orders = [
        order
        for order in (await session.scalars(order_query)).all()
        if unified_mode or live_dex_from_coin(order.coin) == capacity_dex
    ]
    order_ids = [order.id for order in orders if order.id is not None]
    materialized_size_by_order: dict[Any, Decimal] = {}
    if order_ids:
        fill_query = select(TradingFill).where(TradingFill.order_id.in_(order_ids))
        if lock:
            fill_query = fill_query.execution_options(populate_existing=True).with_for_update()
        for fill in (await session.scalars(fill_query)).all():
            if fill.order_id is None:
                continue
            materialized_size_by_order[fill.order_id] = materialized_size_by_order.get(
                fill.order_id, ZERO
            ) + abs(fill.size or ZERO)

    def position_notional(position: TradingPosition) -> Decimal:
        return max(live_position_current_notional(position), ZERO)

    def order_notional(order: TradingOrder) -> Decimal:
        return live_copy_capacity_order_reservation(
            order,
            materialized_size=materialized_size_by_order.get(order.id, ZERO),
        )

    source_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet == source_wallet),
        ZERO,
    )
    attributed_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet != LIVE_EXCHANGE_SOURCE),
        ZERO,
    )
    exchange_positions = sum(
        (position_notional(p) for p in positions if p.source_wallet == LIVE_EXCHANGE_SOURCE),
        ZERO,
    )
    source_orders = sum(
        (order_notional(order) for order in orders if order.source_wallet == source_wallet),
        ZERO,
    )
    all_orders = sum((order_notional(order) for order in orders), ZERO)
    return LiveCopyExposure(
        source_usd=source_positions + source_orders,
        account_usd=max(attributed_positions, exchange_positions) + all_orders,
    )


LIVE_SOURCE_LOSS_WINDOW = timedelta(days=7)


async def live_source_net_loss_usd(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    now: datetime,
) -> Decimal:
    """Realized net loss over the rolling window plus current unrealized loss."""

    realized = await session.scalar(
        select(
            func.coalesce(func.sum(TradingFill.realized_pnl_usd - TradingFill.fee_usd), ZERO)
        ).where(
            TradingFill.account_key == account_key,
            TradingFill.account_type == "live",
            TradingFill.source_wallet == source_wallet,
            TradingFill.filled_at >= now - LIVE_SOURCE_LOSS_WINDOW,
        )
    )
    position_result = await session.scalars(
        select(TradingPosition).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source_wallet,
            TradingPosition.size > POSITION_EPSILON,
        )
    )
    unrealized = sum(
        (live_position_unrealized_pnl(position) or ZERO for position in position_result.all()),
        ZERO,
    )
    return max(-((decimal_or_none(realized) or ZERO) + unrealized), ZERO)


def live_copy_allocation_equity(
    account: TradingAccount,
    *,
    settings: Settings,
    dex: str | None,
) -> Decimal:
    if live_capital_mode(settings) == LIVE_CAPITAL_MODE_UNIFIED:
        equity_usd = live_unified_equity_usd(account)
        return equity_usd if equity_usd > ZERO else account.equity_usd or ZERO
    return live_perp_equity_usd(account, dex=dex)


async def validate_live_copy_entry_capacity_gate(
    session: AsyncSession,
    *,
    account: TradingAccount,
    intent: TradeIntent,
    settings: Settings,
) -> None:
    """Reject a stale copied entry when current exposure leaves no room for it."""

    if intent.reduce_only or intent.source_wallet in {
        LIVE_EXCHANGE_SOURCE,
        LIVE_MANUAL_TEST_SOURCE,
    }:
        return
    allocation_pct = decimal_or_none(intent.allocation_pct)
    if allocation_pct is None:
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")
    await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
    allocation_equity_usd = live_copy_allocation_equity(
        account,
        settings=settings,
        dex=live_dex_from_coin(intent.coin),
    )
    if allocation_equity_usd <= ZERO:
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")
    exposure = await load_live_copy_exposure(
        session,
        account=account,
        source_wallet=intent.source_wallet,
        coin=intent.coin,
        settings=settings,
        exclude_client_order_id=intent.client_order_id,
        lock=True,
    )
    allocation_usd = allocation_equity_usd * allocation_pct
    source_room = (
        settings.trading_copy_max_source_exposure_multiple * allocation_usd - exposure.source_usd
    )
    total_room = (
        settings.trading_copy_max_total_exposure_multiple * allocation_equity_usd
        - exposure.account_usd
    )
    incoming_notional = max(decimal_or_none(intent.notional_usd) or ZERO, ZERO)
    if (
        incoming_notional > source_room + POSITION_EPSILON
        or incoming_notional > total_room + POSITION_EPSILON
    ):
        raise LiveCopyEntryLifecycleDeferred("live_allocation_capacity_changed")


def live_copy_capacity_order_reservation(
    order: TradingOrder,
    *,
    materialized_size: Decimal,
) -> Decimal:
    """Notional still reserved by an entry order that has not fully materialized."""

    requested_size = decimal_or_none(order.requested_size) or ZERO
    filled_size = decimal_or_none(order.filled_size) or ZERO
    expected_size = (
        (filled_size if filled_size > POSITION_EPSILON else requested_size)
        if order.status == "filled"
        else requested_size
    )
    outstanding_size = max(expected_size - materialized_size, ZERO)
    if outstanding_size <= POSITION_EPSILON:
        return ZERO
    requested_notional = max(decimal_or_none(order.requested_notional_usd) or ZERO, ZERO)
    if requested_size <= POSITION_EPSILON:
        return requested_notional
    return requested_notional * min(outstanding_size / requested_size, Decimal("1"))


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
            is_live_copy_entry = not intent.reduce_only and intent.source_wallet not in {
                LIVE_EXCHANGE_SOURCE,
                LIVE_MANUAL_TEST_SOURCE,
            }
            existing_order = await load_live_order_by_client_order_id(
                session,
                client_order_id=intent.client_order_id,
            )
            will_dispatch = (
                existing_order is None
                or existing_order.status == "ready"
                or is_retryable_live_order_submit_failure(existing_order)
            )
            if not intent.reduce_only and account.status != "enabled":
                raise LiveOrderSubmitError(
                    "Live account is not enabled for entry execution.",
                    status_code=409,
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
                    settings=settings,
                )

            if will_dispatch:
                try:
                    live_client.validate_account_order(account=account, intent=intent)
                except Exception as exc:
                    raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc

            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_entry_lifecycle_gate(
                    session,
                    account=account,
                    intent=intent,
                )
            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_exchange_market_cooldown(
                    session,
                    intent=intent,
                )
            if is_live_copy_entry and will_dispatch:
                await validate_live_copy_entry_capacity_gate(
                    session,
                    account=account,
                    intent=intent,
                    settings=settings,
                )

            order, dispatch, _ = await prepare_live_order_dispatch(
                session,
                intent=intent,
            )
            if order.status in TERMINAL_ORDER_STATUSES:
                if is_retryable_live_order_submit_failure(order):
                    if dispatch.attempt_count > 0 or dispatch.status != "pending":
                        dispatch = await create_next_live_order_dispatch(
                            session,
                            order=order,
                        )
                    reset_live_order_for_retry(order, intent=intent)
                    order.status = "ready"
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
            exchange_intent = replace(intent, client_order_id=dispatch.client_order_id)
            try:
                result = await live_client.submit_order(account=account, intent=exchange_intent)
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

            apply_live_order_result(
                order,
                result,
                dispatch=dispatch,
                updated_at=datetime.now(UTC),
            )
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


async def validate_live_copy_exchange_market_cooldown(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    now: datetime | None = None,
) -> None:
    """Defer copied entries while a recent market-wide OI-cap reject cools down."""

    checked_at = now or datetime.now(UTC)
    recent_reject = await session.scalar(
        select(TradingOrder.id)
        .where(
            TradingOrder.account_type == "live",
            TradingOrder.coin == intent.coin,
            TradingOrder.status == "rejected",
            TradingOrder.error.startswith(f"{OPEN_INTEREST_CAP.code}:"),
            TradingOrder.updated_at
            >= checked_at - timedelta(seconds=OPEN_INTEREST_CAP.retry_delay_seconds),
            TradingOrder.client_order_id != intent.client_order_id,
        )
        .order_by(TradingOrder.updated_at.desc())
        .limit(1)
    )
    if recent_reject is not None:
        raise LiveCopyEntryLifecycleDeferred(OPEN_INTEREST_CAP_COOLDOWN_REASON)


async def sync_live_position_margin_setting(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    coin: str,
    leverage: Decimal,
    margin_mode: MarginMode,
    settings: Settings,
    client: HyperliquidLiveTradingClient | None = None,
) -> bool:
    live_client = client or HyperliquidLiveTradingClient(settings=settings)
    position_query = (
        select(TradingPosition)
        .where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.coin == coin,
            TradingPosition.source_wallet.in_([source_wallet, LIVE_EXCHANGE_SOURCE]),
        )
        .execution_options(populate_existing=True)
    )

    def position_pair(
        positions: list[TradingPosition],
    ) -> tuple[TradingPosition | None, TradingPosition | None]:
        exchange_position = next(
            (position for position in positions if position.source_wallet == LIVE_EXCHANGE_SOURCE),
            None,
        )
        source_position = next(
            (position for position in positions if position.source_wallet == source_wallet),
            None,
        )
        return exchange_position, source_position

    def synchronize_position_metadata(
        positions: list[TradingPosition],
        *,
        response: dict[str, Any] | None,
    ) -> None:
        synced_at = datetime.now(UTC)
        for position in positions:
            position.leverage = leverage
            position.margin_mode = margin_mode
            position.margin_usd = margin_from_notional(position.notional_usd, leverage)
            position.raw_payload = merge_raw_payload(
                position.raw_payload,
                {
                    "marginSettingSync": {
                        "leverage": str(leverage),
                        "marginMode": margin_mode,
                        "sourceWallet": source_wallet,
                        "syncedAt": synced_at.isoformat(),
                        "exchangeResponse": response,
                    }
                },
            )

    preflight_account = await load_live_account(session, account_key=account_key)
    validate_live_account_identity(preflight_account, settings=settings)
    preflight_result = await session.scalars(position_query)
    preflight_positions = list(preflight_result.all())
    exchange_position, source_position = position_pair(preflight_positions)
    if exchange_position is None or source_position is None:
        await session.commit()
        return False

    exchange_change_needed = (
        exchange_position.leverage != leverage or exchange_position.margin_mode != margin_mode
    )
    if not exchange_change_needed:
        locked_result = await session.scalars(position_query.with_for_update())
        locked_positions = list(locked_result.all())
        exchange_position, source_position = position_pair(locked_positions)
        if exchange_position is None or source_position is None:
            await session.commit()
            return False
        exchange_change_needed = (
            exchange_position.leverage != leverage or exchange_position.margin_mode != margin_mode
        )
        if not exchange_change_needed:
            synchronize_position_metadata(locked_positions, response=None)
            await session.commit()
            return False

        # Do not retain position row locks while waiting for the account execution lock.
        await session.commit()

    try:
        async with job_lock(
            session,
            key=f"live_execution:{account_key}",
            ttl_seconds=120,
        ):
            account = await load_live_account_for_update(session, account_key=account_key)
            validate_live_account_identity(account, settings=settings)
            result = await session.scalars(position_query.with_for_update())
            positions = list(result.all())
            exchange_position, source_position = position_pair(positions)
            if exchange_position is None or source_position is None:
                await session.commit()
                return False

            changed = (
                exchange_position.leverage != leverage
                or exchange_position.margin_mode != margin_mode
            )
            await session.commit()
            response: dict[str, Any] | None = None
            if changed:
                try:
                    response = await live_client.update_margin_setting(
                        account=account,
                        coin=coin,
                        leverage=leverage,
                        margin_mode=margin_mode,
                    )
                except (
                    HyperliquidLiveOrderRejectedError,
                    HyperliquidLiveTradingConfigurationError,
                ) as exc:
                    raise LiveOrderSubmitError(str(exc) or exc.__class__.__name__) from exc
                except Exception as exc:
                    raise LiveOrderSubmitError(
                        "Live margin setting update failed before synchronization."
                    ) from exc

            locked_result = await session.scalars(position_query.with_for_update())
            positions = list(locked_result.all())
            if not any(
                position.source_wallet == LIVE_EXCHANGE_SOURCE for position in positions
            ) or not any(position.source_wallet == source_wallet for position in positions):
                raise LiveOrderSubmitError(
                    "Live margin setting changed externally but local position state disappeared."
                )
            synchronize_position_metadata(positions, response=response)
            if changed:
                record_audit_log(
                    session,
                    actor="copy_recovery",
                    action="live_position.margin_setting_synced",
                    payload={
                        "accountKey": account_key,
                        "coin": coin,
                        "leverage": str(leverage),
                        "marginMode": margin_mode,
                        "sourceWallet": source_wallet,
                    },
                )
            await session.commit()
            return changed
    except JobLockAlreadyHeldError as exc:
        raise LiveOrderSubmitError(
            "Live margin setting sync deferred because account execution is busy.",
            status_code=409,
        ) from exc


async def ensure_live_entry_intent_is_fresh(
    session: AsyncSession,
    *,
    intent: TradeIntent,
    order: TradingOrder | None,
    settings: Settings,
) -> None:
    ttl_reference = order.created_at if order is not None else intent.created_at
    if ttl_reference.tzinfo is None:
        ttl_reference = ttl_reference.replace(tzinfo=UTC)
    if datetime.now(UTC) - ttl_reference.astimezone(UTC) <= timedelta(
        seconds=settings.live_trading_entry_intent_ttl_seconds
    ):
        return

    expiry_error = live_entry_expiry_error(order)
    if order is not None and (
        order.status in {"planned", "ready", "failed", "canceled"}
        or (order.status == "rejected" and is_retryable_live_order_submit_failure(order))
    ):
        dispatch = await load_live_order_dispatch(session, order_id=order.id)
        order.status = "canceled"
        order.error = expiry_error
        if dispatch is not None and dispatch.status in {"pending", "dispatching", "uncertain"}:
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
        expiry_error,
        status_code=409,
    )


def live_entry_expiry_error(order: TradingOrder | None) -> str:
    if order is None:
        return LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR
    if order.status == "rejected":
        return LIVE_ENTRY_RETRY_EXPIRED_AFTER_REJECTION_ERROR
    if order.submitted_at is not None:
        return LIVE_ENTRY_RETRY_EXPIRED_AFTER_ATTEMPT_ERROR
    return LIVE_ENTRY_EXPIRED_BEFORE_SUBMISSION_ERROR


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
                    oid=dispatch.client_order_id,
                )
            except Exception as exc:
                refreshed_order = await session.get(TradingOrder, order.id)
                refreshed_dispatch = await session.get(TradingOrderDispatch, dispatch.id)
                if refreshed_order is not None and refreshed_dispatch is not None:
                    record_live_order_status_lookup(refreshed_dispatch, error=exc)
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
                record_live_order_status_lookup(
                    refreshed_dispatch,
                    response=status_response,
                )
                record_live_order_exchange_result(
                    refreshed_dispatch,
                    exchange_status=mapped_exchange_order_status(status_response) or "unknown",
                    error_code=None,
                    error_message=None,
                    raw_response=status_response,
                )
                await session.commit()
                recovered += 1
                continue

            record_live_order_status_lookup(
                refreshed_dispatch,
                response=status_response,
            )
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
    settings: Settings,
) -> None:
    now = datetime.now(UTC)

    async def reject(
        *,
        rule: str,
        message: str,
        observed: str | int | float | None = None,
        limit: str | int | float | None = None,
        trip_account: bool = True,
    ) -> None:
        if trip_account:
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
            trip_account=False,
        )
    if not live_reconciliation_is_fresh(account, settings=settings, now=now):
        await reject(
            rule="reconciliation_stale",
            message="Live entries require a fresh exchange reconciliation snapshot.",
            observed=(
                account.last_reconciled_at.isoformat() if account.last_reconciled_at else None
            ),
            limit=settings.live_trading_reconciliation_max_snapshot_age_seconds,
            trip_account=False,
        )
    signer_block = live_signer_entry_block(account, settings=settings)
    if signer_block is not None:
        await reject(
            rule=signer_block.rule,
            message=signer_block.message,
            observed=signer_block.status,
            limit="api_wallet",
            trip_account=signer_block.trip_account,
        )
    breach = account_loss_breach(
        await live_account_loss_state(
            session,
            account_key=account.key,
            now=now,
            since=account.loss_limits_reset_at,
        ),
        settings=settings,
    )
    if breach is not None:
        await reject(
            rule=breach.rule,
            message=f"Live account {breach.label} guard is active.",
            observed=str(breach.observed),
            limit=str(breach.limit),
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
    if order.exchange_order_id or order.filled_size > ZERO or order.filled_notional_usd > ZERO:
        return False
    if order.status == "rejected":
        payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
        exchange_reject = payload.get("exchangeReject")
        return bool(
            isinstance(exchange_reject, dict)
            and exchange_reject.get("transient") is True
            and exchange_reject.get("retryExhausted") is not True
        )
    if order.status != "failed":
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
    if (
        order.error == "skip:live_close_below_min_order_notional"
        and order.reduce_only
        and order.action in {"reduce", "close", "flip_close"}
    ):
        return True
    return order.error in {
        "skip:live_account_not_enabled",
        "skip:live_account_exit_only",
        "skip:live_execution_busy",
        "skip:live_execution_price_unavailable",
        "skip:live_matching_position_missing",
        "skip:live_preexisting_source_add",
        "skip:live_reconciliation_failed",
        "skip:live_reconciliation_incomplete",
        "skip:live_reconciliation_stale",
        "skip:live_source_leverage_missing",
        "skip:live_source_margin_mode_missing",
        "skip:source_account_margin_summary_missing",
        "skip:source_account_mode_unavailable",
        "skip:source_account_state_fetch_failed",
        "skip:source_account_state_missing",
        "skip:source_perp_equity_missing",
        "skip:source_perp_equity_zero",
        "skip:source_unified_equity_unavailable",
    }


def reset_live_order_for_retry(order: TradingOrder, *, intent: TradeIntent) -> None:
    preserve_original_submit_at = order.status == "rejected"
    retry_reason = live_order_retry_reason(order)
    order.status = "planned"
    order.error = None
    if not preserve_original_submit_at:
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
    order.margin_mode = intent.margin_mode
    order.limit_price = intent.limit_price
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {
            "retry": {
                "reason": retry_reason,
                "requestedAt": datetime.now(UTC).isoformat(),
            }
        },
    )


def live_order_retry_reason(order: TradingOrder) -> str:
    if is_retryable_live_copy_skip(order):
        return str(order.error or "retryable_live_copy_skip").removeprefix("skip:")
    payload = order.raw_payload if isinstance(order.raw_payload, dict) else {}
    exchange_reject = payload.get("exchangeReject")
    if isinstance(exchange_reject, dict) and exchange_reject.get("code"):
        return str(exchange_reject["code"])
    return "market_metadata_available_after_previous_submit_failure"


def apply_live_order_result(
    order: TradingOrder,
    result: LiveOrderResult,
    *,
    dispatch: TradingOrderDispatch | None = None,
    updated_at: datetime,
) -> None:
    order.status = result.status
    order.exchange_order_id = result.exchange_order_id or order.exchange_order_id
    order.error = result.error
    order.raw_payload = merge_raw_payload(
        order.raw_payload,
        {"exchangeResponse": result.raw_response},
    )
    if result.status == "rejected":
        classification = classify_live_order_reject(result.error)
        retry_exhausted = (
            dispatch is not None and dispatch.attempt_number >= MAX_LIVE_ORDER_SUBMIT_ATTEMPTS
        )
        order.raw_payload = merge_raw_payload(
            order.raw_payload,
            {
                "exchangeReject": {
                    "code": classification.code,
                    "message": result.error,
                    "transient": classification.transient,
                    "retryExhausted": retry_exhausted,
                    "retryDelaySeconds": classification.retry_delay_seconds,
                }
            },
        )
        order.error = f"{classification.code}: {result.error or 'Order rejected.'}"
        if dispatch is not None:
            record_live_order_exchange_result(
                dispatch,
                exchange_status=result.status,
                error_code=classification.code,
                error_message=result.error,
                raw_response=result.raw_response,
            )
    elif dispatch is not None:
        record_live_order_exchange_result(
            dispatch,
            exchange_status=result.status,
            error_code=None,
            error_message=result.error,
            raw_response=result.raw_response,
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
    force_signer_check: bool = False,
) -> LiveReconciliationResult:
    """Run under the caller-held live_execution job lock.

    Network reads complete before the lifecycle lock. The subsequent fill and
    position mutation transaction shares the lifecycle fence with attribution
    recovery, so source ownership cannot be derived from rows reconciliation is
    concurrently replacing.
    """

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
        # Committed on its own so a failed reconciliation keeps the signer result.
        if await refresh_live_account_signer(
            account,
            client=client,
            settings=settings,
            now=reconciled_at,
            force=force_signer_check,
        ):
            await session.commit()
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
        funding_start_time_ms = await live_funding_reconciliation_start_time_ms(
            session,
            account_key=account.key,
            settings=settings,
            now=reconciled_at,
            lookback_minutes=lookback_minutes,
        )
        funding_result = await fetch_live_funding_by_time(
            client,
            user=user_address,
            start_time_ms=funding_start_time_ms,
        )
        portfolio_history_result = await fetch_live_portfolio_history(
            client,
            user=user_address,
        )
        cash_flow_start_time_ms = await live_cash_flow_reconciliation_start_time_ms(
            session,
            account=account,
            settings=settings,
            now=reconciled_at,
            lookback_minutes=lookback_minutes,
        )
        cash_flow_result = await fetch_live_cash_flows_by_time(
            client,
            user=user_address,
            start_time_ms=cash_flow_start_time_ms,
            end_time_ms=int(reconciled_at.timestamp() * 1000),
        )
        historical_cash_flow_result: LiveCashFlowFetchResult | None = None
        if (
            live_performance_backfill_version(account) < LIVE_PERFORMANCE_BACKFILL_VERSION
            and portfolio_history_result.complete
            and portfolio_history_result.account_values
        ):
            historical_cash_flow_result = (
                cash_flow_result
                if cash_flow_start_time_ms == 0
                else await fetch_live_cash_flows_by_time(
                    client,
                    user=user_address,
                    start_time_ms=0,
                    end_time_ms=int(reconciled_at.timestamp() * 1000),
                )
            )
        perp_snapshot = await fetch_live_perp_states(client, user_address=user_address)
        spot_state = await fetch_live_spot_state(client, user_address=user_address)
        user_abstraction = await fetch_live_user_abstraction(
            client,
            user_address=user_address,
        )
        await acquire_live_copy_lifecycle_lock(session, account_key=account.key)
        inserted_fills = await reconcile_live_fills(
            session,
            account=account,
            fills=list(fill_result.fills),
        )
        inserted_funding_payments = await reconcile_live_funding_payments(
            session,
            account=account,
            payments=list(funding_result.payments),
        )
        inserted_cash_flows = await reconcile_live_account_cash_flows(
            session,
            account=account,
            user_address=user_address,
            updates=[
                *cash_flow_result.updates,
                *(
                    historical_cash_flow_result.updates
                    if historical_cash_flow_result is not None
                    else ()
                ),
            ],
        )
        if cash_flow_start_time_ms == 0 and cash_flow_result.complete:
            mark_live_cash_flow_backfill_complete(account, reconciled_at=reconciled_at)
        updated_orders_from_fills = await update_live_orders_from_reconciled_fills(
            session,
            account_key=account.key,
        )
        await recompute_live_account_fill_totals(session, account=account)
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
            funding_result=funding_result,
            cash_flow_result=cash_flow_result,
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
            funding_result=funding_result,
            cash_flow_result=cash_flow_result,
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
        previous_reconciliation_status = live_reconciliation_status(account)
        if reconciliation_status == "complete":
            history_backfilled = await backfill_live_account_performance_history(
                session,
                account=account,
                user_address=user_address,
                portfolio_history=portfolio_history_result,
                cash_flow_history=historical_cash_flow_result,
                reconciled_at=reconciled_at,
            )
            if not history_backfilled:
                await ensure_live_account_performance_baseline(
                    session,
                    account=account,
                )
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
        if reconciliation_status == "complete":
            await record_live_account_performance_snapshot(
                session,
                account=account,
                recorded_at=reconciled_at,
            )
        if (
            reconciliation_status != "complete"
            and account.status == "enabled"
            and previous_reconciliation_status != reconciliation_status
        ):
            payload = {
                "accountKey": account.key,
                "incompleteComponents": list(incomplete_components),
                "componentErrors": component_errors,
                "accountStatus": account.status,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_partial",
                severity="warning",
                message=(
                    "Live account reconciliation is partial. New entries are blocked until "
                    "a complete snapshot succeeds."
                ),
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
        await thin_live_account_performance_snapshots(session, account_key=account.key)
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
        fetched_funding_payments=len(funding_result.payments),
        inserted_funding_payments=inserted_funding_payments,
        fetched_cash_flows=(
            len(cash_flow_result.updates)
            + (
                len(historical_cash_flow_result.updates)
                if historical_cash_flow_result is not None
                else 0
            )
        ),
        inserted_cash_flows=inserted_cash_flows,
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


def reconciliation_component_errors(
    *,
    order_result: LiveOrderReconciliationResult,
    unresolved_order_ids: tuple[UUID, ...],
    fill_result: LiveFillFetchResult,
    funding_result: LiveFundingFetchResult,
    cash_flow_result: LiveCashFlowFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
) -> dict[str, str]:
    errors: dict[str, str] = {}
    if unresolved_order_ids:
        errors["orders"] = f"{len(unresolved_order_ids)} live order statuses remain unresolved."
    if not fill_result.complete:
        errors["fills"] = fill_result.error or "Live fill history is incomplete."
    if not funding_result.complete:
        errors["funding"] = funding_result.error or "Live funding history is incomplete."
    if not cash_flow_result.complete:
        errors["cash_flows"] = (
            cash_flow_result.error or "Live account cash flow history is incomplete."
        )
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
    funding_result: LiveFundingFetchResult,
    cash_flow_result: LiveCashFlowFetchResult,
    perp_snapshot: LivePerpSnapshot,
    spot_state: dict[str, Any],
    user_abstraction: Any,
    position_result: LivePositionReconciliationResult,
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
        "funding": {
            "status": "complete" if funding_result.complete else "partial",
            "fetched": len(funding_result.payments),
            "pages": funding_result.pages,
            "nextStartTimeMs": funding_result.next_start_time_ms,
            "error": funding_result.error,
        },
        "cashFlows": {
            "status": "complete" if cash_flow_result.complete else "partial",
            "fetched": len(cash_flow_result.updates),
            "pages": cash_flow_result.pages,
            "nextStartTimeMs": cash_flow_result.next_start_time_ms,
            "error": cash_flow_result.error,
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
        previous_reconciliation_status = live_reconciliation_status(account)
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
        if account.status == "enabled" and previous_reconciliation_status != "failed":
            payload = {
                "accountKey": account.key,
                "error": error,
                "accountStatus": account.status,
                "lifecycleVersion": account.lifecycle_version,
            }
            record_risk_event(
                session,
                event_type="live_reconciliation_failed",
                severity="warning",
                message=(
                    "Live account reconciliation failed. New entries are blocked until "
                    "a complete snapshot succeeds."
                ),
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


def build_testnet_live_trade_intent(
    *,
    account: TradingAccount,
    coin: str,
    side: str,
    notional_usd: Decimal,
    limit_price: Decimal,
    leverage: Decimal,
    reduce_only: bool,
    margin_mode: MarginMode = "cross",
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
        side=parse_trade_side(side),
        size=size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        margin_mode=margin_mode,
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
        side=parse_trade_side(position.side),
        size=position.size,
        notional_usd=notional_usd,
        margin_usd=margin_from_notional(notional_usd, leverage),
        leverage=leverage,
        margin_mode=(
            parse_margin_mode(position.margin_mode)
            if position.margin_mode in MARGIN_MODES
            else "cross"
        ),
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
    for summary in state_summaries:
        state_time = max_optional_numeric(state_time, summary.get("time"))

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


def live_account_user_address(account: TradingAccount, *, settings: Settings) -> str:
    user_address = account.wallet_address or settings.hyperliquid_wallet_address
    if not user_address:
        raise LiveReconciliationError(
            "Live account requires wallet_address or HYPERLIQUID_WALLET_ADDRESS."
        )
    return user_address.lower()
