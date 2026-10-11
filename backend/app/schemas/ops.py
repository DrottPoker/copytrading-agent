from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

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
    "disabled",
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


class OpsWorkerLoopState(CamelModel):
    name: str
    status: str
    health: CheckStatus
    restart_count: int
    consecutive_failures: int
    last_error: str | None
    last_started_at: datetime | None
    last_progress_at: datetime | None
    updated_at: datetime | None


class OpsRealtimeQueueHealth(CamelModel):
    depth: int
    capacity: int
    dropped: int
    utilization_pct: Decimal | None
    status: CheckStatus


class OpsHyperliquidRequestType(CamelModel):
    type: str
    weight: int


class OpsHyperliquidProcessUsage(CamelModel):
    """One process's Hyperliquid info request weight, over complete minutes."""

    process: str
    instance_id: str | None = None
    updated_at: datetime | None = None
    budget_mode: str
    budget_weight_per_minute: int
    weight_last_minute: int
    weight_per_minute_15m: float
    execution_weight_per_minute_15m: float
    background_weight_per_minute_15m: float
    peak_weight_per_minute_60m: int
    requests_15m: int
    execution_wait_seconds_15m: float
    background_wait_seconds_15m: float
    rate_limited_60m: int
    top_request_types_15m: list[OpsHyperliquidRequestType] = Field(default_factory=list)


class OpsHyperliquidBudget(CamelModel):
    """Info request weight of every process against Hyperliquid's per-IP limit."""

    status: CheckStatus
    ip_weight_limit_per_minute: int
    process_weight_per_minute: int
    total_weight_last_minute: int
    total_weight_per_minute_15m: float
    rate_limited_60m: int
    processes: list[OpsHyperliquidProcessUsage] = Field(default_factory=list)


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
    instance_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    loops: list[OpsWorkerLoopState] = Field(default_factory=list)
    realtime_queue: OpsRealtimeQueueHealth | None = None
    hyperliquid_usage: OpsHyperliquidProcessUsage | None = None


class OpsDiscoverySourceHealth(CamelModel):
    source: str
    status: CheckStatus
    last_run_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_fetched_count: int | None
    last_error: str | None


class OpsLiveSigner(CamelModel):
    account_key: str
    account_status: str
    status: str
    description: str
    required: bool
    signer_address: str | None
    master_address: str | None
    checked_at: datetime | None
    attempted_at: datetime | None
    error: str | None


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
    discovery_sources: list[OpsDiscoverySourceHealth] = Field(default_factory=list)
    live_signers: list[OpsLiveSigner] = Field(default_factory=list)
    hyperliquid_budget: OpsHyperliquidBudget | None = None
    notes: list[str]
    metadata: dict[str, Any] = {}
