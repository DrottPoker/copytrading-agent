import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import blake2s
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import (
    PaperCopyAllocation,
    TradingAccount,
    TradingOrder,
    TradingPosition,
)
from app.integrations.hyperliquid_client import HyperliquidClient
from app.integrations.hyperliquid_live_client import (
    HyperliquidLiveTradingClient,
)
from app.services.hyperliquid_account_mode import (
    user_abstraction_is_standard,
)
from app.services.job_lock_service import (
    JobLockAlreadyHeldError,
    job_lock,
)
from app.services.live_account_access import (
    get_live_position,
    live_account_has_incomplete_close_operation,
    live_account_nonterminal_order_count,
    load_live_account,
    load_live_account_for_update,
    load_live_order_by_client_order_id,
    load_live_position,
    validate_live_account_identity,
)
from app.services.live_account_state import (
    LIVE_CAPITAL_MODE_UNIFIED,
    account_last_reconciliation,
    live_capital_mode,
    live_reconciliation_is_fresh,
    live_reconciliation_status,
    live_tradable_equity_usd,
)
from app.services.live_close_service import (
    LIVE_HARD_STOP_MARKER,
    build_live_close_position_intent,
    load_live_close_mids,
)
from app.services.live_copy_state_service import (
    activate_live_copy_account_sources,
    load_owned_live_copy_account_source_pairs,
    synchronize_live_copy_account_source_activity,
)
from app.services.live_exchange_parsing import (
    decimal_or_none,
    merge_raw_payload,
    normalize_optional_address,
)
from app.services.live_order_submission import LiveOrderLifecycleResult, submit_live_trade_intent
from app.services.live_performance_service import (
    restart_breached_live_loss_limits,
)
from app.services.live_position_reconciliation import (
    live_position_mark_price,
)
from app.services.live_reconciliation_service import (
    reconcile_live_trading_account,
    run_live_trading_account_reconciliation,
)
from app.services.live_signer_service import (
    live_signer_entry_block,
)
from app.services.live_trading_constants import (
    LIVE_MANUAL_TEST_SOURCE,
    POSITION_EPSILON,
)
from app.services.live_trading_errors import (
    LiveAccountCreateError,
    LiveAccountDeleteError,
    LiveOrderSubmitError,
    LiveTradingServiceError,
)
from app.services.trading_core import (
    MarginMode,
    TradeIntent,
    build_copy_trade_intent,
    margin_from_notional,
    parse_trade_side,
)
from app.services.trading_safety_service import (
    apply_live_account_status,
    cancel_unsent_live_entries,
    record_audit_log,
)

logger = logging.getLogger(__name__)

ZERO = Decimal("0")
LIVE_ACCOUNT_KEY_MAX_LENGTH = 64
EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-f0-9]{40}$")


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
