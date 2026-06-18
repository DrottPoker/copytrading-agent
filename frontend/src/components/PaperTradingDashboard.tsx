"use client";

import {
  Activity,
  BarChart3,
  Clock,
  Loader2,
  RadioTower,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  WalletCards,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

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
  PaperTradingAccount,
  PaperTradingSummaryResponse,
  PaperWalletPerformance,
} from "@/types/paper";

import { StatusPill } from "./StatusPill";

const PAPER_REFRESH_MS = 4000;

type Tone = "positive" | "warning" | "danger" | "neutral";

type MonitoredSource = {
  sourceWallet: string;
  rank: number | null;
  score: string | null;
  active: boolean;
  status: "currently trading" | "monitored" | "exit only";
  accountCount: number;
  openPositionCount: number;
  allocationPct: number | null;
  allocationUsd: number;
  openMarginUsd: number;
  remainingAllocationUsd: number;
  pocketUsedPct: number | null;
  realizedPnlUsd: string;
  unrealizedPnlUsd: string;
  totalPnlUsd: string;
};

export function PaperTradingDashboard({
  initialSummary,
}: {
  initialSummary: PaperTradingSummaryResponse;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [connectionState, setConnectionState] = useState<"live" | "refreshing" | "offline">("live");
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(new Date());
  const [closingPositionId, setClosingPositionId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

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

  const handleManualClose = useCallback(
    async (position: PaperPosition) => {
      if (closingPositionId) {
        return;
      }
      const confirmed = window.confirm(
        `Close ${position.coin} ${position.side} paper position for ${position.accountKey}?`,
      );
      if (!confirmed) {
        return;
      }

      setClosingPositionId(position.id);
      setActionError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/paper-trading/positions/${position.id}/close`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response));
          return;
        }
        const payload = (await response.json()) as PaperTradingSummaryResponse;
        setSummary(payload);
        setLastRefreshAt(new Date());
        setConnectionState("live");
      } catch {
        setConnectionState("offline");
        setActionError("Manual close failed.");
      } finally {
        setClosingPositionId(null);
      }
    },
    [closingPositionId],
  );

  const metrics = useMemo(() => buildMetrics(summary), [summary]);
  const monitoredSources = useMemo(() => buildMonitoredSources(summary), [summary]);
  const walletHistory = useMemo(() => buildWalletHistory(summary.walletPerformance), [summary.walletPerformance]);
  const tradingSourceCount = countSourcesWithOpenPositions(summary.positions);

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

      {actionError ? (
        <div className="rounded-lg border border-[#f2aaa5] bg-[#fff2f0] px-4 py-3 text-sm font-medium text-danger">
          {actionError}
        </div>
      ) : null}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <HeroMetric
          icon={WalletCards}
          label="Net equity"
          value={formatCurrency(metrics.netEquity)}
          detail={`${formatCurrency(metrics.cashEquity)} cash`}
          tone={metrics.totalPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={metrics.totalPnl >= 0 ? TrendingUp : TrendingDown}
          label="Total PnL"
          value={formatCurrency(metrics.totalPnl)}
          detail={`${formatCurrency(metrics.realizedPnl)} realized, ${formatCurrency(metrics.unrealizedPnl)} unrealized`}
          tone={metrics.totalPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={TrendingUp}
          label="Realized PnL"
          value={formatCurrency(metrics.realizedPnl)}
          detail={`${formatCurrency(metrics.fees)} total fees`}
          tone={metrics.realizedPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={TrendingDown}
          label="Unrealized PnL"
          value={formatCurrency(metrics.unrealizedPnl)}
          detail={`${formatInteger(summary.positions.length)} open positions`}
          tone={metrics.unrealizedPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={Activity}
          label="Open margin"
          value={formatCurrency(metrics.openMargin)}
          detail={`${formatCurrency(metrics.openNotional)} notional`}
        />
        <HeroMetric
          icon={RadioTower}
          label="Sources"
          value={`${formatInteger(monitoredSources.length)} monitored`}
          detail={`${formatInteger(tradingSourceCount)} currently trading`}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
        <Panel title="Accounts" meta={`${formatInteger(summary.accounts.length)} accounts`}>
          <div className="grid gap-3 md:grid-cols-2">
            {summary.accounts.length === 0 ? (
              <EmptyState text="No paper accounts synced." />
            ) : (
              summary.accounts.map((account) => <AccountCard key={account.key} account={account} />)
            )}
          </div>
        </Panel>

        <Panel title="Execution Policy" meta={`Updated ${formatDate(summary.updatedAt)}`}>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-1">
            <DataPoint label="Wallets" value={formatInteger(summary.policy.topWalletCount)} detail="Top ranked sources" />
            <DataPoint label="Pocket" value={formatPercent(summary.policy.standardAllocationPct)} detail="Per source wallet" />
            <DataPoint label="Total cap" value={formatPercent(summary.policy.maxTotalAllocationPct)} detail="Max open margin" />
            <DataPoint label="Min order" value={formatCurrency(summary.policy.minOrderNotionalUsd)} />
            <DataPoint label="Fee" value={formatPercent(summary.policy.feeRate)} />
            <DataPoint label="Slippage" value={formatBps(summary.policy.slippageBps)} />
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-line pt-4 text-sm text-[#5b6770]">
            <Clock className="h-4 w-4" aria-hidden="true" />
            <span>Last refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
            <span className="font-mono">{formatInteger(PAPER_REFRESH_MS)} ms polling</span>
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[0.95fr_1.05fr]">
        <Panel
          title="Monitored Sources"
          meta={`${formatInteger(tradingSourceCount)} trading, ${formatInteger(retainedAllocationSourceCount(summary.allocations))} exit only`}
        >
          <div className="grid gap-3">
            {monitoredSources.length === 0 ? (
              <EmptyState text="No monitored sources." />
            ) : (
              monitoredSources.map((source) => (
                <MonitoredSourceCard key={source.sourceWallet} source={source} />
              ))
            )}
          </div>
        </Panel>

        <Panel title="Open Positions" meta={`${formatInteger(summary.positions.length)} live paper positions`}>
          <div className="grid gap-3">
            {summary.positions.length === 0 ? (
              <EmptyState text="No open paper positions." />
            ) : (
              summary.positions.map((position) => (
                <PositionCard
                  key={position.id}
                  isClosing={closingPositionId === position.id}
                  onClose={handleManualClose}
                  position={position}
                />
              ))
            )}
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[0.95fr_1.05fr]">
        <Panel title="Wallet PnL History" meta={`${formatInteger(walletHistory.length)} traded sources`}>
          <div className="grid gap-3">
            {walletHistory.length === 0 ? (
              <EmptyState text="No wallet trading history yet." />
            ) : (
              walletHistory.map((wallet) => (
                <WalletHistoryCard key={wallet.sourceWallet} wallet={wallet} />
              ))
            )}
          </div>
        </Panel>

        <Panel title="Trade History" meta={`${formatInteger(summary.recentFills.length)} latest rows`}>
          <div className="grid gap-3">
            {summary.recentFills.length === 0 ? (
              <EmptyState text="No paper fills recorded." />
            ) : (
              summary.recentFills.map((fill) => (
                <FillCard
                  key={fill.id}
                  fill={fill}
                  minOrderNotionalUsd={summary.policy.minOrderNotionalUsd}
                />
              ))
            )}
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
          <p className="mt-2 truncate text-xl font-semibold text-ink">{value}</p>
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
  children: ReactNode;
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

function AccountCard({ account }: { account: PaperTradingAccount }) {
  const totalPnl = numberValue(account.totalPnlUsd);
  return (
    <article className="rounded-md border border-line bg-[#f8fafb] p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-semibold text-ink">{account.label}</p>
          <p className="mt-1 break-all font-mono text-xs text-[#5b6770]">{account.key}</p>
        </div>
        <StatusPill label={account.enabled ? "enabled" : "disabled"} tone={account.enabled ? "positive" : "warning"} />
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <MiniStat label="Net equity" value={formatCurrency(accountNetEquity(account))} />
        <MiniStat label="Total PnL" value={formatCurrency(account.totalPnlUsd)} tone={totalPnl >= 0 ? "positive" : "danger"} />
        <MiniStat label="Realized" value={formatCurrency(account.realizedPnlUsd)} tone={numberValue(account.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <MiniStat label="Unrealized" value={formatCurrency(account.unrealizedPnlUsd)} tone={numberValue(account.unrealizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <MiniStat label="Open margin" value={formatCurrency(account.openMarginUsd)} />
        <MiniStat label="Positions" value={formatInteger(account.openPositionCount)} />
      </div>
    </article>
  );
}

function MonitoredSourceCard({ source }: { source: MonitoredSource }) {
  const usedPct = clampPercent(numberValue(source.pocketUsedPct ?? 0));
  const statusTone = source.status === "currently trading" ? "positive" : source.status === "exit only" ? "warning" : "neutral";
  return (
    <article className="rounded-md border border-line bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href={`/wallets/${source.sourceWallet}`}
            className="break-all font-mono text-xs font-semibold text-ink hover:text-[#297c73]"
          >
            {shortAddress(source.sourceWallet)}
          </Link>
          <p className="mt-1 text-xs text-[#5b6770]">
            {source.rank ? `#${source.rank}` : "unranked"} slot, {formatScore(source.score)} score
          </p>
        </div>
        <StatusPill label={source.status} tone={statusTone} />
      </div>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <MiniStat label="Pocket" value={formatCurrency(source.allocationUsd)} detail={formatPercent(source.allocationPct)} />
        <MiniStat label="Used" value={formatCurrency(source.openMarginUsd)} detail={`${formatPercent(source.pocketUsedPct)} used`} />
        <MiniStat label="PnL" value={formatCurrency(source.totalPnlUsd)} tone={numberValue(source.totalPnlUsd) >= 0 ? "positive" : "danger"} />
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-[#e8edf2]">
        <div
          className={`h-full ${usedPct >= 0.9 ? "bg-danger" : usedPct >= 0.7 ? "bg-warning" : "bg-positive"}`}
          style={{ width: `${Math.min(usedPct * 100, 100)}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-[#5b6770]">
        {formatCurrency(source.remainingAllocationUsd)} free, {formatInteger(source.openPositionCount)} open positions
      </p>
    </article>
  );
}

function PositionCard({
  isClosing,
  onClose,
  position,
}: {
  isClosing: boolean;
  onClose: (position: PaperPosition) => void;
  position: PaperPosition;
}) {
  const canClose = position.markPrice !== null;
  const unrealizedPnl = numberValue(position.unrealizedPnlUsd ?? 0);
  return (
    <article className="rounded-md border border-line bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-ink">{position.coin}</p>
            <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
            <span className="font-mono text-xs text-[#5b6770]">{formatLeverage(position.leverage)}</span>
          </div>
          <Link
            href={`/wallets/${position.sourceWallet}`}
            className="mt-2 block break-all font-mono text-xs text-ink hover:text-[#297c73]"
          >
            {shortAddress(position.sourceWallet)}
          </Link>
          <p className="mt-1 font-mono text-xs text-[#5b6770]">{position.accountKey}</p>
        </div>
        <button
          type="button"
          onClick={() => onClose(position)}
          disabled={!canClose || isClosing}
          title={canClose ? "Close paper position" : "Execution price unavailable"}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-3 text-sm font-semibold text-danger shadow-sm hover:bg-[#ffe6e2] disabled:cursor-not-allowed disabled:border-line disabled:bg-[#f7f9fb] disabled:text-[#98a2b3]"
        >
          {isClosing ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <XCircle className="h-4 w-4" aria-hidden="true" />}
          Close
        </button>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat label="Unrealized" value={formatCurrency(position.unrealizedPnlUsd)} detail={formatPercent(position.unrealizedPnlPct)} tone={unrealizedPnl >= 0 ? "positive" : "danger"} />
        <MiniStat label="Margin" value={formatCurrency(position.marginUsd)} detail={`${formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)} notional`} />
        <MiniStat label="Entry" value={formatPrice(position.entryPrice)} detail={`size ${formatSize(position.size)}`} />
        <MiniStat label="Mark" value={formatPrice(position.markPrice)} detail={formatDate(position.priceUpdatedAt)} />
      </div>
    </article>
  );
}

function WalletHistoryCard({ wallet }: { wallet: PaperWalletPerformance }) {
  const totalPnl = numberValue(wallet.totalPnlUsd);
  return (
    <article className="rounded-md border border-line bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href={`/wallets/${wallet.sourceWallet}`}
            className="break-all font-mono text-xs font-semibold text-ink hover:text-[#297c73]"
          >
            {shortAddress(wallet.sourceWallet)}
          </Link>
          <p className="mt-1 text-xs text-[#5b6770]">
            {wallet.rank ? `#${wallet.rank}` : "unranked"}, {formatScore(wallet.score)} score
          </p>
        </div>
        <StatusPill
          label={wallet.openPositionCount > 0 ? "currently trading" : wallet.active ? "monitored" : "history"}
          tone={wallet.openPositionCount > 0 ? "positive" : wallet.active ? "neutral" : "warning"}
        />
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat label="Total PnL" value={formatCurrency(wallet.totalPnlUsd)} tone={totalPnl >= 0 ? "positive" : "danger"} />
        <MiniStat label="Realized" value={formatCurrency(wallet.realizedPnlUsd)} tone={numberValue(wallet.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <MiniStat label="Unrealized" value={formatCurrency(wallet.unrealizedPnlUsd)} tone={numberValue(wallet.unrealizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <MiniStat label="Open margin" value={formatCurrency(wallet.openMarginUsd)} detail={`${formatCurrency(wallet.openNotionalUsd)} notional`} />
        <MiniStat label="Copied" value={formatInteger(wallet.copiedFillCount)} detail={`${formatInteger(wallet.skippedFillCount)} skipped`} />
        <MiniStat label="Fees" value={formatCurrency(wallet.feeUsd)} />
        <MiniStat label="Accounts" value={formatInteger(wallet.accountCount)} />
        <MiniStat label="Last fill" value={formatDate(wallet.lastFillAt)} />
      </div>
    </article>
  );
}

function FillCard({
  fill,
  minOrderNotionalUsd,
}: {
  fill: PaperCopyFill;
  minOrderNotionalUsd: string;
}) {
  const targetNotional = targetPaperNotional(fill);
  const skipDetail = skipReasonDetail(fill.skippedReason, targetNotional, minOrderNotionalUsd);
  const isSkip = fill.action === "skip";
  return (
    <article className="rounded-md border border-line bg-white p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill label={fill.action} tone={isSkip ? "warning" : "positive"} />
            <p className="font-semibold text-ink">{fill.coin}</p>
            <span className="text-sm text-[#5b6770]">{fill.side ?? "-"}</span>
          </div>
          <Link
            href={`/wallets/${fill.sourceWallet}`}
            className="mt-2 block break-all font-mono text-xs text-ink hover:text-[#297c73]"
          >
            {shortAddress(fill.sourceWallet)}
          </Link>
          <p className="mt-1 font-mono text-xs text-[#5b6770]">{fill.accountKey}</p>
        </div>
        <p className="text-sm text-[#5b6770]">{formatDate(fill.filledAt)}</p>
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MiniStat
          label="Notional"
          value={formatCurrency(fill.notionalUsd)}
          detail={`${formatCurrency(fill.marginUsd)} margin, ${formatLeverage(fill.leverage)}`}
        />
        <MiniStat label="Realized PnL" value={formatCurrency(fill.realizedPnlUsd)} tone={numberValue(fill.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <MiniStat label="Fee" value={formatCurrency(fill.feeUsd)} />
        <MiniStat label="Skip reason" value={formatSkipReason(fill.skippedReason)} detail={skipDetail ?? undefined} />
      </div>
      {isSkip && targetNotional !== null ? (
        <p className="mt-3 text-xs text-[#5b6770]">Target {formatCurrency(targetNotional)}</p>
      ) : null}
    </article>
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

function MiniStat({
  detail,
  label,
  tone = "neutral",
  value,
}: {
  detail?: string;
  label: string;
  tone?: Tone;
  value: string;
}) {
  const valueClass =
    tone === "positive" ? "text-positive" : tone === "danger" ? "text-danger" : "text-ink";
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className={`mt-1 truncate font-mono text-sm font-semibold ${valueClass}`}>{value}</p>
      {detail ? <p className="mt-1 truncate text-xs text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-md border border-dashed border-line bg-[#f8fafb] px-4 py-8 text-center text-sm text-[#5b6770]">
      {text}
    </div>
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

function buildMonitoredSources(summary: PaperTradingSummaryResponse): MonitoredSource[] {
  const walletPerformanceBySource = new Map(
    summary.walletPerformance.map((wallet) => [wallet.sourceWallet.toLowerCase(), wallet]),
  );
  const allocationsBySource = new Map<string, PaperCopyAllocation[]>();
  for (const allocation of summary.allocations) {
    const source = allocation.sourceWallet.toLowerCase();
    allocationsBySource.set(source, [...(allocationsBySource.get(source) ?? []), allocation]);
  }
  const positionsBySource = new Map<string, PaperPosition[]>();
  for (const position of summary.positions) {
    const source = position.sourceWallet.toLowerCase();
    positionsBySource.set(source, [...(positionsBySource.get(source) ?? []), position]);
  }

  const sources = new Set([...allocationsBySource.keys(), ...positionsBySource.keys()]);
  return Array.from(sources)
    .map((source) => {
      const allocations = allocationsBySource.get(source) ?? [];
      const wallet = walletPerformanceBySource.get(source);
      const openPositions = positionsBySource.get(source) ?? [];
      const allocationUsd = sumNumbers(allocations.map((allocation) => allocation.allocationUsd));
      const openMarginUsd = sumNumbers(allocations.map((allocation) => allocation.openMarginUsd));
      const remainingAllocationUsd = sumNumbers(allocations.map((allocation) => allocation.remainingAllocationUsd));
      const active = allocations.some((allocation) => allocation.active);
      const status: MonitoredSource["status"] =
        openPositions.length > 0 ? "currently trading" : active ? "monitored" : "exit only";
      return {
        sourceWallet: source,
        rank: minNumber(allocations.map((allocation) => allocation.rank)),
        score: firstString(allocations.map((allocation) => allocation.score)) ?? wallet?.score ?? null,
        active,
        status,
        accountCount: new Set(allocations.map((allocation) => allocation.accountKey)).size,
        openPositionCount: openPositions.length,
        allocationPct: firstNumber(allocations.map((allocation) => allocation.allocationPct)),
        allocationUsd,
        openMarginUsd,
        remainingAllocationUsd,
        pocketUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
        realizedPnlUsd: wallet?.realizedPnlUsd ?? "0",
        unrealizedPnlUsd: wallet?.unrealizedPnlUsd ?? "0",
        totalPnlUsd: wallet?.totalPnlUsd ?? "0",
      };
    })
    .sort((left, right) => {
      if (left.status !== right.status) {
        return statusOrder(left.status) - statusOrder(right.status);
      }
      return (left.rank ?? 9999) - (right.rank ?? 9999);
    });
}

function buildWalletHistory(wallets: PaperWalletPerformance[]) {
  return wallets
    .filter(
      (wallet) =>
        wallet.openPositionCount > 0 ||
        wallet.copiedFillCount > 0 ||
        wallet.skippedFillCount > 0 ||
        numberValue(wallet.totalPnlUsd) !== 0,
    )
    .sort((left, right) => {
      if (left.openPositionCount !== right.openPositionCount) {
        return right.openPositionCount - left.openPositionCount;
      }
      return numberValue(right.totalPnlUsd) - numberValue(left.totalPnlUsd);
    });
}

function countSourcesWithOpenPositions(positions: PaperPosition[]) {
  return new Set(positions.map((position) => position.sourceWallet.toLowerCase())).size;
}

function accountNetEquity(account: { equityUsd: string; unrealizedPnlUsd: string }) {
  return numberValue(account.equityUsd) + numberValue(account.unrealizedPnlUsd);
}

function retainedAllocationSourceCount(allocations: PaperCopyAllocation[]) {
  return uniqueAllocationSources(allocations.filter((allocation) => !allocation.active)).length;
}

function uniqueAllocationSources(allocations: PaperCopyAllocation[]) {
  return Array.from(new Set(allocations.map((allocation) => allocation.sourceWallet)));
}

function sumNumbers(values: Array<string | number | null | undefined>): number {
  return values.reduce<number>(
    (total, value) => total + (value === null || value === undefined ? 0 : numberValue(value)),
    0,
  );
}

function firstNumber(values: Array<string | number | null | undefined>): number | null {
  for (const value of values) {
    if (value !== null && value !== undefined) {
      return numberValue(value);
    }
  }
  return null;
}

function firstString(values: Array<string | number | null | undefined>) {
  for (const value of values) {
    if (value !== null && value !== undefined) {
      return String(value);
    }
  }
  return null;
}

function minNumber(values: Array<string | number | null | undefined>): number | null {
  const numbers = values
    .filter((value): value is string | number => value !== null && value !== undefined)
    .map((value) => numberValue(value));
  if (numbers.length === 0) {
    return null;
  }
  return Math.min(...numbers);
}

function statusOrder(status: MonitoredSource["status"]) {
  if (status === "currently trading") {
    return 0;
  }
  if (status === "monitored") {
    return 1;
  }
  return 2;
}

function clampPercent(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(value, 1));
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
    retained_source_new_position_blocked: "Retained source new position blocked",
    source_account_margin_summary_missing: "Source account margin summary missing",
    source_account_state_fetch_failed: "Source account state fetch failed",
    source_account_state_missing: "Source account state missing",
    source_account_value_missing: "Source perp equity missing",
    source_account_value_zero: "Source perp equity zero",
    source_allocation_cap_reached: "Source allocation cap reached",
    source_and_total_allocation_caps_reached: "Source and total allocation caps reached",
    source_perp_equity_missing: "Source perp equity missing",
    source_perp_equity_zero: "Source perp equity zero",
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

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    return `Manual close failed with HTTP ${response.status}.`;
  }
  return `Manual close failed with HTTP ${response.status}.`;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
