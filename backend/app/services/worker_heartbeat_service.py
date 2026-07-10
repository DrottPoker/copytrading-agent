import os
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

WORKER_HEARTBEAT_PREFIX = "worker_heartbeat:"


async def mark_worker_heartbeat(
    session: AsyncSession,
    *,
    role: str,
    trading_loops: bool,
    maintenance_loops: bool,
    started_at: datetime,
    runtime_payload: Mapping[str, Any] | None = None,
) -> None:
    value = build_worker_heartbeat_value(
        role=role,
        trading_loops=trading_loops,
        maintenance_loops=maintenance_loops,
        started_at=started_at,
        runtime_payload=runtime_payload,
    )
    key = str(value["key"])
    stmt = insert(Setting).values(key=key, value=value)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value},
        )
    )


def build_worker_heartbeat_value(
    *,
    role: str,
    trading_loops: bool,
    maintenance_loops: bool,
    started_at: datetime,
    runtime_payload: Mapping[str, Any] | None = None,
    observed_at: datetime | None = None,
    hostname: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    now = observed_at or datetime.now(UTC)
    runtime = dict(runtime_payload) if runtime_payload is not None else {}
    instance_id = optional_string(runtime.get("instanceId"))
    key = worker_heartbeat_key(role, instance_id=instance_id)
    value: dict[str, Any] = {
        "key": key,
        "role": role,
        "hostname": hostname or socket.gethostname(),
        "pid": pid if pid is not None else os.getpid(),
        "tradingLoops": trading_loops,
        "maintenanceLoops": maintenance_loops,
        "startedAt": started_at.isoformat(),
        "updatedAt": now.isoformat(),
    }
    if instance_id is not None:
        value["instanceId"] = instance_id

    capabilities = string_list(runtime.get("capabilities"))
    if capabilities:
        value["capabilities"] = capabilities

    loops = runtime.get("loops")
    if isinstance(loops, Mapping):
        value["loops"] = {
            str(name): dict(loop_state)
            for name, loop_state in loops.items()
            if isinstance(name, str) and isinstance(loop_state, Mapping)
        }

    realtime_queue = runtime.get("realtimeQueue")
    if isinstance(realtime_queue, Mapping):
        value["realtimeQueue"] = dict(realtime_queue)

    return value


async def load_worker_heartbeat_values(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        select(Setting)
        .where(Setting.key.like(f"{WORKER_HEARTBEAT_PREFIX}%"))
        .order_by(Setting.key.asc())
    )
    rows = []
    for setting in result.scalars().all():
        value = dict(setting.value) if isinstance(setting.value, dict) else {}
        value.setdefault("key", setting.key)
        if "updatedAt" not in value and setting.updated_at is not None:
            value["updatedAt"] = setting.updated_at.isoformat()
        rows.append(value)
    return rows


async def delete_worker_heartbeat(
    session: AsyncSession,
    *,
    role: str,
    instance_id: str,
) -> None:
    await session.execute(
        delete(Setting).where(Setting.key == worker_heartbeat_key(role, instance_id=instance_id))
    )


def worker_heartbeat_key(role: str, *, instance_id: str | None = None) -> str:
    key = f"{WORKER_HEARTBEAT_PREFIX}{role}"
    return f"{key}:{instance_id}" if instance_id else key


def optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    values: list[str] = []
    for item in value:
        normalized = optional_string(item)
        if normalized is not None and normalized not in values:
            values.append(normalized)
    return values
