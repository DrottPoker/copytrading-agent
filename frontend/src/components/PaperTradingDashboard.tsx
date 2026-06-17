"use client";

import {
  Activity,
  BarChart3,
  Clock,
  RefreshCw,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
  WalletCards,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type {
  PaperCopyAllocation,
  PaperCopyFill,
  PaperPosition,
  PaperTradingSummaryResponse,
  PaperWalletPerformance,
} from "@/types/paper";

import { StatusPill } from "./StatusPill";

const PAPER_REFRESH_MS = 4000;

type Tone = "positive" | "warning" | "danger" | "neutral";

export function PaperTradingDashboard({
  initialSummary,
}: {
  initialSummary: PaperTradingSummaryResponse;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [connectionState, setConnectionState] = useState<"live" | "refreshing" | "offline">("live");
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(new Date());

  const refresh = useCallback(async () => {
    setConnectionState("refreshing");
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/paper-trading`, {
        cache: "no-store",
      });
      if (!response.ok) {
        setConnectionState("offline");
        return;
      }
      const payload = (await response.json()) as PaperTradingSummaryResponse;
      setSummary(payload);
      setLastRefreshAt(new Date());
      setConnectionState("live");
    } catch {
      setConnectionState("offline");
    }
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void refresh();
    }, PAPER_REFRESH_MS);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  const metrics = useMemo(() => buildMetrics(summary), [summary]);
  const topWallet = summary.walletPerformance[0] ?? null;
  const worstWallet = summary.walletPerformance
    .filter((wallet) => numberValue(wallet.totalPnlUsd) < 0)
    .sort((left, right) => numberValue(left.totalPnlUsd) - numberValue(right.totalPnlUsd))[0] ?? null;

  return (
    <>
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-sm font-medium text-[#5b6770]">Paper execution cockpit</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-normal text-ink">
            <BarChart3 className="h-6 w-6 text-[#5b6770]" aria-hidden="true" />
            Paper Trading
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill
            label={summary.policy.enabled ? "paper copy enabled" : "paper copy disabled"}
            tone={summary.policy.enabled ? "positive" : "warning"}
          />
          <StatusPill label={marketStatusLabel(summary.marketDataStatus)} tone={marketStatusTone(summary.marketDataStatus)} />
          <StatusPill label={connectionState} tone={connectionState === "offline" ? "danger" : "positive"} />
          <button
            type="button"
            onClick={() => void refresh()}
            title="Refresh paper trading data"
            className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-line bg-white text-[#344054] shadow-sm hover:bg-[#f7f9fb]"
          >
            <RefreshCw className={`h-4 w-4 ${connectionState === "refreshing" ? "animate-spin" : ""}`} aria-hidden="true" />
            <span className="sr-only">Refresh</span>
          </button>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <HeroMetric
          icon={WalletCards}
          label="Net equity"
          value={formatCurrency(metrics.netEquity)}
          detail={`${formatCurrency(metrics.cashEquity)} cash, ${formatCurrency(metrics.unrealizedPnl)} unrealized`}
          tone={metrics.totalPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={Activity}
          label="Open risk"
          value={formatCurrency(metrics.openMargin)}
          detail={`${formatCurrency(metrics.openNotional)} notional across ${formatInteger(summary.positions.length)} positions`}
        />
        <HeroMetric
          icon={metrics.totalPnl >= 0 ? TrendingUp : TrendingDown}
          label="Total PnL"
          value={formatCurrency(metrics.totalPnl)}
          detail={`${formatCurrency(metrics.realizedPnl)} realized, ${formatCurrency(metrics.fees)} fees`}
          tone={metrics.totalPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={ShieldCheck}
          label="Sources"
          value={formatInteger(summary.walletPerformance.length)}
          detail={`${topWallet ? shortAddress(topWallet.sourceWallet) : "-"} best, ${worstWallet ? shortAddress(worstWallet.sourceWallet) : "-"} worst`}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Panel
          title="Accounts"
          meta={`${formatInteger(summary.accounts.length)} accounts`}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[920px] border-collapse text-left text-sm">
              <TableHead
                columns={[
                  "Account",
                  "Net equity",
                  "Total PnL",
                  "Realized",
                  "Unrealized",
                  "Open margin",
                  "Positions",
                  "State",
                ]}
              />
              <tbody>
                {summary.accounts.length === 0 ? (
                  <EmptyRow colSpan={8} text="No paper accounts synced." />
                ) : (
                  summary.accounts.map((account) => (
                    <tr key={account.key} className="border-b border-line last:border-b-0">
                      <td className="px-4 py-3">
                        <p className="font-semibold text-ink">{account.label}</p>
                        <p className="mt-1 font-mono text-xs text-[#5b6770]">{account.key}</p>
                      </td>
                      <td className="px-4 py-3 font-mono">{formatCurrency(accountNetEquity(account))}</td>
                      <td className={pnlCellClass(account.totalPnlUsd)}>{formatCurrency(account.totalPnlUsd)}</td>
                      <td className={pnlCellClass(account.realizedPnlUsd)}>{formatCurrency(account.realizedPnlUsd)}</td>
                      <td className={pnlCellClass(account.unrealizedPnlUsd)}>{formatCurrency(account.unrealizedPnlUsd)}</td>
                      <td className="px-4 py-3 font-mono">{formatCurrency(account.openMarginUsd)}</td>
                      <td className="px-4 py-3 font-mono">{formatInteger(account.openPositionCount)}</td>
                      <td className="px-4 py-3">
                        <StatusPill label={account.enabled ? "enabled" : "disabled"} tone={account.enabled ? "positive" : "warning"} />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Execution Policy" meta={`Updated ${formatDate(summary.updatedAt)}`}>
          <div className="grid gap-3 sm:grid-cols-2">
            <DataPoint label="Wallets" value={formatInteger(summary.policy.topWalletCount)} detail="Top ranked sources" />
            <DataPoint label="Pocket" value={formatPercent(summary.policy.standardAllocationPct)} detail="Per source wallet" />
            <DataPoint label="Total cap" value={formatPercent(summary.policy.maxTotalAllocationPct)} detail="Max open margin" />
            <DataPoint label="Min order" value={formatCurrency(summary.policy.minOrderNotionalUsd)} />
            <DataPoint label="Fee" value={formatPercent(summary.policy.feeRate)} />
            <DataPoint label="Slippage" value={formatBps(summary.policy.slippageBps)} />
            <DataPoint label="Latency" value={`${formatInteger(summary.policy.latencyMs)} ms`} />
            <DataPoint label="Max drift" value={formatBps(summary.policy.maxPriceDriftBps)} />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4 text-sm text-[#5b6770]">
            <Clock className="h-4 w-4" aria-hidden="true" />
            <span>Last browser refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
            <span className="font-mono">{formatInteger(PAPER_REFRESH_MS)} ms polling</span>
          </div>
        </Panel>
      </section>

      <Panel title="Source Wallet PnL" meta={`${formatInteger(summary.walletPerformance.length)} sources`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] border-collapse text-left text-sm">
            <TableHead
              columns={[
                "Source wallet",
                "Rank",
                "Allocation",
                "Total PnL",
                "Realized",
                "Unrealized",
                "Open margin",
                "Fills",
                "Last fill",
              ]}
            />
            <tbody>
              {summary.walletPerformance.length === 0 ? (
                <EmptyRow colSpan={9} text="No source wallet performance yet." />
              ) : (
                summary.walletPerformance.map((wallet) => (
                  <WalletPerformanceRow key={wallet.sourceWallet} wallet={wallet} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Open Positions" meta={`${formatInteger(summary.positions.length)} live paper positions`}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1280px] border-collapse text-left text-sm">
            <TableHead
              columns={[
                "Account",
                "Source",
                "Market",
                "Side",
                "Size",
                "Entry",
                "Mark",
                "Unrealized PnL",
                "ROE",
                "Margin",
                "Notional",
                "Updated",
              ]}
            />
            <tbody>
              {summary.positions.length === 0 ? (
                <EmptyRow colSpan={12} text="No open paper positions." />
              ) : (
                summary.positions.map((position) => (
                  <PositionRow key={position.id} position={position} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel
          title="Allocations"
          meta={`${formatInteger(activeAllocationSourceCount(summary.allocations))} active sources, ${formatInteger(retainedAllocationSourceCount(summary.allocations))} retained`}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-sm">
              <TableHead columns={["Account", "Source", "Rank", "Score", "Pocket", "Used", "State"]} />
              <tbody>
                {summary.allocations.length === 0 ? (
                  <EmptyRow colSpan={7} text="No active allocation sources." />
                ) : (
                  summary.allocations.map((allocation) => (
                    <AllocationRow key={allocation.id} allocation={allocation} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Recent Paper Fills" meta={`${formatInteger(summary.recentFills.length)} latest rows`}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1060px] border-collapse text-left text-sm">
              <TableHead
                columns={[
                  "Time",
                  "Account",
                  "Source",
                  "Action",
                  "Market",
                  "Notional",
                  "PnL",
                  "Skip reason",
                ]}
              />
              <tbody>
                {summary.recentFills.length === 0 ? (
                  <EmptyRow colSpan={8} text="No paper fills recorded." />
                ) : (
                  summary.recentFills.map((fill) => (
                    <FillRow
                      key={fill.id}
                      fill={fill}
                      minOrderNotionalUsd={summary.policy.minOrderNotionalUsd}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>
    </>
  );
}

function HeroMetric({
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: Tone;
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
  meta,
  title,
}: {
  children: React.ReactNode;
  meta?: string;
  title: string;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex flex-col gap-2 border-b border-line px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <h2 className="text-base font-semibold text-ink">{title}</h2>
        {meta ? <p className="text-sm text-[#5b6770]">{meta}</p> : null}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function DataPoint({
  detail,
  label,
  value,
}: {
  detail?: string;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 break-words text-lg font-semibold leading-snug text-ink">{value}</p>
      {detail ? <p className="mt-1 truncate text-sm text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function TableHead({ columns }: { columns: string[] }) {
  return (
    <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
      <tr>
        {columns.map((column) => (
          <th key={column} className="px-4 py-3 font-semibold">
            {column}
          </th>
        ))}
      </tr>
    </thead>
  );
}

function WalletPerformanceRow({ wallet }: { wallet: PaperWalletPerformance }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${wallet.sourceWallet}`}
          className="font-mono text-xs text-ink hover:text-[#297c73]"
        >
          {shortAddress(wallet.sourceWallet)}
        </Link>
        <p className="mt-1 text-xs text-[#5b6770]">
          {formatInteger(wallet.openPositionCount)} open positions
        </p>
      </td>
      <td className="px-4 py-3 font-mono">{wallet.rank ? `#${wallet.rank}` : "-"}</td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatPercent(wallet.allocationPct)}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{formatScore(wallet.score)} score</p>
      </td>
      <td className={pnlCellClass(wallet.totalPnlUsd)}>{formatCurrency(wallet.totalPnlUsd)}</td>
      <td className={pnlCellClass(wallet.realizedPnlUsd)}>{formatCurrency(wallet.realizedPnlUsd)}</td>
      <td className={pnlCellClass(wallet.unrealizedPnlUsd)}>{formatCurrency(wallet.unrealizedPnlUsd)}</td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatCurrency(wallet.openMarginUsd)}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{formatCurrency(wallet.openNotionalUsd)} notional</p>
      </td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatInteger(wallet.copiedFillCount)} copied</p>
        <p className="mt-1 text-xs text-[#5b6770]">{formatInteger(wallet.skippedFillCount)} skipped</p>
      </td>
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(wallet.lastFillAt)}</td>
    </tr>
  );
}

