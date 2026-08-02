from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text
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

CANCELABLE_OPERATION_KEYS = frozenset(
    {
        "discovery_import",
        "pool_fill_import",
        "wallet_scoring",
    }
)

OperationValueBuilder = Callable[[dict[str, Any]], dict[str, Any]]


class OperationCanceledError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"{OPERATION_LABELS.get(key, key)} was canceled.")
        self.key = key


class OperationNotRunningError(Exception):
    pass


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
    setting = await session.get(Setting, setting_key(key), populate_existing=True)
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
) -> str:
    now = now_iso()
    resolved_run_id = string_or_none((payload or {}).get("runId")) or new_operation_run_id()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        next_payload = {**(payload or {}), "runId": resolved_run_id}
        existing_payload = dict_payload(existing.get("payload"))
        if (
            existing.get("status") == "running"
            and existing_payload.get("runId") == resolved_run_id
            and existing_payload.get("cancelRequested") is True
        ):
            next_payload = preserve_cancellation(existing_payload, next_payload)
        return {
            **existing,
            "key": key,
            "label": OPERATION_LABELS.get(key, key),
            "status": "running",
            "startedAt": now,
            "completedAt": None,
            "updatedAt": now,
            "lastError": None,
            "durationMs": None,
            "payload": next_payload,
        }

    await write_operation_value(session, key, build_value)
    return resolved_run_id


