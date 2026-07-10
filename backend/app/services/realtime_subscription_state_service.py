from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

REALTIME_SUBSCRIPTION_STATE_KEY = "realtime_subscription_state"
ACTIVE_SUBSCRIPTION_STATUSES = frozenset({"connecting", "connected"})
SUBSCRIPTION_STATUSES = ACTIVE_SUBSCRIPTION_STATUSES | frozenset({"idle", "disconnected"})


def realtime_subscription_stale_after_seconds(refresh_seconds: int) -> int:
    return max(refresh_seconds * 3, 30)


@dataclass(frozen=True)
class RealtimeSubscriptionSnapshot:
    status: str
    desired_wallets: tuple[str, ...]
    monitored_wallets: frozenset[str]
    worker_role: str | None
    worker_instance_id: str | None
    updated_at: datetime | None


async def mark_realtime_subscription_state(
    session: AsyncSession,
    *,
    status: str,
    desired_wallets: list[str] | tuple[str, ...],
    monitored_wallets: list[str] | tuple[str, ...],
    worker_role: str,
    worker_instance_id: str,
    observed_at: datetime | None = None,
) -> None:
    normalized_status = status if status in SUBSCRIPTION_STATUSES else "disconnected"
    desired = normalize_wallets(desired_wallets)
    monitored = [wallet for wallet in normalize_wallets(monitored_wallets) if wallet in desired]
    if normalized_status not in ACTIVE_SUBSCRIPTION_STATUSES:
        monitored = []
    now = observed_at or datetime.now(UTC)
    value = {
        "status": normalized_status,
        "desiredWallets": desired,
        "monitoredWallets": monitored,
        "workerRole": worker_role,
        "workerInstanceId": worker_instance_id,
        "updatedAt": now.isoformat(),
    }
    stmt = insert(Setting).values(key=REALTIME_SUBSCRIPTION_STATE_KEY, value=value)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value},
        )
    )


async def load_realtime_subscription_state(
    session: AsyncSession,
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> RealtimeSubscriptionSnapshot:
    setting = await session.scalar(
        select(Setting).where(Setting.key == REALTIME_SUBSCRIPTION_STATE_KEY)
    )
    value = dict(setting.value) if setting is not None and isinstance(setting.value, dict) else {}
    if setting is not None and "updatedAt" not in value and setting.updated_at is not None:
        value["updatedAt"] = setting.updated_at.isoformat()
    return parse_realtime_subscription_state(
        value,
        stale_after_seconds=stale_after_seconds,
        now=now,
    )


def parse_realtime_subscription_state(
    value: dict[str, Any],
    *,
    stale_after_seconds: int,
    now: datetime | None = None,
) -> RealtimeSubscriptionSnapshot:
    observed_at = now or datetime.now(UTC)
    updated_at = parse_datetime(value.get("updatedAt"))
    is_stale = (
        updated_at is None
        or max((observed_at - updated_at).total_seconds(), 0) > stale_after_seconds
    )
    status = optional_string(value.get("status")) or "disconnected"
    if status not in SUBSCRIPTION_STATUSES:
        status = "disconnected"
    desired_wallets = tuple(normalize_wallets(value.get("desiredWallets")))
    monitored_wallets = frozenset(
        wallet
        for wallet in normalize_wallets(value.get("monitoredWallets"))
        if wallet in desired_wallets
    )
    if is_stale:
        status = "disconnected"
        desired_wallets = ()
        monitored_wallets = frozenset()
    elif status not in ACTIVE_SUBSCRIPTION_STATUSES:
        monitored_wallets = frozenset()
    elif not desired_wallets:
        status = "idle"
        monitored_wallets = frozenset()
    elif monitored_wallets == frozenset(desired_wallets):
        status = "connected"
    else:
        status = "connecting"
    return RealtimeSubscriptionSnapshot(
        status=status,
        desired_wallets=desired_wallets,
        monitored_wallets=monitored_wallets,
        worker_role=optional_string(value.get("workerRole")),
        worker_instance_id=optional_string(value.get("workerInstanceId")),
        updated_at=updated_at,
    )


def normalize_wallets(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    wallets: list[str] = []
    for item in value:
        wallet = optional_string(item)
        if wallet is None:
            continue
        normalized = wallet.lower()
        if normalized not in wallets:
            wallets.append(normalized)
    return wallets


def optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def parse_datetime(value: object) -> datetime | None:
    text = optional_string(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
