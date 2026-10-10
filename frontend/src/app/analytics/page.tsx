import type { Metadata } from "next";
import Link from "next/link";

import { RefreshControl, TimeAgo } from "@/components/ui/client";
import {
  Callout,
  EmptyState,
  KeyValues,
  Meter,
  PageHeader,
  Panel,
  Pnl,
  PnlPercent,
  scoreTone,
  Stat,
  StatStrip,
  StatusDot,
  Usd,
} from "@/components/ui/primitives";
import { toneText } from "@/components/ui/tone";
import { getAnalytics } from "@/lib/api";
import { cx } from "@/lib/cx";
import {
  decimal,
  formatInteger,
  formatNumber,
  formatPercent,
  formatScore,
  formatUsd,
  humanize,
  shortAddress,
} from "@/lib/format";
import type { AnalyticsBucket, AnalyticsResponse, AnalyticsWalletRow } from "@/types/analytics";

export const metadata: Metadata = { title: "Analytics" };
export const dynamic = "force-dynamic";

export default async function AnalyticsPage() {
  const analytics = await getAnalytics();

  return (
    <>
      <PageHeader
        title="Analytics"
        description="How the pool scores, which wallets look promising or risky, and how copying performs by source and coin."
        actions={<RefreshControl intervalMs={30_000} updatedAt={analytics?.freshness.generatedAt} />}
      />
      {analytics ? <AnalyticsContent analytics={analytics} /> : (
        <Callout tone="negative" title="Analytics are unavailable">
          The analytics endpoint did not respond. Check the backend on the System page.
        </Callout>
      )}
    </>
  );
}

