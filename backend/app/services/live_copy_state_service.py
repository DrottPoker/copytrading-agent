"""Durable per-account live-copy lifecycle state.

This module intentionally owns the state that decides whether an imported source
fill is eligible for live execution.  Raw ``WalletFill`` rows remain the audit
record.  ``TradingOrder`` is only linked after the execution layer has made a
real order or terminal order-skip decision.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import (
    Numeric,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    not_,
    or_,
    select,
    text,
    union,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LiveCopyFillState,
    LiveCopySourceState,
    TradingFill,
    TradingOrder,
    TradingPosition,
    WalletFill,
    WatchedWallet,
)

if TYPE_CHECKING:
    from app.services.paper_trading_service import SourceFillPart

from app.services.source_fill_ordering import (
    SOURCE_CLOSE_DIRECTIONS,
    source_fill_order_components,
    source_fill_order_key,
)

type LiveCopyProcessingOrigin = Literal[
    "realtime",
    "snapshot_recovery",
    "startup_recovery",
    "periodic_recovery",
]

LIVE_COPY_ORIGIN_REALTIME: LiveCopyProcessingOrigin = "realtime"
LIVE_COPY_ORIGIN_SNAPSHOT_RECOVERY: LiveCopyProcessingOrigin = "snapshot_recovery"
LIVE_COPY_ORIGIN_STARTUP_RECOVERY: LiveCopyProcessingOrigin = "startup_recovery"
LIVE_COPY_ORIGIN_PERIODIC_RECOVERY: LiveCopyProcessingOrigin = "periodic_recovery"

LIVE_COPY_OUTCOME_PENDING = "pending"
LIVE_COPY_OUTCOME_RETRYABLE = "retryable"
LIVE_COPY_OUTCOME_ORDER = "order"
LIVE_COPY_OUTCOME_TERMINAL_SKIP = "terminal_skip"
LIVE_COPY_OUTCOME_BASELINE_IGNORED = "baseline_ignored"

LIVE_COPY_TERMINAL_OUTCOMES = frozenset(
    {
        LIVE_COPY_OUTCOME_ORDER,
        LIVE_COPY_OUTCOME_TERMINAL_SKIP,
        LIVE_COPY_OUTCOME_BASELINE_IGNORED,
    }
)
LIVE_COPY_RETRY_BASE_SECONDS = 5
LIVE_COPY_RETRY_MAX_SECONDS = 300
LIVE_COPY_PROCESSING_LEASE_SECONDS = 60
LIVE_COPY_FILL_PLAN_VERSION = 1
POSITION_EPSILON = Decimal("0.000000000001")
LIVE_COPY_RESERVED_SOURCE_WALLETS = frozenset({"__exchange__", "__manual_testnet__"})
LIVE_COPY_ACTIVE_ORDER_STATUSES = frozenset(
    {
        "planned",
        "ready",
        "submitting",
        "uncertain",
        "submitted",
        "accepted",
        "partially_filled",
    }
)
LIVE_COPY_EXIT_ACTIONS = frozenset({"reduce", "close", "flip_close"})


def live_copy_unresolved_order_predicate():
    """Return orders whose exchange effect is not yet safe to overtake."""

    has_no_materialized_fill = not_(
        exists(select(TradingFill.id).where(TradingFill.order_id == TradingOrder.id))
    )
    materialized_filled_size = (
        select(func.coalesce(func.sum(TradingFill.size), literal(0)))
        .where(TradingFill.order_id == TradingOrder.id)
        .scalar_subquery()
    )
    return or_(
        TradingOrder.status.in_(LIVE_COPY_ACTIVE_ORDER_STATUSES),
        and_(
            TradingOrder.status == "filled",
            or_(
                has_no_materialized_fill,
                materialized_filled_size + literal(POSITION_EPSILON)
                < func.coalesce(TradingOrder.filled_size, literal(0)),
            ),
        ),
    )


def live_copy_unresolved_exit_predicate():
    """Return exit lifecycle rows that must retain source monitoring."""

    return and_(
        LiveCopyFillState.source_wallet != "",
        LiveCopyFillState.source_wallet.not_in(LIVE_COPY_RESERVED_SOURCE_WALLETS),
        LiveCopyFillState.action.in_(LIVE_COPY_EXIT_ACTIONS),
        LiveCopyFillState.fill_complete.is_(False),
        LiveCopyFillState.outcome.in_(
            (LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)
        ),
    )


class LiveCopyProcessingDeferred(RuntimeError):
    """A transient prerequisite prevented a live-copy part from being decided."""

    def __init__(
        self,
        reason: str,
        message: str | None = None,
        *,
        retry_base_seconds: int | None = None,
    ) -> None:
        self.reason = reason
        self.retry_base_seconds = retry_base_seconds
        super().__init__(message or reason)


@dataclass(frozen=True, slots=True)
class LiveCopyPartClaim:
    """A lease for one source-fill part, or the reason it was not claimable."""

    state: LiveCopyFillState | None
    claimed: bool
    reason: Literal[
        "claimed",
        "baseline",
        "blocked",
        "complete",
        "missing_plan",
        "not_due",
    ]


def normalize_live_copy_source_wallet(source_wallet: str) -> str:
    return str(source_wallet).strip().lower()


def live_copy_retry_delay_seconds(
    attempt_count: int,
    *,
    base_seconds: int = LIVE_COPY_RETRY_BASE_SECONDS,
    max_seconds: int = LIVE_COPY_RETRY_MAX_SECONDS,
) -> int:
    """Return a bounded exponential delay for a failed processing attempt."""

    safe_base = max(int(base_seconds), 1)
    safe_max = max(int(max_seconds), safe_base)
    exponent = min(max(int(attempt_count) - 1, 0), 16)
    return min(safe_base * (2**exponent), safe_max)


async def get_live_copy_source_state(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    for_update: bool = False,
) -> LiveCopySourceState | None:
    source = normalize_live_copy_source_wallet(source_wallet)
    query = select(LiveCopySourceState).where(
        LiveCopySourceState.account_key == account_key,
        LiveCopySourceState.source_wallet == source,
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def ensure_live_copy_source_state(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    now: datetime | None = None,
    reactivate: bool = True,
    eligibility_started_at: datetime | None = None,
    entry_eligible: bool | None = None,
) -> LiveCopySourceState:
    """Get or atomically establish a source baseline for one live account.

    The baseline contains every external fill identifier at the latest source
    timestamp that existed when the account began copying this source.  A late
    fill at that same timestamp remains eligible because it was not in this
    captured identifier set.
    """

    source = normalize_live_copy_source_wallet(source_wallet)
    await acquire_live_copy_lifecycle_lock(session, account_key=account_key)
    existing = await get_live_copy_source_state(
        session,
        account_key=account_key,
        source_wallet=source,
        for_update=True,
    )
    if existing is not None:
        activation_epoch = (
            ensure_utc(eligibility_started_at) if eligibility_started_at is not None else None
        )
        selected_after_existing_activation = (
            activation_epoch is not None and activation_epoch > ensure_utc(existing.activated_at)
        )
        if (existing.status == "inactive" and reactivate) or selected_after_existing_activation:
            await activate_live_copy_source_state(
                session,
                source_state=existing,
                now=now,
                activation_epoch=activation_epoch,
                entry_eligible=(
                    bool(entry_eligible)
                    if entry_eligible is not None
                    else activation_epoch is not None
                ),
            )
        elif entry_eligible is not None:
            existing.entry_eligible = bool(entry_eligible)
            await session.flush()
        return existing

    baseline_timestamp_ms, baseline_fill_ids = await capture_live_copy_source_baseline(
        session,
        source_wallet=source,
    )

    baseline_captured_at = utc_now(now)
    activated_at = (
        ensure_utc(eligibility_started_at)
        if eligibility_started_at is not None
        else baseline_captured_at
    )
    state = LiveCopySourceState(
        account_key=account_key,
        source_wallet=source,
        account_type="live",
        status="active",
        entry_eligible=bool(entry_eligible),
        activated_at=activated_at,
        baseline_completed_at=baseline_captured_at,
        baseline_source_timestamp_ms=(
            int(baseline_timestamp_ms) if baseline_timestamp_ms is not None else None
        ),
        baseline_fill_ids=baseline_fill_ids,
        scan_high_water_timestamp_ms=None,
        scan_high_water_coin=None,
        scan_high_water_direction_rank=None,
        scan_high_water_position=None,
        scan_high_water_fill_id_numeric=None,
        scan_high_water_fill_id=None,
        preexisting_markets={},
    )
    session.add(state)
    await session.flush()
    return state


async def activate_live_copy_source_state(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    now: datetime | None = None,
    activation_epoch: datetime | None = None,
    entry_eligible: bool = True,
) -> None:
    """Start a fresh baseline after a source becomes eligible again."""

    baseline_timestamp_ms, baseline_fill_ids = await capture_live_copy_source_baseline(
        session,
        source_wallet=source_state.source_wallet,
    )
    baseline_captured_at = utc_now(now)
    source_state.status = "active"
    source_state.entry_eligible = entry_eligible
    source_state.activated_at = (
        ensure_utc(activation_epoch) if activation_epoch is not None else baseline_captured_at
    )
    source_state.baseline_completed_at = baseline_captured_at
    source_state.baseline_source_timestamp_ms = baseline_timestamp_ms
    source_state.baseline_fill_ids = baseline_fill_ids
    source_state.scan_high_water_timestamp_ms = None
    source_state.scan_high_water_coin = None
    source_state.scan_high_water_direction_rank = None
    source_state.scan_high_water_position = None
    source_state.scan_high_water_fill_id_numeric = None
    source_state.scan_high_water_fill_id = None
    source_state.preexisting_markets = {}
    await close_live_copy_entry_states_at_activation_baseline(
        session,
        source_state=source_state,
    )
    await session.flush()


async def close_live_copy_entry_states_at_activation_baseline(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
) -> int:
    """Prevent pre-Start entry retries while preserving unfinished exit work."""

    baseline_timestamp_ms = source_state.baseline_source_timestamp_ms
    if baseline_timestamp_ms is None:
        return 0
    baseline_fill_ids = sorted(live_copy_baseline_fill_ids(source_state))
    at_baseline = literal(False)
    if baseline_fill_ids:
        at_baseline = and_(
            LiveCopyFillState.source_timestamp_ms == int(baseline_timestamp_ms),
            LiveCopyFillState.source_fill_id.in_(baseline_fill_ids),
        )
    result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == source_state.account_key,
            LiveCopyFillState.source_wallet == source_state.source_wallet,
            LiveCopyFillState.action.in_(("open", "add", "flip_open")),
            LiveCopyFillState.outcome.in_((LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)),
            or_(
                LiveCopyFillState.source_timestamp_ms < int(baseline_timestamp_ms),
                at_baseline,
            ),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    entry_states = list(result.all())
    if not entry_states:
        return 0
    affected_fill_ids = {state.source_fill_id for state in entry_states}
    for state in entry_states:
        state.outcome = LIVE_COPY_OUTCOME_BASELINE_IGNORED
        state.reason = "live_account_restart_baseline"
        state.next_attempt_at = None
        state.fill_complete = False
        state.trading_order_id = None
        state.decision_at = utc_now()

    all_result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == source_state.account_key,
            LiveCopyFillState.source_wallet == source_state.source_wallet,
            LiveCopyFillState.source_fill_id.in_(affected_fill_ids),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    states_by_fill: dict[str, list[LiveCopyFillState]] = {}
    for state in all_result.all():
        states_by_fill.setdefault(state.source_fill_id, []).append(state)
    for states in states_by_fill.values():
        if states and all(state.outcome in LIVE_COPY_TERMINAL_OUTCOMES for state in states):
            for state in states:
                state.fill_complete = True
    return len(entry_states)


async def capture_live_copy_source_baseline(
    session: AsyncSession,
    *,
    source_wallet: str,
) -> tuple[int | None, list[str]]:
    source = normalize_live_copy_source_wallet(source_wallet)
    baseline_timestamp_ms = await session.scalar(
        select(func.max(WalletFill.timestamp_ms)).where(WalletFill.wallet_address == source)
    )
    if baseline_timestamp_ms is None:
        return None, []
    result = await session.scalars(
        select(WalletFill.external_fill_id)
        .where(
            WalletFill.wallet_address == source,
            WalletFill.timestamp_ms == int(baseline_timestamp_ms),
        )
        .order_by(WalletFill.external_fill_id.asc())
    )
    baseline_fill_ids = sorted({str(value) for value in result.all() if value is not None})
    return int(baseline_timestamp_ms), baseline_fill_ids


async def synchronize_live_copy_source_activity(
    session: AsyncSession,
    *,
    eligible_account_source_pairs: set[tuple[str, str]],
    entry_eligible_account_source_pairs: set[tuple[str, str]] | None = None,
    eligible_account_source_epochs: Mapping[tuple[str, str], datetime] | None = None,
    protected_account_keys: set[str] | None = None,
    target_account_keys: set[str] | None = None,
) -> int:
    """Deactivate flat source lanes that are no longer eligible for an account.

    Eligibility is account-specific.  A source assigned to one enabled account
    must not keep the corresponding source state active for another disabled
    account.
    """

    normalized_pairs = {
        (str(account_key), normalize_live_copy_source_wallet(source_wallet))
        for account_key, source_wallet in eligible_account_source_pairs
        if account_key and source_wallet
    }
    normalized_entry_eligible_pairs = {
        (str(account_key), normalize_live_copy_source_wallet(source_wallet))
        for account_key, source_wallet in (
            entry_eligible_account_source_pairs
            if entry_eligible_account_source_pairs is not None
            else eligible_account_source_pairs
        )
        if account_key and source_wallet
    }
    normalized_epochs = {
        (str(account_key), normalize_live_copy_source_wallet(source_wallet)): ensure_utc(epoch)
        for (account_key, source_wallet), epoch in (eligible_account_source_epochs or {}).items()
        if account_key and source_wallet and epoch is not None
    }
    for account_key, source_wallet in sorted(normalized_pairs):
        await ensure_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            reactivate=True,
            eligibility_started_at=normalized_epochs.get((account_key, source_wallet)),
            entry_eligible=(account_key, source_wallet) in normalized_entry_eligible_pairs,
        )
    protected_accounts = protected_account_keys or set()
    query = select(LiveCopySourceState).where(LiveCopySourceState.status == "active")
    if target_account_keys is not None:
        query = query.where(LiveCopySourceState.account_key.in_(target_account_keys))
    result = await session.scalars(
        query.order_by(
            LiveCopySourceState.account_key.asc(),
            LiveCopySourceState.source_wallet.asc(),
        )
    )
    state_keys = [
        (str(state.account_key), normalize_live_copy_source_wallet(state.source_wallet))
        for state in result.all()
    ]
    deactivated = 0
    for account_key, source_wallet in state_keys:
        # The lifecycle advisory lock is always acquired before locking the
        # source state and its fill rows.  Other lifecycle paths use this same
        # order, which prevents an activity sweep from inverting their locks.
        await acquire_live_copy_lifecycle_lock(session, account_key=account_key)
        source_state = await get_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            for_update=True,
        )
        if source_state is None or source_state.status != "active":
            continue
        if source_state.account_key in protected_accounts:
            continue
        eligible = (source_state.account_key, source_state.source_wallet) in normalized_pairs
        if eligible:
            continue
        source_state.entry_eligible = False
        owns_position = await session.scalar(
            select(
                exists(
                    select(TradingPosition.id).where(
                        TradingPosition.account_key == source_state.account_key,
                        TradingPosition.account_type == "live",
                        TradingPosition.source_wallet == source_state.source_wallet,
                        TradingPosition.size > POSITION_EPSILON,
                    )
                )
            )
        )
        if owns_position:
            continue
        has_active_order = await session.scalar(
            select(
                exists(
                    select(TradingOrder.id).where(
                        TradingOrder.account_key == source_state.account_key,
                        TradingOrder.account_type == "live",
                        TradingOrder.source_wallet == source_state.source_wallet,
                        live_copy_unresolved_order_predicate(),
                    )
                )
            )
        )
        if has_active_order:
            continue
        source_state.status = "inactive"
        source_state.entry_eligible = False
        source_state.preexisting_markets = {}
        fill_result = await session.scalars(
            select(LiveCopyFillState)
            .where(
                LiveCopyFillState.account_key == source_state.account_key,
                LiveCopyFillState.source_wallet == source_state.source_wallet,
                LiveCopyFillState.fill_complete.is_(False),
                LiveCopyFillState.outcome.in_(
                    (LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)
                ),
            )
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        for fill_state in fill_result.all():
            fill_state.outcome = LIVE_COPY_OUTCOME_BASELINE_IGNORED
            fill_state.reason = "live_source_deactivated"
            fill_state.next_attempt_at = None
            fill_state.fill_complete = True
            fill_state.trading_order_id = None
            fill_state.decision_at = utc_now()
        deactivated += 1
    if deactivated:
        await session.flush()
    return deactivated


async def load_owned_live_copy_account_source_pairs(
    session: AsyncSession,
    *,
    account_keys: set[str] | None = None,
) -> set[tuple[str, str]]:
    """Return retained live source lanes with copied exposure or in-flight orders."""

    position_query = select(TradingPosition.account_key, TradingPosition.source_wallet).where(
        TradingPosition.account_type == "live",
        TradingPosition.source_wallet != "",
        TradingPosition.source_wallet.not_in(LIVE_COPY_RESERVED_SOURCE_WALLETS),
        TradingPosition.size > POSITION_EPSILON,
    )
    order_query = select(TradingOrder.account_key, TradingOrder.source_wallet).where(
        TradingOrder.account_type == "live",
        TradingOrder.source_wallet != "",
        TradingOrder.source_wallet.not_in(LIVE_COPY_RESERVED_SOURCE_WALLETS),
        live_copy_unresolved_order_predicate(),
    )
    exit_query = select(
        LiveCopyFillState.account_key,
        LiveCopyFillState.source_wallet,
    ).where(live_copy_unresolved_exit_predicate())
    if account_keys is not None:
        if not account_keys:
            return set()
        position_query = position_query.where(TradingPosition.account_key.in_(account_keys))
        order_query = order_query.where(TradingOrder.account_key.in_(account_keys))
        exit_query = exit_query.where(LiveCopyFillState.account_key.in_(account_keys))
    result = await session.execute(union(position_query, order_query, exit_query))
    return {
        (str(account_key), normalize_live_copy_source_wallet(source_wallet))
        for account_key, source_wallet in result.all()
        if account_key and source_wallet
    }


async def load_live_copy_source_eligibility_epochs(
    session: AsyncSession,
) -> dict[str, datetime]:
    """Return the durable active-selection epoch for each selected source."""

    result = await session.execute(
        select(
            WatchedWallet.address,
            WatchedWallet.copy_eligibility_started_at,
        ).where(
            WatchedWallet.copy_eligibility_started_at.is_not(None),
        )
    )
    return {
        normalize_live_copy_source_wallet(source_wallet): ensure_utc(eligibility_started_at)
        for source_wallet, eligibility_started_at in result.all()
        if source_wallet and eligibility_started_at is not None
    }


async def synchronize_live_copy_account_source_activity(
    session: AsyncSession,
    *,
    account_key: str,
    eligible_source_wallets: set[str],
    protected: bool = False,
) -> int:
    """Synchronize source activity for one account without touching other accounts."""

    return await synchronize_live_copy_source_activity(
        session,
        eligible_account_source_pairs={
            (account_key, source_wallet)
            for source_wallet in eligible_source_wallets
            if source_wallet
        },
        entry_eligible_account_source_pairs={
            (account_key, source_wallet)
            for source_wallet in eligible_source_wallets
            if source_wallet
        },
        protected_account_keys={account_key} if protected else set(),
        target_account_keys={account_key},
    )


async def activate_live_copy_account_sources(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallets: set[str],
    entry_eligible_source_wallets: set[str] | None = None,
    now: datetime | None = None,
) -> list[LiveCopySourceState]:
    """Activate one account's source lanes and capture fresh baselines.

    Callers use this in the same transaction as an account Start transition so
    a lane never becomes entry eligible before its baseline is durable.
    """

    states: list[LiveCopySourceState] = []
    normalized_entry_eligible_sources = {
        normalize_live_copy_source_wallet(source_wallet)
        for source_wallet in (
            entry_eligible_source_wallets
            if entry_eligible_source_wallets is not None
            else source_wallets
        )
        if source_wallet
    }
    for source_wallet in sorted(
        {
            normalize_live_copy_source_wallet(source_wallet)
            for source_wallet in source_wallets
            if source_wallet
        }
    ):
        await acquire_live_copy_lifecycle_lock(session, account_key=account_key)
        source_state = await get_live_copy_source_state(
            session,
            account_key=account_key,
            source_wallet=source_wallet,
            for_update=True,
        )
        if source_state is None:
            source_state = await ensure_live_copy_source_state(
                session,
                account_key=account_key,
                source_wallet=source_wallet,
                now=now,
                entry_eligible=source_wallet in normalized_entry_eligible_sources,
            )
        else:
            await activate_live_copy_source_state(
                session,
                source_state=source_state,
                now=now,
                entry_eligible=source_wallet in normalized_entry_eligible_sources,
            )
        states.append(source_state)
    return states


async def acquire_live_copy_lifecycle_lock(
    session: AsyncSession,
    *,
    account_key: str,
) -> None:
    """Serialize all live-copy lifecycle changes for one account."""

    if not session_uses_postgresql(session):
        return
    lock_key = f"live-copy-lifecycle:{account_key}"
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:lock_key)::bigint)"),
        {"lock_key": lock_key},
    )


async def acquire_live_copy_source_lock(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
) -> None:
    """Compatibility wrapper for callers that still request a source lock.

    A lifecycle lock is intentionally account scoped.  Source rows and fill
    rows are subsequently locked while that single account lock is held.
    """

    del source_wallet
    await acquire_live_copy_lifecycle_lock(session, account_key=account_key)


def session_uses_postgresql(session: Any) -> bool:
    get_bind = getattr(session, "get_bind", None)
    if not callable(get_bind):
        return False
    try:
        bind = get_bind()
    except (AttributeError, RuntimeError):
        return False
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "") == "postgresql"


def is_live_copy_fill_post_baseline(
    source_state: LiveCopySourceState,
    *,
    fill: WalletFill | Mapping[str, Any],
    origin: LiveCopyProcessingOrigin,
    observed_at: datetime | None = None,
    first_observed_at: datetime | None = None,
) -> bool:
    """Return whether a fill may create a live-copy decision.

    Normal entries need both a post-activation durable observation and a
    source timestamp at or after activation.  Baseline fill identifiers only
    scope recovery candidates and never grant entry permission.
    """

    del origin, observed_at
    if first_observed_at is None:
        return False
    activated_at = ensure_utc(source_state.activated_at)
    if ensure_utc(first_observed_at) < activated_at:
        return False
    source_timestamp_ms = live_copy_fill_timestamp_ms(fill)
    activation_timestamp_ms = int(activated_at.timestamp() * 1000)
    return source_timestamp_ms >= activation_timestamp_ms


async def live_copy_entry_follows_owned_position_lifecycle(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    fill: WalletFill | Mapping[str, Any],
    action: str,
    side: str,
    start_position: Any,
) -> bool:
    """Allow a post-opening owned continuation through a fresh lane baseline."""

    source = normalize_live_copy_source_wallet(source_state.source_wallet)
    if not is_same_side_owned_continuation_part(
        action=action,
        side=side,
        start_position=start_position,
    ):
        return False
    source_key = source_fill_order_key(live_copy_fill_mapping(fill))
    positions = await session.scalars(
        select(TradingPosition)
        .where(
            TradingPosition.account_key == source_state.account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source,
            TradingPosition.coin == live_copy_fill_coin(fill),
            TradingPosition.side == side,
            TradingPosition.size > POSITION_EPSILON,
            TradingPosition.source_lifecycle_timestamp_ms.is_not(None),
            TradingPosition.source_lifecycle_direction_rank.is_not(None),
            TradingPosition.source_lifecycle_position.is_not(None),
            TradingPosition.source_lifecycle_fill_id.is_not(None),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    for position in positions.all():
        numeric_fill_id = position.source_lifecycle_fill_id_numeric
        position_key = (
            int(position.source_lifecycle_timestamp_ms),
            position.coin,
            int(position.source_lifecycle_direction_rank),
            Decimal(position.source_lifecycle_position),
            0 if numeric_fill_id is not None else 1,
            Decimal(numeric_fill_id) if numeric_fill_id is not None else Decimal("0"),
            str(position.source_lifecycle_fill_id),
        )
        if source_key > position_key:
            return True
    return False


def is_same_side_owned_continuation_part(
    *,
    action: str,
    side: str,
    start_position: Any,
) -> bool:
    if action not in {"open", "add"} or start_position is None:
        return False
    try:
        normalized_start_position = Decimal(str(start_position))
    except (ArithmeticError, TypeError, ValueError):
        return False
    if side == "long":
        return normalized_start_position > POSITION_EPSILON
    return normalized_start_position < -POSITION_EPSILON


async def get_live_copy_fill_state(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_fill_id: str,
    sequence_index: int,
    for_update: bool = False,
) -> LiveCopyFillState | None:
    source = normalize_live_copy_source_wallet(source_wallet)
    query = select(LiveCopyFillState).where(
        LiveCopyFillState.account_key == account_key,
        LiveCopyFillState.source_wallet == source,
        LiveCopyFillState.source_fill_id == source_fill_id,
        LiveCopyFillState.sequence_index == int(sequence_index),
    )
    if for_update:
        query = query.execution_options(populate_existing=True).with_for_update()
    return await session.scalar(query)


async def ensure_live_copy_fill_part_state(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    fill: WalletFill | Mapping[str, Any],
    part: SourceFillPart,
    origin: LiveCopyProcessingOrigin,
    now: datetime | None = None,
    observed_at: datetime | None = None,
    first_observed_at: datetime | None = None,
) -> LiveCopyFillState:
    """Get one durable state from a one-part fill plan.

    New live-copy execution must use ``ensure_live_copy_fill_plan_states`` with
    every planned part before executing the first part.  This compatibility
    helper preserves the one-part API for callers that cannot produce a
    multipart plan.
    """

    states = await ensure_live_copy_fill_plan_states(
        session,
        source_state=source_state,
        fill=fill,
        planned_parts=(part,),
        origin=origin,
        now=now,
        observed_at=observed_at,
        first_observed_at=first_observed_at,
    )
    return states[0]


async def ensure_live_copy_fill_plan_states(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    fill: WalletFill | Mapping[str, Any],
    planned_parts: Iterable[SourceFillPart],
    origin: LiveCopyProcessingOrigin,
    now: datetime | None = None,
    observed_at: datetime | None = None,
    first_observed_at: datetime | None = None,
    execution_claimed_at: datetime | None = None,
) -> list[LiveCopyFillState]:
    """Atomically persist every part of one source-fill execution plan.

    A multipart plan must exist before its first part may create an order or a
    terminal decision.  Consequently, a committed first flip part always leaves
    the later flip part as a durable pending blocker after a worker crash.
    """

    ordered_parts = normalize_live_copy_fill_plan_parts(planned_parts)
    source_fill_id = live_copy_fill_id(fill)
    if not source_fill_id:
        raise ValueError("A live-copy fill plan requires an external fill identifier.")

    source = normalize_live_copy_source_wallet(source_state.source_wallet)
    await acquire_live_copy_lifecycle_lock(
        session,
        account_key=source_state.account_key,
    )
    result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == source_state.account_key,
            LiveCopyFillState.source_wallet == source,
            LiveCopyFillState.source_fill_id == source_fill_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    existing_states = list(result.all())
    expected_part_count = len(ordered_parts)
    parts_by_sequence = {int(part.sequence_index): part for part in ordered_parts}
    existing_by_sequence = {int(state.sequence_index): state for state in existing_states}

    unexpected_sequences = set(existing_by_sequence) - set(parts_by_sequence)
    if unexpected_sequences:
        raise ValueError("The persisted live-copy fill plan has unexpected part sequences.")

    for sequence_index, state in existing_by_sequence.items():
        part = parts_by_sequence[sequence_index]
        validate_live_copy_fill_plan_state(
            state,
            fill=fill,
            part=part,
            expected_part_count=expected_part_count,
        )
        if state.execution_claimed_at is None and execution_claimed_at is not None:
            state.execution_claimed_at = ensure_utc(execution_claimed_at)

    first_seen_at = utc_now(now)
    fill_observed_at = live_copy_fill_observed_at(fill) or (
        ensure_utc(observed_at) if observed_at is not None else None
    )
    source_order_direction_rank, source_order_position, source_order_fill_id_numeric = (
        live_copy_fill_order_components(fill)
    )
    for sequence_index, part in parts_by_sequence.items():
        if sequence_index in existing_by_sequence:
            continue
        state = LiveCopyFillState(
            account_key=source_state.account_key,
            account_type="live",
            source_wallet=source,
            source_fill_id=source_fill_id,
            sequence_index=sequence_index,
            expected_part_count=expected_part_count,
            plan_version=LIVE_COPY_FILL_PLAN_VERSION,
            coin=live_copy_fill_coin(fill),
            action=part.action,
            side=part.side,
            source_timestamp_ms=live_copy_fill_timestamp_ms(fill),
            source_order_direction_rank=source_order_direction_rank,
            source_order_position=source_order_position,
            source_order_fill_id_numeric=source_order_fill_id_numeric,
            observed_at=fill_observed_at,
            first_observed_at=(
                ensure_utc(first_observed_at) if first_observed_at is not None else None
            ),
            execution_claimed_at=(
                ensure_utc(execution_claimed_at) if execution_claimed_at is not None else None
            ),
            origin=origin,
            outcome=LIVE_COPY_OUTCOME_PENDING,
            attempt_count=0,
            first_seen_at=first_seen_at,
            fill_complete=False,
        )
        session.add(state)
        existing_by_sequence[sequence_index] = state

    if len(existing_by_sequence) != expected_part_count:
        raise RuntimeError("The live-copy fill plan could not be fully materialized.")
    await session.flush()
    return [existing_by_sequence[int(part.sequence_index)] for part in ordered_parts]


def normalize_live_copy_fill_plan_parts(
    planned_parts: Iterable[SourceFillPart],
) -> list[SourceFillPart]:
    """Validate and order the immutable per-fill execution plan."""

    parts_by_sequence: dict[int, SourceFillPart] = {}
    for part in planned_parts:
        sequence_index = int(part.sequence_index)
        if sequence_index < 0:
            raise ValueError("A live-copy fill plan cannot contain a negative sequence index.")
        if sequence_index in parts_by_sequence:
            raise ValueError("A live-copy fill plan cannot contain duplicate sequence indexes.")
        parts_by_sequence[sequence_index] = part
    if not parts_by_sequence:
        raise ValueError("A live-copy fill plan requires at least one part.")
    expected_sequences = set(range(len(parts_by_sequence)))
    if set(parts_by_sequence) != expected_sequences:
        raise ValueError("A live-copy fill plan must use contiguous sequence indexes from zero.")
    return [parts_by_sequence[sequence_index] for sequence_index in sorted(parts_by_sequence)]


def validate_live_copy_fill_plan_state(
    state: LiveCopyFillState,
    *,
    fill: WalletFill | Mapping[str, Any],
    part: SourceFillPart,
    expected_part_count: int,
) -> None:
    """Reject a divergent retry plan instead of executing ambiguous history."""

    if int(state.expected_part_count) != expected_part_count:
        raise ValueError("The persisted live-copy fill plan has a different part count.")
    if int(state.plan_version) != LIVE_COPY_FILL_PLAN_VERSION:
        raise ValueError("The persisted live-copy fill plan uses an unsupported version.")
    expected_direction_rank, expected_position, expected_numeric_fill_id = (
        live_copy_fill_order_components(fill)
    )
    if (
        state.coin != live_copy_fill_coin(fill)
        or state.action != part.action
        or state.side != part.side
        or int(state.source_timestamp_ms) != live_copy_fill_timestamp_ms(fill)
        or int(state.source_order_direction_rank) != expected_direction_rank
        or state.source_order_position != expected_position
        or state.source_order_fill_id_numeric != expected_numeric_fill_id
    ):
        raise ValueError("The persisted live-copy fill plan does not match the source fill.")


async def claim_live_copy_fill_part(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    fill: WalletFill | Mapping[str, Any],
    part: SourceFillPart,
    origin: LiveCopyProcessingOrigin,
    now: datetime | None = None,
    lease_seconds: int = LIVE_COPY_PROCESSING_LEASE_SECONDS,
    entry_is_stale: bool = False,
) -> LiveCopyPartClaim:
    """Claim one due post-baseline part with a durable processing lease."""

    await acquire_live_copy_lifecycle_lock(
        session,
        account_key=source_state.account_key,
    )
    refreshed_source_state = await get_live_copy_source_state(
        session,
        account_key=source_state.account_key,
        source_wallet=source_state.source_wallet,
        for_update=True,
    )
    if refreshed_source_state is None or refreshed_source_state.status != "active":
        return LiveCopyPartClaim(state=None, claimed=False, reason="missing_plan")
    source_state = refreshed_source_state

    source = normalize_live_copy_source_wallet(source_state.source_wallet)
    source_fill_id = live_copy_fill_id(fill)
    if not source_fill_id:
        return LiveCopyPartClaim(state=None, claimed=False, reason="missing_plan")
    plan_result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == source_state.account_key,
            LiveCopyFillState.source_wallet == source,
            LiveCopyFillState.source_fill_id == source_fill_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    plan_states = list(plan_result.all())
    plan_by_sequence = {int(state.sequence_index): state for state in plan_states}
    target_sequence = int(part.sequence_index)
    target_state = plan_by_sequence.get(target_sequence)
    expected_part_count = len(plan_states)
    expected_sequences = set(range(expected_part_count))
    if (
        target_state is None
        or expected_part_count == 0
        or set(plan_by_sequence) != expected_sequences
        or any(
            int(state.expected_part_count) != expected_part_count
            or int(state.plan_version) != LIVE_COPY_FILL_PLAN_VERSION
            for state in plan_states
        )
    ):
        return LiveCopyPartClaim(
            state=target_state,
            claimed=False,
            reason="missing_plan",
        )
    try:
        validate_live_copy_fill_plan_state(
            target_state,
            fill=fill,
            part=part,
            expected_part_count=expected_part_count,
        )
    except ValueError:
        return LiveCopyPartClaim(
            state=target_state,
            claimed=False,
            reason="missing_plan",
        )

    if target_state.fill_complete or target_state.outcome in LIVE_COPY_TERMINAL_OUTCOMES:
        return LiveCopyPartClaim(state=target_state, claimed=False, reason="complete")
    is_entry_part = part.action in {"open", "add", "flip_open"}
    if is_entry_part and not is_live_copy_fill_post_baseline(
        source_state,
        fill=fill,
        origin=target_state.origin,
        observed_at=target_state.observed_at,
        first_observed_at=target_state.first_observed_at,
    ):
        if not await live_copy_entry_follows_owned_position_lifecycle(
            session,
            source_state=source_state,
            fill=fill,
            action=part.action,
            side=part.side,
            start_position=part.start_position,
        ):
            return LiveCopyPartClaim(state=target_state, claimed=False, reason="baseline")

    # A stale entry is a terminal decision.  It must not remain indefinitely
    # behind an earlier uncertain exchange order.  Exits always retain order.
    pre_barrier_terminal_entry = is_entry_part and entry_is_stale
    if not pre_barrier_terminal_entry:
        lower_sequence_states = [
            state
            for sequence_index, state in plan_by_sequence.items()
            if sequence_index < target_sequence
        ]
        unresolved_lower_sequence = await session.scalar(
            select(LiveCopyFillState.sequence_index)
            .where(
                LiveCopyFillState.account_key == source_state.account_key,
                LiveCopyFillState.source_wallet == source,
                LiveCopyFillState.source_fill_id == source_fill_id,
                LiveCopyFillState.sequence_index < target_sequence,
                exists(
                    select(TradingOrder.id).where(
                        TradingOrder.id == LiveCopyFillState.trading_order_id,
                        live_copy_unresolved_order_predicate(),
                    )
                ),
            )
            .order_by(LiveCopyFillState.sequence_index.asc())
            .limit(1)
        )
        lower_sequence_blocker = next(
            (
                state
                for state in sorted(lower_sequence_states, key=lambda state: state.sequence_index)
                if (
                    state.outcome not in LIVE_COPY_TERMINAL_OUTCOMES
                    or int(state.sequence_index) == unresolved_lower_sequence
                )
            ),
            None,
        )
        if lower_sequence_blocker is not None:
            return LiveCopyPartClaim(
                state=lower_sequence_blocker,
                claimed=False,
                reason="blocked",
            )

        blocker = await get_prior_live_copy_fill_blocker(
            session,
            account_key=source_state.account_key,
            source_wallet=source_state.source_wallet,
            fill=fill,
        )
        if blocker is not None:
            return LiveCopyPartClaim(state=blocker, claimed=False, reason="blocked")

    observed_at = utc_now(now)
    if (
        target_state.next_attempt_at is not None
        and ensure_utc(target_state.next_attempt_at) > observed_at
    ):
        return LiveCopyPartClaim(state=target_state, claimed=False, reason="not_due")

    target_state.outcome = LIVE_COPY_OUTCOME_RETRYABLE
    target_state.reason = "processing"
    target_state.last_attempt_at = observed_at
    if target_state.processing_started_at is None:
        target_state.processing_started_at = observed_at
    target_state.next_attempt_at = observed_at + timedelta(seconds=max(int(lease_seconds), 1))
    await session.flush()
    return LiveCopyPartClaim(state=target_state, claimed=True, reason="claimed")


async def get_prior_live_copy_fill_blocker(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    fill: WalletFill | Mapping[str, Any],
) -> LiveCopyFillState | None:
    """Return an earlier unfinished fill in the same source market lane."""

    source = normalize_live_copy_source_wallet(source_wallet)
    source_fill_id = live_copy_fill_id(fill)
    source_timestamp_ms = live_copy_fill_timestamp_ms(fill)
    coin = live_copy_fill_coin(fill)
    if not source_fill_id or not coin:
        return None
    (
        source_order_direction_rank,
        source_order_position,
        source_order_fill_id_numeric,
    ) = live_copy_fill_order_components(fill)
    source_order_numeric_rank = 0 if source_order_fill_id_numeric is not None else 1
    state_order_numeric_rank = case(
        (LiveCopyFillState.source_order_fill_id_numeric.is_not(None), literal(0)),
        else_=literal(1),
    )
    earlier_source_order = or_(
        LiveCopyFillState.source_timestamp_ms < source_timestamp_ms,
        and_(
            LiveCopyFillState.source_timestamp_ms == source_timestamp_ms,
            LiveCopyFillState.source_order_direction_rank < source_order_direction_rank,
        ),
        and_(
            LiveCopyFillState.source_timestamp_ms == source_timestamp_ms,
            LiveCopyFillState.source_order_direction_rank == source_order_direction_rank,
            LiveCopyFillState.source_order_position < source_order_position,
        ),
        and_(
            LiveCopyFillState.source_timestamp_ms == source_timestamp_ms,
            LiveCopyFillState.source_order_direction_rank == source_order_direction_rank,
            LiveCopyFillState.source_order_position == source_order_position,
            state_order_numeric_rank < source_order_numeric_rank,
        ),
        and_(
            LiveCopyFillState.source_timestamp_ms == source_timestamp_ms,
            LiveCopyFillState.source_order_direction_rank == source_order_direction_rank,
            LiveCopyFillState.source_order_position == source_order_position,
            state_order_numeric_rank == source_order_numeric_rank,
            LiveCopyFillState.source_order_fill_id_numeric < source_order_fill_id_numeric,
        )
        if source_order_fill_id_numeric is not None
        else literal(False),
        and_(
            LiveCopyFillState.source_timestamp_ms == source_timestamp_ms,
            LiveCopyFillState.source_order_direction_rank == source_order_direction_rank,
            LiveCopyFillState.source_order_position == source_order_position,
            state_order_numeric_rank == source_order_numeric_rank,
            or_(
                and_(
                    literal(source_order_fill_id_numeric is None),
                    LiveCopyFillState.source_fill_id < source_fill_id,
                ),
                and_(
                    literal(source_order_fill_id_numeric is not None),
                    LiveCopyFillState.source_order_fill_id_numeric == source_order_fill_id_numeric,
                    LiveCopyFillState.source_fill_id < source_fill_id,
                ),
            ),
        ),
    )
    unresolved_order_effect = exists(
        select(TradingOrder.id).where(
            TradingOrder.id == LiveCopyFillState.trading_order_id,
            live_copy_unresolved_order_predicate(),
        )
    )
    return await session.scalar(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == account_key,
            LiveCopyFillState.source_wallet == source,
            LiveCopyFillState.coin == coin,
            or_(
                and_(
                    LiveCopyFillState.fill_complete.is_(False),
                    LiveCopyFillState.outcome.in_(
                        (LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)
                    ),
                ),
                unresolved_order_effect,
            ),
            earlier_source_order,
        )
        .order_by(
            LiveCopyFillState.source_timestamp_ms.asc(),
            LiveCopyFillState.source_order_direction_rank.asc(),
            LiveCopyFillState.source_order_position.asc(),
            state_order_numeric_rank.asc(),
            LiveCopyFillState.source_order_fill_id_numeric.asc().nulls_last(),
            LiveCopyFillState.source_fill_id.asc(),
            LiveCopyFillState.sequence_index.asc(),
        )
        .limit(1)
        .execution_options(populate_existing=True)
        .with_for_update()
    )


async def mark_live_copy_fill_retryable(
    session: AsyncSession,
    *,
    fill_state: LiveCopyFillState,
    reason: str,
    now: datetime | None = None,
    base_seconds: int = LIVE_COPY_RETRY_BASE_SECONDS,
    max_seconds: int = LIVE_COPY_RETRY_MAX_SECONDS,
) -> int:
    """Defer a part without creating or mutating a TradingOrder."""

    observed_at = utc_now(now)
    fill_state.attempt_count = max(int(fill_state.attempt_count or 0), 0) + 1
    delay_seconds = live_copy_retry_delay_seconds(
        fill_state.attempt_count,
        base_seconds=base_seconds,
        max_seconds=max_seconds,
    )
    fill_state.outcome = LIVE_COPY_OUTCOME_RETRYABLE
    fill_state.reason = reason[:2000]
    fill_state.last_attempt_at = observed_at
    fill_state.next_attempt_at = observed_at + timedelta(seconds=delay_seconds)
    fill_state.fill_complete = False
    fill_state.trading_order_id = None
    await session.flush()
    return delay_seconds


async def defer_live_copy_fill_part(
    session: AsyncSession,
    *,
    fill_state: LiveCopyFillState,
    error: LiveCopyProcessingDeferred,
    now: datetime | None = None,
) -> int:
    return await mark_live_copy_fill_retryable(
        session,
        fill_state=fill_state,
        reason=error.reason,
        now=now,
    )


async def mark_live_copy_fill_baseline_ignored(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    fill_state: LiveCopyFillState,
    part: SourceFillPart,
    reason: str = "live_copy_baseline",
    record_preexisting_market: bool = True,
) -> None:
    """Record a historical source part without manufacturing an order failure."""

    if record_preexisting_market:
        update_preexisting_markets_for_part(
            source_state,
            coin=fill_state.coin,
            part=part,
            source_fill_id=fill_state.source_fill_id,
            source_timestamp_ms=fill_state.source_timestamp_ms,
        )
    fill_state.outcome = LIVE_COPY_OUTCOME_BASELINE_IGNORED
    fill_state.reason = reason[:2000]
    fill_state.next_attempt_at = None
    fill_state.fill_complete = False
    fill_state.trading_order_id = None
    fill_state.decision_at = utc_now()
    await session.flush()


async def mark_live_copy_fill_terminal_skip(
    session: AsyncSession,
    *,
    fill_state: LiveCopyFillState,
    reason: str,
) -> None:
    """Persist a terminal decision that intentionally has no order row."""

    fill_state.outcome = LIVE_COPY_OUTCOME_TERMINAL_SKIP
    fill_state.reason = reason[:2000]
    fill_state.next_attempt_at = None
    fill_state.fill_complete = False
    fill_state.trading_order_id = None
    fill_state.decision_at = utc_now()
    await session.flush()


async def link_live_copy_fill_state_to_order(
    session: AsyncSession,
    *,
    fill_state: LiveCopyFillState,
    order: TradingOrder,
    terminal_skip: bool = False,
    reason: str | None = None,
) -> None:
    """Attach a completed per-part decision to an already durable order row."""

    if order.id is None:
        raise ValueError("The TradingOrder must be flushed before it is linked to live-copy state.")
    if order.account_key != fill_state.account_key:
        raise ValueError("The TradingOrder belongs to a different live-copy account.")
    if order.source_wallet != fill_state.source_wallet:
        raise ValueError("The TradingOrder belongs to a different live-copy source.")
    if order.source_fill_id != fill_state.source_fill_id:
        raise ValueError("The TradingOrder belongs to a different source fill.")
    if order.sequence_index != fill_state.sequence_index:
        raise ValueError("The TradingOrder belongs to a different source-fill part.")

    fill_state.outcome = (
        LIVE_COPY_OUTCOME_TERMINAL_SKIP if terminal_skip else LIVE_COPY_OUTCOME_ORDER
    )
    fill_state.reason = reason[:2000] if reason else None
    fill_state.next_attempt_at = None
    fill_state.fill_complete = False
    fill_state.trading_order_id = order.id
    fill_state.decision_at = utc_now()
    await session.flush()


async def mark_live_copy_fill_complete_if_durable(
    session: AsyncSession,
    *,
    source_state: LiveCopySourceState,
    source_fill_id: str,
    planned_parts: Iterable[SourceFillPart],
) -> bool:
    """Mark every part complete only after every planned part is terminal."""

    ordered_parts = normalize_live_copy_fill_plan_parts(planned_parts)
    expected_sequences = {int(part.sequence_index) for part in ordered_parts}
    expected_part_count = len(ordered_parts)
    source = normalize_live_copy_source_wallet(source_state.source_wallet)
    await acquire_live_copy_lifecycle_lock(
        session,
        account_key=source_state.account_key,
    )
    result = await session.scalars(
        select(LiveCopyFillState)
        .where(
            LiveCopyFillState.account_key == source_state.account_key,
            LiveCopyFillState.source_wallet == source,
            LiveCopyFillState.source_fill_id == source_fill_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    states = result.all()
    by_sequence = {state.sequence_index: state for state in states}
    if set(by_sequence) != expected_sequences:
        return False
    if any(
        by_sequence[int(part.sequence_index)].outcome not in LIVE_COPY_TERMINAL_OUTCOMES
        for part in ordered_parts
    ):
        return False
    if any(
        int(by_sequence[int(part.sequence_index)].expected_part_count) != expected_part_count
        or int(by_sequence[int(part.sequence_index)].plan_version) != LIVE_COPY_FILL_PLAN_VERSION
        for part in ordered_parts
    ):
        return False
    for sequence in expected_sequences:
        by_sequence[sequence].fill_complete = True
        by_sequence[sequence].next_attempt_at = None
    await session.flush()
    return True


def preexisting_market_matches_part(
    source_state: LiveCopySourceState,
    *,
    coin: str,
    part: SourceFillPart,
) -> bool:
    """Return whether this part manages source exposure that predated copying."""

    market = compact_preexisting_markets(source_state.preexisting_markets).get(coin)
    if not isinstance(market, dict) or market.get("side") != part.side:
        return False
    if part.action in {"close", "reduce", "flip_close"}:
        return True
    if part.action in {"open", "add"}:
        return is_same_side_preexisting_add(part)
    return False


def update_preexisting_markets_for_part(
    source_state: LiveCopySourceState,
    *,
    coin: str,
    part: SourceFillPart,
    source_fill_id: str,
    source_timestamp_ms: int,
) -> None:
    """Maintain one compact current lifecycle marker per source market."""

    markets = compact_preexisting_markets(source_state.preexisting_markets)
    existing = markets.get(coin)
    if part.action in {"open", "add", "flip_open"}:
        opened_at = source_timestamp_ms
        ignored_fill_count = 1
        if isinstance(existing, dict) and existing.get("side") == part.side:
            opened_at = int_or_default(existing.get("openedAtTimestampMs"), source_timestamp_ms)
            ignored_fill_count = int_or_default(existing.get("ignoredFillCount"), 0) + 1
        markets[coin] = {
            "side": part.side,
            "openedAtTimestampMs": opened_at,
            "lastSourceTimestampMs": int(source_timestamp_ms),
            "lastSourceFillId": source_fill_id,
            "ignoredFillCount": ignored_fill_count,
        }
    elif (
        part.action in {"close", "reduce", "flip_close"}
        and isinstance(existing, dict)
        and existing.get("side") == part.side
    ):
        if source_part_is_final_close(part):
            markets.pop(coin, None)
        else:
            markets[coin] = {
                **existing,
                "lastSourceTimestampMs": int(source_timestamp_ms),
                "lastSourceFillId": source_fill_id,
                "ignoredFillCount": int_or_default(existing.get("ignoredFillCount"), 0) + 1,
            }
    source_state.preexisting_markets = markets


def compact_preexisting_markets(value: object) -> dict[str, dict[str, Any]]:
    """Normalize persisted lifecycle markers to one small record per coin."""

    if not isinstance(value, dict):
        return {}
    compact: dict[str, dict[str, Any]] = {}
    for coin, raw_market in value.items():
        if not isinstance(coin, str) or not coin or not isinstance(raw_market, dict):
            continue
        side = raw_market.get("side")
        if side not in {"long", "short"}:
            continue
        compact[coin] = {
            "side": side,
            "openedAtTimestampMs": int_or_default(raw_market.get("openedAtTimestampMs"), 0),
            "lastSourceTimestampMs": int_or_default(raw_market.get("lastSourceTimestampMs"), 0),
            "lastSourceFillId": str(raw_market.get("lastSourceFillId") or ""),
            "ignoredFillCount": int_or_default(raw_market.get("ignoredFillCount"), 0),
        }
    return compact


def is_same_side_preexisting_add(part: SourceFillPart) -> bool:
    start_position = getattr(part, "start_position", None)
    if start_position is None:
        return False
    if part.side == "long":
        return start_position > 0
    return start_position < 0


def source_part_is_final_close(part: SourceFillPart) -> bool:
    if part.action not in {"close", "reduce", "flip_close"}:
        return False
    close_ratio = getattr(part, "close_ratio", None)
    return close_ratio is not None and close_ratio >= 1


def build_live_copy_recovery_candidate_query(
    *,
    account_key: str,
    source_wallet: str,
    source_state: LiveCopySourceState,
    limit: int,
    now: datetime | None = None,
    max_entry_age_seconds: int = 0,
):
    """Build the recovery query without using high-water fields as a cursor.

    Completed decisions are removed before ``LIMIT``.  Retryable state is only
    selected when due, preventing a historical prefix from starving newer fills.
    Active source-attributed positions add an opened-at overlap so their exits
    remain recoverable even when the opening fill predates a later baseline.
    """

    source = normalize_live_copy_source_wallet(source_wallet)
    observed_at = utc_now(now)
    (
        wallet_direction_rank,
        wallet_position_rank,
        wallet_numeric_rank,
        wallet_fill_id_numeric,
    ) = wallet_fill_source_order_expressions()
    state_numeric_rank = case(
        (LiveCopyFillState.source_order_fill_id_numeric.is_not(None), literal(0)),
        else_=literal(1),
    )
    per_fill = and_(
        LiveCopyFillState.account_key == account_key,
        LiveCopyFillState.source_wallet == source,
        LiveCopyFillState.source_fill_id == WalletFill.external_fill_id,
    )
    has_any_state = exists(select(LiveCopyFillState.id).where(per_fill))
    has_unresolved_order_effect = exists(
        select(LiveCopyFillState.id).where(
            per_fill,
            exists(
                select(TradingOrder.id).where(
                    TradingOrder.id == LiveCopyFillState.trading_order_id,
                    live_copy_unresolved_order_predicate(),
                )
            ),
        )
    )
    has_completed_state = and_(
        exists(
            select(LiveCopyFillState.id).where(
                per_fill,
                LiveCopyFillState.fill_complete.is_(True),
            )
        ),
        not_(has_unresolved_order_effect),
    )
    has_due_retry = exists(
        select(LiveCopyFillState.id).where(
            per_fill,
            LiveCopyFillState.outcome == LIVE_COPY_OUTCOME_RETRYABLE,
            or_(
                LiveCopyFillState.next_attempt_at.is_(None),
                LiveCopyFillState.next_attempt_at <= observed_at,
            ),
        )
    )
    has_due_pending = exists(
        select(LiveCopyFillState.id).where(
            per_fill,
            LiveCopyFillState.outcome == LIVE_COPY_OUTCOME_PENDING,
            or_(
                LiveCopyFillState.next_attempt_at.is_(None),
                LiveCopyFillState.next_attempt_at <= observed_at,
            ),
        )
    )
    has_terminal_incomplete = exists(
        select(LiveCopyFillState.id).where(
            per_fill,
            LiveCopyFillState.fill_complete.is_(False),
            LiveCopyFillState.outcome.in_(LIVE_COPY_TERMINAL_OUTCOMES),
        )
    )
    has_nonterminal_incomplete = exists(
        select(LiveCopyFillState.id).where(
            per_fill,
            LiveCopyFillState.fill_complete.is_(False),
            LiveCopyFillState.outcome.in_((LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)),
        )
    )
    has_only_terminal_incomplete = and_(
        has_terminal_incomplete,
        not_(has_nonterminal_incomplete),
    )
    has_prior_incomplete = exists(
        select(LiveCopyFillState.id).where(
            LiveCopyFillState.account_key == account_key,
            LiveCopyFillState.source_wallet == source,
            LiveCopyFillState.coin == WalletFill.coin,
            or_(
                and_(
                    LiveCopyFillState.fill_complete.is_(False),
                    LiveCopyFillState.outcome.in_(
                        (LIVE_COPY_OUTCOME_PENDING, LIVE_COPY_OUTCOME_RETRYABLE)
                    ),
                ),
                exists(
                    select(TradingOrder.id).where(
                        TradingOrder.id == LiveCopyFillState.trading_order_id,
                        live_copy_unresolved_order_predicate(),
                    )
                ),
            ),
            or_(
                LiveCopyFillState.source_timestamp_ms < WalletFill.timestamp_ms,
                and_(
                    LiveCopyFillState.source_timestamp_ms == WalletFill.timestamp_ms,
                    LiveCopyFillState.source_order_direction_rank < wallet_direction_rank,
                ),
                and_(
                    LiveCopyFillState.source_timestamp_ms == WalletFill.timestamp_ms,
                    LiveCopyFillState.source_order_direction_rank == wallet_direction_rank,
                    LiveCopyFillState.source_order_position < wallet_position_rank,
                ),
                and_(
                    LiveCopyFillState.source_timestamp_ms == WalletFill.timestamp_ms,
                    LiveCopyFillState.source_order_direction_rank == wallet_direction_rank,
                    LiveCopyFillState.source_order_position == wallet_position_rank,
                    state_numeric_rank < wallet_numeric_rank,
                ),
                and_(
                    LiveCopyFillState.source_timestamp_ms == WalletFill.timestamp_ms,
                    LiveCopyFillState.source_order_direction_rank == wallet_direction_rank,
                    LiveCopyFillState.source_order_position == wallet_position_rank,
                    state_numeric_rank == wallet_numeric_rank,
                    LiveCopyFillState.source_order_fill_id_numeric < wallet_fill_id_numeric,
                ),
                and_(
                    LiveCopyFillState.source_timestamp_ms == WalletFill.timestamp_ms,
                    LiveCopyFillState.source_order_direction_rank == wallet_direction_rank,
                    LiveCopyFillState.source_order_position == wallet_position_rank,
                    state_numeric_rank == wallet_numeric_rank,
                    or_(
                        and_(
                            wallet_numeric_rank == 1,
                            LiveCopyFillState.source_fill_id < WalletFill.external_fill_id,
                        ),
                        and_(
                            wallet_numeric_rank == 0,
                            LiveCopyFillState.source_order_fill_id_numeric
                            == wallet_fill_id_numeric,
                            LiveCopyFillState.source_fill_id < WalletFill.external_fill_id,
                        ),
                    ),
                ),
            ),
        )
    )

    activation_timestamp_ms = int(ensure_utc(source_state.activated_at).timestamp() * 1000)
    normal_entry_eligible = and_(
        WalletFill.received_at >= source_state.activated_at,
        WalletFill.timestamp_ms >= activation_timestamp_ms,
    )
    position_numeric_rank = case(
        (TradingPosition.source_lifecycle_fill_id_numeric.is_not(None), literal(0)),
        else_=literal(1),
    )
    owned_position_precedes_fill = or_(
        TradingPosition.source_lifecycle_timestamp_ms < WalletFill.timestamp_ms,
        and_(
            TradingPosition.source_lifecycle_timestamp_ms == WalletFill.timestamp_ms,
            TradingPosition.source_lifecycle_direction_rank < wallet_direction_rank,
        ),
        and_(
            TradingPosition.source_lifecycle_timestamp_ms == WalletFill.timestamp_ms,
            TradingPosition.source_lifecycle_direction_rank == wallet_direction_rank,
            TradingPosition.source_lifecycle_position < wallet_position_rank,
        ),
        and_(
            TradingPosition.source_lifecycle_timestamp_ms == WalletFill.timestamp_ms,
            TradingPosition.source_lifecycle_direction_rank == wallet_direction_rank,
            TradingPosition.source_lifecycle_position == wallet_position_rank,
            position_numeric_rank < wallet_numeric_rank,
        ),
        and_(
            TradingPosition.source_lifecycle_timestamp_ms == WalletFill.timestamp_ms,
            TradingPosition.source_lifecycle_direction_rank == wallet_direction_rank,
            TradingPosition.source_lifecycle_position == wallet_position_rank,
            position_numeric_rank == wallet_numeric_rank,
            TradingPosition.source_lifecycle_fill_id_numeric < wallet_fill_id_numeric,
        ),
        and_(
            TradingPosition.source_lifecycle_timestamp_ms == WalletFill.timestamp_ms,
            TradingPosition.source_lifecycle_direction_rank == wallet_direction_rank,
            TradingPosition.source_lifecycle_position == wallet_position_rank,
            position_numeric_rank == wallet_numeric_rank,
            or_(
                and_(
                    wallet_numeric_rank == 1,
                    TradingPosition.source_lifecycle_fill_id < WalletFill.external_fill_id,
                ),
                and_(
                    wallet_numeric_rank == 0,
                    TradingPosition.source_lifecycle_fill_id_numeric == wallet_fill_id_numeric,
                    TradingPosition.source_lifecycle_fill_id < WalletFill.external_fill_id,
                ),
            ),
        ),
    )
    owned_position_overlap = exists(
        select(TradingPosition.id).where(
            TradingPosition.account_key == account_key,
            TradingPosition.account_type == "live",
            TradingPosition.source_wallet == source,
            TradingPosition.coin == WalletFill.coin,
            TradingPosition.size > POSITION_EPSILON,
            TradingPosition.source_lifecycle_timestamp_ms.is_not(None),
            TradingPosition.source_lifecycle_direction_rank.is_not(None),
            TradingPosition.source_lifecycle_position.is_not(None),
            TradingPosition.source_lifecycle_fill_id.is_not(None),
            owned_position_precedes_fill,
        )
    )
    work_is_due = or_(
        not_(has_any_state),
        has_due_retry,
        has_due_pending,
        has_only_terminal_incomplete,
    )
    stale_entry_bypasses_barrier = literal(False)
    if max_entry_age_seconds > 0:
        stale_cutoff_timestamp_ms = int(
            (observed_at - timedelta(seconds=max(int(max_entry_age_seconds), 0))).timestamp() * 1000
        )
        source_direction = WalletFill.raw_json["dir"].astext
        stale_entry_bypasses_barrier = and_(
            WalletFill.timestamp_ms < stale_cutoff_timestamp_ms,
            source_direction.not_in(SOURCE_CLOSE_DIRECTIONS),
        )
    recovery_scope = or_(
        normal_entry_eligible,
        owned_position_overlap,
        has_due_retry,
        has_due_pending,
        has_only_terminal_incomplete,
    )
    return (
        select(WalletFill)
        .where(
            WalletFill.wallet_address == source,
            recovery_scope,
            not_(has_completed_state),
            work_is_due,
            or_(not_(has_prior_incomplete), stale_entry_bypasses_barrier),
        )
        .order_by(
            WalletFill.timestamp_ms.asc(),
            WalletFill.coin.asc(),
            wallet_direction_rank.asc(),
            wallet_position_rank.asc(),
            wallet_numeric_rank.asc(),
            wallet_fill_id_numeric.asc().nulls_last(),
            WalletFill.external_fill_id.asc(),
        )
        .limit(max(int(limit), 1))
    )


async def load_live_copy_recovery_candidate_fills(
    session: AsyncSession,
    *,
    account_key: str,
    source_wallet: str,
    source_state: LiveCopySourceState,
    limit: int,
    now: datetime | None = None,
    max_entry_age_seconds: int = 0,
) -> list[WalletFill]:
    result = await session.scalars(
        build_live_copy_recovery_candidate_query(
            account_key=account_key,
            source_wallet=source_wallet,
            source_state=source_state,
            limit=limit,
            now=now,
            max_entry_age_seconds=max_entry_age_seconds,
        )
    )
    fills = result.all()
    update_live_copy_scan_high_water(source_state, fills)
    return fills


def live_copy_recovery_baseline_condition(source_state: LiveCopySourceState):
    baseline_timestamp_ms = source_state.baseline_source_timestamp_ms
    if baseline_timestamp_ms is None:
        return literal(True)
    baseline_fill_ids = sorted(live_copy_baseline_fill_ids(source_state))
    same_timestamp_late_arrival = (
        WalletFill.timestamp_ms == int(baseline_timestamp_ms)
        if not baseline_fill_ids
        else and_(
            WalletFill.timestamp_ms == int(baseline_timestamp_ms),
            WalletFill.external_fill_id.not_in(baseline_fill_ids),
        )
    )
    return or_(
        WalletFill.timestamp_ms > int(baseline_timestamp_ms),
        same_timestamp_late_arrival,
    )


def update_live_copy_scan_high_water(
    source_state: LiveCopySourceState,
    fills: Iterable[WalletFill | Mapping[str, Any]],
) -> None:
    """Update observability only. Recovery never reads this as an eligibility cursor."""

    observed = [
        (source_fill_order_key(live_copy_fill_mapping(fill)), fill)
        for fill in fills
        if live_copy_fill_id(fill)
    ]
    if not observed:
        return
    candidate, _ = max(observed, key=lambda value: value[0])
    existing = (
        int(source_state.scan_high_water_timestamp_ms or 0),
        str(source_state.scan_high_water_coin or ""),
        int(source_state.scan_high_water_direction_rank or 0),
        source_state.scan_high_water_position or 0,
        0 if source_state.scan_high_water_fill_id_numeric is not None else 1,
        source_state.scan_high_water_fill_id_numeric or 0,
        str(source_state.scan_high_water_fill_id or ""),
    )
    if candidate > existing:
        source_state.scan_high_water_timestamp_ms = candidate[0]
        source_state.scan_high_water_coin = candidate[1]
        source_state.scan_high_water_direction_rank = candidate[2]
        source_state.scan_high_water_position = candidate[3]
        source_state.scan_high_water_fill_id_numeric = candidate[5] if candidate[4] == 0 else None
        source_state.scan_high_water_fill_id = candidate[6]


def wallet_fill_source_order_expressions():
    """Build SQL expressions equivalent to ``source_fill_order_key``."""

    direction = WalletFill.raw_json["dir"].astext
    direction_rank = case(
        (direction.in_(SOURCE_CLOSE_DIRECTIONS), literal(0)),
        else_=literal(1),
    )
    raw_position = WalletFill.raw_json["startPosition"].astext
    numeric_position = raw_position.op("~")(r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
    source_position = case(
        (numeric_position, cast(raw_position, Numeric)),
        else_=literal(0),
    )
    position_rank = case(
        (direction_rank == 0, -func.abs(source_position)),
        else_=func.abs(source_position),
    )
    numeric_id = WalletFill.external_fill_id.op("~")(r"^[0-9]+$")
    numeric_rank = case((numeric_id, literal(0)), else_=literal(1))
    numeric_value = case(
        (numeric_id, cast(WalletFill.external_fill_id, Numeric)),
        else_=None,
    )
    return direction_rank, position_rank, numeric_rank, numeric_value


def live_copy_baseline_fill_ids(source_state: LiveCopySourceState) -> set[str]:
    raw_ids = source_state.baseline_fill_ids
    if not isinstance(raw_ids, list):
        return set()
    return {str(value) for value in raw_ids if value is not None}


def live_copy_fill_id(fill: WalletFill | Mapping[str, Any]) -> str:
    if isinstance(fill, Mapping):
        return str(fill.get("externalFillId") or fill.get("external_fill_id") or "")
    return str(fill.external_fill_id or "")


def live_copy_fill_timestamp_ms(fill: WalletFill | Mapping[str, Any]) -> int:
    if isinstance(fill, Mapping):
        value = fill.get("timestampMs", fill.get("timestamp_ms", 0))
    else:
        value = fill.timestamp_ms
    return int(value or 0)


def live_copy_fill_coin(fill: WalletFill | Mapping[str, Any]) -> str:
    if isinstance(fill, Mapping):
        return str(fill.get("coin") or "")
    return str(fill.coin or "")


def live_copy_fill_order_components(
    fill: WalletFill | Mapping[str, Any],
) -> tuple[int, Any, Any]:
    return source_fill_order_components(live_copy_fill_mapping(fill))


def live_copy_fill_observed_at(fill: WalletFill | Mapping[str, Any]) -> datetime | None:
    if isinstance(fill, Mapping):
        value = fill.get("observedAt", fill.get("observed_at"))
    else:
        value = None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def live_copy_fill_mapping(fill: WalletFill | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(fill, Mapping):
        return dict(fill)
    return {
        "externalFillId": fill.external_fill_id,
        "coin": fill.coin,
        "timestampMs": fill.timestamp_ms,
        "rawJson": fill.raw_json,
    }


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utc_now(value: datetime | None = None) -> datetime:
    return ensure_utc(value) if value is not None else datetime.now(UTC)


def int_or_default(value: object, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default