function PositionRow({ position }: { position: PaperPosition }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-mono text-xs">{position.accountKey}</td>
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${position.sourceWallet}`}
          className="font-mono text-xs text-ink hover:text-[#297c73]"
        >
          {shortAddress(position.sourceWallet)}
        </Link>
      </td>
      <td className="px-4 py-3 font-semibold text-ink">{position.coin}</td>
      <td className="px-4 py-3">
        <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
      </td>
      <td className="px-4 py-3 font-mono">{formatSize(position.size)}</td>
      <td className="px-4 py-3 font-mono">{formatPrice(position.entryPrice)}</td>
      <td className="px-4 py-3 font-mono">{formatPrice(position.markPrice)}</td>
      <td className={pnlCellClass(position.unrealizedPnlUsd)}>
        {formatCurrency(position.unrealizedPnlUsd)}
      </td>
      <td className={pnlCellClass(position.unrealizedPnlPct)}>
        {formatPercent(position.unrealizedPnlPct)}
      </td>
      <td className="px-4 py-3 font-mono">{formatCurrency(position.marginUsd)}</td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{formatLeverage(position.leverage)}</p>
      </td>
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(position.priceUpdatedAt)}</td>
    </tr>
  );
}

function AllocationRow({ allocation }: { allocation: PaperCopyAllocation }) {
  const usedPct = clampPercent(numberValue(allocation.pocketUsedPct ?? 0));

  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-mono text-xs">{allocation.accountKey}</td>
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${allocation.sourceWallet}`}
          className="font-mono text-xs text-ink hover:text-[#297c73]"
        >
          {shortAddress(allocation.sourceWallet)}
        </Link>
      </td>
      <td className="px-4 py-3">
        <p className="font-mono">{allocation.active ? `#${allocation.rank}` : "retained"}</p>
        {!allocation.active ? (
          <p className="mt-1 text-xs text-[#5b6770]">slot #{allocation.rank}</p>
        ) : null}
      </td>
      <td className="px-4 py-3 font-mono">{formatScore(allocation.score)}</td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatCurrency(allocation.allocationUsd)}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{formatPercent(allocation.allocationPct)}</p>
      </td>
      <td className="px-4 py-3">
        <div className="h-2 w-full min-w-28 overflow-hidden rounded-full bg-[#e8edf2]">
          <div
            className={`h-full ${usedPct >= 0.9 ? "bg-danger" : usedPct >= 0.7 ? "bg-warning" : "bg-positive"}`}
            style={{ width: `${Math.min(usedPct * 100, 100)}%` }}
          />
        </div>
        <p className="mt-2 font-mono text-xs">
          {formatCurrency(allocation.openMarginUsd)} used
        </p>
        <p className="mt-1 text-xs text-[#5b6770]">
          {formatPercent(allocation.pocketUsedPct)} used, {formatCurrency(allocation.remainingAllocationUsd)} free
        </p>
      </td>
      <td className="px-4 py-3">
        <StatusPill label={allocation.active ? "active" : "retained"} tone={allocation.active ? "positive" : "warning"} />
      </td>
    </tr>
  );
}

