import asyncio
import os
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import check_postgres, get_sessionmaker
from app.integrations.redis_client import check_redis
from app.schemas.ops import (
    OpsBackupStatus,
    OpsDatabaseSummary,
    OpsDependencyStatus,
    OpsDiskStats,
    OpsHealthResponse,
    OpsLoadStats,
    OpsMemoryStats,
    OpsServiceConfig,
    OpsWorkerHeartbeat,
)
from app.services.database_stats_service import get_database_stats
from app.services.operation_status_service import list_operation_statuses
from app.services.worker_heartbeat_service import load_worker_heartbeat_values

OPS_OPERATION_KEYS = (
    "discovery_import",
    "discovery_prefilter",
    "discovery_backfill",
    "discovery_promotion",
    "pool_fill_import",
    "wallet_scoring",
    "wallet_prune",
)


async def get_ops_health(*, settings: Settings) -> OpsHealthResponse:
    measured_at = datetime.now(UTC)
    postgres_status, redis_status = await asyncio.gather(
        check_postgres(settings),
        check_redis(settings),
    )
    disk = get_disk_stats(settings.ops_disk_path)
    memory = get_memory_stats()
    load = get_load_stats()
    backup = get_backup_status(
        directory=settings.backup_status_directory,
        stale_after_seconds=settings.backup_status_stale_seconds,
        now=measured_at,
    )
    database, workers, operations, db_notes = await load_database_backed_ops(
        settings=settings,
        now=measured_at,
    )

    notes = [
        *db_notes,
        *status_note("postgres", postgres_status),
        *status_note("redis", redis_status),
    ]
    status = aggregate_status(
        postgres_status=postgres_status,
        redis_status=redis_status,
        disk=disk,
        memory=memory,
        load=load,
        backup=backup,
        database=database,
        workers=workers,
    )

    return OpsHealthResponse(
        measured_at=measured_at,
        status=status,
        service=settings.app_name,
        version=settings.app_version,
        config=OpsServiceConfig(
            environment=settings.app_env,
            mode=settings.system_mode,
            paper_trading_enabled=settings.paper_trading_enabled,
            live_trading_enabled=settings.live_trading_enabled,
            worker_run_in_api_process=settings.worker_run_in_api_process,
            hyperliquid_network=settings.hyperliquid_network,
            active_copy_wallets=settings.active_copy_wallets,
            max_realtime_wallets=settings.max_realtime_wallets,
        ),
        dependencies={
            "postgres": OpsDependencyStatus(**postgres_status),
            "redis": OpsDependencyStatus(**redis_status),
        },
        disk=disk,
        memory=memory,
        load=load,
        backup=backup,
        database=database,
        workers=workers,
        operations=operations,
        notes=notes,
    )


async def load_database_backed_ops(
    *,
    settings: Settings,
    now: datetime,
) -> tuple[OpsDatabaseSummary, list[OpsWorkerHeartbeat], list[Any], list[str]]:
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        return empty_database_summary("not_configured"), [], [], ["Database is not configured."]

    try:
        async with sessionmaker() as session:
            database = await load_database_summary(session)
            operations = (
                await list_operation_statuses(session, keys=OPS_OPERATION_KEYS)
            ).items
            workers = await load_worker_heartbeats(
                session,
                settings=settings,
                now=now,
            )
            return database, workers, operations, []
    except Exception as exc:
        return (
            empty_database_summary("error", error=exc.__class__.__name__),
            [],
            [],
            [f"Database-backed ops checks failed: {exc.__class__.__name__}."],
        )


async def load_database_summary(session: AsyncSession) -> OpsDatabaseSummary:
    stats = await get_database_stats(session)
    largest_table = stats.tables[0] if stats.tables else None
    return OpsDatabaseSummary(
        status="ok",
        database_name=stats.database_name,
        database_size_bytes=stats.database_size_bytes,
        database_size_pretty=stats.database_size_pretty,
        table_count=stats.table_count,
        connection_total=stats.connections.total,
        connection_max=stats.connections.max_connections,
        connection_usage_pct=stats.connections.usage_pct,
        fill_count=stats.fills.total,
        largest_table_name=largest_table.name if largest_table else None,
        largest_table_size_bytes=largest_table.total_size_bytes if largest_table else None,
        measured_at=stats.measured_at,
    )