async def mark_operation_succeeded(
    session: AsyncSession,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = now_iso()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        started_at = string_or_none(existing.get("startedAt")) or now
        next_payload = preserve_run_id(existing, payload)
        return {
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
            "payload": next_payload,
        }

    await write_operation_value(session, key, build_value)


async def mark_operation_progress(
    session: AsyncSession,
    *,
    key: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = now_iso()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        next_payload = preserve_run_id(existing, payload)
        existing_payload = dict_payload(existing.get("payload"))
        if existing_payload.get("cancelRequested") is True:
            next_payload = preserve_cancellation(existing_payload, next_payload)
        return {
            **existing,
            "key": key,
            "label": OPERATION_LABELS.get(key, key),
            "status": "running",
            "updatedAt": now,
            "lastError": None,
            "payload": next_payload,
        }

    await write_operation_value(session, key, build_value)


async def mark_operation_failed(
    session: AsyncSession,
    *,
    key: str,
    error: str,
    payload: dict[str, Any] | None = None,
) -> None:
    now = now_iso()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        started_at = string_or_none(existing.get("startedAt")) or now
        next_payload = preserve_run_id(existing, payload)
        return {
            **existing,
            "key": key,
            "label": OPERATION_LABELS.get(key, key),
            "status": "failed",
            "startedAt": started_at,
            "completedAt": now,
            "updatedAt": now,
            "durationMs": duration_ms(started_at, now),
            "lastError": error,
            "payload": next_payload,
        }

    await write_operation_value(session, key, build_value)


async def request_operation_cancellation(
    session: AsyncSession,
    *,
    key: str,
) -> OperationStatusRead:
    if key not in CANCELABLE_OPERATION_KEYS:
        raise ValueError(f"Operation {key} cannot be canceled.")

    now = now_iso()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        if existing.get("status") != "running":
            raise OperationNotRunningError(
                f"{OPERATION_LABELS.get(key, key)} is not currently running."
            )
        payload = dict_payload(existing.get("payload"))
        payload.update(
            {
                "cancelRequested": True,
                "cancelRequestedAt": now,
                "stage": "cancel_requested",
                "stageLabel": "Stopping",
                "stageDetail": "Finishing the current safe checkpoint before stopping.",
            }
        )
        return {
            **existing,
            "key": key,
            "label": OPERATION_LABELS.get(key, key),
            "status": "running",
            "updatedAt": now,
            "lastError": None,
            "payload": payload,
        }

    await write_operation_value(session, key, build_value)
    return await get_operation_status(session, key)


async def operation_cancellation_requested(
    session: AsyncSession,
    *,
    key: str,
    run_id: str,
) -> bool:
    value = await load_current_operation_value(session, key)
    if value.get("status") != "running":
        return False
    payload = dict_payload(value.get("payload"))
    return payload.get("runId") == run_id and payload.get("cancelRequested") is True


async def raise_if_operation_cancellation_requested(
    session: AsyncSession,
    *,
    key: str,
    run_id: str,
) -> None:
    if await operation_cancellation_requested(session, key=key, run_id=run_id):
        raise OperationCanceledError(key)


async def mark_operation_canceled(
    session: AsyncSession,
    *,
    key: str,
    run_id: str,
) -> None:
    now = now_iso()

    def build_value(existing: dict[str, Any]) -> dict[str, Any]:
        started_at = string_or_none(existing.get("startedAt")) or now
        payload = dict_payload(existing.get("payload"))
        payload.update(
            {
                "runId": run_id,
                "cancelRequested": True,
                "stage": "canceled",
                "stageLabel": "Canceled",
                "stageDetail": "Stopped safely at the latest completed checkpoint.",
            }
        )
        return {
            **existing,
            "key": key,
            "label": OPERATION_LABELS.get(key, key),
            "status": "canceled",
            "startedAt": started_at,
            "completedAt": now,
            "updatedAt": now,
            "durationMs": duration_ms(started_at, now),
            "lastError": None,
            "payload": payload,
        }

    await write_operation_value(session, key, build_value)


async def load_operation_value(session: AsyncSession, key: str) -> dict[str, Any]:
    setting = await session.get(Setting, setting_key(key), populate_existing=True)
    if setting is None or not isinstance(setting.value, dict):
        return {}
    return dict(setting.value)


async def load_current_operation_value(session: AsyncSession, key: str) -> dict[str, Any]:
    sessionmaker = get_sessionmaker()
    if sessionmaker is not None:
        async with sessionmaker() as status_session:
            return await load_operation_value(status_session, key)
    return await load_operation_value(session, key)


async def load_operation_value_for_update(session: AsyncSession, key: str) -> dict[str, Any]:
    await session.execute(
        text("select pg_advisory_xact_lock(hashtext(:setting_key)::bigint)"),
        {"setting_key": setting_key(key)},
    )
    result = await session.execute(
        select(Setting).where(Setting.key == setting_key(key)).with_for_update()
    )
    setting = result.scalar_one_or_none()
    if setting is None or not isinstance(setting.value, dict):
        return {}
    return dict(setting.value)


async def write_operation_value(
    session: AsyncSession,
    key: str,
    build_value: OperationValueBuilder,
) -> None:
    sessionmaker = get_sessionmaker()
    if sessionmaker is not None:
        async with sessionmaker() as status_session:
            existing = await load_operation_value_for_update(status_session, key)
            value = build_value(existing)
            await upsert_operation_value(status_session, key, value)
            await status_session.commit()
        return

    existing = await load_operation_value_for_update(session, key)
    value = build_value(existing)
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


def new_operation_run_id() -> str:
    return uuid4().hex


def dict_payload(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def preserve_run_id(
    existing: dict[str, Any],
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    next_payload = dict(payload or {})
    existing_run_id = string_or_none(dict_payload(existing.get("payload")).get("runId"))
    if "runId" not in next_payload and existing_run_id is not None:
        next_payload["runId"] = existing_run_id
    return next_payload


def preserve_cancellation(
    existing_payload: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    next_payload = dict(payload)
    for field in ("cancelRequested", "cancelRequestedAt"):
        if field in existing_payload:
            next_payload[field] = existing_payload[field]
    next_payload.update(
        {
            "stage": "cancel_requested",
            "stageLabel": "Stopping",
            "stageDetail": "Finishing the current safe checkpoint before stopping.",
        }
    )
    return next_payload


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
