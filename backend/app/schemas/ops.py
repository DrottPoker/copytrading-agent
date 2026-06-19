from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from app.schemas.base import CamelModel
from app.schemas.operation import OperationStatusRead

OpsStatus = Literal["ok", "warning", "degraded"]
CheckStatus = Literal[
    "ok",
    "warning",
    "degraded",
    "error",
    "missing",
    "not_configured",
    "stale",
    "unknown",
]


class OpsDependencyStatus(CamelModel):
    status: str
    detail: str | None = None


class OpsDiskStats(CamelModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    usage_pct: Decimal
    status: CheckStatus


class OpsMemoryStats(CamelModel):
    total_bytes: int | None
    available_bytes: int | None
    used_bytes: int | None
    usage_pct: Decimal | None
    status: CheckStatus


class OpsLoadStats(CamelModel):
    load_1m: Decimal | None
    load_5m: Decimal | None
    load_15m: Decimal | None
    cpu_count: int | None
    status: CheckStatus


class OpsBackupStatus(CamelModel):
    directory: str
    status: CheckStatus
    latest_file: str | None
    latest_modified_at: datetime | None
    latest_size_bytes: int | None
    latest_age_seconds: int | None
    backup_count: int
    total_size_bytes: int
    stale_after_seconds: int
    note: str


class OpsDatabaseSummary(CamelModel):
    status: CheckStatus
    database_name: str | None
    database_size_bytes: int | None
    database_size_pretty: str | None
    table_count: int | None
    connection_total: int | None
    connection_max: int | None
    connection_usage_pct: Decimal | None
    fill_count: int | None
    largest_table_name: str | None
    largest_table_size_bytes: int | None
    measured_at: datetime | None
    error: str | None = None


class OpsWorkerHeartbeat(CamelModel):
    key: str
    role: str
    status: CheckStatus
    updated_at: datetime | None
    age_seconds: int | None
    stale_after_seconds: int
    hostname: str | None
    pid: int | None
    trading_loops: bool
    maintenance_loops: bool
    started_at: datetime | None


class OpsServiceConfig(CamelModel):
    environment: str
    mode: str
    paper_trading_enabled: bool
    live_trading_enabled: bool
    worker_run_in_api_process: bool
    hyperliquid_network: str
    active_copy_wallets: int
    max_realtime_wallets: int


class OpsHealthResponse(CamelModel):
    measured_at: datetime
    status: OpsStatus
    service: str
    version: str
    config: OpsServiceConfig
    dependencies: dict[str, OpsDependencyStatus]
    disk: OpsDiskStats
    memory: OpsMemoryStats
    load: OpsLoadStats
    backup: OpsBackupStatus
    database: OpsDatabaseSummary
    workers: list[OpsWorkerHeartbeat]
    operations: list[OperationStatusRead]
    notes: list[str]
    metadata: dict[str, Any] = {}