async def load_worker_heartbeats(
    session: AsyncSession,
    *,
    settings: Settings,
    now: datetime,
) -> list[OpsWorkerHeartbeat]:
    raw_values = await load_worker_heartbeat_values(session)
    heartbeats = [
        parse_worker_heartbeat(
            value,
            now=now,
            stale_after_seconds=settings.worker_heartbeat_stale_seconds,
        )
        for value in raw_values
    ]
    existing_roles = {heartbeat.role for heartbeat in heartbeats}
    for role in expected_worker_roles(settings):
        if role not in existing_roles:
            heartbeats.append(
                OpsWorkerHeartbeat(
                    key=f"worker_heartbeat:{role}",
                    role=role,
                    status="missing",
                    updated_at=None,
                    age_seconds=None,
                    stale_after_seconds=settings.worker_heartbeat_stale_seconds,
                    hostname=None,
                    pid=None,
                    trading_loops=role in {"all", "trading"},
                    maintenance_loops=role in {"all", "maintenance"},
                    started_at=None,
                )
            )
    return sorted(heartbeats, key=lambda item: item.role)


def parse_worker_heartbeat(
    value: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: int,
) -> OpsWorkerHeartbeat:
    updated_at = parse_datetime(value.get("updatedAt"))
    started_at = parse_datetime(value.get("startedAt"))
    age_seconds = (
        max(0, int((now - updated_at).total_seconds())) if updated_at is not None else None
    )
    status = (
        "ok"
        if age_seconds is not None and age_seconds <= stale_after_seconds
        else "stale"
    )
    role = string_value(value.get("role")) or string_value(value.get("key")) or "unknown"
    if role.startswith("worker_heartbeat:"):
        role = role.removeprefix("worker_heartbeat:")

    return OpsWorkerHeartbeat(
        key=string_value(value.get("key")) or f"worker_heartbeat:{role}",
        role=role,
        status=status,
        updated_at=updated_at,
        age_seconds=age_seconds,
        stale_after_seconds=stale_after_seconds,
        hostname=string_value(value.get("hostname")),
        pid=int_value(value.get("pid")),
        trading_loops=bool(value.get("tradingLoops")),
        maintenance_loops=bool(value.get("maintenanceLoops")),
        started_at=started_at,
    )


def get_disk_stats(path: str) -> OpsDiskStats:
    try:
        usage = shutil.disk_usage(path)
        usage_pct = ratio(usage.used, usage.total)
        status = resource_status(
            usage_pct,
            warning_at=Decimal("0.85"),
            degraded_at=Decimal("0.95"),
        )
        return OpsDiskStats(
            path=path,
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            usage_pct=usage_pct,
            status=status,
        )
    except OSError:
        return OpsDiskStats(
            path=path,
            total_bytes=0,
            used_bytes=0,
            free_bytes=0,
            usage_pct=Decimal("0"),
            status="error",
        )


def get_memory_stats() -> OpsMemoryStats:
    values = read_meminfo()
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return OpsMemoryStats(
            total_bytes=total,
            available_bytes=available,
            used_bytes=None,
            usage_pct=None,
            status="unknown",
        )

    used = max(0, total - available)
    usage_pct = ratio(used, total)
    status = resource_status(
        usage_pct,
        warning_at=Decimal("0.90"),
        degraded_at=Decimal("0.95"),
    )
    return OpsMemoryStats(
        total_bytes=total,
        available_bytes=available,
        used_bytes=used,
        usage_pct=usage_pct,
        status=status,
    )


def get_load_stats() -> OpsLoadStats:
    cpu_count = os.cpu_count()
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
    except (AttributeError, OSError):
        return OpsLoadStats(
            load_1m=None,
            load_5m=None,
            load_15m=None,
            cpu_count=cpu_count,
            status="unknown",
        )

    status = "ok"
    if cpu_count and cpu_count > 0:
        load_ratio = Decimal(str(load_5m)) / Decimal(cpu_count)
        if load_ratio >= Decimal("2"):
            status = "degraded"
        elif load_ratio >= Decimal("1"):
            status = "warning"

    return OpsLoadStats(
        load_1m=Decimal(str(load_1m)),
        load_5m=Decimal(str(load_5m)),
        load_15m=Decimal(str(load_15m)),
        cpu_count=cpu_count,
        status=status,
    )


