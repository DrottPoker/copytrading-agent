"use client";

import type { ReactNode } from "react";

import { TimeAgo } from "@/components/ui/client";
import { Badge, KeyValues, Meter, Panel, Pnl, SectionLabel, StatusDot } from "@/components/ui/primitives";
import {
  decimal,
  formatBps,
  formatDateTime,
  formatInteger,
  formatMultiple,
  formatPercent,
  formatSignedUsd,
  formatUsd,
} from "@/lib/format";
import { formatCapitalMode } from "@/lib/trading/labels";
import type { EquityPoint, PerformanceAnalytics, PerformancePoint } from "@/lib/trading/performance";
import type { AccountOption, AccountView } from "@/lib/trading/types";
import type { PaperTradingPolicy } from "@/types/paper";
import type { LiveRiskLimits } from "@/types/trading";

import { PerformanceChart } from "./PerformanceChart";

export function PerformancePanel({
  analytics,
  equity,
  error,
  meta,
  points,
}: {
  analytics: PerformanceAnalytics;
  equity: EquityPoint[] | null;
  error?: string | null;
  meta: ReactNode;
  points: PerformancePoint[];
}) {
  const equityBasis = analytics.drawdownBasis === "equity";
  const closedNet: StatItem = {
    label: "Closed net",
    title: equityBasis
      ? "Net PnL of completed round trips after all their fees and funding. Open positions and their partial closes are not included."
      : undefined,
    value: <Pnl value={analytics.netPnlUsd} />,
  };
  const tradeStats: StatItem[] = [
    { label: "Gross profit", value: <Pnl value={analytics.grossProfitUsd} /> },
    { label: "Gross loss", value: <Pnl value={-analytics.grossLossUsd} /> },
    {
      label: "Profit factor",
      title: "Gross profit divided by gross loss",
      value: formatMultiple(analytics.profitFactor),
    },
  ];
  const tradeShapeStats: StatItem[] = [
    { label: "Average win", value: <Pnl value={analytics.averageWinUsd} /> },
    { label: "Average loss", value: <Pnl value={analytics.averageLossUsd} /> },
    { label: "Payoff ratio", title: "Average win divided by average loss", value: formatMultiple(analytics.payoffRatio) },
    {
      label: "Best and worst",
      value: (
        <>
          <Pnl value={analytics.bestTradeUsd} /> <span className="text-fg-4">/</span> <Pnl value={analytics.worstTradeUsd} />
        </>
      ),
    },
    {
      label: "Streak",
      title: `Longest: ${analytics.longestWinStreak} wins, ${analytics.longestLossStreak} losses`,
      value:
        analytics.currentStreak.kind === null ? (
          "-"
        ) : (
          <span className={analytics.currentStreak.kind === "win" ? "text-positive" : "text-negative"}>
            {analytics.currentStreak.count} {analytics.currentStreak.kind === "win" ? "wins" : "losses"}
          </span>
        ),
    },
  ];
  // Paper drawdown comes from equity; the expectancy is already in the stat strip under the profit factor.
  const stats: StatItem[] = equityBasis
    ? [
        closedNet,
        ...tradeStats,
        {
          label: "Max drawdown",
          title: "Largest fall of marked-to-market equity from a peak, in equity snapshots and the live mark",
          value: <DrawdownValue pct={analytics.maxDrawdownPct} usd={analytics.maxDrawdownUsd} />,
        },
        {
          label: "Current drawdown",
          title: "Current equity below the highest equity since the last reset",
          value: <DrawdownValue pct={analytics.currentDrawdownPct} usd={analytics.currentDrawdownUsd ?? 0} />,
        },
        ...tradeShapeStats,
        {
          label: "Recovery factor",
          title: "Total PnL, including open positions, divided by the max drawdown",
          value: formatMultiple(analytics.recoveryFactor),
        },
      ]
    : [
        closedNet,
        ...tradeStats,
        { label: "Expectancy", title: "Average net PnL per closed trade", value: <Pnl value={analytics.expectancyUsd} /> },
        {
          label: "Max drawdown",
          title: "Largest fall of the cumulative closed PnL from a peak",
          value: <Pnl value={-analytics.maxDrawdownUsd} />,
        },
        ...tradeShapeStats,
        {
          label: "Recovery factor",
          title: "Net closed PnL divided by the max drawdown",
          value: formatMultiple(analytics.recoveryFactor),
        },
      ];

  return (
    <Panel title="Performance" meta={meta} bodyClassName="p-3.5">
      {error ? (
        <p className="mb-2 text-xs text-warning" role="status">
          {error}
        </p>
      ) : null}
      <PerformanceChart equity={equity} trades={points} />
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2 border-t border-line pt-3 sm:grid-cols-3 lg:grid-cols-6">
        {stats.map((stat) => (
          <div key={stat.label} className="min-w-0">
            <dt className="truncate text-xs text-fg-3" title={stat.title}>
              {stat.title ? <span className="cursor-help border-b border-dotted border-fg-4">{stat.label}</span> : stat.label}
            </dt>
            <dd className="truncate text-sm font-medium tabular-nums">{stat.value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}

type StatItem = { label: string; title?: string; value: ReactNode };

/** Dollar drawdown with its share of the peak on a second line, so narrow columns do not cut it. */
function DrawdownValue({ pct, usd }: { pct: number | null; usd: number }) {
  return (
    <>
      <Pnl value={-usd} />
      <span className="block text-2xs font-normal text-fg-3">
        {pct !== null && usd > 0 ? formatPercent(-pct, { decimals: 1 }) : "at the peak"}
      </span>
    </>
  );
}

export function RiskPanel({ view }: { view: AccountView }) {
  const { marketRows, summary } = view;
  const longNotional = marketRows.reduce((total, row) => total + row.longNotionalUsd, 0);
  const shortNotional = marketRows.reduce((total, row) => total + row.shortNotionalUsd, 0);
  const gross = longNotional + shortNotional;
  const longShare = gross > 0 ? longNotional / gross : 0.5;
  const maxNotional = Math.max(...marketRows.map((row) => row.notionalUsd), 1);

  return (
    <Panel title="Risk and exposure" bodyClassName="p-3.5">
      <div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-positive">Long {formatUsd(longNotional)}</span>
          <span className="text-negative">Short {formatUsd(shortNotional)}</span>
        </div>
        <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-surface-3">
          {gross > 0 ? (
            <>
              <div className="bg-positive" style={{ width: `${longShare * 100}%` }} />
              <div className="bg-negative" style={{ width: `${(1 - longShare) * 100}%` }} />
            </>
          ) : null}
        </div>
      </div>

      <KeyValues
        className="mt-3"
        items={[
          { label: "Gross exposure", value: formatUsd(summary.openNotionalUsd) },
          {
            hint: "Open notional divided by account equity",
            label: "Exposure of equity",
            tone: summary.equityUsd > 0 && summary.openNotionalUsd / summary.equityUsd > 1 ? "warning" : "neutral",
            value: summary.equityUsd > 0 ? formatPercent(summary.openNotionalUsd / summary.equityUsd) : "-",
          },
          { label: "Margin in use", value: formatUsd(summary.openMarginUsd) },
          {
            hint: "Open notional divided by open margin",
            label: "Average leverage",
            value: summary.openMarginUsd > 0 ? formatMultiple(summary.openNotionalUsd / summary.openMarginUsd) : "-",
          },
          { label: "Open PnL", value: <Pnl value={summary.unrealizedPnlUsd} /> },
        ]}
      />

      <SectionLabel className="mt-4">Markets</SectionLabel>
      {marketRows.length === 0 ? (
        <p className="text-xs text-fg-3">No open market exposure.</p>
      ) : (
        <ul className="grid gap-1.5">
          {marketRows.slice(0, 6).map((row) => (
            <li key={row.coin} className="grid grid-cols-[4rem_minmax(0,1fr)_5.25rem_4.75rem] items-center gap-2 text-xs">
              <span className="truncate font-medium">
                {row.coin}
                <span className="ml-1 font-normal text-fg-4">
                  {row.longCount > 0 ? `${row.longCount}L` : ""}
                  {row.longCount > 0 && row.shortCount > 0 ? " " : ""}
                  {row.shortCount > 0 ? `${row.shortCount}S` : ""}
                </span>
              </span>
              <Meter value={row.notionalUsd / maxNotional} tone="neutral" />
              <span className="text-right tabular-nums text-fg-2">{formatUsd(row.notionalUsd)}</span>
              <span className="text-right">
                <Pnl value={row.unrealizedPnlUsd} />
              </span>
            </li>
          ))}
          {marketRows.length > 6 ? (
            <li className="text-2xs text-fg-4">and {formatInteger(marketRows.length - 6)} more markets</li>
          ) : null}
        </ul>
      )}

    </Panel>
  );
}

export function AccountDetailsPanel({
  allocationUsd,
  lastRefreshAt,
  liveTradingEnabled,
  option,
  policy,
  riskLimits,
  view,
}: {
  allocationUsd: number;
  lastRefreshAt: Date | null;
  liveTradingEnabled: boolean;
  option: AccountOption;
  policy: PaperTradingPolicy;
  riskLimits: LiveRiskLimits;
  view: AccountView;
}) {
  const items =
    option.accountType === "paper"
      ? [
          { label: "Internal key", value: <span className="font-mono text-xs">{option.paper.key}</span> },
          { label: "Starting balance", value: formatUsd(option.paper.startingBalanceUsd) },
          { label: "Cash balance", value: formatUsd(option.paper.cashBalanceUsd) },
          { label: "Equity before open PnL", value: formatUsd(option.paper.equityUsd) },
          { label: "Total allocated to sources", value: formatUsd(allocationUsd) },
          { label: "Fees paid", value: formatUsd(option.paper.feeUsd) },
          { label: "Funding", value: formatSignedUsd(decimal(option.paper.fundingUsd)) },
          { label: "Created", value: formatDateTime(option.paper.createdAt) },
          { label: "Last change", value: formatDateTime(option.paper.updatedAt) },
        ]
      : [
          { label: "Internal key", value: <span className="font-mono text-xs">{option.live.key}</span> },
          { label: "Network", value: option.live.network },
          { label: "Wallet address", value: <span className="font-mono text-xs">{option.live.walletAddress ?? "Configured wallet"}</span> },
          { label: "Vault address", value: <span className="font-mono text-xs">{option.live.vaultAddress ?? "None"}</span> },
          { label: "Capital mode", value: formatCapitalMode(option.live.capitalMode) },
          { label: "Account abstraction", value: option.live.userAbstraction ?? "Unknown" },
          { label: "Cash balance", value: formatUsd(option.live.cashBalanceUsd) },
          { label: "Tradable equity", value: formatUsd(option.live.tradableEquityUsd) },
          { label: "Perp equity", value: formatUsd(option.live.perpEquityUsd) },
          { label: "Spot USDC", value: formatUsd(option.live.spotUsdcBalanceUsd) },
          { label: "Net external flows", value: formatSignedUsd(decimal(option.live.netExternalFlowsUsd)) },
          {
            hint: "Equity change minus deposits and withdrawals",
            label: "Cash-flow adjusted PnL",
            value: <Pnl value={option.live.tradingPnlUsd} />,
          },
          { label: "Performance tracking since", value: formatDateTime(option.live.performanceTrackingStartedAt) },
          { label: "Status reason", value: option.live.statusReason ?? "None" },
          { label: "Status changed", value: formatDateTime(option.live.statusChangedAt) },
          {
            label: "Reconciliation",
            tone:
              option.live.reconciliationStatus === "complete"
                ? ("positive" as const)
                : option.live.reconciliationStatus === "failed"
                  ? ("negative" as const)
                  : ("warning" as const),
            value: option.live.reconciliationStatus,
          },
          { label: "Last complete snapshot", value: formatDateTime(option.live.lastReconciledAt) },
          { label: "Last attempt", value: formatDateTime(option.live.reconciliationAttemptedAt) },
          {
            label: "Incomplete parts",
            value: option.live.incompleteReconciliationComponents.join(", ") || "None",
          },
          { label: "Created", value: formatDateTime(option.live.createdAt) },
        ];
  const half = Math.ceil(items.length / 2);

  return (
    <details className="group min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <summary className="flex min-h-10 list-none items-center justify-between gap-3 px-3.5 py-2 text-sm font-semibold marker:hidden hover:bg-surface-2">
        <span>
          Account details and {option.accountType === "live" ? "safety limits" : "copy rules"}
        </span>
        <span className="text-xs font-normal text-fg-3">
          {lastRefreshAt ? (
            <>
              data from <TimeAgo value={lastRefreshAt} />
            </>
          ) : null}
          <span className="ml-2 group-open:hidden">Show</span>
          <span className="ml-2 hidden group-open:inline">Hide</span>
        </span>
      </summary>
      <div className="grid gap-x-8 gap-y-4 border-t border-line px-3.5 py-3 lg:grid-cols-3">
        <KeyValues items={items.slice(0, half)} />
        <KeyValues items={items.slice(half)} />
        <RulesList
          accountType={option.accountType}
          liveTradingEnabled={liveTradingEnabled}
          policy={policy}
          riskLimits={riskLimits}
        />
      </div>
      {view.capitalBalances.length > 0 ? (
        <div className="border-t border-line">
          <p className="px-3.5 pt-3 text-xs font-medium text-fg-3">Capital by scope</p>
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Scope</th>
                  <th className="num">Equity</th>
                  <th className="num">Available</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {view.capitalBalances.map((balance) => (
                  <tr key={balance.key}>
                    <td>
                      {balance.label}
                      <span className="sub font-mono">{balance.key}</span>
                    </td>
                    <td className="num">{formatUsd(balance.equityUsd)}</td>
                    <td className="num">{formatUsd(balance.availableUsd)}</td>
                    <td>
                      {balance.stale ? (
                        <Badge tone="warning" title={balance.error ?? undefined}>
                          Stale snapshot
                        </Badge>
                      ) : (
                        <StatusDot
                          tone={balance.tradable ? "positive" : "neutral"}
                          label={balance.tradable ? "Tradable" : "Not tradable"}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </details>
  );
}

function RulesList({
  accountType,
  liveTradingEnabled,
  policy,
  riskLimits,
}: {
  accountType: "paper" | "live";
  liveTradingEnabled: boolean;
  policy: PaperTradingPolicy;
  riskLimits: LiveRiskLimits;
}) {
  return (
    <div>
      {accountType === "live" ? (
        <>
          <SectionLabel>Live safety limits</SectionLabel>
          <KeyValues
            items={[
              { label: "Daily loss limit", value: formatPercent(riskLimits.maxDailyLossPct) },
              { label: "Weekly loss limit", value: formatPercent(riskLimits.maxWeeklyLossPct) },
              { label: "Drawdown limit", value: formatPercent(riskLimits.maxDrawdownPct) },
              { label: "Loss limit per source", value: formatPercent(riskLimits.maxSourceLossPct) },
              { label: "Order rate limit", value: `${formatInteger(riskLimits.maxOrdersPerMinute)} per minute` },
              {
                hint: "New entries pause when the last complete exchange snapshot is older than this",
                label: "Snapshot max age",
                value: `${formatInteger(riskLimits.reconciliationMaxSnapshotAgeSeconds)} s`,
              },
              { label: "Entry intent lifetime", value: `${formatInteger(riskLimits.entryIntentTtlSeconds)} s` },
              { label: "Exits when stopped", value: riskLimits.reduceOnlyWhenStopped ? "Allowed" : "Blocked" },
              {
                label: "Live trading on server",
                tone: liveTradingEnabled ? "positive" : "neutral",
                value: liveTradingEnabled ? "On" : "Off",
              },
            ]}
          />
        </>
      ) : (
        <>
          <SectionLabel>Copy rules</SectionLabel>
          <KeyValues
            items={[
              { label: "Wallets copied", value: formatInteger(policy.topWalletCount) },
              {
                hint: "Share of the account each source may use. The sum may exceed the account; exposure caps limit the total.",
                label: "Share per source",
                value: `${formatPercent(policy.sourceAllocationPct, { decimals: 0 })} of ${formatPercent(policy.maxTotalAllocationPct, { decimals: 0 })} budget`,
              },
              {
                label: "Exposure cap",
                value: `${formatMultiple(policy.maxSourceExposureMultiple)} per source, ${formatMultiple(policy.maxTotalExposureMultiple)} total`,
              },
              {
                hint: "Orders below the exchange minimum are rounded up when they reach this share of it",
                label: "Minimum order",
                value: `${formatUsd(policy.minOrderNotionalUsd)}, rounds up from ${formatPercent(policy.minOrderRoundUpRatio, { decimals: 0 })}`,
              },
              { label: "Simulated fee", value: formatPercent(policy.feeRate, { decimals: 3 }) },
              { label: "Simulated slippage", value: formatBps(policy.slippageBps) },
              { label: "Simulated latency", value: `${formatInteger(policy.latencyMs)} ms` },
              {
                hint: "Entries are skipped when the price moved further than this against us since the source traded",
                label: "Max adverse drift",
                value: formatBps(policy.maxPriceDriftBps),
              },
              {
                label: "Price cache",
                value: policy.marketPriceCacheEnabled
                  ? `On, stale after ${formatInteger(policy.marketPriceCacheStaleSeconds)} s`
                  : "Off",
              },
            ]}
          />
        </>
      )}
    </div>
  );
}
