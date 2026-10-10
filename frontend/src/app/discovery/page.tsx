import { Search } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { DiscoveryRunPanel } from "@/components/DiscoveryRunPanel";
import { Address, RefreshControl, TimeAgo } from "@/components/ui/client";
import {
  buttonClass,
  EmptyState,
  LinkButton,
  Meter,
  PageHeader,
  Panel,
  Pnl,
  Stat,
  StatStrip,
  StatusDot,
  Usd,
} from "@/components/ui/primitives";
import { statusTone, type Tone } from "@/components/ui/tone";
import {
  getAnalytics,
  getDiscoveryCandidates,
  getDiscoveryRuns,
  getDiscoverySources,
  getOperationStatuses,
  getOpsHealth,
} from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  formatDateTime,
  formatInteger,
  formatMultiple,
  formatPercent,
  formatPercentNumber,
  formatScore,
  formatUsd,
  humanize,
} from "@/lib/format";
import type { DiscoveryCandidate } from "@/types/discovery";

export const metadata: Metadata = { title: "Discovery" };
export const dynamic = "force-dynamic";

const PAGE_SIZE = 100;
const STATUSES = ["discovered", "accepted", "rejected", "promoted", "ignored"];

type SearchParams = {
  page?: string | string[];
  q?: string | string[];
  source?: string | string[];
  status?: string | string[];
};