function FillRow({
  fill,
  minOrderNotionalUsd,
}: {
  fill: PaperCopyFill;
  minOrderNotionalUsd: string;
}) {
  const targetNotional = targetPaperNotional(fill);
  const skipDetail = skipReasonDetail(fill.skippedReason, targetNotional, minOrderNotionalUsd);

  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(fill.filledAt)}</td>
      <td className="px-4 py-3 font-mono text-xs">{fill.accountKey}</td>
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${fill.sourceWallet}`}
          className="font-mono text-xs text-ink hover:text-[#297c73]"
        >
          {shortAddress(fill.sourceWallet)}
        </Link>
      </td>
      <td className="px-4 py-3">
        <StatusPill label={fill.action} tone={fill.action === "skip" ? "warning" : "positive"} />
      </td>
      <td className="px-4 py-3">
        <p className="font-semibold text-ink">{fill.coin}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{fill.side ?? "-"}</p>
      </td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatCurrency(fill.notionalUsd)}</p>
        <p className="mt-1 text-xs text-[#5b6770]">
          {formatCurrency(fill.marginUsd)} margin, {formatLeverage(fill.leverage)}
        </p>
        {fill.action === "skip" && targetNotional !== null ? (
          <p className="mt-1 text-xs text-[#5b6770]">target {formatCurrency(targetNotional)}</p>
        ) : null}
      </td>
      <td className={pnlCellClass(fill.realizedPnlUsd)}>{formatCurrency(fill.realizedPnlUsd)}</td>
      <td className="px-4 py-3 text-[#5b6770]">
        <p>{formatSkipReason(fill.skippedReason)}</p>
        {skipDetail ? <p className="mt-1 text-xs">{skipDetail}</p> : null}
      </td>
    </tr>
  );
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-10 text-center text-[#5b6770]">
        {text}
      </td>
    </tr>
  );
}

function buildMetrics(summary: PaperTradingSummaryResponse) {
  const cashEquity = summary.accounts.reduce(
    (total, account) => total + numberValue(account.equityUsd),
    0,
  );
  const unrealizedPnl = summary.accounts.reduce(
    (total, account) => total + numberValue(account.unrealizedPnlUsd),
    0,
  );
  const realizedPnl = summary.accounts.reduce(
    (total, account) => total + numberValue(account.realizedPnlUsd),
    0,
  );
  const fees = summary.accounts.reduce((total, account) => total + numberValue(account.feeUsd), 0);
  const openMargin = summary.accounts.reduce(
    (total, account) => total + numberValue(account.openMarginUsd),
    0,
  );
  const openNotional = summary.accounts.reduce(
    (total, account) => total + numberValue(account.openNotionalUsd),
    0,
  );
  return {
    cashEquity,
    fees,
    netEquity: cashEquity + unrealizedPnl,
    openMargin,
    openNotional,
    realizedPnl,
    totalPnl: realizedPnl + unrealizedPnl,
    unrealizedPnl,
  };
}

function accountNetEquity(account: { equityUsd: string; unrealizedPnlUsd: string }) {
  return numberValue(account.equityUsd) + numberValue(account.unrealizedPnlUsd);
}

function activeAllocationSourceCount(allocations: PaperCopyAllocation[]) {
  return uniqueAllocationSources(allocations.filter((allocation) => allocation.active)).length;
}

function retainedAllocationSourceCount(allocations: PaperCopyAllocation[]) {
  return uniqueAllocationSources(allocations.filter((allocation) => !allocation.active)).length;
}

function uniqueAllocationSources(allocations: PaperCopyAllocation[]) {
  return Array.from(new Set(allocations.map((allocation) => allocation.sourceWallet)));
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(value, 1));
}

function pnlCellClass(value: string | number | null | undefined) {
  const numericValue = value === null || value === undefined ? 0 : numberValue(value);
  return `px-4 py-3 font-mono ${numericValue >= 0 ? "text-positive" : "text-danger"}`;
}

function formatSize(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 6 }).format(numberValue(value));
}

function formatPrice(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 6,
    minimumFractionDigits: 2,
  }).format(numberValue(value));
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )} bps`;
}