def get_backup_status(
    *,
    directory: str,
    stale_after_seconds: int,
    now: datetime,
) -> OpsBackupStatus:
    backup_dir = Path(directory)
    if not backup_dir.exists():
        return OpsBackupStatus(
            directory=directory,
            status="missing",
            latest_file=None,
            latest_modified_at=None,
            latest_size_bytes=None,
            latest_age_seconds=None,
            backup_count=0,
            total_size_bytes=0,
            stale_after_seconds=stale_after_seconds,
            note="Backup directory is not mounted or does not exist.",
        )

    dump_files = [path for path in backup_dir.glob("*.dump") if path.is_file()]
    total_size = sum(safe_stat(path).st_size for path in dump_files)
    if not dump_files:
        return OpsBackupStatus(
            directory=directory,
            status="missing",
            latest_file=None,
            latest_modified_at=None,
            latest_size_bytes=None,
            latest_age_seconds=None,
            backup_count=0,
            total_size_bytes=total_size,
            stale_after_seconds=stale_after_seconds,
            note="No Postgres dump files were found.",
        )

    latest = max(dump_files, key=lambda path: safe_stat(path).st_mtime)
    latest_stat = safe_stat(latest)
    modified_at = datetime.fromtimestamp(latest_stat.st_mtime, UTC)
    age_seconds = max(0, int((now - modified_at).total_seconds()))
    status = "ok" if age_seconds <= stale_after_seconds else "stale"
    return OpsBackupStatus(
        directory=directory,
        status=status,
        latest_file=latest.name,
        latest_modified_at=modified_at,
        latest_size_bytes=latest_stat.st_size,
        latest_age_seconds=age_seconds,
        backup_count=len(dump_files),
        total_size_bytes=total_size,
        stale_after_seconds=stale_after_seconds,
        note="Latest backup is within the expected window."
        if status == "ok"
        else "Latest backup is older than the expected window.",
    )


def read_meminfo() -> dict[str, int]:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return {}

    values: dict[str, int] = {}
    for line in meminfo_path.read_text(encoding="utf-8").splitlines():
        key, _, raw_value = line.partition(":")
        parts = raw_value.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    return values


def expected_worker_roles(settings: Settings) -> list[str]:
    if settings.worker_run_in_api_process:
        return [settings.worker_role]
    return ["trading", "maintenance"]


def aggregate_status(
    *,
    postgres_status: dict[str, Any],
    redis_status: dict[str, Any],
    disk: OpsDiskStats,
    memory: OpsMemoryStats,
    load: OpsLoadStats,
    backup: OpsBackupStatus,
    database: OpsDatabaseSummary,
    workers: list[OpsWorkerHeartbeat],
) -> str:
    if (
        postgres_status.get("status") != "ok"
        or redis_status.get("status") != "ok"
        or disk.status == "degraded"
        or memory.status == "degraded"
        or load.status == "degraded"
        or database.status in {"error", "degraded", "not_configured"}
    ):
        return "degraded"
    if (
        disk.status != "ok"
        or memory.status not in {"ok", "unknown"}
        or load.status not in {"ok", "unknown"}
        or backup.status != "ok"
        or any(worker.status != "ok" for worker in workers)
    ):
        return "warning"
    return "ok"


def status_note(name: str, status: dict[str, Any]) -> list[str]:
    if status.get("status") == "ok":
        return []
    detail = status.get("detail")
    if detail:
        return [f"{name} status is {status.get('status')}: {detail}."]
    return [f"{name} status is {status.get('status')}."]


def empty_database_summary(status: str, error: str | None = None) -> OpsDatabaseSummary:
    return OpsDatabaseSummary(
        status=status,
        database_name=None,
        database_size_bytes=None,
        database_size_pretty=None,
        table_count=None,
        connection_total=None,
        connection_max=None,
        connection_usage_pct=None,
        fill_count=None,
        largest_table_name=None,
        largest_table_size_bytes=None,
        measured_at=None,
        error=error,
    )


def ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(numerator) / Decimal(denominator)


def resource_status(
    value: Decimal,
    *,
    warning_at: Decimal,
    degraded_at: Decimal,
) -> str:
    if value >= degraded_at:
        return "degraded"
    if value >= warning_at:
        return "warning"
    return "ok"


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def int_value(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_stat(path: Path) -> os.stat_result:
    return path.stat()
