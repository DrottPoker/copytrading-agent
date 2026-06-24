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
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
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
  PaperCopyFill,
  PaperPosition,
  PaperTradingAccount,
  PaperTradingSummaryResponse,
  PaperWalletPerformance,
} from "@/types/paper";

import { HeaderRefresh } from "./HeaderRefresh";
import { PageTopPanel } from "./PageTopPanel";
import { StatusPill } from "./StatusPill";

const PAPER_REFRESH_MS = 4000;
const HISTORY_PAGE_SIZE = 10;

type Tone = "positive" | "warning" | "danger" | "neutral";

type MonitoredSource = {
  sourceWallet: string;
  sourceLabel: string | null;
  rank: number | null;
  poolRank: number | null;
  score: string | null;
  monitorStatus: "monitored" | "waiting";
  sourceStatus: "trading" | "retained" | "waiting_for_trades" | "waiting_for_slot";
  sourceStatusReason: string | null;
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
  const [closingSourceWallet, setClosingSourceWallet] = useState<string | null>(null);
  const [resettingAccountKey, setResettingAccountKey] = useState<string | null>(null);
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
          setActionError(await responseError(response, "Manual close failed"));
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

  const handleCloseSource = useCallback(
    async (source: MonitoredSource) => {
      if (closingSourceWallet || closingPositionId || source.openPositionCount <= 0) {
        return;
      }
      const sourceName = sourceDisplayName(source.sourceLabel, source.sourceWallet);
      const confirmed = window.confirm(
        `Close all ${formatInteger(source.openPositionCount)} open paper positions for ${sourceName}?`,
      );
      if (!confirmed) {
        return;
      }

      setClosingSourceWallet(source.sourceWallet);
      setActionError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/paper-trading/sources/${encodeURIComponent(source.sourceWallet)}/close`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, "Source close failed"));
          return;
        }
        const payload = (await response.json()) as PaperTradingSummaryResponse;
        setSummary(payload);
        setLastRefreshAt(new Date());
        setConnectionState("live");
      } catch {
        setConnectionState("offline");
        setActionError("Source close failed.");
      } finally {
        setClosingSourceWallet(null);
      }
    },
    [closingPositionId, closingSourceWallet],
  );

  const handleResetAccount = useCallback(
    async (account: PaperTradingAccount) => {
      if (resettingAccountKey) {
        return;
      }
      const confirmed = window.confirm(
        `Reset ${account.label} balance to ${formatCurrency(account.startingBalanceUsd)}? Open positions and fill history will stay.`,
      );
      if (!confirmed) {
        return;
      }

      setResettingAccountKey(account.key);
      setActionError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/paper-trading/accounts/${encodeURIComponent(account.key)}/reset`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, "Account reset failed"));
          return;
        }
        const payload = (await response.json()) as PaperTradingSummaryResponse;
        setSummary(payload);
        setLastRefreshAt(new Date());
        setConnectionState("live");
      } catch {
        setConnectionState("offline");
        setActionError("Account reset failed.");
      } finally {
        setResettingAccountKey(null);
      }
    },
    [resettingAccountKey],
  );

  const metrics = useMemo(() => buildMetrics(summary), [summary]);
  const monitoredSources = useMemo(() => buildMonitoredSources(summary), [summary]);
  const walletHistory = useMemo(() => buildWalletHistory(summary.walletPerformance), [summary.walletPerformance]);
  const tradingSourceCount = countSourcesWithOpenPositions(summary.positions);
  const monitoredSlotCount = countSourcesByMonitorStatus(monitoredSources, "monitored");
  const waitingSlotCount = countSourcesBySourceStatus(monitoredSources, "waiting_for_slot");

  return (
    <>
      <PageTopPanel
        eyebrow="Paper execution cockpit"
        icon={BarChart3}
        title="Paper Trading"
        actions={
          <>
            <HeaderRefresh
              isRefreshing={connectionState === "refreshing"}
              label={`Updated ${formatDate(summary.updatedAt)}`}
              onRefresh={refresh}
              title="Refresh paper trading data"
            />
            <StatusPill
              label={summary.policy.enabled ? "paper copy enabled" : "paper copy disabled"}
              tone={summary.policy.enabled ? "positive" : "warning"}
            />
            <StatusPill label={marketStatusLabel(summary.marketDataStatus)} tone={marketStatusTone(summary.marketDataStatus)} />
            {connectionState === "offline" ? <StatusPill label="offline" tone="danger" /> : null}
          </>
        }
      />

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
            summary.accounts.map((account) => (
              <AccountRow
                key={account.key}
                account={account}
                isResetting={resettingAccountKey === account.key}
                onReset={handleResetAccount}
              />
            ))
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
            monitoredSources.map((source) => (
              <SourceRow
                key={source.sourceWallet}
                isClosing={closingSourceWallet === source.sourceWallet}
                onCloseSource={handleCloseSource}
                source={source}
              />
            ))
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
        <PaginatedListPanel
          emptyText="No wallet trading history yet."
          getKey={(wallet) => wallet.sourceWallet}
          items={walletHistory}
          meta={`${formatInteger(walletHistory.length)} traded sources`}
          renderItem={(wallet) => <WalletHistoryRow wallet={wallet} />}
          title="Wallet PnL History"
        />

        <PaginatedListPanel
          emptyText="No closed paper trades yet."
          getKey={(trade) => trade.id}
          items={summary.closedTrades}
          meta={`${formatInteger(summary.closedTrades.length)} closed trades`}
          renderItem={(trade) => <ClosedTradeRow trade={trade} />}
          title="Closed Trade History"
        />
      </section>

      <section>
        <PaginatedListPanel
          emptyText="No paper fills recorded yet."
          getKey={(fill) => fill.id}
          items={summary.recentFills}
          meta={`${formatInteger(summary.recentFills.length)} recent fills`}
          renderItem={(fill) => <PaperFillRow fill={fill} />}
          title="Recent Fills"
        />
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

function PaginatedListPanel<T>({
  emptyText,
  getKey,
  items,
  meta,
  renderItem,
  title,
}: {
  emptyText: string;
  getKey: (item: T) => string;
  items: T[];
  meta: string;
  renderItem: (item: T) => ReactNode;
  title: string;
}) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / HISTORY_PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);

  useEffect(() => {
    if (page !== safePage) {
      setPage(safePage);
    }
  }, [page, safePage]);

  const visibleItems = items.slice(
    safePage * HISTORY_PAGE_SIZE,
    safePage * HISTORY_PAGE_SIZE + HISTORY_PAGE_SIZE,
  );

  return (
    <ListPanel title={title} meta={meta}>
      {items.length === 0 ? (
        <EmptyState text={emptyText} />
      ) : (
        <>
          {visibleItems.map((item) => (
            <Fragment key={getKey(item)}>{renderItem(item)}</Fragment>
          ))}
          <PaginationControls
            page={safePage}
            pageCount={pageCount}
            onNext={() => setPage((current) => Math.min(current + 1, pageCount - 1))}
            onPrevious={() => setPage((current) => Math.max(current - 1, 0))}
          />
        </>
      )}
    </ListPanel>
  );
}

