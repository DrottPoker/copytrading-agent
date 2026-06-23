import type { OperationStatus } from "./operation";

export type OpsCheckStatus =
  | "ok"
  | "warning"
  | "degraded"
  | "error"
  | "missing"
  | "not_configured"
  | "stale"
  | "unknown"
  | "disabled";

export type OpsDependencyStatus = {
  status: string;
  detail: string | null;
};

export type OpsDiskStats = {
  path: string;
  totalBytes: number;
  usedBytes: number;
  freeBytes: number;
  usagePct: string;
  status: OpsCheckStatus;
};

export type OpsMemoryStats = {
  totalBytes: number | null;
  availableBytes: number | null;
  usedBytes: number | null;
  usagePct: string | null;
  status: OpsCheckStatus;
};

export type OpsLoadStats = {
  load1m: string | null;
  load5m: string | null;
  load15m: string | null;
  cpuCount: number | null;
  status: OpsCheckStatus;
};

export type OpsBackupStatus = {
  directory: string;
  status: OpsCheckStatus;
  latestFile: string | null;
  latestModifiedAt: string | null;
  latestSizeBytes: number | null;
  latestAgeSeconds: number | null;
  backupCount: number;
  totalSizeBytes: number;
  staleAfterSeconds: number;
  note: string;
};

export type OpsDatabaseSummary = {
  status: OpsCheckStatus;
  databaseName: string | null;
  databaseSizeBytes: number | null;
  databaseSizePretty: string | null;
  tableCount: number | null;
  connectionTotal: number | null;
  connectionMax: number | null;
  connectionUsagePct: string | null;
  fillCount: number | null;
  largestTableName: string | null;
  largestTableSizeBytes: number | null;
  measuredAt: string | null;
  error: string | null;
};

export type OpsWorkerHeartbeat = {
  key: string;
  role: string;
  status: OpsCheckStatus;
  updatedAt: string | null;
  ageSeconds: number | null;
  staleAfterSeconds: number;
  hostname: string | null;
  pid: number | null;
  tradingLoops: boolean;
  maintenanceLoops: boolean;
  startedAt: string | null;
};

export type OpsServiceConfig = {
  environment: string;
  mode: string;
  paperTradingEnabled: boolean;
  liveTradingEnabled: boolean;
  workerRunInApiProcess: boolean;
  hyperliquidNetwork: string;
  activeCopyWallets: number;
  maxRealtimeWallets: number;
};

export type OpsHealthResponse = {
  measuredAt: string;
  status: "ok" | "warning" | "degraded";
  service: string;
  version: string;
  config: OpsServiceConfig;
  dependencies: Record<string, OpsDependencyStatus>;
  disk: OpsDiskStats;
  memory: OpsMemoryStats;
  load: OpsLoadStats;
  backup: OpsBackupStatus;
  database: OpsDatabaseSummary;
  workers: OpsWorkerHeartbeat[];
  operations: OperationStatus[];
  notes: string[];
  metadata: Record<string, unknown>;
};
