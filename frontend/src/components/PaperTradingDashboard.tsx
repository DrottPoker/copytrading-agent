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
  PaperClosedTrade,
  PaperCopyAllocation,
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
  monitorStatus: "monitored" | "waiting";
  sourceStatus: "trading" | "retained" | "waiting_for_trades" | "waiting_for_slot";
  hasRealtimeSlot: boolean;
  canOpenNewPositions: boolean;
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
  const monitoredSlotCount = countSourcesByMonitorStatus(monitoredSources, "monitored");
  const waitingSlotCount = countSourcesBySourceStatus(monitoredSources, "waiting_for_slot");

  return (
    <>
      <header className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
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
        <div className="rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-3 py-2 text-sm font-medium text-danger">
          {actionError}
        </div>
      ) : null}

      <section className="grid gap-2 sm:grid-cols-2 xl:grid-cols-6">
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
          detail="realized + unrealized"
          tone={metrics.totalPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={TrendingUp}
          label="Realized"
          value={formatCurrency(metrics.realizedPnl)}
          detail={`${formatCurrency(metrics.fees)} fees`}
          tone={metrics.realizedPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={TrendingDown}
          label="Unrealized"
          value={formatCurrency(metrics.unrealizedPnl)}
          detail={`${formatInteger(summary.positions.length)} open`}
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
          value={`${formatInteger(tradingSourceCount)} trading`}
          detail={`${formatInteger(monitoredSlotCount)} monitored, ${formatInteger(waitingSlotCount)} waiting`}
        />
      </section>

      <section className="grid gap-3 xl:grid-cols-[1fr_0.9fr]">
        <ListPanel title="Accounts" meta={`${formatInteger(summary.accounts.length)} accounts`}>
          {summary.accounts.length === 0 ? (
            <EmptyState text="No paper accounts synced." />
          ) : (
            summary.accounts.map((account) => <AccountRow key={account.key} account={account} />)
          )}
        </ListPanel>

        <ListPanel title="Policy" meta={`Updated ${formatDate(summary.updatedAt)}`}>
          <PolicyRow summary={summary} lastRefreshAt={lastRefreshAt} />
        </ListPanel>
      </section>

      <section className="grid gap-3 2xl:grid-cols-[0.95fr_1.05fr]">
        <ListPanel
          title="Copy Sources"
          meta={`${formatInteger(monitoredSlotCount)} monitored, ${formatInteger(waitingSlotCount)} waiting for slot`}
        >
          {monitoredSources.length === 0 ? (
            <EmptyState text="No monitored sources." />
          ) : (
            monitoredSources.map((source) => <SourceRow key={source.sourceWallet} source={source} />)
          )}
        </ListPanel>

        <ListPanel title="Open Positions" meta={`${formatInteger(summary.positions.length)} open`}>
          {summary.positions.length === 0 ? (
            <EmptyState text="No open paper positions." />
          ) : (
            summary.positions.map((position) => (
              <PositionRow
                key={position.id}
                isClosing={closingPositionId === position.id}
                onClose={handleManualClose}
                position={position}
              />
            ))
          )}
        </ListPanel>
      </section>

      <section className="grid gap-3 2xl:grid-cols-[0.95fr_1.05fr]">
        <ListPanel title="Wallet PnL History" meta={`${formatInteger(walletHistory.length)} traded sources`}>
          {walletHistory.length === 0 ? (
            <EmptyState text="No wallet trading history yet." />
          ) : (
            walletHistory.map((wallet) => <WalletHistoryRow key={wallet.sourceWallet} wallet={wallet} />)
          )}
        </ListPanel>

        <ListPanel title="Closed Trade History" meta={`${formatInteger(summary.closedTrades.length)} closed trades`}>
          {summary.closedTrades.length === 0 ? (
            <EmptyState text="No closed paper trades yet." />
          ) : (
            summary.closedTrades.map((trade) => <ClosedTradeRow key={trade.id} trade={trade} />)
          )}
        </ListPanel>
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
    <article className={`rounded-md border px-3 py-2 shadow-sm ${toneClass}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
        <Icon className="h-4 w-4 shrink-0 text-[#5b6770]" aria-hidden="true" />
      </div>
      <p className="mt-1 truncate text-lg font-semibold text-ink">{value}</p>
      <p className="mt-1 truncate text-xs text-[#5b6770]">{detail}</p>
    </article>
  );
}

function ListPanel({
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
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {meta ? <p className="text-xs text-[#5b6770]">{meta}</p> : null}
      </div>
      <div>{children}</div>
    </section>
  );
}

function AccountRow({ account }: { account: PaperTradingAccount }) {
  return (
    <ListRow>
      <div className="grid gap-2 sm:grid-cols-[1.2fr_repeat(5,minmax(0,1fr))] sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold text-ink">{account.label}</p>
            <StatusPill label={account.enabled ? "enabled" : "disabled"} tone={account.enabled ? "positive" : "warning"} />
          </div>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{account.key}</p>
        </div>
        <RowStat label="Equity" value={formatCurrency(accountNetEquity(account))} />
        <RowStat label="Total" value={formatCurrency(account.totalPnlUsd)} tone={numberValue(account.totalPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Realized" value={formatCurrency(account.realizedPnlUsd)} tone={numberValue(account.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Unrealized" value={formatCurrency(account.unrealizedPnlUsd)} tone={numberValue(account.unrealizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Open" value={`${formatCurrency(account.openMarginUsd)} / ${formatInteger(account.openPositionCount)}`} />
      </div>
    </ListRow>
  );
}

function PolicyRow({
  lastRefreshAt,
  summary,
}: {
  lastRefreshAt: Date | null;
  summary: PaperTradingSummaryResponse;
}) {
  return (
    <ListRow>
      <div className="grid gap-2 sm:grid-cols-3">
        <RowStat label="Wallets" value={formatInteger(summary.policy.topWalletCount)} />
        <RowStat label="Pocket" value={formatPercent(summary.policy.standardAllocationPct)} />
        <RowStat label="Total cap" value={formatPercent(summary.policy.maxTotalAllocationPct)} />
        <RowStat label="Min order" value={formatCurrency(summary.policy.minOrderNotionalUsd)} />
        <RowStat label="Fee" value={formatPercent(summary.policy.feeRate)} />
        <RowStat label="Slippage" value={formatBps(summary.policy.slippageBps)} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#5b6770]">
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Last refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
        <span className="font-mono">{formatInteger(PAPER_REFRESH_MS)} ms polling</span>
      </div>
    </ListRow>
  );
}

function SourceRow({ source }: { source: MonitoredSource }) {
  const usedPct = clampPercent(numberValue(source.pocketUsedPct ?? 0));
  const monitorTone = source.monitorStatus === "monitored" ? "positive" : "neutral";
  const sourceTone =
    source.sourceStatus === "trading"
      ? "positive"
      : source.sourceStatus === "retained"
        ? "warning"
        : "neutral";
  return (
    <ListRow>
      <div className="grid gap-2 lg:grid-cols-[1.05fr_0.8fr_1.15fr_0.9fr] lg:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/wallets/${source.sourceWallet}`}
              className="truncate font-mono text-xs font-semibold text-ink hover:text-[#297c73]"
            >
              {shortAddress(source.sourceWallet)}
            </Link>
            <StatusPill label={source.monitorStatus} tone={monitorTone} />
            <StatusPill label={formatSourceStatus(source.sourceStatus)} tone={sourceTone} />
          </div>
          <p className="mt-1 text-xs text-[#5b6770]">
            {source.rank ? `#${source.rank}` : "unranked"}, {formatScore(source.score)} score, {formatInteger(source.accountCount)} accounts, {sourceStatusDetail(source.sourceStatus)}
          </p>
        </div>
        <RowStat label="PnL" value={formatCurrency(source.totalPnlUsd)} tone={numberValue(source.totalPnlUsd) >= 0 ? "positive" : "danger"} />
        <div className="min-w-0">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="font-medium uppercase text-[#5b6770]">Pocket</span>
            <span className="font-mono text-ink">{formatPercent(source.pocketUsedPct)}</span>
          </div>
          <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-[#e8edf2]">
            <div
              className={`h-full ${usedPct >= 0.9 ? "bg-danger" : usedPct >= 0.7 ? "bg-warning" : "bg-positive"}`}
              style={{ width: `${Math.min(usedPct * 100, 100)}%` }}
            />
          </div>
          <p className="mt-1 truncate text-xs text-[#5b6770]">
            {formatCurrency(source.openMarginUsd)} used, {formatCurrency(source.remainingAllocationUsd)} free
          </p>
        </div>
        <RowStat
          label="Allocation"
          value={formatCurrency(source.allocationUsd)}
          detail={`${formatPercent(source.allocationPct)} pocket, ${formatInteger(source.openPositionCount)} open`}
        />
      </div>
    </ListRow>
  );
}

function PositionRow({
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
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.15fr_0.7fr_0.85fr_0.85fr_0.85fr_auto] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-ink">{position.coin}</p>
            <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
            <span className="font-mono text-xs text-[#5b6770]">{formatLeverage(position.leverage)}</span>
          </div>
          <Link
            href={`/wallets/${position.sourceWallet}`}
            className="mt-1 block truncate font-mono text-xs text-ink hover:text-[#297c73]"
          >
            {shortAddress(position.sourceWallet)}
          </Link>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{position.accountKey}</p>
        </div>
        <RowStat label="Unrealized" value={formatCurrency(position.unrealizedPnlUsd)} detail={formatPercent(position.unrealizedPnlPct)} tone={unrealizedPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Margin" value={formatCurrency(position.marginUsd)} detail={`${formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)} notional`} />
        <RowStat label="Entry" value={formatPrice(position.entryPrice)} detail={`size ${formatSize(position.size)}`} />
        <RowStat label="Mark" value={formatPrice(position.markPrice)} detail={formatDate(position.priceUpdatedAt)} />
        <button
          type="button"
          onClick={() => onClose(position)}
          disabled={!canClose || isClosing}
          title={canClose ? "Close paper position" : "Execution price unavailable"}
          className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-2.5 text-xs font-semibold text-danger shadow-sm hover:bg-[#ffe6e2] disabled:cursor-not-allowed disabled:border-line disabled:bg-[#f7f9fb] disabled:text-[#98a2b3]"
        >
          {isClosing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <XCircle className="h-3.5 w-3.5" aria-hidden="true" />}
          Close
        </button>
      </div>
    </ListRow>
  );
}

function WalletHistoryRow({ wallet }: { wallet: PaperWalletPerformance }) {
  const totalPnl = numberValue(wallet.totalPnlUsd);
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_repeat(5,minmax(0,0.75fr))] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href={`/wallets/${wallet.sourceWallet}`}
              className="truncate font-mono text-xs font-semibold text-ink hover:text-[#297c73]"
            >
              {shortAddress(wallet.sourceWallet)}
            </Link>
            <StatusPill
              label={wallet.openPositionCount > 0 ? "trading" : wallet.active ? "monitored" : "history"}
              tone={wallet.openPositionCount > 0 ? "positive" : wallet.active ? "neutral" : "warning"}
            />
          </div>
          <p className="mt-1 text-xs text-[#5b6770]">
            {wallet.rank ? `#${wallet.rank}` : "unranked"}, {formatScore(wallet.score)} score
          </p>
        </div>
        <RowStat label="Total" value={formatCurrency(wallet.totalPnlUsd)} tone={totalPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Realized" value={formatCurrency(wallet.realizedPnlUsd)} tone={numberValue(wallet.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Unrealized" value={formatCurrency(wallet.unrealizedPnlUsd)} tone={numberValue(wallet.unrealizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Open" value={formatCurrency(wallet.openMarginUsd)} detail={`${formatInteger(wallet.openPositionCount)} positions`} />
        <RowStat label="Fills" value={`${formatInteger(wallet.copiedFillCount)} / ${formatInteger(wallet.skippedFillCount)}`} detail={formatDate(wallet.lastFillAt)} />
      </div>
    </ListRow>
  );
}

function ClosedTradeRow({ trade }: { trade: PaperClosedTrade }) {
  const netPnl = numberValue(trade.netPnlUsd);
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_0.85fr_0.75fr_0.85fr_0.85fr_0.75fr] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-ink">{trade.coin}</p>
            {trade.side ? <StatusPill label={trade.side} tone={trade.side === "long" ? "positive" : "warning"} /> : null}
            <span className="text-xs text-[#5b6770]">{formatCloseType(trade.closeType)}</span>
          </div>
          <Link
            href={`/wallets/${trade.sourceWallet}`}
            className="mt-1 block truncate font-mono text-xs text-ink hover:text-[#297c73]"
          >
            {shortAddress(trade.sourceWallet)}
          </Link>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{trade.accountKey}</p>
        </div>
        <RowStat label="Net PnL" value={formatCurrency(trade.netPnlUsd)} detail={`${formatCurrency(trade.realizedPnlUsd)} realized`} tone={netPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Closed" value={formatDate(trade.closedAt)} />
        <RowStat label="Exit" value={formatPrice(trade.exitPrice)} detail={`size ${formatSize(trade.size)}`} />
        <RowStat label="Notional" value={formatCurrency(trade.notionalUsd)} detail={`${formatCurrency(trade.marginUsd)} margin`} />
        <RowStat label="Fee" value={formatCurrency(trade.feeUsd)} detail={formatLeverage(trade.leverage)} />
      </div>
    </ListRow>
  );
}

function ListRow({ children }: { children: ReactNode }) {
  return <div className="border-b border-line px-3 py-2 last:border-b-0">{children}</div>;
}

function RowStat({
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
      <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-xs font-semibold ${valueClass}`}>{value}</p>
      {detail ? <p className="mt-0.5 truncate text-[11px] text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="px-3 py-6 text-center text-sm text-[#5b6770]">
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
      const hasRealtimeSlot = allocations.some((allocation) => allocation.hasRealtimeSlot);
      const canOpenNewPositions = allocations.some((allocation) => allocation.canOpenNewPositions);
      const monitorStatus: MonitoredSource["monitorStatus"] = hasRealtimeSlot
        ? "monitored"
        : "waiting";
      const sourceStatus = resolveSourceStatus(allocations, openPositions.length);
      return {
        sourceWallet: source,
        rank: minNumber(allocations.map((allocation) => allocation.rank)),
        score: firstString(allocations.map((allocation) => allocation.score)) ?? wallet?.score ?? null,
        monitorStatus,
        sourceStatus,
        hasRealtimeSlot,
        canOpenNewPositions,
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
      if (left.sourceStatus !== right.sourceStatus) {
        return statusOrder(left.sourceStatus) - statusOrder(right.sourceStatus);
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

function countSourcesByMonitorStatus(
  sources: MonitoredSource[],
  status: MonitoredSource["monitorStatus"],
) {
  return sources.filter((source) => source.monitorStatus === status).length;
}

function countSourcesBySourceStatus(
  sources: MonitoredSource[],
  status: MonitoredSource["sourceStatus"],
) {
  return sources.filter((source) => source.sourceStatus === status).length;
}

function resolveSourceStatus(
  allocations: PaperCopyAllocation[],
  openPositionCount: number,
): MonitoredSource["sourceStatus"] {
  const sourceStatus = allocations.find((allocation) => allocation.sourceStatus)?.sourceStatus;
  if (sourceStatus) {
    return sourceStatus;
  }
  const hasRealtimeSlot = allocations.some((allocation) => allocation.hasRealtimeSlot);
  const canOpenNewPositions = allocations.some((allocation) => allocation.canOpenNewPositions);
  if (!hasRealtimeSlot) {
    return "waiting_for_slot";
  }
  if (openPositionCount > 0 && canOpenNewPositions) {
    return "trading";
  }
  if (openPositionCount > 0) {
    return "retained";
  }
  return "waiting_for_trades";
}

function formatSourceStatus(status: MonitoredSource["sourceStatus"]) {
  if (status === "waiting_for_slot") {
    return "waiting for slot";
  }
  if (status === "waiting_for_trades") {
    return "waiting for trades";
  }
  return status;
}

function sourceStatusDetail(status: MonitoredSource["sourceStatus"]) {
  if (status === "trading") {
    return "active slot";
  }
  if (status === "retained") {
    return "existing exposure only";
  }
  if (status === "waiting_for_trades") {
    return "ready for new entries";
  }
  return "waiting for slot";
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

function statusOrder(status: MonitoredSource["sourceStatus"]) {
  if (status === "trading") {
    return 0;
  }
  if (status === "retained") {
    return 1;
  }
  if (status === "waiting_for_trades") {
    return 2;
  }
  return 3;
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

function formatCloseType(value: string) {
  if (value === "flip_close") {
    return "flip close";
  }
  return value;
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
