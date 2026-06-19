import os
import socket
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
) -> None:
    now = datetime.now(UTC)
    key = worker_heartbeat_key(role)
    value = {
        "key": key,
        "role": role,
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "tradingLoops": trading_loops,
        "maintenanceLoops": maintenance_loops,
        "startedAt": started_at.isoformat(),
        "updatedAt": now.isoformat(),
    }
    stmt = insert(Setting).values(key=key, value=value)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["key"],
            set_={"value": stmt.excluded.value},
        )
    )


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


def worker_heartbeat_key(role: str) -> str:
    return f"{WORKER_HEARTBEAT_PREFIX}{role}"