export default async function DiscoveryPage({ searchParams }: { searchParams?: Promise<SearchParams> }) {
  const params = (await searchParams) ?? {};
  const query = first(params.q);
  const source = first(params.source);
  const status = first(params.status);
  const page = Math.max(0, Number.parseInt(first(params.page) || "1", 10) - 1 || 0);

  const [sources, analytics, runs, operations, ops, candidates, rejected] = await Promise.all([
    getDiscoverySources(),
    getAnalytics(),
    getDiscoveryRuns(15),
    getOperationStatuses(),
    getOpsHealth(),
    getDiscoveryCandidates({ limit: PAGE_SIZE, offset: page * PAGE_SIZE, query, source, status }),
    getDiscoveryCandidates({ limit: 500, status: "rejected" }),
  ]);

  const funnel = analytics?.discoverySources ?? [];
  const totals = funnel.reduce(
    (sum, row) => ({
      accepted: sum.accepted + row.accepted,
      backfilled: sum.backfilled + row.backfillSucceeded,
      promoted: sum.promoted + row.promoted,
      rejected: sum.rejected + row.rejected,
      total: sum.total + row.total,
    }),
    { accepted: 0, backfilled: 0, promoted: 0, rejected: 0, total: 0 },
  );
  const funnelBySource = new Map(funnel.map((row) => [row.source, row]));
  const healthBySource = new Map((ops?.discoverySources ?? []).map((row) => [row.source, row]));
  const rejectReasons = countBy(rejected.items.map((candidate) => candidate.failReason ?? "unknown"));
  const rejectTotal = rejected.items.length;
  const discoveryOperation = operations.items.find((item) => item.key === "discovery_import") ?? null;
  const enabledSources = sources.items.filter((item) => item.enabled && item.configured).length;
  const pageCount = Math.max(1, Math.ceil(candidates.total / PAGE_SIZE));
  const filtered = Boolean(query || source || status);

  return (
    <>
      <PageHeader
        title="Discovery"
        description="Finds traders on public leaderboards, drops the ones we cannot copy, imports their trade history, and admits the good ones to the wallet pool."
        actions={<RefreshControl updatedAt={new Date().toISOString()} />}
      />

      <DiscoveryRunPanel initialOperation={discoveryOperation} sources={sources.items} />

      <StatStrip className="lg:grid-cols-6">
        <Stat label="Candidates" value={formatInteger(totals.total)} sub={`from ${formatInteger(funnel.length)} sources`} />
        <Stat
          label="Passed the prefilter"
          value={formatInteger(totals.accepted + totals.promoted)}
          sub={totals.total > 0 ? `${formatPercent((totals.accepted + totals.promoted) / totals.total)} of candidates` : "-"}
        />
        <Stat label="Rejected" value={formatInteger(totals.rejected)} sub="see the reasons below" />
        <Stat
          label="History imported"
          hint="Candidates whose trade history was downloaded to check their quality"
          value={formatInteger(totals.backfilled)}
        />
        <Stat
          label="Admitted to the pool"
          tone={totals.promoted > 0 ? "positive" : "neutral"}
          value={formatInteger(totals.promoted)}
        />
        <Stat
          label="Sources in use"
          value={`${formatInteger(enabledSources)} of ${formatInteger(sources.items.length)}`}
          sub="enabled and configured"
        />
      </StatStrip>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(20rem,1fr)]">
        <Panel title="Sources" meta="where candidates come from and how many make it through">
          {sources.items.length === 0 ? (
            <EmptyState title="No discovery sources configured" />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Health</th>
                    <th className="num">Candidates</th>
                    <th className="num">Passed</th>
                    <th className="num">Admitted</th>
                    <th className="num" title="Average ROI reported by the source">
                      Avg ROI
                    </th>
                    <th className="num">Avg account</th>
                    <th className="num">Last success</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.items.map((item) => {
                    const row = funnelBySource.get(item.key);
                    const health = healthBySource.get(item.key);
                    const passed = row ? row.accepted + row.promoted : 0;
                    return (
                      <tr key={item.key}>
                        <td>
                          <Link
                            href={`/discovery?source=${encodeURIComponent(item.key)}`}
                            className="font-medium hover:underline"
                            title={item.notes ?? undefined}
                          >
                            {item.label}
                          </Link>
                          <span className="sub font-mono">{item.key}</span>
                        </td>
                        <td>
                          {!item.enabled ? (
                            <StatusDot tone="neutral" label="Disabled" />
                          ) : !item.configured ? (
                            <StatusDot tone="warning" label="Needs configuration" />
                          ) : health ? (
                            <StatusDot
                              tone={health.consecutiveFailures > 0 ? "negative" : statusTone(health.status)}
                              label={
                                health.consecutiveFailures > 0
                                  ? `${formatInteger(health.consecutiveFailures)} failed runs`
                                  : humanize(health.status)
                              }
                              title={health.lastError ?? undefined}
                            />
                          ) : (
                            <StatusDot tone="neutral" label="Not run yet" />
                          )}
                        </td>
                        <td className="num">{formatInteger(row?.total ?? 0)}</td>
                        <td className="num">
                          {formatInteger(passed)}
                          <span className="sub">{row && row.total > 0 ? formatPercent(passed / row.total, { decimals: 0 }) : "-"}</span>
                        </td>
                        <td className="num">{formatInteger(row?.promoted ?? 0)}</td>
                        <td className="num text-fg-2">{formatPercentNumber(row?.averageRoiPct)}</td>
                        <td className="num text-fg-2">{formatUsd(row?.averageAccountValueUsd, { compact: true })}</td>
                        <td className="num text-fg-3">
                          <TimeAgo value={health?.lastSuccessAt ?? row?.lastSeenAt} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Panel
          title="Why candidates are rejected"
          meta={rejectTotal > 0 ? `latest ${formatInteger(rejectTotal)} rejections` : undefined}
          bodyClassName="px-3.5 py-3"
        >
          {rejectReasons.length === 0 ? (
            <p className="text-sm text-fg-3">No rejected candidates.</p>
          ) : (
            <ul className="grid gap-2.5">
              {rejectReasons.map(([reason, value]) => (
                <li key={reason}>
                  <Link
                    href="/discovery?status=rejected"
                    className="flex items-center justify-between gap-3 text-sm hover:underline"
                  >
                    <span className="truncate">{humanize(reason)}</span>
                    <span className="shrink-0 tabular-nums text-fg-2">
                      {formatInteger(value)}
                      <span className="ml-2 text-xs text-fg-4">{formatPercent(value / rejectTotal, { decimals: 0 })}</span>
                    </span>
                  </Link>
                  <Meter className="mt-1" value={value / rejectTotal} tone="neutral" />
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <details className="group min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
        <summary className="flex min-h-10 list-none items-center justify-between gap-3 px-3.5 py-2 text-sm font-semibold hover:bg-surface-2">
          <span>
            Import runs{" "}
            <span className="font-normal text-fg-3">
              latest {formatInteger(runs.items.length)} of {formatInteger(runs.total)}
            </span>
          </span>
          <span className="text-xs font-normal text-fg-3">
            <span className="group-open:hidden">Show</span>
            <span className="hidden group-open:inline">Hide</span>
          </span>
        </summary>
        <div className="overflow-x-auto border-t border-line">
          <table className="data-table">
            <thead>
              <tr>
                <th>Started</th>
                <th>Source</th>
                <th>Status</th>
                <th className="num">Fetched</th>
                <th className="num">New</th>
                <th className="num">Updated</th>
                <th className="num">Skipped</th>
                <th>Error</th>
              </tr>
            </thead>
            <tbody>
              {runs.items.map((run) => (
                <tr key={run.id}>
                  <td className="tabular-nums text-fg-2">{formatDateTime(run.startedAt)}</td>
                  <td className="font-mono text-xs">{run.source}</td>
                  <td>
                    <StatusDot tone={statusTone(run.status)} label={humanize(run.status)} />
                  </td>
                  <td className="num">{formatInteger(run.fetchedCount)}</td>
                  <td className="num">{formatInteger(run.insertedCount)}</td>
                  <td className="num text-fg-2">{formatInteger(run.updatedCount)}</td>
                  <td className="num text-fg-2">{formatInteger(run.skippedCount)}</td>
                  <td className="wrap max-w-[22.5rem] text-xs text-negative">{run.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
        <form action="/discovery" className="flex flex-col gap-2 border-b border-line px-3.5 py-2.5 lg:flex-row lg:items-center">
          <h2 className="text-sm font-semibold lg:mr-2">Candidates</h2>
          <label className="relative block lg:w-64">
            <span className="sr-only">Search candidates</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-4" aria-hidden="true" />
            <input name="q" type="search" defaultValue={query} placeholder="Wallet or name" className="field w-full pl-8" />
          </label>
          <select name="source" defaultValue={source} className="field" aria-label="Source">
            <option value="">All sources</option>
            {sources.items.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          <select name="status" defaultValue={status} className="field" aria-label="Status">
            <option value="">All statuses</option>
            {STATUSES.map((value) => (
              <option key={value} value={value}>
                {humanize(value)}
              </option>
            ))}
          </select>
          <button type="submit" className={buttonClass({ size: "md" })}>
            Filter
          </button>
          {filtered ? (
            <LinkButton href="/discovery" variant="ghost">
              Clear
            </LinkButton>
          ) : null}
          <span className="text-xs text-fg-3 lg:ml-auto">
            {formatInteger(candidates.total)} {filtered ? "matching" : "in total"}
          </span>
        </form>
        {candidates.items.length === 0 ? (
          <EmptyState title={filtered ? "No candidates match" : "No candidates yet"}>
            {filtered ? "Try another filter." : "Run discovery to import candidates from the leaderboards."}
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Wallet</th>
                  <th>Source</th>
                  <th>Status</th>
                  <th className="num" title="Metrics reported by the leaderboard">
                    Source PnL
                  </th>
                  <th className="num">Source ROI</th>
                  <th className="num">Account</th>
                  <th title="Trade history import">History</th>
                  <th className="num" title="Closed trades in the imported history">
                    Trades
                  </th>
                  <th className="num">Net PnL</th>
                  <th className="num">Win rate</th>
                  <th className="num">PF</th>
                  <th className="num">Max DD</th>
                  <th className="num">Last trade</th>
                </tr>
              </thead>
              <tbody>
                {candidates.items.map((candidate) => (
                  <CandidateRow key={candidate.id} candidate={candidate} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {pageCount > 1 ? (
          <div className="flex items-center justify-between gap-3 border-t border-line px-3.5 py-2 text-xs text-fg-3">
            <span className="tabular-nums">
              Page {formatInteger(page + 1)} of {formatInteger(pageCount)}
            </span>
            <span className="flex gap-1.5">
              <PageLink disabled={page === 0} label="Previous" params={{ page: String(page), q: query, source, status }} />
              <PageLink
                disabled={page >= pageCount - 1}
                label="Next"
                params={{ page: String(page + 2), q: query, source, status }}
              />
            </span>
          </div>
        ) : null}
      </section>
    </>
  );
}

function CandidateRow({ candidate }: { candidate: DiscoveryCandidate }) {
  const name = candidate.sourceLabel || candidate.subaccountName;
  return (
    <tr>
      <td>
        {candidate.status === "promoted" ? (
          <Link href={`/wallets/${candidate.walletAddress}`} className="block max-w-[12.5rem] truncate font-medium hover:underline">
            {name || candidate.walletAddress}
          </Link>
        ) : name ? (
          <span className="block max-w-[12.5rem] truncate font-medium">{name}</span>
        ) : null}
        <Address address={candidate.walletAddress} />
      </td>
      <td>
        <span className="font-mono text-xs">{candidate.source}</span>
        <span className="sub">
          {candidate.sourceRank ? `rank #${candidate.sourceRank}` : "unranked"}
          {candidate.sourceCohort ? `, ${candidate.sourceCohort}` : ""}
        </span>
      </td>
      <td>
        <StatusDot tone={candidateTone(candidate.status)} label={humanize(candidate.status)} />
        {candidate.failReason ? (
          <span className="sub max-w-[12.5rem] truncate" title={humanize(candidate.failReason)}>
            {humanize(candidate.failReason)}
          </span>
        ) : null}
      </td>
      <td className="num">
        <Pnl value={candidate.sourcePnlUsd ?? candidate.sourcePnl} compact />
      </td>
      <td className="num text-fg-2">{formatPercentNumber(candidate.sourceRoiPct ?? candidate.sourceRoi)}</td>
      <td className="num">
        <Usd value={candidate.sourceAccountValueUsd ?? candidate.accountValue} compact />
      </td>
      <td>
        <StatusDot
          tone={candidate.backfillStatus === "succeeded" ? "positive" : candidate.backfillStatus === "failed" ? "negative" : "neutral"}
          label={humanize(candidate.backfillStatus)}
          title={candidate.backfillError ?? undefined}
        />
        {candidate.lastBackfilledAt ? (
          <span className="sub">
            {formatInteger(candidate.backfillInsertedCount)} fills, <TimeAgo value={candidate.lastBackfilledAt} />
          </span>
        ) : null}
      </td>
      <td className="num">
        {formatInteger(candidate.closedTradeCount)}
        {candidate.openTradeCount > 0 ? <span className="sub">{formatInteger(candidate.openTradeCount)} open</span> : null}
      </td>
      <td className="num">{candidate.netPnlUsd === null ? "-" : <Pnl value={candidate.netPnlUsd} compact />}</td>
      <td className="num text-fg-2">{formatPercent(candidate.winRate, { decimals: 0 })}</td>
      <td className="num text-fg-2">{formatMultiple(candidate.profitFactor)}</td>
      <td className="num text-fg-2">{formatPercent(candidate.maxDrawdownPct)}</td>
      <td className="num text-fg-3">
        <TimeAgo value={candidate.lastTradeTimeMs} />
        {candidate.sourceCopyScore ? (
          <span className="sub" title="Copy score reported by the source">
            source score {formatScore(candidate.sourceCopyScore)}
          </span>
        ) : null}
      </td>
    </tr>
  );
}

function PageLink({
  disabled,
  label,
  params,
}: {
  disabled: boolean;
  label: string;
  params: Record<string, string>;
}) {
  if (disabled) {
    return (
      <span className={cx(buttonClass({ size: "xs" }), "pointer-events-none opacity-50")} aria-disabled="true">
        {label}
      </span>
    );
  }
  const search = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  return (
    <LinkButton href={`/discovery?${search.toString()}`} size="xs">
      {label}
    </LinkButton>
  );
}

function candidateTone(status: string): Tone {
  if (status === "promoted") {
    return "positive";
  }
  if (status === "accepted") {
    return "info";
  }
  if (status === "rejected") {
    return "negative";
  }
  return "neutral";
}

function countBy(values: string[]) {
  const counts = new Map<string, number>();
  for (const value of values) {
    counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return Array.from(counts.entries()).sort((left, right) => right[1] - left[1]);
}

function first(value: string | string[] | undefined) {
  return ((Array.isArray(value) ? value[0] : value) ?? "").trim();
}
