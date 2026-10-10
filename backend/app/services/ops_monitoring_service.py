import asyncio
import os
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import TradingAccount
from app.db.session import check_postgres, get_sessionmaker
from app.integrations.redis_client import check_redis
from app.schemas.ops import (
    OpsBackupStatus,
    OpsDatabaseSummary,
    OpsDependencyStatus,
    OpsDiscoverySourceHealth,
    OpsDiskStats,
    OpsHealthResponse,
    OpsLiveSigner,
    OpsLoadStats,
    OpsMemoryStats,
    OpsRealtimeQueueHealth,
    OpsServiceConfig,
    OpsWorkerHeartbeat,
    OpsWorkerLoopState,
)
from app.services.database_stats_service import get_database_summary_stats
from app.services.live_signer_service import (
    SIGNER_API_WALLET,
    current_live_signer,
    live_signer_required,
)
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
# Consecutive failed runs before a discovery source counts as down.
DISCOVERY_SOURCE_DEGRADED_FAILURES = 3
DISCOVERY_SOURCE_RECENT_RUNS = 10


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
        enabled=settings.backup_status_enabled,
        directory=settings.backup_status_directory,
        stale_after_seconds=settings.backup_status_stale_seconds,
        now=measured_at,
    )
    (
        database,
        workers,
        operations,
        discovery_sources,
        live_signers,
        db_notes,
    ) = await load_database_backed_ops(settings=settings, now=measured_at)

    notes = [
        *db_notes,
        *status_note("postgres", postgres_status),
        *status_note("redis", redis_status),
        *discovery_source_notes(discovery_sources),
        *live_signer_notes(live_signers, settings=settings),
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
        discovery_sources=discovery_sources,
        live_signer_warning=bool(live_signer_notes(live_signers, settings=settings)),
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
        discovery_sources=discovery_sources,
        live_signers=live_signers,
        notes=notes,
    )


async def load_database_backed_ops(
    *,
    settings: Settings,
    now: datetime,
) -> tuple[
    OpsDatabaseSummary,
    list[OpsWorkerHeartbeat],
    list[Any],
    list[OpsDiscoverySourceHealth],
    list[OpsLiveSigner],
    list[str],
]:
    sessionmaker = get_sessionmaker(settings)
    if sessionmaker is None:
        return (
            empty_database_summary("not_configured"),
            [],
            [],
            [],
            [],
            ["Database is not configured."],
        )

    try:
        async with sessionmaker() as session:
            database = await load_database_summary(session)
            operations = (await list_operation_statuses(session, keys=OPS_OPERATION_KEYS)).items
            workers = await load_worker_heartbeats(
                session,
                settings=settings,
                now=now,
            )
            discovery_sources = await load_discovery_source_health(session, settings=settings)
            live_signers = await load_live_signers(session, settings=settings)
            return database, workers, operations, discovery_sources, live_signers, []
    except Exception as exc:
        return (
            empty_database_summary("error", error=exc.__class__.__name__),
            [],
            [],
            [],
            [],
            [f"Database-backed ops checks failed: {exc.__class__.__name__}."],
        )


async def load_live_signers(session: AsyncSession, *, settings: Settings) -> list[OpsLiveSigner]:
    accounts = (
        await session.scalars(
            select(TradingAccount)
            .where(TradingAccount.account_type == "live", TradingAccount.archived_at.is_(None))
            .order_by(TradingAccount.key)
        )
    ).all()
    required = live_signer_required(settings)
    signers = []
    for account in accounts:
        signer = current_live_signer(account, settings=settings)
        signers.append(
            OpsLiveSigner(
                account_key=account.key,
                account_status=account.status,
                status=signer.status,
                description=signer.description,
                required=required,
                signer_address=signer.signer_address or None,
                master_address=signer.master_address or None,
                checked_at=signer.checked_at,
                attempted_at=signer.attempted_at,
                error=signer.error,
            )
        )
    return signers


