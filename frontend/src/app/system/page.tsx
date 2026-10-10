import type { Metadata } from "next";
import Link from "next/link";

import { JobsPanel } from "@/components/JobsPanel";
import { MaintenanceTasks } from "@/components/system/MaintenanceTasks";
import { RefreshControl, TimeAgo } from "@/components/ui/client";
import {
  Badge,
  Callout,
  EmptyState,
  KeyValues,
  Meter,
  PageHeader,
  Panel,
  Pnl,
  SectionLabel,
  Stat,
  StatStrip,
  StatusDot,
} from "@/components/ui/primitives";
import { statusTone, type Tone } from "@/components/ui/tone";
import { getDatabaseStats, getOperationStatuses, getOpsHealth } from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  formatBytes,
  formatCompact,
  formatDateTime,
  formatInteger,
  formatNumber,
  formatPercent,
  formatScore,
  formatSeconds,
  formatUsd,
  humanize,
  shortAddress,
} from "@/lib/format";
import type { DatabaseStatsResponse } from "@/types/database";
import type { OpsHealthResponse, OpsWorkerHeartbeat } from "@/types/ops";

export const metadata: Metadata = { title: "System" };
export const dynamic = "force-dynamic";

type Tab = "health" | "database" | "maintenance";

const TABS: Array<{ description: string; id: Tab; label: string }> = [
  { description: "Workers, services, jobs, and backups", id: "health", label: "Health" },
  { description: "Storage, tables, and indexes", id: "database", label: "Database" },
  { description: "Cleanup and pruning tools", id: "maintenance", label: "Maintenance" },
];