function PaginationControls({
  onNext,
  onPrevious,
  page,
  pageCount,
}: {
  onNext: () => void;
  onPrevious: () => void;
  page: number;
  pageCount: number;
}) {
  if (pageCount <= 1) {
    return null;
  }

  return (
    <div className="flex items-center justify-between gap-3 border-t border-line px-3 py-2">
      <button
        type="button"
        onClick={onPrevious}
        disabled={page === 0}
        className="inline-flex h-8 items-center rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink hover:bg-[#f7f9fb] disabled:cursor-not-allowed disabled:opacity-50"
      >
        Previous
      </button>
      <p className="text-xs text-[#5b6770]">
        Page {formatInteger(page + 1)} of {formatInteger(pageCount)}
      </p>
      <button
        type="button"
        onClick={onNext}
        disabled={page >= pageCount - 1}
        className="inline-flex h-8 items-center rounded-md border border-line bg-white px-3 text-xs font-semibold text-ink hover:bg-[#f7f9fb] disabled:cursor-not-allowed disabled:opacity-50"
      >
        Next
      </button>
    </div>
  );
}

function AccountRow({
  account,
  isResetting,
  onReset,
}: {
  account: PaperTradingAccount;
  isResetting: boolean;
  onReset: (account: PaperTradingAccount) => void;
}) {
  return (
    <ListRow>
      <div className="grid gap-2 sm:grid-cols-[1.2fr_repeat(5,minmax(0,1fr))_auto] sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink">{account.label}</p>
            <StatusPill label={account.enabled ? "enabled" : "disabled"} tone={account.enabled ? "positive" : "warning"} />
          </div>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{account.key}</p>
        </div>
        <RowStat label="Equity" value={formatCurrency(accountNetEquity(account))} />
        <RowStat label="Total" value={formatCurrency(account.totalPnlUsd)} tone={numberValue(account.totalPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Realized" value={formatCurrency(account.realizedPnlUsd)} tone={numberValue(account.realizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Unrealized" value={formatCurrency(account.unrealizedPnlUsd)} tone={numberValue(account.unrealizedPnlUsd) >= 0 ? "positive" : "danger"} />
        <RowStat label="Open" value={`${formatCurrency(account.openMarginUsd)} / ${formatInteger(account.openPositionCount)}`} />
        <button
          type="button"
          onClick={() => onReset(account)}
          disabled={isResetting}
          title={`Reset ${account.label} balance`}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-white text-[#344054] hover:bg-[#f7f9fb] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isResetting ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          )}
          <span className="sr-only">Reset account balance</span>
        </button>
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
        <RowStat label="Latency" value={`${formatInteger(summary.policy.latencyMs)} ms`} />
        <RowStat label="Max drift" value={formatBps(summary.policy.maxPriceDriftBps)} />
        <RowStat
          label="Price cache"
          value={summary.policy.marketPriceCacheEnabled ? "enabled" : "disabled"}
          detail={`${formatInteger(summary.policy.marketPriceCacheStaleSeconds)}s stale`}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#5b6770]">
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Last refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
        <span className="font-mono">{formatInteger(PAPER_REFRESH_MS)} ms polling</span>
      </div>
    </ListRow>
  );
}

function SourceRow({
  isClosing,
  onCloseSource,
  source,
}: {
  isClosing: boolean;
  onCloseSource: (source: MonitoredSource) => void;
  source: MonitoredSource;
}) {
  const usedPct = clampPercent(numberValue(source.pocketUsedPct ?? 0));
  const monitorTone = source.monitorStatus === "monitored" ? "positive" : "neutral";
  const sourceTone =
    source.sourceStatus === "trading"
      ? "positive"
      : source.sourceStatus === "retained"
        ? "warning"
        : "neutral";
  const sourceMeta = `${formatPoolRank(source.poolRank)}, ${formatScore(source.score)} score, ${formatInteger(
    source.accountCount,
  )} accounts`;
  const sourceDetail = sourceStatusDetail(source);
  return (
    <ListRow>
      <div className="grid gap-2 py-0.5">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-1 gap-y-0.5">
              <Link
                href={`/wallets/${source.sourceWallet}`}
                className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold leading-5 text-ink hover:text-[#297c73]"
              >
                {sourceDisplayName(source.sourceLabel, source.sourceWallet)}
              </Link>
              <CompactSourcePill label={source.monitorStatus} tone={monitorTone} />
              <CompactSourcePill label={formatSourceStatus(source.sourceStatus)} tone={sourceTone} />
            </div>
            <p className="mt-0.5 whitespace-normal break-words font-mono text-[11px] leading-4 text-[#5b6770]">
              {shortAddress(source.sourceWallet)} | {sourceMeta}
            </p>
            {sourceDetail !== "active slot" ? (
              <p className="mt-0.5 whitespace-normal break-words text-[11px] leading-4 text-[#5b6770]">
                {sourceDetail}
              </p>
            ) : null}
          </div>
          {source.openPositionCount > 0 ? (
            <button
              type="button"
              onClick={() => onCloseSource(source)}
              disabled={isClosing}
              title="Close all open paper positions for this source"
              className="inline-flex min-h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-2 py-1 text-xs font-semibold text-danger shadow-sm hover:bg-[#ffe6e2] disabled:cursor-not-allowed disabled:border-line disabled:bg-[#f7f9fb] disabled:text-[#98a2b3]"
            >
              {isClosing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Close all
            </button>
          ) : null}
        </div>
        <div className="grid min-w-0 gap-x-4 gap-y-1 sm:grid-cols-2 xl:grid-cols-4">
          <CompactSourceStat
            label="Realized"
            value={formatCurrency(source.realizedPnlUsd)}
            tone={numberValue(source.realizedPnlUsd) >= 0 ? "positive" : "danger"}
          />
          <CompactSourceStat
            label="Unrealized"
            value={formatCurrency(source.unrealizedPnlUsd)}
            detail={`Total ${formatCurrency(source.totalPnlUsd)}`}
            tone={numberValue(source.unrealizedPnlUsd) >= 0 ? "positive" : "danger"}
          />
          <SourcePocketStat
            remaining={formatCurrency(source.remainingAllocationUsd)}
            tone={usedPct >= 0.9 ? "danger" : usedPct >= 0.7 ? "warning" : "positive"}
            used={formatCurrency(source.openMarginUsd)}
            usedPct={usedPct}
            value={formatPercent(source.pocketUsedPct)}
          />
          <CompactSourceStat
            label="Allocation"
            value={formatCurrency(source.allocationUsd)}
            detail={`${formatPercent(source.allocationPct)} pocket, ${formatInteger(source.openPositionCount)} open`}
          />
        </div>
      </div>
    </ListRow>
  );
}

const compactSourcePillClasses: Record<Tone, string> = {
  positive: "border-[#a7d8c4] bg-[#eefaf5] text-positive",
  warning: "border-[#f0c36d] bg-[#fff8e8] text-warning",
  danger: "border-[#f2aaa5] bg-[#fff2f0] text-danger",
  neutral: "border-line bg-[#f7f9fb] text-[#344054]",
};

function CompactSourcePill({ label, tone = "neutral" }: { label: string; tone?: Tone }) {
  return (
    <span
      className={`inline-flex h-5 max-w-full items-center whitespace-nowrap rounded-md border px-1.5 text-[11px] font-medium leading-none ${
        compactSourcePillClasses[tone]
      }`}
    >
      {label}
    </span>
  );
}

function CompactSourceStat({
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
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
        <p className="text-[10px] font-medium uppercase leading-4 text-[#5b6770]">
          {label}
        </p>
        <p className={`whitespace-normal break-words font-mono text-xs font-semibold leading-4 ${valueClass}`}>
          {value}
        </p>
      </div>
      {detail ? (
        <p className="whitespace-normal break-words text-[11px] leading-4 text-[#5b6770]">
          {detail}
        </p>
      ) : null}
    </div>
  );
}

function SourcePocketStat({
  remaining,
  tone,
  used,
  usedPct,
  value,
}: {
  remaining: string;
  tone: "positive" | "warning" | "danger";
  used: string;
  usedPct: number;
  value: string;
}) {
  const barClass =
    tone === "danger" ? "bg-danger" : tone === "warning" ? "bg-warning" : "bg-positive";
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5">
        <p className="text-[10px] font-medium uppercase leading-4 text-[#5b6770]">
          Pocket
        </p>
        <p className="whitespace-normal break-words font-mono text-xs font-semibold leading-4 text-ink">
          {value}
        </p>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[#e8edf2]">
        <div
          className={`h-full ${barClass}`}
          style={{ width: `${Math.min(usedPct * 100, 100)}%` }}
        />
      </div>
      <p className="whitespace-normal break-words text-[11px] leading-4 text-[#5b6770]">
        {used} used, {remaining} free
      </p>
    </div>
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
      <div className="grid gap-2 xl:grid-cols-[1.15fr_0.7fr_0.8fr_0.8fr_0.75fr_0.8fr_auto] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{position.coin}</p>
            <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
            <span className="font-mono text-xs text-[#5b6770]">{formatLeverage(position.leverage)}</span>
          </div>
          <Link
            href={`/wallets/${position.sourceWallet}`}
            className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
          >
            {sourceDisplayName(position.sourceLabel, position.sourceWallet)}
          </Link>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {shortAddress(position.sourceWallet)} | {position.accountKey}
          </p>
        </div>
        <RowStat label="Unrealized" value={formatCurrency(position.unrealizedPnlUsd)} detail={formatPercent(position.unrealizedPnlPct)} tone={unrealizedPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Margin" value={formatCurrency(position.marginUsd)} detail={`${formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)} notional`} />
        <RowStat label="Entry" value={formatPrice(position.entryPrice)} detail={`size ${formatSize(position.size)}`} />
        <RowStat label="Execution" value={formatExecutionMs(position.entryExecutionDelayMs)} detail="source to open" />
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
  const isMonitored = wallet.monitorStatus === "monitored";
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_repeat(5,minmax(0,0.75fr))] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <Link
              href={`/wallets/${wallet.sourceWallet}`}
              className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-[#297c73]"
            >
              {sourceDisplayName(wallet.sourceLabel, wallet.sourceWallet)}
            </Link>
            <StatusPill
              label={isMonitored ? "monitored" : "history"}
              tone={isMonitored ? "positive" : "neutral"}
            />
          </div>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {shortAddress(wallet.sourceWallet)}
          </p>
          <p className="mt-1 text-xs text-[#5b6770]">
            {formatPoolRank(wallet.poolRank)}, {formatScore(wallet.score)} score
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
      <div className="grid gap-2 xl:grid-cols-[1.05fr_0.85fr_0.8fr_0.85fr_0.85fr_0.75fr] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{trade.coin}</p>
            {trade.side ? <StatusPill label={trade.side} tone={trade.side === "long" ? "positive" : "warning"} /> : null}
            {trade.isSourceLiquidation ? <StatusPill label="liquidation" tone="danger" /> : null}
            <span className="text-xs text-[#5b6770]">{formatCloseType(trade.closeType)}</span>
          </div>
          <Link
            href={`/wallets/${trade.sourceWallet}`}
            className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
          >
            {sourceDisplayName(trade.sourceLabel, trade.sourceWallet)}
          </Link>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {shortAddress(trade.sourceWallet)} | {trade.accountKey}
          </p>
        </div>
        <RowStat label="Net PnL" value={formatCurrency(trade.netPnlUsd)} detail={`${formatCurrency(trade.realizedPnlUsd)} realized`} tone={netPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Closed" value={formatShortDateTime(trade.closedAt)} detail={formatTradeDuration(trade.durationMs)} />
        <RowStat label="Exit" value={formatPrice(trade.exitPrice)} detail={`size ${formatSize(trade.size)}`} />
        <RowStat label="Notional" value={formatCurrency(trade.notionalUsd)} detail={`${formatCurrency(trade.marginUsd)} margin`} />
        <RowStat label="Fee" value={formatCurrency(trade.feeUsd)} detail={formatLeverage(trade.leverage)} />
      </div>
    </ListRow>
  );
}

function PaperFillRow({ fill }: { fill: PaperCopyFill }) {
  const realizedPnl = numberValue(fill.realizedPnlUsd);
  const actionTone: Tone =
    fill.action === "skip" ? "warning" : fill.action.includes("close") ? "neutral" : "positive";

  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_0.7fr_0.85fr_0.85fr_0.85fr_0.9fr] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <Link
              href={`/wallets/${fill.sourceWallet}`}
              className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-[#297c73]"
            >
              {sourceDisplayName(fill.sourceLabel, fill.sourceWallet)}
            </Link>
            <StatusPill label={fill.action} tone={actionTone} />
            {fill.side ? (
              <StatusPill label={fill.side} tone={fill.side === "long" ? "positive" : "warning"} />
            ) : null}
          </div>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {shortAddress(fill.sourceWallet)} | {fill.accountKey}
          </p>
        </div>
        <RowStat label="Market" value={fill.coin} detail={formatDate(fill.filledAt)} />
        <RowStat label="Notional" value={formatCurrency(fill.notionalUsd)} detail={`${formatCurrency(fill.marginUsd)} margin`} />
        <RowStat label="Realized" value={formatCurrency(fill.realizedPnlUsd)} tone={realizedPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Price" value={formatPrice(fill.price)} detail={fillPriceDetail(fill)} />
        <RowStat
          label={fill.skippedReason ? "Skip reason" : "Fee"}
          value={fill.skippedReason ? reasonLabel(fill.skippedReason) : formatCurrency(fill.feeUsd)}
          detail={fill.skippedReason ? fillSkipDetail(fill) : formatLeverage(fill.leverage)}
        />
      </div>
    </ListRow>
  );
}

function fillPriceDetail(fill: PaperCopyFill) {
  const parts = [
    fill.sourcePrice ? `src ${formatPrice(fill.sourcePrice)}` : null,
    fill.observedPrice ? `live ${formatPrice(fill.observedPrice)}` : null,
    fill.size ? `size ${formatSize(fill.size)}` : null,
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(" | ") : "size -";
}

function fillSkipDetail(fill: PaperCopyFill) {
  const parts = [
    fill.priceDriftBps ? `drift ${formatBps(fill.priceDriftBps)}` : null,
    fill.maxPriceDriftBps ? `max ${formatBps(fill.maxPriceDriftBps)}` : null,
    formatLeverage(fill.leverage),
  ].filter((item) => item && item !== "-");
  return parts.length > 0 ? parts.join(" | ") : "-";
}

function ListRow({ children }: { children: ReactNode }) {
  return <div className="border-b border-line px-3 py-1.5 last:border-b-0">{children}</div>;
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
        sourceLabel:
          firstString(allocations.map((allocation) => allocation.sourceLabel)) ??
          wallet?.sourceLabel ??
          firstString(openPositions.map((position) => position.sourceLabel)) ??
          null,
        rank: minNumber(allocations.map((allocation) => allocation.rank)),
        poolRank: minNumber(allocations.map((allocation) => allocation.poolRank)),
        score: firstString(allocations.map((allocation) => allocation.score)) ?? wallet?.score ?? null,
        monitorStatus,
        sourceStatus,
        sourceStatusReason: resolveSourceStatusReason(allocations),
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
      return (left.poolRank ?? left.rank ?? 9999) - (right.poolRank ?? right.rank ?? 9999);
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
      const realizedDiff = numberValue(right.realizedPnlUsd) - numberValue(left.realizedPnlUsd);
      if (realizedDiff !== 0) {
        return realizedDiff;
      }
      const totalDiff = numberValue(right.totalPnlUsd) - numberValue(left.totalPnlUsd);
      if (totalDiff !== 0) {
        return totalDiff;
      }
      return (left.poolRank ?? 9999) - (right.poolRank ?? 9999);
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

function resolveSourceStatusReason(allocations: PaperCopyAllocation[]) {
  const activeReason = allocations.find((allocation) => allocation.canOpenNewPositions)?.sourceStatusReason;
  if (activeReason) {
    return activeReason;
  }
  const slotReason = allocations.find((allocation) => allocation.hasRealtimeSlot)?.sourceStatusReason;
  if (slotReason) {
    return slotReason;
  }
  return allocations.find((allocation) => allocation.sourceStatusReason)?.sourceStatusReason ?? null;
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

function sourceStatusDetail(source: MonitoredSource) {
  if (source.sourceStatus === "trading") {
    return "active slot";
  }
  if (source.sourceStatus === "retained") {
    return `${formatSourceStatusReason(source.sourceStatusReason)}, existing exposure only`;
  }
  if (source.sourceStatus === "waiting_for_trades") {
    return "ready for new entries";
  }
  return formatSourceStatusReason(source.sourceStatusReason);
}

function formatSourceStatusReason(reason: string | null) {
  if (reason === "outside_copy_top_wallet_count") {
    return "outside copy top 10";
  }
  if (reason === "current_drawdown_blocked") {
    return "drawdown blocked";
  }
  if (reason === "wallet_disabled_or_missing") {
    return "wallet disabled or missing";
  }
  if (reason === "wallet_cooldown") {
    return "wallet in cooldown";
  }
  if (reason === "score_unavailable") {
    return "score unavailable";
  }
  if (reason === "score_not_positive") {
    return "score not positive";
  }
  if (reason === "waiting_for_realtime_slot") {
    return "waiting for realtime slot";
  }
  if (reason === "copy_candidate") {
    return "active copy source";
  }
  if (reason === "active_copy_source") {
    return "active copy source";
  }
  if (reason === "paper_account_disabled") {
    return "paper account disabled";
  }
  if (reason === "allocation_inactive") {
    return "allocation inactive";
  }
  if (reason === "existing_exposure_only") {
    return "existing exposure";
  }
  if (reason === "allocation_missing") {
    return "allocation missing";
  }
  return "existing exposure";
}

function formatPoolRank(rank: number | null | undefined) {
  return rank ? `pool #${formatInteger(rank)}` : "pool unranked";
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

function formatShortDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("sv-SE", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  }).format(new Date(value));
}

function formatTradeDuration(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "duration -";
  }
  const totalMinutes = Math.max(0, Math.round(value / 60_000));
  if (totalMinutes < 60) {
    return `duration ${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 48) {
    return minutes > 0 ? `duration ${hours}h ${minutes}m` : `duration ${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `duration ${days}d ${restHours}h` : `duration ${days}d`;
}

function formatExecutionMs(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 1000) {
    return `${formatInteger(value)} ms`;
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    value / 1000,
  )} s`;
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

function reasonLabel(value: string) {
  return value.replaceAll("_", " ");
}

function sourceDisplayName(label: string | null | undefined, address: string) {
  const trimmed = label?.trim();
  return trimmed || shortAddress(address);
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

async function responseError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    return `${fallback} with HTTP ${response.status}.`;
  }
  return `${fallback} with HTTP ${response.status}.`;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
