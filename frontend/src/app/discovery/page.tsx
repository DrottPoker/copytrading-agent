import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Compass,
  DatabaseZap,
  Filter,
  ListChecks,
  Search,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { DiscoveryPipelineActions } from "@/components/DiscoveryPipelineActions";
import { HeaderRefreshButton, HeaderUpdatedLabel } from "@/components/HeaderRefresh";
import { PageTopPanel } from "@/components/PageTopPanel";
import { StatusPill } from "@/components/StatusPill";
import {
  getDiscoveryCandidates,
  getDiscoveryRuns,
  getDiscoverySources,
  getOperationStatuses,
} from "@/lib/api";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatMs,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type {
  DiscoveryCandidate,
  DiscoveryImportRun,
  DiscoverySource,
} from "@/types/discovery";

type DiscoveryPageProps = {
  searchParams?: Promise<{
    q?: string | string[];
    source?: string | string[];
    status?: string | string[];
  }>;
};

export default async function DiscoveryPage({ searchParams }: DiscoveryPageProps) {
  const params = await searchParams;
  const query = searchParamValue(params?.q);
  const selectedSource = searchParamValue(params?.source);
  const selectedStatus = searchParamValue(params?.status);
  const hasFilters = Boolean(query || selectedSource || selectedStatus);
  const overviewCandidatesPromise = getDiscoveryCandidates({ limit: 500 });
  const filteredCandidatesPromise = hasFilters
    ? getDiscoveryCandidates({
        limit: 500,
        query,
        source: selectedSource,
        status: selectedStatus,
      })
    : overviewCandidatesPromise;

  const [sources, overviewCandidates, runs, operations, filteredCandidates] = await Promise.all([
    getDiscoverySources(),
    overviewCandidatesPromise,
    getDiscoveryRuns(20),
    getOperationStatuses(),
    filteredCandidatesPromise,
  ]);

  const overview = buildOverview(overviewCandidates.items);
  const displayed = filteredCandidates.items;
  const lastRun = runs.items[0] ?? null;
  const discoveryOperation =
    operations.items.find((operation) => operation.key === "discovery_import") ?? null;

  return (
    <>
      <PageTopPanel
        eyebrow="Trader sourcing"
        title="Discovery"
        actions={
          <>
            <HeaderUpdatedLabel
              label={lastRun ? `Updated ${formatDate(lastRun.startedAt)}` : "No discovery run yet"}
            />
            <StatusPill label={`${formatInteger(overviewCandidates.total)} candidates`} />
            <StatusPill label={`${formatInteger(overview.accepted)} accepted`} tone="positive" />
            <StatusPill
              label={`${formatInteger(overview.promoted)} in pool`}
              tone={overview.promoted > 0 ? "positive" : "neutral"}
            />
          </>
        }
        refresh={<HeaderRefreshButton title="Refresh discovery data" />}
      />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricTile
          icon={Compass}
          label="Candidates"
          value={formatInteger(overviewCandidates.total)}
          detail={`${formatInteger(overviewCandidates.items.length)} loaded`}
        />
        <MetricTile
          icon={Filter}
          label="Prefilter"
          value={`${formatInteger(overview.accepted)}/${formatInteger(overview.rejected)}`}
          detail="accepted / rejected"
          tone={overview.accepted > 0 ? "positive" : "neutral"}
        />
        <MetricTile
          icon={DatabaseZap}
          label="Backfilled"
          value={formatInteger(overview.backfilled)}
          detail={`${formatInteger(overview.backfillFailed)} failed`}
          tone={overview.backfillFailed > 0 ? "warning" : "neutral"}
        />
        <MetricTile
          icon={CheckCircle2}
          label="Added to pool"
          value={formatInteger(overview.promoted)}
          detail="Approved by discovery backfill"
          tone={overview.promoted > 0 ? "positive" : "neutral"}
        />
        <MetricTile
          icon={Clock3}
          label="Last import"
          value={lastRun ? formatDate(lastRun.startedAt) : "-"}
          detail={lastRun ? `${lastRun.source} ${lastRun.status}` : "No runs yet"}
          tone={lastRun?.status === "failed" ? "warning" : "neutral"}
        />
      </section>

      <DiscoveryPipelineActions initialOperation={discoveryOperation} sources={sources.items} />

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Panel icon={ListChecks} title="Source Coverage">
          <div className="grid gap-3">
            {sources.items.length === 0 ? (
              <EmptyState text="No discovery sources returned by API." />
            ) : (
              sources.items.map((source) => (
                <SourceRow
                  key={source.key}
                  count={overview.sourceCounts[source.key] ?? 0}
                  source={source}
                />
              ))
            )}
          </div>
        </Panel>

        <Panel icon={AlertTriangle} title="Reject Reasons">
          <DictionaryBars values={overview.rejectReasons} emptyText="No rejected candidates loaded." />
        </Panel>
      </section>

      <Panel icon={Clock3} title="Recent Import Runs">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left text-sm">
            <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
              <tr>
                <th className="px-4 py-3 font-semibold">Source</th>
                <th className="px-4 py-3 font-semibold">Status</th>
                <th className="px-4 py-3 font-semibold">Fetched</th>
                <th className="px-4 py-3 font-semibold">Inserted / updated</th>
                <th className="px-4 py-3 font-semibold">Started</th>
                <th className="px-4 py-3 font-semibold">Finished</th>
              </tr>
            </thead>
            <tbody>
              {runs.items.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-4 py-10 text-center text-[#5b6770]">
                    No import runs yet.
                  </td>
                </tr>
              ) : (
                runs.items.map((run) => <RunRow key={run.id} run={run} />)
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
        <div className="flex flex-col gap-3 border-b border-line px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <h2 className="text-base font-semibold">Candidate Pipeline</h2>
            <p className="mt-1 text-sm text-[#5b6770]">
              Showing {formatInteger(displayed.length)} of {formatInteger(filteredCandidates.total)}
              {hasFilters ? " matching" : ""} candidates.
            </p>
          </div>
          <CandidateFilters
            query={query}
            selectedSource={selectedSource}
            selectedStatus={selectedStatus}
            sources={sources.items}
          />
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1440px] border-collapse text-left text-sm">
            <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
              <tr>
                <th className="px-4 py-3 font-semibold">Wallet</th>
                <th className="px-4 py-3 font-semibold">Source</th>
                <th className="px-4 py-3 font-semibold">Source metrics</th>
                <th className="px-4 py-3 font-semibold">Pipeline</th>
                <th className="px-4 py-3 font-semibold">Backfill</th>
                <th className="px-4 py-3 font-semibold">Quality</th>
                <th className="px-4 py-3 font-semibold">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {displayed.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-10 text-center text-[#5b6770]">
                    {hasFilters ? "No discovery candidates match this filter." : "No candidates yet."}
                  </td>
                </tr>
              ) : (
                displayed.map((candidate) => (
                  <CandidateRow key={candidate.id} candidate={candidate} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function CandidateFilters({
  query,
  selectedSource,
  selectedStatus,
  sources,
}: {
  query: string;
  selectedSource: string;
  selectedStatus: string;
  sources: DiscoverySource[];
}) {
  const hasFilters = Boolean(query || selectedSource || selectedStatus);

  return (
    <form action="/discovery" className="flex flex-col gap-2 xl:flex-row xl:items-center">
      <label className="relative block min-w-[260px]">
        <span className="sr-only">Search wallet or label</span>
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#5b6770]"
          aria-hidden="true"
        />
        <input
          type="search"
          name="q"
          defaultValue={query}
          placeholder="Search wallet or label"
          className="h-10 w-full rounded-md border border-line bg-white pl-9 pr-3 text-sm font-medium text-ink outline-none transition focus:border-[#297c73] focus:ring-2 focus:ring-[#d9ece8]"
        />
      </label>
      <select
        name="source"
        defaultValue={selectedSource}
        className="h-10 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink outline-none transition focus:border-[#297c73] focus:ring-2 focus:ring-[#d9ece8]"
      >
        <option value="">All sources</option>
        {sources.map((source) => (
          <option key={source.key} value={source.key}>
            {source.label}
          </option>
        ))}
      </select>
      <select
        name="status"
        defaultValue={selectedStatus}
        className="h-10 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink outline-none transition focus:border-[#297c73] focus:ring-2 focus:ring-[#d9ece8]"
      >
        <option value="">All statuses</option>
        <option value="discovered">Discovered</option>
        <option value="accepted">Accepted</option>
        <option value="rejected">Rejected</option>
        <option value="promoted">Promoted</option>
        <option value="ignored">Ignored</option>
      </select>
      <div className="flex shrink-0 gap-2">
        <button
          type="submit"
          className="inline-flex h-10 items-center gap-2 rounded-md border border-line bg-ink px-3 text-sm font-medium text-white"
        >
          <Search className="h-4 w-4" aria-hidden="true" />
          Filter
        </button>
        {hasFilters ? (
          <Link
            href="/discovery"
            className="inline-flex h-10 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink"
          >
            <X className="h-4 w-4" aria-hidden="true" />
            Clear
          </Link>
        ) : null}
      </div>
    </form>
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
          <p className="mt-2 truncate text-2xl font-semibold text-ink">{value}</p>
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

function SourceRow({
  count,
  source,
}: {
  count: number;
  source: DiscoverySource;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-md border border-line bg-[#f8fafb] p-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-semibold">{source.label}</p>
          <StatusPill
            label={source.enabled ? "enabled" : "disabled"}
            tone={source.enabled ? "positive" : "neutral"}
          />
          <StatusPill
            label={source.configured ? "configured" : "needs config"}
            tone={source.configured ? "positive" : "warning"}
          />
        </div>
        <p className="mt-2 font-mono text-xs text-[#5b6770]">{source.key}</p>
        {source.notes ? <p className="mt-2 text-sm text-[#5b6770]">{source.notes}</p> : null}
      </div>
      <div className="shrink-0 text-left sm:text-right">
        <p className="text-xl font-semibold">{formatInteger(count)}</p>
        <p className="text-xs uppercase text-[#5b6770]">loaded</p>
      </div>
    </div>
  );
}

function RunRow({ run }: { run: DiscoveryImportRun }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-semibold">{run.source}</td>
      <td className="px-4 py-3">
        <StatusPill label={run.status} tone={run.status === "succeeded" ? "positive" : "warning"} />
      </td>
      <td className="px-4 py-3 font-mono">{formatInteger(run.fetchedCount)}</td>
      <td className="px-4 py-3 text-[#5b6770]">
        {formatInteger(run.insertedCount)} / {formatInteger(run.updatedCount)}
      </td>
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(run.startedAt)}</td>
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(run.finishedAt)}</td>
    </tr>
  );
}

function CandidateRow({ candidate }: { candidate: DiscoveryCandidate }) {
  const canOpenWallet = candidate.status === "promoted";

  return (
    <tr className="border-b border-line last:border-b-0 hover:bg-[#fbfcfd]">
      <td className="px-4 py-3 align-top">
        <div className="flex min-w-0 flex-col gap-1">
          {canOpenWallet ? (
            <Link
              href={`/wallets/${candidate.walletAddress}`}
              className="min-w-0 max-w-full whitespace-normal break-words font-semibold hover:text-[#297c73]"
            >
              {candidate.sourceLabel || candidate.subaccountName || shortAddress(candidate.walletAddress)}
            </Link>
          ) : (
            <p className="min-w-0 max-w-full whitespace-normal break-words font-semibold">
              {candidate.sourceLabel || candidate.subaccountName || shortAddress(candidate.walletAddress)}
            </p>
          )}
          <p className="font-mono text-xs text-[#5b6770]">
            {shortAddress(candidate.walletAddress)}
          </p>
          <p className="text-xs text-[#5b6770]">{candidate.accountRole}</p>
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <div className="grid gap-1">
          <span className="font-semibold">{candidate.source}</span>
          <span className="text-xs text-[#5b6770]">
            {candidate.sourceRank ? `Rank #${candidate.sourceRank}` : "No rank"}
          </span>
          {candidate.sourceCohort ? (
            <span className="text-xs text-[#5b6770]">{candidate.sourceCohort}</span>
          ) : null}
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <div className="grid gap-1">
          <MetricLine
            label="PnL"
            value={formatCurrency(candidate.sourcePnlUsd ?? candidate.sourcePnl)}
          />
          <MetricLine
            label="ROI"
            value={formatSourceRoi(candidate.sourceRoiPct ?? candidate.sourceRoi)}
          />
          <MetricLine
            label="Equity"
            value={formatCurrency(candidate.sourceAccountValueUsd ?? candidate.accountValue)}
          />
          <MetricLine label="Copy score" value={formatScore(candidate.sourceCopyScore)} />
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <div className="flex max-w-[260px] flex-wrap gap-2">
          <StatusPill label={candidate.status} tone={candidateStatusTone(candidate.status)} />
          {candidate.failReason ? (
            <StatusPill label={reasonLabel(candidate.failReason)} tone="warning" />
          ) : null}
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <div className="grid gap-2">
          <StatusPill
            label={candidate.backfillStatus}
            tone={backfillStatusTone(candidate.backfillStatus)}
          />
          <div className="grid gap-1 text-xs text-[#5b6770]">
            <span>
              {formatInteger(candidate.backfillInsertedCount)} new /{" "}
              {formatInteger(candidate.backfillDuplicateCount)} dup
            </span>
            <span>{formatDate(candidate.lastBackfilledAt)}</span>
          </div>
          {candidate.backfillError ? (
            <p className="max-w-[280px] truncate text-xs text-danger">{candidate.backfillError}</p>
          ) : null}
        </div>
      </td>
      <td className="px-4 py-3 align-top">
        <div className="grid gap-1">
          <MetricLine
            label="Trades"
            value={`${formatInteger(candidate.closedTradeCount)} closed / ${formatInteger(
              candidate.openTradeCount,
            )} open`}
          />
          <MetricLine label="Fills" value={formatInteger(candidate.fillCount)} />
          <MetricLine label="Net PnL" value={formatCurrency(candidate.netPnlUsd)} />
          <MetricLine label="Profit factor" value={formatScore(candidate.profitFactor)} />
          <MetricLine label="Win rate" value={formatPercent(candidate.winRate)} />
          <MetricLine label="Max DD" value={formatPercent(candidate.maxDrawdownPct)} />
          <MetricLine label="Avg notional" value={formatCurrency(candidate.averageTradeNotionalUsd)} />
          <MetricLine label="Last trade" value={formatMs(candidate.lastTradeTimeMs)} />
        </div>
      </td>
      <td className="px-4 py-3 align-top text-[#5b6770]">
        <div className="grid gap-1">
          <span>{formatDate(candidate.lastSeenAt)}</span>
          <span className="text-xs">First {formatDate(candidate.firstSeenAt)}</span>
        </div>
      </td>
    </tr>
  );
}

function MetricLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-[#5b6770]">{label}</span>
      <span className="min-w-0 truncate text-right font-mono text-xs font-semibold">{value}</span>
    </div>
  );
}

function DictionaryBars({
  emptyText,
  values,
}: {
  emptyText: string;
  values: Record<string, number>;
}) {
  const entries = Object.entries(values).sort((left, right) => right[1] - left[1]);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);

  if (entries.length === 0) {
    return <EmptyState text={emptyText} />;
  }

  return (
    <div className="grid gap-3">
      {entries.map(([key, value]) => (
        <div key={key}>
          <div className="flex items-center justify-between gap-3 text-sm">
            <span className="truncate font-medium">{reasonLabel(key)}</span>
            <span className="shrink-0 font-mono text-[#5b6770]">{formatInteger(value)}</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-[#e3e9ee]">
            <div
              className="h-full rounded-full bg-[#297c73]"
              style={{ width: `${Math.max(0, Math.min(100, (value / total) * 100))}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-10 text-center text-sm text-[#5b6770]">{text}</div>;
}

function buildOverview(candidates: DiscoveryCandidate[]) {
  const statusCounts = countBy(candidates, (candidate) => candidate.status);
  const rejectReasons = countBy(
    candidates.filter((candidate) => candidate.failReason),
    (candidate) => candidate.failReason ?? "unknown",
  );
  const sourceCounts = countBy(candidates, (candidate) => candidate.source);

  return {
    accepted: (statusCounts.accepted ?? 0) + (statusCounts.promoted ?? 0),
    backfillFailed: candidates.filter((candidate) => candidate.backfillStatus === "failed").length,
    backfilled: candidates.filter((candidate) => candidate.backfillStatus === "succeeded").length,
    promoted: statusCounts.promoted ?? 0,
    rejected: statusCounts.rejected ?? 0,
    rejectReasons,
    sourceCounts,
  };
}

function countBy<T>(items: T[], keyFn: (item: T) => string) {
  return items.reduce<Record<string, number>>((accumulator, item) => {
    const key = keyFn(item);
    accumulator[key] = (accumulator[key] ?? 0) + 1;
    return accumulator;
  }, {});
}

function candidateStatusTone(status: string) {
  if (status === "accepted" || status === "promoted") {
    return "positive";
  }
  if (status === "rejected") {
    return "warning";
  }
  return "neutral";
}

function backfillStatusTone(status: string) {
  if (status === "succeeded") {
    return "positive";
  }
  if (status === "failed") {
    return "warning";
  }
  if (status === "running") {
    return "neutral";
  }
  return "neutral";
}

function formatSourceRoi(value: string | null) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(numberValue(value))}%`;
}

function reasonLabel(value: string) {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function searchParamValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return (value[0] ?? "").trim();
  }
  return (value ?? "").trim();
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