export default async function SystemPage({ searchParams }: { searchParams?: Promise<{ tab?: string | string[] }> }) {
  const params = await searchParams;
  const requested = Array.isArray(params?.tab) ? params?.tab[0] : params?.tab;
  const tab: Tab = requested === "database" || requested === "maintenance" ? requested : "health";

  return (
    <>
      <SystemHeader tab={tab} />
      <nav aria-label="System sections" className="flex gap-4 border-b border-line">
        {TABS.map((item) => (
          <Link
            key={item.id}
            href={item.id === "health" ? "/system" : `/system?tab=${item.id}`}
            aria-current={tab === item.id ? "page" : undefined}
            title={item.description}
            className={cx(
              "-mb-px inline-flex h-9 items-center border-b-2 text-sm",
              tab === item.id ? "border-fg font-medium text-fg" : "border-transparent text-fg-3 hover:text-fg",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>
      {tab === "health" ? <HealthTab /> : tab === "database" ? <DatabaseTab /> : <MaintenanceTasks />}
    </>
  );
}

async function SystemHeader({ tab }: { tab: Tab }) {
  return (
    <PageHeader
      title="System"
      description="The infrastructure behind the copy agent: whether the workers are alive, how the database holds up, and the tools to keep it lean."
      actions={tab === "maintenance" ? null : <RefreshControl intervalMs={15_000} updatedAt={new Date().toISOString()} />}
    />
  );
}

async function HealthTab() {
  const [ops, operations] = await Promise.all([getOpsHealth(), getOperationStatuses()]);
  if (!ops) {
    return (
      <Callout tone="negative" title="Health data is unavailable">
        The operations health endpoint did not respond. The backend API may be down.
      </Callout>
    );
  }
  return <HealthContent ops={ops} operations={operations.items} />;
}

function HealthContent({ ops, operations }: { ops: OpsHealthResponse; operations: OpsHealthResponse["operations"] }) {
  const overallTone = statusTone(ops.status);
  return (
    <>
      <StatStrip className="lg:grid-cols-6">
        <Stat
          label="Overall"
          tone={overallTone}
          value={humanize(ops.status)}
          sub={<>measured <TimeAgo value={ops.measuredAt} /></>}
        />
        <Stat
          label={`Disk ${ops.disk.path}`}
          tone={statusTone(ops.disk.status)}
          value={formatPercent(ops.disk.usagePct)}
          sub={`${formatBytes(ops.disk.freeBytes)} free`}
        />
        <Stat
          label="Memory"
          tone={statusTone(ops.memory.status)}
          value={formatPercent(ops.memory.usagePct)}
          sub={ops.memory.availableBytes === null ? "not reported" : `${formatBytes(ops.memory.availableBytes)} available`}
        />
        <Stat
          label="Load"
          tone={statusTone(ops.load.status)}
          value={ops.load.load1m === null ? "-" : formatNumber(ops.load.load1m, 2)}
          sub={ops.load.cpuCount ? `${formatInteger(ops.load.cpuCount)} CPUs, 5m ${formatNumber(ops.load.load5m, 2)}` : "not reported"}
        />
        <Stat
          label="Backups"
          tone={ops.backup.status === "disabled" ? "neutral" : statusTone(ops.backup.status)}
          value={
            ops.backup.status === "disabled"
              ? "Off"
              : ops.backup.latestAgeSeconds === null
                ? "None"
                : `${formatSeconds(ops.backup.latestAgeSeconds)} old`
          }
          sub={`${formatInteger(ops.backup.backupCount)} files, ${formatBytes(ops.backup.totalSizeBytes)}`}
        />
        <Stat
          label="Version"
          value={`v${ops.version}`}
          sub={`${ops.config.environment}, ${ops.config.hyperliquidNetwork}`}
        />
      </StatStrip>

      {ops.notes.map((note) => (
        <Callout key={note} tone="warning" title={note} />
      ))}

      <Panel title="Workers" meta={`${formatInteger(ops.workers.length)} running processes`}>
        {ops.workers.length === 0 ? (
          <EmptyState title="No worker heartbeats">Start the trading and maintenance workers.</EmptyState>
        ) : (
          <div className="divide-y divide-line">
            {ops.workers.map((worker) => (
              <WorkerBlock key={worker.key} worker={worker} />
            ))}
          </div>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <JobsPanel initialItems={operations} />
        <Panel title="Services and configuration" bodyClassName="px-3.5 py-2.5">
          <ul className="grid gap-1.5">
            {Object.entries(ops.dependencies).map(([name, dependency]) => (
              <li key={name} className="flex items-center justify-between gap-3 text-sm">
                <StatusDot tone={statusTone(dependency.status)} label={humanize(name)} />
                <span className="truncate text-xs text-fg-3" title={dependency.detail ?? undefined}>
                  {dependency.detail ?? humanize(dependency.status)}
                </span>
              </li>
            ))}
          </ul>
          <SectionLabel className="mt-3">Configuration</SectionLabel>
          <KeyValues
            items={[
              { label: "Environment", value: ops.config.environment },
              { label: "Mode", value: humanize(ops.config.mode) },
              { label: "Hyperliquid network", value: ops.config.hyperliquidNetwork },
              { label: "Paper trading", value: ops.config.paperTradingEnabled ? "On" : "Off" },
              {
                label: "Live trading",
                tone: ops.config.liveTradingEnabled ? "negative" : "neutral",
                value: ops.config.liveTradingEnabled ? "On" : "Off",
              },
              { label: "Workers inside the API process", value: ops.config.workerRunInApiProcess ? "Yes" : "No" },
              {
                label: "Realtime wallets",
                value: `${formatInteger(ops.config.activeCopyWallets)} of ${formatInteger(ops.config.maxRealtimeWallets)}`,
              },
            ]}
          />
        </Panel>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Backups" bodyClassName="px-3.5 py-2.5">
          <KeyValues
            items={[
              {
                label: "Status",
                tone: ops.backup.status === "ok" ? "positive" : ops.backup.status === "disabled" ? "neutral" : "warning",
                value: humanize(ops.backup.status),
              },
              { label: "Latest file", value: <span className="font-mono text-xs">{ops.backup.latestFile ?? "-"}</span> },
              { label: "Latest backup", value: formatDateTime(ops.backup.latestModifiedAt) },
              { label: "Latest size", value: formatBytes(ops.backup.latestSizeBytes) },
              { label: "Stale after", value: formatSeconds(ops.backup.staleAfterSeconds) },
              { label: "Directory", value: <span className="font-mono text-xs">{ops.backup.directory}</span> },
            ]}
          />
          <p className="mt-2 text-xs text-fg-3">{ops.backup.note}</p>
        </Panel>
        <Panel title="Discovery sources" meta="last import per source">
          {(ops.discoverySources ?? []).length === 0 ? (
            <EmptyState title="No discovery runs recorded" />
          ) : (
            <div className="max-h-80 overflow-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Status</th>
                    <th className="num">Wallets</th>
                    <th className="num">Last success</th>
                  </tr>
                </thead>
                <tbody>
                  {(ops.discoverySources ?? []).map((source) => (
                    <tr key={source.source}>
                      <td className="font-mono text-xs">{source.source}</td>
                      <td>
                        <StatusDot
                          tone={source.consecutiveFailures > 0 ? "negative" : statusTone(source.status)}
                          label={
                            source.consecutiveFailures > 0
                              ? `${formatInteger(source.consecutiveFailures)} failed in a row`
                              : humanize(source.status)
                          }
                          title={source.lastError ?? undefined}
                        />
                      </td>
                      <td className="num">{formatInteger(source.lastFetchedCount)}</td>
                      <td className="num text-fg-3">
                        <TimeAgo value={source.lastSuccessAt} fallback="never" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      {(ops.liveSigners ?? []).length > 0 ? (
        <Panel title="Live signing keys" description="Live trading only accepts a Hyperliquid API wallet, which can trade but not withdraw.">
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Signer</th>
                  <th>Key</th>
                  <th>Details</th>
                  <th className="num">Checked</th>
                </tr>
              </thead>
              <tbody>
                {(ops.liveSigners ?? []).map((signer) => {
                  const tone: Tone =
                    signer.status === "api_wallet"
                      ? "positive"
                      : signer.status === "unchecked" || signer.status === "unavailable"
                        ? "warning"
                        : signer.required
                          ? "negative"
                          : "warning";
                  return (
                    <tr key={signer.accountKey}>
                      <td>
                        <span className="font-mono text-xs">{signer.accountKey}</span>
                        <span className="sub">
                          {humanize(signer.accountStatus)}
                          {signer.required ? ", API wallet required" : ""}
                        </span>
                      </td>
                      <td>
                        <StatusDot tone={tone} label={humanize(signer.status)} />
                      </td>
                      <td className="font-mono text-xs text-fg-2">
                        {signer.signerAddress ? `${shortAddress(signer.signerAddress)} for ${shortAddress(signer.masterAddress)}` : "Not configured"}
                      </td>
                      <td className="wrap max-w-[26.25rem] text-xs text-fg-2">
                        {signer.description}
                        {signer.error ? <span className="block text-negative">Last attempt failed: {signer.error}</span> : null}
                      </td>
                      <td className="num text-fg-3">
                        <TimeAgo value={signer.checkedAt} fallback="never" />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </>
  );
}

function WorkerBlock({ worker }: { worker: OpsWorkerHeartbeat }) {
  const capabilities =
    worker.capabilities.length > 0
      ? worker.capabilities
      : [worker.tradingLoops ? "trading" : null, worker.maintenanceLoops ? "maintenance" : null].filter(
          (value): value is string => value !== null,
        );
  return (
    <div className="px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <StatusDot tone={statusTone(worker.status)} pulse={worker.status === "ok"} label={<span className="text-sm font-medium">{humanize(worker.role)} worker</span>} />
        <span className="text-xs text-fg-3">
          heartbeat {worker.ageSeconds === null ? "never" : `${formatSeconds(worker.ageSeconds)} ago`}, started{" "}
          <TimeAgo value={worker.startedAt} />
        </span>
        <span className="font-mono text-xs text-fg-3">
          {worker.hostname ?? "-"}
          {worker.pid ? ` pid ${worker.pid}` : ""}
        </span>
        <span className="flex flex-wrap gap-1">
          {capabilities.map((capability) => (
            <Badge key={capability}>{capability}</Badge>
          ))}
        </span>
      </div>
      {worker.loops.length > 0 ? (
        <div className="mt-2 overflow-x-auto rounded-md border border-line">
          <table className="data-table data-table-compact">
            <thead>
              <tr>
                <th>Loop</th>
                <th>State</th>
                <th className="num">Last progress</th>
                <th className="num">Restarts</th>
                <th className="num">Failures in a row</th>
                <th>Last error</th>
              </tr>
            </thead>
            <tbody>
              {worker.loops.map((loop) => (
                <tr key={loop.name}>
                  <td className="font-mono text-xs">{loop.name}</td>
                  <td>
                    <StatusDot tone={statusTone(loop.health)} label={humanize(loop.status)} />
                  </td>
                  <td className="num text-fg-2">
                    <TimeAgo value={loop.lastProgressAt} />
                  </td>
                  <td className="num">{formatInteger(loop.restartCount)}</td>
                  <td className={cx("num", loop.consecutiveFailures > 0 && "text-negative")}>
                    {formatInteger(loop.consecutiveFailures)}
                  </td>
                  <td className="wrap max-w-[22.5rem] text-xs text-negative">{loop.lastError ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {worker.realtimeQueue ? (
        <div className="mt-2 flex items-center gap-3 text-xs text-fg-3">
          <span className="w-28">Realtime queue</span>
          <Meter
            className="w-32"
            value={worker.realtimeQueue.capacity > 0 ? worker.realtimeQueue.depth / worker.realtimeQueue.capacity : 0}
            tone={statusTone(worker.realtimeQueue.status)}
          />
          <span className="tabular-nums">
            {formatInteger(worker.realtimeQueue.depth)} of {formatInteger(worker.realtimeQueue.capacity)} queued
          </span>
          <span className={cx("tabular-nums", worker.realtimeQueue.dropped > 0 && "text-negative")}>
            {formatInteger(worker.realtimeQueue.dropped)} dropped
          </span>
        </div>
      ) : null}
    </div>
  );
}

async function DatabaseTab() {
  const stats = await getDatabaseStats();
  if (!stats) {
    return (
      <Callout tone="negative" title="Database statistics are unavailable">
        The database stats endpoint did not respond.
      </Callout>
    );
  }
  return <DatabaseContent stats={stats} />;
}

function DatabaseContent({ stats }: { stats: DatabaseStatsResponse }) {
  const largest = stats.tables[0];
  return (
    <>
      <StatStrip className="lg:grid-cols-6">
        <Stat label="Database size" value={stats.databaseSizePretty} sub={`${formatInteger(stats.tableCount)} tables in ${stats.databaseName}`} />
        <Stat
          label={stats.fills.exact ? "Stored fills" : "Stored fills, estimated"}
          value={formatCompact(stats.fills.total)}
          sub={`${formatInteger(stats.fills.walletCount)} wallets, ${formatInteger(stats.fills.orphanWalletCount)} orphaned`}
        />
        <Stat
          label="Connections"
          tone={(Number(stats.connections.usagePct) || 0) > 0.75 ? "warning" : "neutral"}
          value={`${formatInteger(stats.connections.total)} of ${formatInteger(stats.connections.maxConnections)}`}
          sub={`${formatInteger(stats.connections.active)} active, ${formatInteger(stats.connections.idleInTransaction)} idle in transaction`}
        />
        <Stat
          label="Largest table"
          value={largest ? formatBytes(largest.totalSizeBytes) : "-"}
          sub={largest ? `${largest.name}, ${formatPercent(largest.totalSizeBytes / Math.max(stats.databaseSizeBytes, 1), { decimals: 0 })} of the database` : "-"}
        />
        <Stat label="Copy trades" value={formatInteger(stats.copyTrades.total)} sub={`${formatInteger(stats.copyTrades.open)} open`} />
        <Stat label="Signals" value={formatInteger(stats.signals.total)} sub={<>latest <TimeAgo value={stats.signals.lastCreatedAt} /></>} />
      </StatStrip>

      <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-4">
        <Panel title="Fill history" bodyClassName="px-3.5 py-2.5">
          <KeyValues
            items={[
              { label: "Volume", value: formatUsd(stats.fills.totalNotionalUsd, { compact: true }) },
              { label: "Fees", value: formatUsd(stats.fills.totalFeeUsd, { compact: true }) },
              { label: "Fill PnL", value: <Pnl value={stats.fills.totalPnlUsd} compact /> },
              { label: "From snapshots", value: stats.fills.exact ? formatInteger(stats.fills.snapshot) : "not counted" },
              { label: "From the realtime feed", value: stats.fills.exact ? formatInteger(stats.fills.realtime) : "not counted" },
              { label: "Coins", value: formatInteger(stats.fills.coinCount) },
              { label: "Latest insert", value: <TimeAgo value={stats.fills.lastInsertedAt} /> },
            ]}
          />
        </Panel>
        <Panel title="Wallet pool" bodyClassName="px-3.5 py-2.5">
          <KeyValues
            items={[
              { label: "Wallets", value: formatInteger(stats.wallets.total) },
              { label: "Enabled", value: formatInteger(stats.wallets.enabled) },
              { label: "Eligible", value: formatInteger(stats.wallets.eligible) },
              { label: "Copy enabled", value: formatInteger(stats.wallets.copyEnabled) },
              { label: "Not polled in 24h", value: formatInteger(stats.wallets.stale24h) },
              { label: "Never polled", value: formatInteger(stats.wallets.unpolled) },
              { label: "Latest poll", value: <TimeAgo value={stats.wallets.lastPolledAt} /> },
            ]}
          />
          <Distribution title="Polling tiers" values={stats.wallets.tiers} />
        </Panel>
        <Panel title="Scores" bodyClassName="px-3.5 py-2.5">
          <KeyValues
            items={[
              { label: "Scored wallets", value: formatInteger(stats.scores.scoredWallets) },
              { label: "Average", value: formatScore(stats.scores.averageScore) },
              { label: "Best", value: formatScore(stats.scores.bestScore) },
              { label: "Score 70 or more", value: formatInteger(stats.scores.above70) },
              { label: "Zero or negative", value: formatInteger(stats.scores.zeroOrNegative) },
              { label: "Latest scoring", value: <TimeAgo value={stats.scores.lastScoredAt} /> },
            ]}
          />
        </Panel>
        <Panel title="Copy pipeline" bodyClassName="px-3.5 py-2.5">
          <KeyValues
            items={[
              { label: "Copy trades closed", value: formatInteger(stats.copyTrades.closed) },
              { label: "Copy trade errors", tone: stats.copyTrades.error > 0 ? "warning" : "neutral", value: formatInteger(stats.copyTrades.error) },
              { label: "Copy trade PnL", value: <Pnl value={stats.copyTrades.totalPnlUsd} /> },
              { label: "Signals: copy, skip, exit", value: `${formatInteger(stats.signals.copy)}, ${formatInteger(stats.signals.skip)}, ${formatInteger(stats.signals.exit)}` },
              { label: "Source trade links", value: formatInteger(stats.operational.sourceTradeLinks) },
              { label: "Risk events", value: formatInteger(stats.operational.riskEvents) },
              { label: "Audit log entries", value: formatInteger(stats.operational.auditLogs) },
            ]}
          />
          <Distribution title="Active copy status" values={stats.operational.activeCopyStatuses} />
        </Panel>
      </div>

      <Panel title="Tables" meta={`${formatInteger(stats.tables.length)} tables, largest first`}>
        <div className="max-h-[32.5rem] overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Table</th>
                <th>Share of size</th>
                <th className="num">Total</th>
                <th className="num">Data</th>
                <th className="num">Indexes</th>
                <th className="num">Rows, estimated</th>
                <th className="num">Dead rows</th>
                <th className="num" title="Sequential scans and index scans">
                  Scans
                </th>
                <th className="num">Analyzed</th>
                <th className="num">Vacuumed</th>
              </tr>
            </thead>
            <tbody>
              {stats.tables.map((table) => (
                <tr key={table.name}>
                  <td className="font-mono text-xs">{table.name}</td>
                  <td className="w-32">
                    <Meter value={table.totalSizeBytes / Math.max(stats.databaseSizeBytes, 1)} tone="neutral" />
                  </td>
                  <td className="num">{formatBytes(table.totalSizeBytes)}</td>
                  <td className="num text-fg-2">{formatBytes(table.tableSizeBytes)}</td>
                  <td className="num text-fg-2">{formatBytes(table.indexSizeBytes)}</td>
                  <td className="num">{formatInteger(table.estimatedRows)}</td>
                  <td className={cx("num", table.deadRows > table.estimatedRows * 0.2 && table.deadRows > 1000 ? "text-warning" : "text-fg-2")}>
                    {formatInteger(table.deadRows)}
                  </td>
                  <td className="num text-fg-2">
                    {formatCompact(table.seqScanCount)} / {formatCompact(table.indexScanCount)}
                  </td>
                  <td className="num text-fg-3">
                    <TimeAgo value={table.lastAutoanalyzeAt ?? table.lastAnalyzeAt} fallback="never" />
                  </td>
                  <td className="num text-fg-3">
                    <TimeAgo value={table.lastAutovacuumAt ?? table.lastVacuumAt} fallback="never" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Indexes" meta="30 largest">
        <div className="max-h-[32.5rem] overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Index</th>
                <th>Table</th>
                <th className="num">Size</th>
                <th className="num">Scans</th>
                <th className="num">Tuples read</th>
                <th className="num">Tuples fetched</th>
                <th>Kind</th>
              </tr>
            </thead>
            <tbody>
              {stats.indexes.slice(0, 30).map((index) => (
                <tr key={`${index.tableName}:${index.indexName}`}>
                  <td className="font-mono text-xs">{index.indexName}</td>
                  <td className="font-mono text-xs text-fg-2">{index.tableName}</td>
                  <td className="num">{formatBytes(index.indexSizeBytes)}</td>
                  <td className="num">{formatCompact(index.indexScanCount)}</td>
                  <td className="num text-fg-2">{formatCompact(index.tuplesRead)}</td>
                  <td className="num text-fg-2">{formatCompact(index.tuplesFetched)}</td>
                  <td>
                    <span className="flex gap-1">
                      {index.isPrimary ? <Badge>primary</Badge> : null}
                      {index.isUnique && !index.isPrimary ? <Badge>unique</Badge> : null}
                      {index.indexScanCount === 0 ? <Badge tone="warning">unused</Badge> : null}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function Distribution({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values).sort((left, right) => right[1] - left[1]);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  if (entries.length === 0) {
    return null;
  }
  return (
    <div className="mt-3">
      <SectionLabel>{title}</SectionLabel>
      <ul className="grid gap-1.5">
        {entries.map(([key, value]) => (
          <li key={key} className="grid grid-cols-[minmax(0,1fr)_5rem_2.75rem] items-center gap-2 text-xs">
            <span className="truncate text-fg-2">{humanize(key)}</span>
            <Meter value={total > 0 ? value / total : 0} tone="neutral" />
            <span className="text-right tabular-nums">{formatInteger(value)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