function formatLeverage(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )}x`;
}

function marketStatusLabel(status: PaperTradingSummaryResponse["marketDataStatus"]) {
  const labels = {
    live: "live marks",
    no_open_positions: "no open positions",
    partial: "partial marks",
    unavailable: "marks unavailable",
  };
  return labels[status] ?? status;
}

function marketStatusTone(status: PaperTradingSummaryResponse["marketDataStatus"]): Tone {
  if (status === "live" || status === "no_open_positions") {
    return "positive";
  }
  if (status === "partial") {
    return "warning";
  }
  return "danger";
}

function formatSkipReason(reason: string | null) {
  if (!reason) {
    return "-";
  }
  const labels: Record<string, string> = {
    below_min_order_notional: "Below min order notional",
    below_min_or_cap_blocked: "Below min or allocation cap",
    execution_price_unavailable: "Execution price unavailable",
    invalid_close_size: "Invalid close size",
    invalid_price: "Invalid price",
    missing_source_start_position: "Missing source start position",
    no_matching_paper_position: "No matching paper position",
    opposite_paper_position: "Opposite paper position",
    preexisting_source_position: "Preexisting source position",
    price_drift_too_high: "Price drift too high",
    source_account_margin_summary_missing: "Source account margin summary missing",
    source_account_state_fetch_failed: "Source account state fetch failed",
    source_account_state_missing: "Source account state missing",
    source_account_value_missing: "Source account value missing",
    source_account_value_zero: "Source account value zero",
    source_allocation_cap_reached: "Source allocation cap reached",
    source_and_total_allocation_caps_reached: "Source and total allocation caps reached",
    total_allocation_cap_reached: "Total allocation cap reached",
    unsupported_source_fill_direction: "Unsupported source fill direction",
  };
  return labels[reason] ?? reason;
}

function skipReasonDetail(
  reason: string | null,
  targetNotional: number | null,
  minOrderNotionalUsd: string,
) {
  if (!reason || targetNotional === null) {
    return null;
  }
  const minOrderNotional = numberValue(minOrderNotionalUsd);
  if (reason === "below_min_order_notional" && targetNotional < minOrderNotional) {
    return `Target ${formatCurrency(targetNotional)}, min ${formatCurrency(minOrderNotional)}`;
  }
  if (
    reason === "source_allocation_cap_reached" ||
    reason === "total_allocation_cap_reached" ||
    reason === "source_and_total_allocation_caps_reached" ||
    reason === "below_min_or_cap_blocked"
  ) {
    return `Target before caps ${formatCurrency(targetNotional)}`;
  }
  return null;
}

function targetPaperNotional(fill: PaperCopyFill) {
  if (fill.allocationUsd === null || fill.sourceExposurePct === null) {
    return null;
  }
  const value = numberValue(fill.allocationUsd) * numberValue(fill.sourceExposurePct);
  return Number.isFinite(value) ? value : null;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