function AnalyticsContent({ analytics }: { analytics: AnalyticsResponse }) {
  const { freshness, overview } = analytics;
  const averages: Array<[string, string | null]> = [
    ["Profitability", analytics.scoreAverages.profitabilityScore],
    ["Consistency", analytics.scoreAverages.consistencyScore],
    ["Risk control", analytics.scoreAverages.riskScore],
    ["Copyability", analytics.scoreAverages.copyabilityScore],
    ["Recency", analytics.scoreAverages.recencyScore],
  ];
  return (
    <>
      <StatStrip className="lg:grid-cols-6">
        <Stat
          label="Scoring coverage"
          value={formatPercent(overview.scoringCoveragePct)}
          sub={`${formatInteger(overview.scoredWalletCount)} of ${formatInteger(overview.enabledWalletCount)} enabled`}
        />
        <Stat label="Average score" tone={scoreTone(overview.averageScore)} value={formatScore(overview.averageScore)} sub={`penalty ${formatScore(analytics.scoreAverages.penaltyScore)} on average`} />
        <Stat
          label="Followed sources"
          value={formatInteger(overview.activeSourceCount)}
          sub={`${formatInteger(overview.openPaperSourceCount)} with open paper positions`}
        />
        <Stat
          label="Paper realized PnL"
          value={<Pnl value={overview.paperRealizedPnlUsd} />}
          sub={`fees ${formatUsd(overview.paperFeeUsd)}`}
        />
        <Stat
          label="Open paper margin"
          value={<Usd value={overview.paperOpenMarginUsd} />}
          sub={`${formatInteger(overview.openPaperPositionCount)} open positions`}
        />
        <Stat
          label="Paper skip rate"
          hint="Share of source trades the paper accounts did not copy"
          tone={decimal(overview.paperSkipRatePct) > 0.5 ? "warning" : "neutral"}
          value={formatPercent(overview.paperSkipRatePct)}
          sub="see skip reasons below"
        />
      </StatStrip>

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Score distribution" meta={`${formatInteger(overview.scoredWalletCount)} wallets`} bodyClassName="px-3.5 py-3">
          <Buckets buckets={analytics.scoreBuckets} tone="score" />
        </Panel>
        <Panel title="Average component scores" bodyClassName="px-3.5 py-3">
          <ul className="grid gap-2.5">
            {averages.map(([label, value]) => (
              <li key={label} className="grid grid-cols-[110px_minmax(0,1fr)_40px] items-center gap-3 text-sm">
                <span className="text-fg-2">{label}</span>
                <Meter value={decimal(value) / 100} tone={scoreTone(value)} />
                <span className={cx("text-right tabular-nums", toneText[scoreTone(value)])}>{formatScore(value)}</span>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Data freshness" bodyClassName="px-3.5 py-2">
          <KeyValues
            items={[
              { label: "Latest stored fill", value: <TimeAgo value={freshness.latestWalletFillAt} /> },
              { label: "Latest scoring", value: <TimeAgo value={freshness.latestScoringAt} /> },
              { label: "Latest position snapshot", value: <TimeAgo value={freshness.latestPositionSnapshotAt} /> },
              {
                hint: "Enabled wallets without a recent poll",
                label: "Stale enabled wallets",
                tone: freshness.staleEnabledWalletCount > 0 ? "warning" : "neutral",
                value: formatInteger(freshness.staleEnabledWalletCount),
              },
              {
                hint: "Wallets whose current drawdown could not be read",
                label: "Drawdown unavailable",
                tone: freshness.currentDrawdownUnavailableCount > 0 ? "warning" : "neutral",
                value: formatInteger(freshness.currentDrawdownUnavailableCount),
              },
            ]}
          />
          <p className="mt-3 text-xs font-medium text-fg-3">Current drawdown state</p>
          <div className="mt-1.5">
            <Buckets buckets={analytics.drawdownStatusBuckets} tone="neutral" humanizeLabels />
          </div>
        </Panel>
      </div>

      <div className="grid gap-4 2xl:grid-cols-2">
        <Panel title="Most promising wallets" description="Highest scores among wallets with enough history.">
          <WalletTable rows={analytics.opportunityWallets} variant="opportunity" />
        </Panel>
        <Panel title="Risk watchlist" description="Wallets in a drawdown or under margin stress right now.">
          <WalletTable rows={analytics.riskWatchlist} variant="risk" />
        </Panel>
      </div>

      <div className="grid gap-4 2xl:grid-cols-2">
        <Panel title="Source wallets, last 30 days" description="From the wallets' own closed trades.">
          {analytics.sourcePerformance.length === 0 ? (
            <EmptyState title="No closed source trades in the last 30 days" />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Wallet</th>
                    <th className="num">Score</th>
                    <th className="num">Trades</th>
                    <th className="num">Win rate</th>
                    <th className="num">Net PnL</th>
                    <th className="num" title="Net PnL divided by entry volume">
                      ROI on volume
                    </th>
                    <th className="num">Avg hold</th>
                    <th className="num">Last close</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.sourcePerformance.map((row) => (
                    <tr key={row.sourceWallet}>
                      <td>
                        <WalletLink address={row.sourceWallet} label={row.sourceLabel} />
                        <span className="sub">pool #{row.poolRank ?? "-"}</span>
                      </td>
                      <td className={cx("num", toneText[scoreTone(row.score)])}>{formatScore(row.score)}</td>
                      <td className="num">{formatInteger(row.closedTradeCount)}</td>
                      <td className="num text-fg-2">{formatPercent(row.winRate, { decimals: 0 })}</td>
                      <td className="num">
                        <Pnl value={row.netPnlUsd} compact />
                      </td>
                      <td className="num">
                        <PnlPercent value={row.roiPct} />
                      </td>
                      <td className="num text-fg-2">{hours(row.averageDurationHours)}</td>
                      <td className="num text-fg-3">
                        <TimeAgo value={row.lastClosedAt} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Coins, last 30 days" description="All source wallets' closed trades grouped by coin.">
          {analytics.coinPerformance.length === 0 ? (
            <EmptyState title="No coin performance yet" />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Coin</th>
                    <th className="num">Trades</th>
                    <th className="num">Win rate</th>
                    <th className="num">Net PnL</th>
                    <th className="num">ROI on volume</th>
                    <th className="num">Volume</th>
                    <th className="num">Avg hold</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.coinPerformance.map((row) => (
                    <tr key={row.coin}>
                      <td className="font-medium">{row.coin}</td>
                      <td className="num">{formatInteger(row.closedTradeCount)}</td>
                      <td className="num text-fg-2">{formatPercent(row.winRate, { decimals: 0 })}</td>
                      <td className="num">
                        <Pnl value={row.netPnlUsd} compact />
                      </td>
                      <td className="num">
                        <PnlPercent value={row.roiPct} />
                      </td>
                      <td className="num text-fg-2">
                        <Usd value={row.entryNotionalUsd} compact />
                      </td>
                      <td className="num text-fg-2">{hours(row.averageDurationHours)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title="Paper copying by source" description="What the paper accounts got out of each source.">
          {analytics.paperSources.length === 0 ? (
            <EmptyState title="No paper copy activity yet" />
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Source wallet</th>
                    <th className="num">Realized</th>
                    <th className="num">Fees</th>
                    <th className="num">Copied</th>
                    <th className="num">Skip rate</th>
                    <th className="num">Open</th>
                    <th className="num">Last fill</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.paperSources.map((row) => (
                    <tr key={row.sourceWallet}>
                      <td>
                        <WalletLink address={row.sourceWallet} label={row.sourceLabel} />
                      </td>
                      <td className="num">
                        <Pnl value={row.realizedPnlUsd} />
                      </td>
                      <td className="num text-fg-2">
                        <Usd value={row.feeUsd} />
                      </td>
                      <td className="num">{formatInteger(row.copiedFillCount)}</td>
                      <td className={cx("num", decimal(row.skipRatePct) > 0.5 ? "text-warning" : "text-fg-2")}>
                        {formatPercent(row.skipRatePct, { decimals: 0 })}
                      </td>
                      <td className="num">
                        {formatInteger(row.openPositionCount)}
                        {row.openPositionCount > 0 ? (
                          <span className="sub">
                            <Usd value={row.openMarginUsd} /> margin
                          </span>
                        ) : null}
                      </td>
                      <td className="num text-fg-3">
                        <TimeAgo value={row.lastFillAt} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Why paper trades are skipped" bodyClassName="px-3.5 py-3">
          {analytics.skipReasons.length === 0 ? (
            <p className="text-sm text-fg-3">No skipped trades.</p>
          ) : (
            <ul className="grid gap-2.5">
              {analytics.skipReasons.map((row) => (
                <li key={row.reason}>
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="truncate">{humanize(row.reason)}</span>
                    <span className="shrink-0 tabular-nums text-fg-2">
                      {formatInteger(row.count)}
                      <span className="ml-2 text-xs text-fg-4">{formatPercent(row.pct, { decimals: 0 })}</span>
                    </span>
                  </div>
                  <Meter className="mt-1" value={decimal(row.pct)} tone="neutral" />
                  <p className="mt-0.5 text-2xs text-fg-4">
                    last <TimeAgo value={row.lastSeenAt} />
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <p className="text-xs text-fg-3">
        The discovery funnel per source is on the <Link href="/discovery" className="underline">Discovery</Link> page.
      </p>
    </>
  );
}

function Buckets({
  buckets,
  humanizeLabels = false,
  tone,
}: {
  buckets: AnalyticsBucket[];
  humanizeLabels?: boolean;
  tone: "score" | "neutral";
}) {
  const max = Math.max(...buckets.map((bucket) => bucket.count), 1);
  return (
    <ul className="grid gap-1.5">
      {buckets.map((bucket) => (
        <li key={bucket.label} className="grid grid-cols-[84px_minmax(0,1fr)_44px] items-center gap-3 text-sm">
          <span className="truncate text-fg-2">{humanizeLabels ? humanize(bucket.label) : bucket.label}</span>
          <Meter
            value={bucket.count / max}
            tone={tone === "score" ? scoreTone(bucketScore(bucket.label)) : "neutral"}
          />
          <span className="text-right tabular-nums text-fg-2">{formatInteger(bucket.count)}</span>
        </li>
      ))}
    </ul>
  );
}

function bucketScore(label: string) {
  const parsed = Number.parseInt(label, 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function WalletTable({ rows, variant }: { rows: AnalyticsWalletRow[]; variant: "opportunity" | "risk" }) {
  if (rows.length === 0) {
    return <EmptyState title="No wallets match" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Wallet</th>
            <th className="num">Score</th>
            <th className="num">Trades</th>
            {variant === "opportunity" ? (
              <>
                <th className="num">Win rate</th>
                <th className="num">PF</th>
                <th className="num" title="The wallet's own realized PnL in the scoring window">
                  Realized PnL
                </th>
              </>
            ) : (
              <>
                <th className="num">Current DD</th>
                <th className="num" title="Margin used relative to the account">
                  Margin stress
                </th>
                <th>Drawdown state</th>
              </>
            )}
            <th className="num">Last trade</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.walletAddress}>
              <td>
                <WalletLink address={row.walletAddress} label={row.label} />
                <span className="sub">pool #{row.poolRank ?? "-"}</span>
              </td>
              <td className={cx("num", toneText[scoreTone(row.score)])}>{formatScore(row.score)}</td>
              <td className="num">{formatInteger(row.tradeCount)}</td>
              {variant === "opportunity" ? (
                <>
                  <td className="num text-fg-2">{formatPercent(row.winRate, { decimals: 0 })}</td>
                  <td className="num text-fg-2">{formatNumber(row.profitFactor, 2)}</td>
                  <td className="num">
                    <Pnl value={row.copyablePnlUsd} compact />
                  </td>
                </>
              ) : (
                <>
                  <td className={cx("num", decimal(row.currentDrawdownPct) > 0.1 ? "text-negative" : "text-fg-2")}>
                    {formatPercent(row.currentDrawdownPct)}
                  </td>
                  <td className={cx("num", decimal(row.marginStressPct) > 0.5 ? "text-warning" : "text-fg-2")}>
                    {formatPercent(row.marginStressPct)}
                  </td>
                  <td>
                    <StatusDot
                      tone={row.currentDrawdownStatus === "ok" ? "neutral" : "warning"}
                      label={humanize(row.currentDrawdownStatus)}
                    />
                  </td>
                </>
              )}
              <td className="num text-fg-3">
                <TimeAgo value={row.lastSeenFillAt} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WalletLink({ address, label }: { address: string; label: string | null }) {
  return (
    <Link href={`/wallets/${address}`} className="block max-w-[220px] truncate font-medium hover:underline" title={address}>
      {label?.trim() || shortAddress(address)}
    </Link>
  );
}

function hours(value: string | null) {
  if (value === null) {
    return "-";
  }
  const parsed = decimal(value);
  return parsed >= 48 ? `${formatNumber(parsed / 24, 1)} d` : `${formatNumber(parsed, 1)} h`;
}
