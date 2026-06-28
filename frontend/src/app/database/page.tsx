import {
  Activity,
  AlertTriangle,
  BarChart3,
  Database,
  Gauge,
  HardDrive,
  ListChecks,
  RadioTower,
  WalletCards,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { DatabaseFillCompactPanel } from "@/components/DatabaseFillCompactPanel";
import { DatabaseIgnoredFillCleanupPanel } from "@/components/DatabaseIgnoredFillCleanupPanel";
import { DatabaseFillRetentionPanel } from "@/components/DatabaseFillRetentionPanel";
import { DatabasePrunePanel } from "@/components/DatabasePrunePanel";
import { HeaderRefreshButton, HeaderUpdatedLabel } from "@/components/HeaderRefresh";
import { PageTopPanel } from "@/components/PageTopPanel";
import { StatusPill } from "@/components/StatusPill";
import { getDatabaseStats, getHealth } from "@/lib/api";
import {
  formatBytes,
  formatCompact,
  formatCurrency,
  formatDate,
  formatInteger,
  formatMs,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type { DatabaseIndexStats, DatabaseTableStats } from "@/types/database";

export default async function DatabasePage() {
  const [health, stats] = await Promise.all([getHealth(), getDatabaseStats()]);

  if (!stats) {
    return (
      <>
        <PageTitle />
        <section className="rounded-lg border border-[#efb1aa] bg-[#fff5f3] p-6 text-danger">
          Could not reach database stats API.
        </section>
      </>
    );
  }

  const connectionUsage = numberValue(stats.connections.usagePct ?? 0);
  const fillDominance =
    stats.databaseSizeBytes > 0 && stats.tables.length > 0
      ? stats.tables[0].totalSizeBytes / stats.databaseSizeBytes
      : 0;
  const fillCountLabel = stats.fills.exact ? "Stored fills" : "Estimated fills";
  const snapshotFillValue = stats.fills.exact
    ? formatInteger(stats.fills.snapshot)
    : "Exact scan skipped";
  const realtimeFillValue = stats.fills.exact
    ? formatInteger(stats.fills.realtime)
    : "Exact scan skipped";

  return (
    <>
      <PageTitle updatedAt={stats.measuredAt} />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          icon={HardDrive}
          label="Database size"
          value={stats.databaseSizePretty}
          detail={`${formatInteger(stats.tableCount)} tables`}
        />
        <MetricTile
          icon={Database}
          label={fillCountLabel}
          value={formatCompact(stats.fills.total)}
          detail={`${formatInteger(stats.fills.poolWalletCount)} pool, ${formatInteger(
            stats.fills.orphanWalletCount,
          )} orphan`}
          tone={stats.fills.orphanWalletCount > 0 ? "warning" : "neutral"}
        />
        <MetricTile
          icon={Gauge}
          label="Connections"
          value={`${formatInteger(stats.connections.total)}/${formatInteger(
            stats.connections.maxConnections,
          )}`}
          detail={`${formatPercent(stats.connections.usagePct)} used`}
          tone={connectionUsage > 0.75 ? "warning" : "neutral"}
        />
        <MetricTile
          icon={WalletCards}
          label="Wallet coverage"
          value={`${formatInteger(stats.scores.scoredWallets)}/${formatInteger(
            stats.wallets.total,
          )}`}
          detail={`${formatInteger(stats.wallets.unpolled)} unpolled`}
          tone={stats.wallets.unpolled > 0 ? "warning" : "positive"}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel icon={BarChart3} title="Data Volume">
          <div className="grid gap-3 md:grid-cols-3">
            <DataPoint label="Notional" value={formatCurrency(stats.fills.totalNotionalUsd)} />
            <DataPoint label="PnL" value={formatCurrency(stats.fills.totalPnlUsd)} />
            <DataPoint label="Fees" value={formatCurrency(stats.fills.totalFeeUsd)} />
            <DataPoint label="Snapshot fills" value={snapshotFillValue} />
            <DataPoint label="Realtime fills" value={realtimeFillValue} />
            <DataPoint label="Last inserted" value={formatDate(stats.fills.lastInsertedAt)} />
          </div>
          <div className="mt-4 border-t border-line pt-4">
            <ProgressLine
              label={`Largest table: ${stats.tables[0]?.name ?? "-"}`}
              value={fillDominance}
              rightLabel={formatBytes(stats.tables[0]?.totalSizeBytes)}
            />
          </div>
        </Panel>

        <Panel icon={Activity} title="Runtime Health">
          <div className="grid gap-3 sm:grid-cols-2">
            <HealthRow label="API" value={health?.status ?? "unknown"} />
            <HealthRow label="Postgres" value={health?.dependencies.postgres.status ?? "unknown"} />
            <HealthRow label="Redis" value={health?.dependencies.redis.status ?? "unknown"} />
            <HealthRow label="Network" value={health?.hyperliquidNetwork ?? "unknown"} />
          </div>
          <div className="mt-4 grid gap-2 text-sm text-[#5b6770]">
            <p>Measured {formatDate(stats.measuredAt)}</p>
            <p>Database {stats.databaseName}</p>
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-3">
        <Panel icon={WalletCards} title="Wallet Pool">
          <KeyValueGrid
            items={[
              ["Total", formatInteger(stats.wallets.total)],
              ["Enabled", formatInteger(stats.wallets.enabled)],
              ["Eligible", formatInteger(stats.wallets.eligible)],
              ["Copy enabled", formatInteger(stats.wallets.copyEnabled)],
              ["Stale 24h", formatInteger(stats.wallets.stale24h)],
              ["Last poll", formatDate(stats.wallets.lastPolledAt)],
            ]}
          />
          <DictionaryBars title="Polling tiers" values={stats.wallets.tiers} />
        </Panel>

        <Panel icon={ListChecks} title="Scores">
          <KeyValueGrid
            items={[
              ["Scored wallets", formatInteger(stats.scores.scoredWallets)],
              ["Average", formatScore(stats.scores.averageScore)],
              ["Best", formatScore(stats.scores.bestScore)],
              ["Score >= 70", formatInteger(stats.scores.above70)],
              ["Score <= 0", formatInteger(stats.scores.zeroOrNegative)],
              ["Last scored", formatDate(stats.scores.lastScoredAt)],
            ]}
          />
        </Panel>

        <Panel icon={RadioTower} title="Copy Pipeline">
          <KeyValueGrid
            items={[
              ["Copy trades", formatInteger(stats.copyTrades.total)],
              ["Open trades", formatInteger(stats.copyTrades.open)],
              ["Closed trades", formatInteger(stats.copyTrades.closed)],
              ["Trade PnL", formatCurrency(stats.copyTrades.totalPnlUsd)],
              ["Signals", formatInteger(stats.signals.total)],
              ["Source links", formatInteger(stats.operational.sourceTradeLinks)],
            ]}
          />
          <DictionaryBars title="Copy trade status" values={stats.copyTrades.statuses} />
        </Panel>
      </section>

      <DatabasePrunePanel />

      <DatabaseFillRetentionPanel />

      <DatabaseIgnoredFillCleanupPanel />

      <DatabaseFillCompactPanel />

      <Panel icon={Database} title="Table Storage">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] border-collapse text-left text-sm">
            <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
              <tr>
                <th className="px-4 py-3 font-semibold">Table</th>
                <th className="px-4 py-3 font-semibold">Rows est.</th>
                <th className="px-4 py-3 font-semibold">Dead rows</th>
                <th className="px-4 py-3 font-semibold">Total size</th>
                <th className="px-4 py-3 font-semibold">Table</th>
                <th className="px-4 py-3 font-semibold">Indexes</th>
                <th className="px-4 py-3 font-semibold">Scans</th>
                <th className="px-4 py-3 font-semibold">Analyze</th>
                <th className="px-4 py-3 font-semibold">Vacuum</th>
              </tr>
            </thead>
            <tbody>
              {stats.tables.map((table) => (
                <TableRow key={table.name} table={table} />
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel icon={HardDrive} title="Index Storage">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-left text-sm">
            <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
              <tr>
                <th className="px-4 py-3 font-semibold">Index</th>
                <th className="px-4 py-3 font-semibold">Table</th>
                <th className="px-4 py-3 font-semibold">Size</th>
                <th className="px-4 py-3 font-semibold">Scans</th>
                <th className="px-4 py-3 font-semibold">Tuples read</th>
                <th className="px-4 py-3 font-semibold">Tuples fetched</th>
                <th className="px-4 py-3 font-semibold">Flags</th>
              </tr>
            </thead>
            <tbody>
              {stats.indexes.slice(0, 30).map((index) => (
                <IndexRow key={`${index.tableName}:${index.indexName}`} index={index} />
              ))}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="grid gap-4 xl:grid-cols-2">
        <Panel icon={AlertTriangle} title="Operational Tables">
          <KeyValueGrid
            items={[
              ["Active copy wallets", formatInteger(stats.operational.activeCopyWallets)],
              ["Realtime slots used", formatInteger(stats.operational.realtimeSlotsUsed)],
              ["Risk events", formatInteger(stats.operational.riskEvents)],
              ["Audit logs", formatInteger(stats.operational.auditLogs)],
              ["Settings", formatInteger(stats.operational.settings)],
              ["Last fill", formatMs(stats.fills.lastFillTimeMs)],
            ]}
          />
          <DictionaryBars
            title="Active copy status"
            values={stats.operational.activeCopyStatuses}
          />
        </Panel>

        <Panel icon={ListChecks} title="Signal Decisions">
          <KeyValueGrid
            items={[
              ["Copy", formatInteger(stats.signals.copy)],
              ["Skip", formatInteger(stats.signals.skip)],
              ["Exit", formatInteger(stats.signals.exit)],
              ["Observe", formatInteger(stats.signals.observe)],
              ["Last signal", formatDate(stats.signals.lastCreatedAt)],
              ["Modes", Object.keys(stats.copyTrades.modes).join(", ") || "-"],
            ]}
          />
        </Panel>
      </section>
    </>
  );
}

function PageTitle({ updatedAt }: { updatedAt?: string }) {
  return (
    <PageTopPanel
      eyebrow="Database monitor"
      title="Database"
      actions={
        updatedAt ? (
          <HeaderUpdatedLabel label={`Updated ${formatDate(updatedAt)}`} />
        ) : null
      }
      refresh={<HeaderRefreshButton title="Refresh database data" />}
    />
  );
}

function MetricTile({
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: "positive" | "warning" | "neutral";
  value: string;
}) {
  const toneClass =
    tone === "positive"
      ? "border-[#9ccfc0] bg-[#f2fbf7]"
      : tone === "warning"
        ? "border-[#e7c174] bg-[#fff9e8]"
        : "border-line bg-panel";

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

function DataPoint({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 break-words text-lg font-semibold leading-snug">{value}</p>
    </div>
  );
}

function HealthRow({ label, value }: { label: string; value: string }) {
  const tone = value === "ok" || value === "mainnet" ? "positive" : "warning";
  return (
    <div className="flex items-center justify-between gap-3 rounded-md border border-line bg-[#f8fafb] px-3 py-2">
      <span className="text-sm font-medium">{label}</span>
      <StatusPill label={value} tone={tone} />
    </div>
  );
}

function KeyValueGrid({ items }: { items: [string, string][] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {items.map(([label, value]) => (
        <div key={label}>
          <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
          <p className="mt-1 truncate text-sm font-semibold">{value}</p>
        </div>
      ))}
    </div>
  );
}

function DictionaryBars({ title, values }: { title: string; values: Record<string, number> }) {
  const entries = Object.entries(values);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);

  return (
    <div className="mt-4 border-t border-line pt-4">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{title}</p>
      <div className="mt-3 grid gap-3">
        {entries.length === 0 ? (
          <p className="text-sm text-[#5b6770]">No rows.</p>
        ) : (
          entries.map(([key, value]) => (
            <ProgressLine
              key={key}
              label={key}
              rightLabel={formatInteger(value)}
              value={total > 0 ? value / total : 0}
            />
          ))
        )}
      </div>
    </div>
  );
}

function ProgressLine({
  label,
  rightLabel,
  value,
}: {
  label: string;
  rightLabel: string;
  value: number;
}) {
  const width = Math.max(0, Math.min(100, value * 100));
  return (
    <div>
      <div className="flex items-center justify-between gap-3 text-sm">
        <span className="truncate font-medium">{label}</span>
        <span className="shrink-0 font-mono text-[#5b6770]">{rightLabel}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-[#e3e9ee]">
        <div className="h-full rounded-full bg-[#297c73]" style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function TableRow({ table }: { table: DatabaseTableStats }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-semibold">{table.name}</td>
      <td className="px-4 py-3 font-mono">{formatInteger(table.estimatedRows)}</td>
      <td className="px-4 py-3 font-mono">{formatInteger(table.deadRows)}</td>
      <td className="px-4 py-3 font-mono">{formatBytes(table.totalSizeBytes)}</td>
      <td className="px-4 py-3 font-mono">{formatBytes(table.tableSizeBytes)}</td>
      <td className="px-4 py-3 font-mono">{formatBytes(table.indexSizeBytes)}</td>
      <td className="px-4 py-3 text-[#5b6770]">
        {formatInteger(table.seqScanCount)} seq / {formatInteger(table.indexScanCount)} idx
      </td>
      <td className="px-4 py-3 text-[#5b6770]">
        {formatDate(table.lastAutoanalyzeAt ?? table.lastAnalyzeAt)}
      </td>
      <td className="px-4 py-3 text-[#5b6770]">
        {formatDate(table.lastAutovacuumAt ?? table.lastVacuumAt)}
      </td>
    </tr>
  );
}

function IndexRow({ index }: { index: DatabaseIndexStats }) {
  const flags = [
    index.isPrimary ? "primary" : null,
    index.isUnique ? "unique" : null,
    index.indexScanCount === 0 ? "unused" : null,
  ].filter(Boolean);

  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-semibold">{index.indexName}</td>
      <td className="px-4 py-3 font-mono">{index.tableName}</td>
      <td className="px-4 py-3 font-mono">{formatBytes(index.indexSizeBytes)}</td>
      <td className="px-4 py-3 font-mono">{formatInteger(index.indexScanCount)}</td>
      <td className="px-4 py-3 font-mono">{formatInteger(index.tuplesRead)}</td>
      <td className="px-4 py-3 font-mono">{formatInteger(index.tuplesFetched)}</td>
      <td className="px-4 py-3 text-[#5b6770]">{flags.join(", ") || "-"}</td>
    </tr>
  );
}
