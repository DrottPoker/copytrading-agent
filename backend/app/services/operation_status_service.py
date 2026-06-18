from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting
from app.db.session import get_sessionmaker
from app.schemas.operation import OperationStatusListResponse, OperationStatusRead

OPERATION_STATUS_PREFIX = "operation_status:"

OPERATION_LABELS = {
    "discovery_import": "Discovery import",
    "discovery_prefilter": "Discovery prefilter",
    "discovery_backfill": "Discovery backfill",
    "discovery_promotion": "Discovery promotion",
    "pool_fill_import": "Pool reimport",
    "wallet_scoring": "Wallet pool scoring",
    "wallet_prune": "Wallet pool prune",
}

DEFAULT_OPERATION_KEYS = (
    "discovery_import",
    "pool_fill_import",
    "wallet_scoring",
    "wallet_prune",
)


async def list_operation_statuses(
    session: AsyncSession,
    *,
    keys: tuple[str, ...] = DEFAULT_OPERATION_KEYS,
) -> OperationStatusListResponse:
    items = []
    for key in keys:
        status = await get_operation_status(session, key)
        items.append(status)
    return OperationStatusListResponse(items=items)


async def get_operation_status(session: AsyncSession, key: str) -> OperationStatusRead:
    setting = await session.get(Setting, setting_key(key))
    label = OPERATION_LABELS.get(key, key)
    if setting is None or not isinstance(setting.value, dict):
        return OperationStatusRead(
            key=key,
            label=label,
            status="idle",
            payload={},
        )

    value = setting.value
    return OperationStatusRead(
        key=key,
        label=str(value.get("label") or label),
        status=str(value.get("status") or "idle"),
        started_at=string_or_none(value.get("startedAt")),
        completed_at=string_or_none(value.get("completedAt")),
        updated_at=string_or_none(value.get("updatedAt")),
        last_success_at=string_or_none(value.get("lastSuccessAt")),
        duration_ms=int_or_none(value.get("durationMs")),
        last_error=string_or_none(value.get("lastError")),
        payload=dict(value.get("payload")) if isinstance(value.get("payload"), dict) else {},
    )


async def mark_operation_started(
    session: AsyncSession,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    existing = await load_operation_value_for_update(session, key)
    now = now_iso()
    value = {
        **existing,
        "key": key,
        "label": OPERATION_LABELS.get(key, key),
        "status": "running",
        "startedAt": now,
        "completedAt": None,
        "updatedAt": now,
        "lastError": None,
        "durationMs": None,
        "payload": payload or {},
    }
    await save_operation_value(session, key, value)


async def mark_operation_succeeded(
    session: AsyncSession,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    existing = await load_operation_value_for_update(session, key)
    now = now_iso()
    started_at = string_or_none(existing.get("startedAt")) or now
    value = {
        **existing,
        "key": key,
        "label": OPERATION_LABELS.get(key, key),
        "status": "succeeded",
        "startedAt": started_at,
        "completedAt": now,
        "updatedAt": now,
        "lastSuccessAt": now,
        "durationMs": duration_ms(started_at, now),
        "lastError": None,
        "payload": payload or {},
    }
    await save_operation_value(session, key, value)


async def mark_operation_progress(
    session: AsyncSession,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    existing = await load_operation_value_for_update(session, key)
    now = now_iso()
    value = {
        **existing,
        "key": key,
        "label": OPERATION_LABELS.get(key, key),
        "status": "running",
        "updatedAt": now,
        "lastError": None,
        "payload": payload or {},
    }
    await save_operation_value(session, key, value)


async def mark_operation_failed(
    session: AsyncSession,
    *,
    key: str,
    error: str,
    payload: dict[str, Any] | None = None,
) -> None:
    existing = await load_operation_value_for_update(session, key)
    now = now_iso()
    started_at = string_or_none(existing.get("startedAt")) or now
    value = {
        **existing,
        "key": key,
        "label": OPERATION_LABELS.get(key, key),
        "status": "failed",
        "startedAt": started_at,
        "completedAt": now,
        "updatedAt": now,
        "durationMs": duration_ms(started_at, now),
        "lastError": error,
        "payload": payload or {},
    }
    await save_operation_value(session, key, value)


async def load_operation_value(session: AsyncSession, key: str) -> dict[str, Any]:
    setting = await session.get(Setting, setting_key(key))
    if setting is None or not isinstance(setting.value, dict):
        return {}
    return dict(setting.value)


async def load_operation_value_for_update(session: AsyncSession, key: str) -> dict[str, Any]:
    sessionmaker = get_sessionmaker()
    if sessionmaker is not None:
        async with sessionmaker() as status_session:
            return await load_operation_value(status_session, key)
    return await load_operation_value(session, key)


async def save_operation_value(
    session: AsyncSession,
    key: str,
    value: dict[str, Any],
) -> None:
    sessionmaker = get_sessionmaker()
    if sessionmaker is not None:
        async with sessionmaker() as status_session:
            await upsert_operation_value(status_session, key, value)
            await status_session.commit()
        return

    await upsert_operation_value(session, key, value)
    await session.commit()


async def upsert_operation_value(
    session: AsyncSession,
    key: str,
    value: dict[str, Any],
) -> None:
    stmt = insert(Setting).values(key=setting_key(key), value=value)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value},
        )
    )


def setting_key(key: str) -> str:
    return f"{OPERATION_STATUS_PREFIX}{key}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def duration_ms(started_at: str, completed_at: str) -> int | None:
    try:
        started = datetime.fromisoformat(started_at)
        completed = datetime.fromisoformat(completed_at)
    except ValueError:
        return None
    return max(0, int((completed - started).total_seconds() * 1000))
