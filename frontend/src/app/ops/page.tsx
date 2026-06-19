import {
  Activity,
  AlertTriangle,
  Archive,
  Clock,
  Cpu,
  Database,
  HardDrive,
  MemoryStick,
  RadioTower,
  ServerCog,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { AutoRefresh } from "@/components/AutoRefresh";
import { StatusPill } from "@/components/StatusPill";
import { getOpsHealth } from "@/lib/api";
import {
  formatBytes,
  formatCompact,
  formatDate,
  formatInteger,
  formatPercent,
  numberValue,
} from "@/lib/format";
import type {
  OpsBackupStatus,
  OpsCheckStatus,
  OpsDatabaseSummary,
  OpsDependencyStatus,
  OpsDiskStats,
  OpsHealthResponse,
  OpsLoadStats,
  OpsMemoryStats,
  OpsWorkerHeartbeat,
} from "@/types/ops";
import type { OperationStatus } from "@/types/operation";

export default async function OpsPage() {
  const ops = await getOpsHealth();

  if (!ops) {
    return (
      <>
        <PageTitle />
        <section className="rounded-lg border border-[#efb1aa] bg-[#fff5f3] p-6 text-danger">
          Could not reach ops health API.
        </section>
      </>
    );
  }

  return (
    <>
      <PageTitle ops={ops} />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SummaryTile
          detail={`Measured ${formatDate(ops.measuredAt)}`}
          icon={ShieldCheck}
          label="Overall"
          status={ops.status}
          value={ops.status}
        />
        <SummaryTile
          detail={`${formatBytes(ops.disk.freeBytes)} free`}
          icon={HardDrive}
          label="Disk"
          status={ops.disk.status}
          value={formatPercent(ops.disk.usagePct)}
        />
        <SummaryTile
          detail={`${formatBytes(ops.memory.availableBytes)} available`}
          icon={MemoryStick}
          label="Memory"
          status={ops.memory.status}
          value={formatPercent(ops.memory.usagePct)}
        />
        <SummaryTile
          detail={ops.backup.latestFile ?? "No backup file found"}
          icon={Archive}
          label="Backups"
          status={ops.backup.status}
          value={formatAge(ops.backup.latestAgeSeconds)}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Panel icon={ServerCog} title="VPS Runtime">
          <RuntimeList disk={ops.disk} load={ops.load} memory={ops.memory} />
        </Panel>

        <Panel icon={Activity} title="Services">
          <DependencyList dependencies={ops.dependencies} />
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            <SmallMetric label="Environment" value={ops.config.environment} />
            <SmallMetric label="Mode" value={ops.config.mode} />
            <SmallMetric label="Network" value={ops.config.hyperliquidNetwork} />
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Panel icon={RadioTower} title="Workers">
          <div className="divide-y divide-line">
            {ops.workers.map((worker) => (
              <WorkerRow key={worker.key} worker={worker} />
            ))}
          </div>
        </Panel>

        <Panel icon={Archive} title="Backup Status">
          <BackupDetails backup={ops.backup} />
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel icon={Database} title="Database Summary">
          <DatabaseDetails database={ops.database} />
        </Panel>

        <Panel icon={Clock} title="Operations">
          <div className="divide-y divide-line">
            {ops.operations.map((operation) => (
              <OperationRow key={operation.key} operation={operation} />
            ))}
          </div>
        </Panel>
      </section>

      {ops.notes.length > 0 ? (
        <Panel icon={AlertTriangle} title="Notes">
          <div className="grid gap-2">
            {ops.notes.map((note) => (
              <p key={note} className="rounded-md border border-[#f0c36d] bg-[#fff8e8] p-3 text-sm">
                {note}
              </p>
            ))}
          </div>
        </Panel>
      ) : null}
    </>
  );
}

function PageTitle({ ops }: { ops?: OpsHealthResponse }) {
  return (
    <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div>
        <p className="text-sm font-medium text-[#5b6770]">VPS health and monitoring</p>
        <h1 className="mt-1 text-2xl font-semibold tracking-normal text-ink">Ops Health</h1>
      </div>
      {ops ? (
        <div className="flex flex-wrap gap-2">
          <AutoRefresh intervalMs={15000} />
          <StatusPill label={ops.status} tone={toneForStatus(ops.status)} />
          <StatusPill
            label={ops.config.liveTradingEnabled ? "live enabled" : "paper mode"}
            tone={ops.config.liveTradingEnabled ? "danger" : "positive"}
          />
          <StatusPill label={`v${ops.version}`} />
        </div>
      ) : null}
    </header>
  );
}

function SummaryTile({
  detail,
  icon: Icon,
  label,
  status,
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  status: OpsCheckStatus | "ok" | "warning" | "degraded";
  value: string;
}) {
  const toneClass =
    status === "ok"
      ? "border-[#9ccfc0] bg-[#f2fbf7]"
      : status === "degraded" || status === "error"
        ? "border-[#efb1aa] bg-[#fff5f3]"
        : "border-[#e7c174] bg-[#fff9e8]";

  return (
    <article className={`rounded-lg border p-4 shadow-sm ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
          <p className="mt-2 truncate text-2xl font-semibold">{value}</p>
        </div>
        <Icon className="h-5 w-5 shrink-0 text-[#5b6770]" aria-hidden="true" />
      </div>
      <p className="mt-3 truncate text-sm text-[#5b6770]">{detail}</p>
    </article>
  );
}

function Panel({
  children,
  icon: Icon,
  title,
}: {
  children: ReactNode;
  icon: LucideIcon;
  title: string;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Icon className="h-4 w-4 text-[#5b6770]" aria-hidden="true" />
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function RuntimeList({
  disk,
  load,
  memory,
}: {
  disk: OpsDiskStats;
  load: OpsLoadStats;
  memory: OpsMemoryStats;
}) {
  return (
    <div className="grid gap-3">
      <ResourceRow
        detail={`${formatBytes(disk.usedBytes)} used, ${formatBytes(disk.freeBytes)} free`}
        icon={HardDrive}
        label={`Disk ${disk.path}`}
        status={disk.status}
        value={formatPercent(disk.usagePct)}
      />
      <ResourceRow
        detail={`${formatBytes(memory.usedBytes)} used, ${formatBytes(memory.availableBytes)} available`}
        icon={MemoryStick}
        label="Memory"
        status={memory.status}
        value={formatPercent(memory.usagePct)}
      />
      <ResourceRow
        detail={`${formatInteger(load.cpuCount)} CPU, 5m load ${formatDecimal(load.load5m)}`}
        icon={Cpu}
        label="Load"
        status={load.status}
        value={formatDecimal(load.load1m)}
      />
    </div>
  );
}

function ResourceRow({
  detail,
  icon: Icon,
  label,
  status,
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  status: OpsCheckStatus;
  value: string;
}) {
  return (
    <div className="grid gap-3 rounded-md border border-line bg-[#f8fafb] p-3 sm:grid-cols-[24px_1fr_90px] sm:items-center">
      <Icon className="h-4 w-4 text-[#5b6770]" aria-hidden="true" />
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold">{label}</p>
        <p className="mt-1 truncate text-xs text-[#5b6770]">{detail}</p>
      </div>
      <div className="flex items-center justify-between gap-2 sm:justify-end">
        <span className="font-mono text-sm font-semibold">{value}</span>
        <StatusPill label={status} tone={toneForStatus(status)} />
      </div>
    </div>
  );
}

function DependencyList({
  dependencies,
}: {
  dependencies: Record<string, OpsDependencyStatus>;
}) {
  return (
    <div className="grid gap-3">
      {Object.entries(dependencies).map(([name, dependency]) => (
        <div
          key={name}
          className="flex items-center justify-between gap-3 rounded-md border border-line bg-[#f8fafb] px-3 py-2"
        >
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold capitalize">{name}</p>
            <p className="mt-1 truncate text-xs text-[#5b6770]">{dependency.detail ?? "Ready"}</p>
          </div>
          <StatusPill label={dependency.status} tone={toneForStatus(dependency.status)} />
        </div>
      ))}
    </div>
  );
}

function WorkerRow({ worker }: { worker: OpsWorkerHeartbeat }) {
  const loops = [
    worker.tradingLoops ? "trading" : null,
    worker.maintenanceLoops ? "maintenance" : null,
  ].filter(Boolean);

  return (
    <div className="grid gap-3 py-3 sm:grid-cols-[130px_1fr_120px] sm:items-center">
      <div>
        <p className="text-sm font-semibold">{worker.role}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{loops.join(", ") || "no loops"}</p>
      </div>
      <div className="min-w-0">
        <p className="truncate text-sm">
          {worker.hostname ?? "-"}
          {worker.pid ? `, pid ${worker.pid}` : ""}
        </p>
        <p className="mt-1 text-xs text-[#5b6770]">
          Last heartbeat {formatAge(worker.ageSeconds)}, started {formatDate(worker.startedAt)}
        </p>
      </div>
      <StatusPill label={worker.status} tone={toneForStatus(worker.status)} />
    </div>
  );
}

function BackupDetails({ backup }: { backup: OpsBackupStatus }) {
  return (
    <div className="grid gap-3">
      <KeyValue label="Status" value={<StatusPill label={backup.status} tone={toneForStatus(backup.status)} />} />
      <KeyValue label="Latest file" value={backup.latestFile ?? "-"} />
      <KeyValue label="Latest age" value={formatAge(backup.latestAgeSeconds)} />
      <KeyValue label="Latest size" value={formatBytes(backup.latestSizeBytes)} />
      <KeyValue label="Backup files" value={formatInteger(backup.backupCount)} />
      <KeyValue label="Backup folder size" value={formatBytes(backup.totalSizeBytes)} />
      <KeyValue label="Directory" value={backup.directory} />
      <p className="rounded-md border border-line bg-[#f8fafb] p-3 text-sm text-[#5b6770]">
        {backup.note}
      </p>
    </div>
  );
}

function DatabaseDetails({ database }: { database: OpsDatabaseSummary }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <SmallMetric label="Status" value={database.status} />
      <SmallMetric label="Database" value={database.databaseName ?? "-"} />
      <SmallMetric label="Size" value={database.databaseSizePretty ?? "-"} />
      <SmallMetric label="Tables" value={formatInteger(database.tableCount)} />
      <SmallMetric label="Fills" value={formatCompact(database.fillCount)} />
      <SmallMetric
        label="Connections"
        value={`${formatInteger(database.connectionTotal)}/${formatInteger(database.connectionMax)}`}
      />
      <SmallMetric label="Connection usage" value={formatPercent(database.connectionUsagePct)} />
      <SmallMetric
        label="Largest table"
        value={`${database.largestTableName ?? "-"} ${formatBytes(database.largestTableSizeBytes)}`}
      />
    </div>
  );
}

function OperationRow({ operation }: { operation: OperationStatus }) {
  return (
    <div className="grid gap-3 py-3 sm:grid-cols-[160px_120px_1fr] sm:items-center">
      <div>
        <p className="text-sm font-semibold">{operation.label}</p>
        <p className="mt-1 text-xs text-[#5b6770]">Updated {formatDate(operation.updatedAt)}</p>
      </div>
      <StatusPill label={operation.status} tone={toneForStatus(operation.status)} />
      <div className="min-w-0 text-sm text-[#5b6770]">
        <p className="truncate">Last success {formatDate(operation.lastSuccessAt)}</p>
        {operation.lastError ? <p className="mt-1 truncate text-danger">{operation.lastError}</p> : null}
      </div>
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 truncate text-sm font-semibold">{value}</p>
    </div>
  );
}

function KeyValue({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="grid gap-2 rounded-md border border-line bg-[#f8fafb] p-3 sm:grid-cols-[150px_1fr]">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <div className="min-w-0 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function toneForStatus(status: string): "positive" | "warning" | "danger" | "neutral" {
  if (status === "ok" || status === "succeeded") {
    return "positive";
  }
  if (
    status === "warning" ||
    status === "missing" ||
    status === "not_configured" ||
    status === "stale" ||
    status === "running"
  ) {
    return "warning";
  }
  if (status === "degraded" || status === "error" || status === "failed") {
    return "danger";
  }
  return "neutral";
}

function formatAge(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined) {
    return "-";
  }
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h`;
  }
  return `${Math.floor(hours / 24)}d`;
}

function formatDecimal(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(numberValue(value));
}