def live_signer_notes(signers: list[OpsLiveSigner], *, settings: Settings) -> list[str]:
    """Warn about live accounts that may trade with a key other than a verified API wallet."""

    if not settings.live_trading_enabled:
        return []
    return [
        f"Live account {signer.account_key} signs with {signer.description}."
        for signer in signers
        if signer.account_status in {"enabled", "exit_only"} and signer.status != SIGNER_API_WALLET
    ]


async def load_discovery_source_health(
    session: AsyncSession,
    *,
    settings: Settings,
) -> list[OpsDiscoverySourceHealth]:
    """Health of each configured discovery source from its recent import runs.

    Several sources are unofficial endpoints that can change without notice, so
    repeated failures or empty results are surfaced instead of failing silently.
    """

    if not settings.discovery_enabled or not settings.discovery_default_sources:
        return []
    result = await session.execute(
        text(
            """
            select source, status, fetched_count, error, started_at
            from (
              select
                source,
                status,
                fetched_count,
                error,
                started_at,
                row_number() over (partition by source order by started_at desc) as recent_rank
              from discovery_import_runs
              where source = any(:sources)
                and status in ('succeeded', 'failed')
            ) ranked
            where recent_rank <= :limit
            order by source, started_at desc
            """
        ),
        {
            "sources": list(settings.discovery_default_sources),
            "limit": DISCOVERY_SOURCE_RECENT_RUNS,
        },
    )
    runs_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        runs_by_source.setdefault(str(row["source"]), []).append(dict(row))
    return [
        discovery_source_health(source, runs_by_source.get(source, []))
        for source in settings.discovery_default_sources
    ]


def discovery_source_health(
    source: str,
    runs: list[dict[str, Any]],
) -> OpsDiscoverySourceHealth:
    """Summarize newest-first runs of one source."""

    if not runs:
        return OpsDiscoverySourceHealth(
            source=source,
            status="unknown",
            last_run_at=None,
            last_success_at=None,
            consecutive_failures=0,
            last_fetched_count=None,
            last_error=None,
        )
    consecutive_failures = 0
    for run in runs:
        if run["status"] != "failed":
            break
        consecutive_failures += 1
    last_success = next((run for run in runs if run["status"] == "succeeded"), None)
    last_failure = next((run for run in runs if run["status"] == "failed"), None)
    last_fetched_count = int(last_success["fetched_count"]) if last_success else None
    if consecutive_failures >= DISCOVERY_SOURCE_DEGRADED_FAILURES:
        status = "degraded"
    elif consecutive_failures > 0 or last_fetched_count == 0:
        status = "warning"
    else:
        status = "ok"
    return OpsDiscoverySourceHealth(
        source=source,
        status=status,
        last_run_at=runs[0]["started_at"],
        last_success_at=last_success["started_at"] if last_success else None,
        consecutive_failures=consecutive_failures,
        last_fetched_count=last_fetched_count,
        last_error=(
            str(last_failure["error"])
            if consecutive_failures > 0 and last_failure and last_failure["error"]
            else None
        ),
    )


def discovery_source_notes(sources: list[OpsDiscoverySourceHealth]) -> list[str]:
    notes: list[str] = []
    for source in sources:
        if source.consecutive_failures > 0:
            notes.append(
                f"Discovery source {source.source} failed {source.consecutive_failures} "
                f"run(s) in a row: {source.last_error or 'unknown error'}."
            )
        elif source.last_fetched_count == 0:
            notes.append(f"Discovery source {source.source} returned no wallets last run.")
    return notes


