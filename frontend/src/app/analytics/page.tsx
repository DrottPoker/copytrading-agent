import {
  AlertTriangle,
  BarChart3,
  Compass,
  LineChart,
  ListChecks,
  RadioTower,
  ShieldCheck,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { AutoRefresh } from "@/components/AutoRefresh";
import { StatusPill } from "@/components/StatusPill";
import { getAnalytics } from "@/lib/api";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type {
  AnalyticsBucket,
  AnalyticsCoinPerformanceRow,
  AnalyticsDiscoverySourceRow,
  AnalyticsPaperSourceRow,
  AnalyticsResponse,
  AnalyticsSkipReasonRow,
  AnalyticsSourcePerformanceRow,
  AnalyticsWalletRow,
} from "@/types/analytics";

export default async function AnalyticsPage() {
  const analytics = await getAnalytics();

  if (!analytics) {
    return (
      <>
        <PageHeader />
        <section className="rounded-lg border border-[#efb1aa] bg-[#fff5f3] p-6 text-danger">
          Could not reach analytics API.
        </section>
      </>
    );
  }

  return (
    <>
      <PageHeader analytics={analytics} />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricTile
          detail={`${formatInteger(analytics.overview.enabledWalletCount)} enabled`}
          icon={WalletCards}
          label="Scoring coverage"
          value={formatPercent(analytics.overview.scoringCoveragePct)}
        />
        <MetricTile
          detail={`${formatInteger(analytics.overview.openPaperSourceCount)} open sources`}
          icon={RadioTower}
          label="Monitored sources"
          value={formatInteger(analytics.overview.activeSourceCount)}
        />
        <MetricTile
          detail={`${formatCurrency(analytics.overview.paperFeeUsd)} fees`}
          icon={analytics.overview.paperRealizedPnlUsd.startsWith("-") ? TrendingDown : TrendingUp}
          label="Paper realized PnL"
          tone={analytics.overview.paperRealizedPnlUsd.startsWith("-") ? "danger" : "positive"}
          value={formatCurrency(analytics.overview.paperRealizedPnlUsd)}
        />
        <MetricTile
          detail={`${formatInteger(analytics.overview.openPaperPositionCount)} open positions`}
          icon={Target}
          label="Open paper margin"
          value={formatCurrency(analytics.overview.paperOpenMarginUsd)}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Panel icon={BarChart3} title="Score Distribution">
          <BucketList buckets={analytics.scoreBuckets} />
          <div className="mt-4 border-t border-line pt-3">
            <ScoreAverageGrid analytics={analytics} />
          </div>
        </Panel>

        <Panel icon={ShieldCheck} title="Drawdown State">
          <BucketList buckets={analytics.drawdownStatusBuckets} />
          <FreshnessGrid analytics={analytics} />
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
        <Panel icon={Target} title="Opportunity Wallets">
          <WalletTable rows={analytics.opportunityWallets} variant="opportunity" />
        </Panel>

        <Panel icon={AlertTriangle} title="Risk Watchlist">
          <WalletTable rows={analytics.riskWatchlist} variant="risk" />
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
        <Panel icon={TrendingUp} title="30D Source Performance">
          <SourcePerformanceTable rows={analytics.sourcePerformance} />
        </Panel>

        <Panel icon={LineChart} title="30D Coin Performance">
          <CoinPerformanceTable rows={analytics.coinPerformance} />
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
        <Panel icon={ListChecks} title="Paper Source Performance">
          <PaperSourceTable rows={analytics.paperSources} />
        </Panel>

        <Panel icon={AlertTriangle} title="Paper Skip Reasons">
          <SkipReasonTable rows={analytics.skipReasons} />
        </Panel>
      </section>

      <Panel icon={Compass} title="Discovery Funnel">
        <DiscoveryTable rows={analytics.discoverySources} />
      </Panel>
    </>
  );
}

function PageHeader({ analytics }: { analytics?: AnalyticsResponse }) {
  return (
    <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div>
        <p className="text-sm font-medium text-[#5b6770]">Pool, scoring, copy, and discovery analytics</p>
        <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-normal text-ink">
          <LineChart className="h-6 w-6 text-[#5b6770]" aria-hidden="true" />
          Analytics
        </h1>
      </div>
      {analytics ? (
        <div className="flex flex-wrap gap-2">
          <AutoRefresh intervalMs={30000} />
          <StatusPill label={`Generated ${formatDate(analytics.freshness.generatedAt)}`} />
          <StatusPill
            label={`${formatInteger(analytics.overview.scoredWalletCount)} scored`}
            tone="positive"
          />
          <StatusPill
            label={`${formatPercent(analytics.overview.paperSkipRatePct)} paper skip rate`}
            tone={numberValue(analytics.overview.paperSkipRatePct ?? 0) > 0.5 ? "warning" : "neutral"}
          />
        </div>
      ) : null}
    </header>
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
  tone?: "positive" | "danger" | "neutral";
  value: string;
}) {
  const toneClass =
    tone === "positive"
      ? "border-[#9ccfc0] bg-[#f2fbf7]"
      : tone === "danger"
        ? "border-[#efb1aa] bg-[#fff5f3]"
        : "border-line bg-panel";

  return (
    <article className={`rounded-lg border p-4 shadow-sm ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-medium uppercase text-[#5b6770]">{label}</p>
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
        <h2 className="text-base font-semibold text-ink">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function BucketList({ buckets }: { buckets: AnalyticsBucket[] }) {
  return (
    <div className="grid gap-2">
      {buckets.map((bucket) => (
        <div key={bucket.label} className="grid grid-cols-[92px_1fr_72px] items-center gap-3">
          <p className="truncate text-xs font-medium text-[#344054]">{bucket.label}</p>
          <Bar value={numberValue(bucket.pct ?? 0)} />
          <p className="text-right font-mono text-xs text-[#344054]">
            {formatInteger(bucket.count)}
          </p>
        </div>
      ))}
    </div>
  );
}

function ScoreAverageGrid({ analytics }: { analytics: AnalyticsResponse }) {
  const rows = [
    ["Final", analytics.scoreAverages.score],
    ["Profitability", analytics.scoreAverages.profitabilityScore],
    ["Consistency", analytics.scoreAverages.consistencyScore],
    ["Risk", analytics.scoreAverages.riskScore],
    ["Copyability", analytics.scoreAverages.copyabilityScore],
    ["Recency", analytics.scoreAverages.recencyScore],
    ["Penalty", analytics.scoreAverages.penaltyScore],
  ];
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {rows.map(([label, value]) => (
        <div key={label} className="rounded-md border border-line bg-[#f8fafb] px-3 py-2">
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs font-medium text-[#5b6770]">{label}</p>
            <p className="font-mono text-sm font-semibold text-ink">{formatScore(value)}</p>
          </div>
          <Bar value={clamp(numberValue(value ?? 0) / 100, 0, 1)} />
        </div>
      ))}
    </div>
  );
}

function FreshnessGrid({ analytics }: { analytics: AnalyticsResponse }) {
  return (
    <div className="mt-4 grid gap-2 border-t border-line pt-3 sm:grid-cols-2">
      <SmallMetric label="Latest fill" value={formatDate(analytics.freshness.latestWalletFillAt)} />
      <SmallMetric label="Latest scoring" value={formatDate(analytics.freshness.latestScoringAt)} />
      <SmallMetric
        label="Latest positions"
        value={formatDate(analytics.freshness.latestPositionSnapshotAt)}
      />
      <SmallMetric
        label="Stale enabled"
        value={formatInteger(analytics.freshness.staleEnabledWalletCount)}
      />
      <SmallMetric
        label="Live drawdown gaps"
        value={formatInteger(analytics.freshness.currentDrawdownUnavailableCount)}
      />
      <SmallMetric label="Average score" value={formatScore(analytics.overview.averageScore)} />
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] px-3 py-2">
      <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-1 truncate font-mono text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}

function WalletTable({
  rows,
  variant,
}: {
  rows: AnalyticsWalletRow[];
  variant: "opportunity" | "risk";
}) {
  if (rows.length === 0) {
    return <EmptyState text="No wallets matched this analysis." />;
  }
  return (
    <div className="divide-y divide-line">
      {rows.map((wallet) => (
        <div key={wallet.walletAddress} className="grid gap-3 py-3 lg:grid-cols-[1fr_180px_220px]">
          <WalletIdentity wallet={wallet} />
          <MetricStack
            items={[
              ["score", formatScore(wallet.score)],
              ["trades", formatInteger(wallet.tradeCount)],
            ]}
          />
          <MetricStack
            items={
              variant === "opportunity"
                ? [
                    ["copyable PnL", formatCurrency(wallet.copyablePnlUsd)],
                    ["win rate", formatPercent(wallet.winRate)],
                  ]
                : [
                    ["current DD", formatPercent(wallet.currentDrawdownPct)],
                    ["margin stress", formatPercent(wallet.marginStressPct)],
                  ]
            }
          />
        </div>
      ))}
    </div>
  );
}

function WalletIdentity({ wallet }: { wallet: AnalyticsWalletRow }) {
  return (
    <div className="min-w-0">
      <Link
        href={`/wallets/${wallet.walletAddress}`}
        className="block min-w-0 max-w-full whitespace-normal break-words font-mono text-sm font-semibold text-ink hover:underline"
      >
        {wallet.label ?? shortAddress(wallet.walletAddress)}
      </Link>
      <p className="mt-1 truncate text-xs text-[#5b6770]">
        pool {wallet.poolRank ? `#${formatInteger(wallet.poolRank)}` : "-"},{" "}
        {wallet.currentDrawdownStatus}, last fill {formatDate(wallet.lastSeenFillAt)}
      </p>
    </div>
  );
}

function SourcePerformanceTable({ rows }: { rows: AnalyticsSourcePerformanceRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No closed source trades in the last 30 days." />;
  }
  return (
    <div className="divide-y divide-line">
      {rows.map((row) => (
        <div key={row.sourceWallet} className="grid gap-3 py-3 lg:grid-cols-[1fr_190px_220px]">
          <SourceIdentity
            address={row.sourceWallet}
            label={row.sourceLabel}
            meta={`pool ${row.poolRank ? `#${formatInteger(row.poolRank)}` : "-"}, ${formatInteger(row.closedTradeCount)} closed`}
          />
          <MetricStack
            items={[
              ["net PnL", formatCurrency(row.netPnlUsd)],
              ["ROI", formatPercent(row.roiPct)],
            ]}
          />
          <MetricStack
            items={[
              ["win rate", formatPercent(row.winRate)],
              ["avg hold", formatHours(row.averageDurationHours)],
            ]}
          />
        </div>
      ))}
    </div>
  );
}

function CoinPerformanceTable({ rows }: { rows: AnalyticsCoinPerformanceRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No coin performance yet." />;
  }
  return (
    <div className="grid gap-2">
      {rows.map((row) => (
        <div key={row.coin} className="grid grid-cols-[110px_1fr_96px] items-center gap-3">
          <div className="min-w-0">
            <p className="truncate font-mono text-sm font-semibold text-ink">{row.coin}</p>
            <p className="truncate text-xs text-[#5b6770]">{formatInteger(row.closedTradeCount)} trades</p>
          </div>
          <Bar value={clamp(Math.abs(numberValue(row.roiPct ?? 0)), 0, 1)} />
          <div className="text-right">
            <p className={pnlClass(row.netPnlUsd)}>{formatCurrency(row.netPnlUsd)}</p>
            <p className="font-mono text-[11px] text-[#5b6770]">{formatPercent(row.winRate)}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function PaperSourceTable({ rows }: { rows: AnalyticsPaperSourceRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No paper source activity yet." />;
  }
  return (
    <div className="divide-y divide-line">
      {rows.map((row) => (
        <div key={row.sourceWallet} className="grid gap-3 py-3 lg:grid-cols-[1fr_190px_220px]">
          <SourceIdentity
            address={row.sourceWallet}
            label={row.sourceLabel}
            meta={`${formatInteger(row.openPositionCount)} open, last ${formatDate(row.lastFillAt)}`}
          />
          <MetricStack
            items={[
              ["realized", formatCurrency(row.realizedPnlUsd)],
              ["open margin", formatCurrency(row.openMarginUsd)],
            ]}
          />
          <MetricStack
            items={[
              ["fills", `${formatInteger(row.copiedFillCount)} copied`],
              ["skip rate", formatPercent(row.skipRatePct)],
            ]}
          />
        </div>
      ))}
    </div>
  );
}

function SkipReasonTable({ rows }: { rows: AnalyticsSkipReasonRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No paper skips recorded." />;
  }
  return (
    <div className="grid gap-2">
      {rows.map((row) => (
        <div key={row.reason} className="grid grid-cols-[1fr_120px] items-center gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-ink">{humanReason(row.reason)}</p>
            <p className="truncate text-xs text-[#5b6770]">last {formatDate(row.lastSeenAt)}</p>
          </div>
          <div>
            <div className="flex items-center justify-between gap-2">
              <p className="font-mono text-xs font-semibold text-ink">{formatInteger(row.count)}</p>
              <p className="font-mono text-xs text-[#5b6770]">{formatPercent(row.pct)}</p>
            </div>
            <Bar value={numberValue(row.pct ?? 0)} />
          </div>
        </div>
      ))}
    </div>
  );
}

function DiscoveryTable({ rows }: { rows: AnalyticsDiscoverySourceRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No discovery candidates yet." />;
  }
  return (
    <div className="grid gap-2 xl:grid-cols-2">
      {rows.map((row) => (
        <div key={row.source} className="rounded-md border border-line bg-[#f8fafb] p-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink">{sourceLabel(row.source)}</p>
              <p className="truncate text-xs text-[#5b6770]">last seen {formatDate(row.lastSeenAt)}</p>
            </div>
            <p className="font-mono text-sm font-semibold text-ink">{formatInteger(row.total)}</p>
          </div>
          <div className="mt-3 grid grid-cols-4 gap-2">
            <MiniStat label="accepted" value={formatInteger(row.accepted)} />
            <MiniStat label="promoted" value={formatInteger(row.promoted)} />
            <MiniStat label="rejected" value={formatInteger(row.rejected)} />
            <MiniStat label="backfilled" value={formatInteger(row.backfillSucceeded)} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <MiniStat label="avg ROI" value={formatRawPercent(row.averageRoiPct)} />
            <MiniStat label="avg TVL" value={formatCurrency(row.averageAccountValueUsd)} />
          </div>
        </div>
      ))}
    </div>
  );
}

function SourceIdentity({
  address,
  label,
  meta,
}: {
  address: string;
  label: string | null;
  meta: string;
}) {
  return (
    <div className="min-w-0">
      <Link
        href={`/wallets/${address}`}
        className="block min-w-0 max-w-full whitespace-normal break-words font-mono text-sm font-semibold text-ink hover:underline"
      >
        {label ?? shortAddress(address)}
      </Link>
      <p className="mt-1 truncate text-xs text-[#5b6770]">{meta}</p>
    </div>
  );
}

function MetricStack({ items }: { items: [string, string][] }) {
  return (
    <div className="grid grid-cols-2 gap-2 lg:grid-cols-1">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
          <p className="mt-0.5 truncate font-mono text-xs font-semibold text-ink">{value}</p>
        </div>
      ))}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-line bg-white px-2 py-1.5">
      <p className="truncate text-[10px] font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-0.5 truncate font-mono text-xs font-semibold text-ink">{value}</p>
    </div>
  );
}

function Bar({ value }: { value: number }) {
  const width = `${clamp(value, 0, 1) * 100}%`;
  return (
    <div className="h-2 overflow-hidden rounded-full bg-[#e5ebf0]">
      <div className="h-full rounded-full bg-[#097a5f]" style={{ width }} />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-[#5b6770]">{text}</div>;
}

function pnlClass(value: string | null) {
  return `font-mono text-xs font-semibold ${numberValue(value ?? 0) < 0 ? "text-danger" : "text-success"}`;
}

function shortAddress(address: string) {
  if (address.length <= 14) {
    return address;
  }
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function sourceLabel(source: string) {
  return source.replaceAll("_", " ");
}

function humanReason(reason: string) {
  return reason.replaceAll("_", " ");
}

function formatHours(value: string | null) {
  if (value === null) {
    return "-";
  }
  return `${formatScore(value)} h`;
}

function formatRawPercent(value: string | null) {
  if (value === null) {
    return "-";
  }
  return `${formatScore(value)}%`;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}