async def load_database_summary(session: AsyncSession) -> OpsDatabaseSummary:
    stats = await get_database_summary_stats(session)
    overview = stats["overview"]
    table_rows = stats["table_rows"]
    connections = stats["connections"]
    largest_table = table_rows[0] if table_rows else None
    return OpsDatabaseSummary(
        status="ok",
        database_name=str(overview["database_name"]),
        database_size_bytes=int(overview["database_size_bytes"] or 0),
        database_size_pretty=str(overview["database_size_pretty"]),
        table_count=len(table_rows),
        connection_total=connections.total,
        connection_max=connections.max_connections,
        connection_usage_pct=connections.usage_pct,
        fill_count=stats["fill_count"],
        largest_table_name=str(largest_table["name"]) if largest_table else None,
        largest_table_size_bytes=(
            int(largest_table["total_size_bytes"] or 0) if largest_table else None
        ),
        measured_at=overview["measured_at"],
    )


async def load_worker_heartbeats(
    session: AsyncSession,
    *,
    settings: Settings,
    now: datetime,
) -> list[OpsWorkerHeartbeat]:
    raw_values = await load_worker_heartbeat_values(session)
    expected_roles = set(expected_worker_roles(settings))
    parsed_heartbeats = [
        parse_worker_heartbeat(
            value,
            now=now,
            stale_after_seconds=settings.worker_heartbeat_stale_seconds,
        )
        for value in raw_values
    ]
    heartbeats: list[OpsWorkerHeartbeat] = []
    for role in sorted(expected_roles):
        role_heartbeats = [item for item in parsed_heartbeats if item.role == role]
        active = [item for item in role_heartbeats if item.status != "stale"]
        if active:
            if len(active) > 1:
                active = [item.model_copy(update={"status": "warning"}) for item in active]
            heartbeats.extend(active)
        elif role_heartbeats:
            heartbeats.append(
                max(
                    role_heartbeats,
                    key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
                )
            )
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
    return sorted(heartbeats, key=lambda item: (item.role, item.instance_id or "", item.key))


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
    key = string_value(value.get("key")) or ""
    role = string_value(value.get("role")) or worker_role_from_key(key) or "unknown"
    instance_id = string_value(value.get("instanceId")) or worker_instance_from_key(key)
    capabilities = string_list(value.get("capabilities"))
    trading_loops = bool(value.get("tradingLoops")) or "trading" in capabilities
    maintenance_loops = bool(value.get("maintenanceLoops")) or "maintenance" in capabilities
    if not capabilities:
        capabilities = [
            capability
            for capability, enabled in (
                ("trading", trading_loops),
                ("maintenance", maintenance_loops),
            )
            if enabled
        ]
    loops = parse_worker_loop_states(value.get("loops"))
    realtime_queue = parse_realtime_queue_health(value.get("realtimeQueue"))
    status = worker_heartbeat_status(
        age_seconds=age_seconds,
        stale_after_seconds=stale_after_seconds,
        loops=loops,
        realtime_queue=realtime_queue,
    )

    return OpsWorkerHeartbeat(
        key=key or f"worker_heartbeat:{role}",
        role=role,
        status=status,
        updated_at=updated_at,
        age_seconds=age_seconds,
        stale_after_seconds=stale_after_seconds,
        hostname=string_value(value.get("hostname")),
        pid=int_value(value.get("pid")),
        trading_loops=trading_loops,
        maintenance_loops=maintenance_loops,
        started_at=started_at,
        instance_id=instance_id,
        capabilities=capabilities,
        loops=loops,
        realtime_queue=realtime_queue,
    )


def parse_worker_loop_states(value: object) -> list[OpsWorkerLoopState]:
    if not isinstance(value, dict):
        return []

    loops = []
    for name, raw_state in sorted(value.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str) or not isinstance(raw_state, dict):
            continue
        status = string_value(raw_state.get("status")) or "unknown"
        loops.append(
            OpsWorkerLoopState(
                name=name,
                status=status,
                health=worker_loop_health(status),
                restart_count=non_negative_int(raw_state.get("restartCount")),
                consecutive_failures=non_negative_int(raw_state.get("consecutiveFailures")),
                last_error=string_value(raw_state.get("lastError")),
                last_started_at=parse_datetime(raw_state.get("lastStartedAt")),
                last_progress_at=parse_datetime(raw_state.get("lastProgressAt")),
                updated_at=parse_datetime(raw_state.get("updatedAt")),
            )
        )
    return loops


def parse_realtime_queue_health(value: object) -> OpsRealtimeQueueHealth | None:
    if not isinstance(value, dict):
        return None
    depth = non_negative_int(value.get("depth"))
    capacity = non_negative_int(value.get("capacity"))
    dropped = non_negative_int(value.get("dropped"))
    utilization = Decimal(depth) / Decimal(capacity) if capacity > 0 else None
    if capacity <= 0:
        status = "unknown"
    elif dropped > 0 or utilization is not None and utilization >= Decimal("0.8"):
        status = "warning"
    else:
        status = "ok"
    return OpsRealtimeQueueHealth(
        depth=depth,
        capacity=capacity,
        dropped=dropped,
        utilization_pct=utilization,
        status=status,
    )


def worker_heartbeat_status(
    *,
    age_seconds: int | None,
    stale_after_seconds: int,
    loops: list[OpsWorkerLoopState],
    realtime_queue: OpsRealtimeQueueHealth | None,
) -> str:
    if age_seconds is None or age_seconds > stale_after_seconds:
        return "stale"
    if any(loop.health == "degraded" for loop in loops):
        return "degraded"
    if any(loop.health == "warning" for loop in loops):
        return "warning"
    if realtime_queue is not None and realtime_queue.status == "warning":
        return "warning"
    return "ok"


def worker_loop_health(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"failed", "error", "crashed"}:
        return "degraded"
    if normalized in {"restarting", "retrying", "stopped", "paused", "unknown"}:
        return "warning"
    return "ok"


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
    except AttributeError, OSError:
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
    enabled: bool,
    directory: str,
    stale_after_seconds: int,
    now: datetime,
) -> OpsBackupStatus:
    if not enabled:
        return OpsBackupStatus(
            directory=directory,
            status="disabled",
            latest_file=None,
            latest_modified_at=None,
            latest_size_bytes=None,
            latest_age_seconds=None,
            backup_count=0,
            total_size_bytes=0,
            stale_after_seconds=stale_after_seconds,
            note="Backup status monitoring is disabled.",
        )

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
    discovery_sources: list[OpsDiscoverySourceHealth] | None = None,
    live_signer_warning: bool = False,
) -> str:
    if (
        postgres_status.get("status") != "ok"
        or redis_status.get("status") != "ok"
        or disk.status == "degraded"
        or memory.status == "degraded"
        or load.status == "degraded"
        or database.status in {"error", "degraded", "not_configured"}
        or any(worker.status in {"degraded", "error"} for worker in workers)
    ):
        return "degraded"
    if (
        disk.status != "ok"
        or memory.status not in {"ok", "unknown"}
        or load.status not in {"ok", "unknown"}
        or backup.status not in {"ok", "disabled"}
        or any(worker.status != "ok" for worker in workers)
        # Discovery feeds the pool slowly, so a broken source warns but never degrades.
        or any(source.status in {"warning", "degraded"} for source in discovery_sources or [])
        or live_signer_warning
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


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    values: list[str] = []
    for item in value:
        normalized = string_value(item)
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def worker_role_from_key(key: str) -> str | None:
    if not key.startswith("worker_heartbeat:"):
        return None
    role_and_instance = key.removeprefix("worker_heartbeat:")
    role, _, _instance_id = role_and_instance.partition(":")
    return role or None


def worker_instance_from_key(key: str) -> str | None:
    if not key.startswith("worker_heartbeat:"):
        return None
    role_and_instance = key.removeprefix("worker_heartbeat:")
    _role, separator, instance_id = role_and_instance.partition(":")
    return instance_id if separator and instance_id else None


def int_value(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def non_negative_int(value: object) -> int:
    return max(int_value(value) or 0, 0)


def safe_stat(path: Path) -> os.stat_result:
    return path.stat()
