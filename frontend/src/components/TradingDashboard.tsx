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
} from "@/types/paper";
import type {
  TradingAccount,
  TradingAccountsResponse,
  TradingClosedTrade,
  TradingFill,
  TradingOrder,
  TradingPosition,
} from "@/types/trading";

import { HeaderRefreshButton, HeaderUpdatedLabel } from "./HeaderRefresh";
import { PageTopPanel } from "./PageTopPanel";
import { StatusPill } from "./StatusPill";

const TRADING_REFRESH_MS = 4000;
const HISTORY_PAGE_SIZE = 10;
const LIVE_EXCHANGE_SOURCE = "__exchange__";
const TRADING_MODE_STORAGE_KEY = "trading-dashboard-mode";

type Tone = "positive" | "warning" | "danger" | "neutral";
type TradingMode = "paper" | "live";

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
  enabledLiveAccountCount: number;
  openPaperPositionCount: number;
  openLivePositionCount: number;
  openPositionCount: number;
  allocationPct: number | null;
  allocationUsd: number;
  openMarginUsd: number;
  openNotionalUsd: number;
  remainingAllocationUsd: number;
  pocketUsedPct: number | null;
  recentLiveFillCount: number;
  recentLiveOrderCount: number;
  realizedPnlUsd: string;
  unrealizedPnlUsd: string;
  totalPnlUsd: string;
  monitoredSeconds: number;
  monitoredHours: string;
  realizedPnlPerMonitoredHourUsd: string | null;
  totalPnlPerMonitoredHourUsd: string | null;
  firstMonitoredAt: string | null;
  currentMonitoringStartedAt: string | null;
  lastMonitoredAt: string | null;
};

type DashboardAccount = {
  key: string;
  label: string;
  accountType: "paper" | "live";
  statusLabel: string;
  statusTone: Tone;
  equityUsd: number;
  totalPnlUsd: number;
  realizedPnlUsd: number;
  unrealizedPnlUsd: number | null;
  openMarginUsd: number;
  openNotionalUsd: number;
  openPositionCount: number;
  paperAccount: PaperTradingAccount | null;
};

type DashboardPosition = {
  id: string;
  accountKey: string;
  accountType: "paper" | "live";
  sourceWallet: string;
  sourceLabel: string | null;
  coin: string;
  side: "long" | "short";
  leverage: string | number | null;
  marginUsd: string | number | null;
  notionalUsd: string | number | null;
  currentNotionalUsd: string | number | null;
  entryPrice: string | number | null;
  size: string | number | null;
  realizedPnlUsd: string | number | null;
  unrealizedPnlUsd: string | number | null;
  unrealizedPnlPct: string | number | null;
  addFillCount: number;
  closeFillCount: number;
  markPrice: string | number | null;
  priceUpdatedAt: string | null;
  entryExecutionDelayMs: number | null;
  updatedAt: string | null;
  paperPosition: PaperPosition | null;
  livePosition: TradingPosition | null;
};

type WalletPerformanceRow = {
  sourceWallet: string;
  sourceLabel: string | null;
  rank: number | null;
  poolRank: number | null;
  score: string | null;
  allocationPct: string | null;
  active: boolean;
  monitorStatus: "monitored" | "history";
  accountCount: number;
  openPositionCount: number;
  copiedFillCount: number;
  skippedFillCount: number;
  realizedPnlUsd: string;
  unrealizedPnlUsd: string;
  totalPnlUsd: string;
  monitoredSeconds: number;
  monitoredHours: string;
  realizedPnlPerMonitoredHourUsd: string | null;
  totalPnlPerMonitoredHourUsd: string | null;
  firstMonitoredAt: string | null;
  currentMonitoringStartedAt: string | null;
  lastMonitoredAt: string | null;
  feeUsd: string;
  openNotionalUsd: string;
  openMarginUsd: string;
  lastFillAt: string | null;
};

type SourceMetadata = {
  allocationPct: number | null;
  label: string | null;
  poolRank: number | null;
  rank: number | null;
  score: string | null;
  monitoredSeconds: number;
  monitoredHours: string;
  realizedPnlPerMonitoredHourUsd: string | null;
  totalPnlPerMonitoredHourUsd: string | null;
  firstMonitoredAt: string | null;
  currentMonitoringStartedAt: string | null;
  lastMonitoredAt: string | null;
};

type RowPill = {
  label: string;
  tone: Tone;
};

type RowIdentity = {
  href: string | null;
  label: string;
  meta: string;
};

type RowStatItem = {
  detail?: string;
  label: string;
  tone?: Tone;
  value: string;
};

type ExecutionActivityItem = {
  id: string;
  identity: RowIdentity;
  pills: RowPill[];
  sortAt: string;
  stats: RowStatItem[];
};

export function TradingDashboard({
  initialSummary,
  initialTradingAccounts,
}: {
  initialSummary: PaperTradingSummaryResponse;
  initialTradingAccounts: TradingAccountsResponse;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [tradingAccounts, setTradingAccounts] = useState(initialTradingAccounts);
  const [connectionState, setConnectionState] = useState<"live" | "refreshing" | "offline">("live");
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(new Date());
  const [closingPositionId, setClosingPositionId] = useState<string | null>(null);
  const [closingSourceWallet, setClosingSourceWallet] = useState<string | null>(null);
  const [resettingAccountKey, setResettingAccountKey] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [tradingMode, setTradingMode] = useState<TradingMode>("paper");

  useEffect(() => {
    const storedMode = window.localStorage.getItem(TRADING_MODE_STORAGE_KEY);
    if (storedMode === "paper" || storedMode === "live") {
      setTradingMode(storedMode);
    }
  }, []);

  const handleModeChange = useCallback((mode: TradingMode) => {
    setTradingMode(mode);
    window.localStorage.setItem(TRADING_MODE_STORAGE_KEY, mode);
  }, []);

  const refresh = useCallback(async () => {
    setConnectionState("refreshing");
    try {
      const [paperResponse, tradingResponse] = await Promise.all([
        fetch(`${getPublicApiBaseUrl()}/paper-trading`, {
          cache: "no-store",
        }),
        fetch(`${getPublicApiBaseUrl()}/trading/accounts`, {
          cache: "no-store",
        }),
      ]);
      if (!paperResponse.ok || !tradingResponse.ok) {
        setConnectionState("offline");
        return;
      }
      const [paperPayload, tradingPayload] = await Promise.all([
        paperResponse.json() as Promise<PaperTradingSummaryResponse>,
        tradingResponse.json() as Promise<TradingAccountsResponse>,
      ]);
      setSummary(paperPayload);
      setTradingAccounts(tradingPayload);
      setLastRefreshAt(new Date());
      setConnectionState("live");
    } catch {
      setConnectionState("offline");
    }
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void refresh();
    }, TRADING_REFRESH_MS);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  const handleManualClose = useCallback(
    async (position: DashboardPosition) => {
      if (closingPositionId) {
        return;
      }
      const isLivePosition = position.livePosition !== null;
      const confirmed = window.confirm(
        `Close ${position.coin} ${position.side} ${isLivePosition ? "live" : "paper"} position for ${position.accountKey}?`,
      );
      if (!confirmed) {
        return;
      }

      setClosingPositionId(position.id);
      setActionError(null);
      try {
        const response = await fetch(
          isLivePosition
            ? `${getPublicApiBaseUrl()}/trading/positions/${position.id}/close`
            : `${getPublicApiBaseUrl()}/paper-trading/positions/${position.id}/close`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, "Manual close failed"));
          return;
        }
        if (isLivePosition) {
          await refresh();
        } else {
          const payload = (await response.json()) as PaperTradingSummaryResponse;
          setSummary(payload);
          setLastRefreshAt(new Date());
          setConnectionState("live");
        }
      } catch {
        setConnectionState("offline");
        setActionError("Manual close failed.");
      } finally {
        setClosingPositionId(null);
      }
    },
    [closingPositionId, refresh],
  );

  const handleCloseSource = useCallback(
    async (source: MonitoredSource) => {
      if (closingSourceWallet || closingPositionId || source.openPaperPositionCount <= 0) {
        return;
      }
      const sourceName = sourceDisplayName(source.sourceLabel, source.sourceWallet);
      const confirmed = window.confirm(
        `Close ${formatInteger(source.openPaperPositionCount)} open paper positions for ${sourceName}?`,
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

  const liveSourcePositions = useMemo(
    () => tradingAccounts.positions.filter((position) => !isLiveExchangePosition(position)),
    [tradingAccounts.positions],
  );
  const liveDisplayPositions = useMemo(
    () => displayLivePositions(tradingAccounts.positions),
    [tradingAccounts.positions],
  );
  const sourceLabels = useMemo(
    () => buildSourceLabels(summary, tradingAccounts),
    [summary, tradingAccounts],
  );
  const sourceMetadata = useMemo(
    () => buildSourceMetadata(summary, tradingAccounts),
    [summary, tradingAccounts],
  );
  const dashboardAccounts = useMemo(
    () =>
      tradingMode === "paper"
        ? buildPaperDashboardAccounts(summary)
        : buildLiveDashboardAccounts(tradingAccounts, liveDisplayPositions),
    [liveDisplayPositions, summary, tradingAccounts, tradingMode],
  );
  const dashboardPositions = useMemo(
    () =>
      tradingMode === "paper"
        ? buildPaperDashboardPositions(summary.positions)
        : buildLiveDashboardPositions(liveDisplayPositions, sourceLabels),
    [liveDisplayPositions, sourceLabels, summary.positions, tradingMode],
  );
  const dashboardFills = useMemo(
    () =>
      tradingMode === "paper"
        ? buildPaperDashboardFills(summary.recentFills)
        : buildLiveDashboardFills(
            tradingAccounts.recentFills,
            tradingAccounts.recentOrders,
            sourceLabels,
          ),
    [
      sourceLabels,
      summary.recentFills,
      tradingAccounts.recentFills,
      tradingAccounts.recentOrders,
      tradingMode,
    ],
  );
  const metrics = useMemo(
    () =>
      tradingMode === "paper"
        ? buildPaperMetrics(summary)
        : buildLiveMetrics(tradingAccounts, liveDisplayPositions),
    [liveDisplayPositions, summary, tradingAccounts, tradingMode],
  );
  const liveExecution = useMemo(
    () => buildLiveExecutionStatus(tradingAccounts),
    [tradingAccounts],
  );
  const monitoredSources = useMemo(
    () =>
      tradingMode === "paper"
        ? buildPaperMonitoredSources(summary)
        : buildLiveMonitoredSources(
            summary,
            tradingAccounts,
            liveExecution.copyingAccountCount,
            liveSourcePositions,
          ),
    [liveExecution.copyingAccountCount, liveSourcePositions, summary, tradingAccounts, tradingMode],
  );
  const walletHistory = useMemo(
    () =>
      tradingMode === "paper"
        ? buildWalletHistory(summary.walletPerformance)
        : buildLiveWalletHistory(
            tradingAccounts.recentFills,
            tradingAccounts.recentOrders,
            liveSourcePositions,
            sourceMetadata,
          ),
    [
      liveSourcePositions,
      sourceMetadata,
      summary.walletPerformance,
      tradingAccounts.recentFills,
      tradingAccounts.recentOrders,
      tradingMode,
    ],
  );
  const closedTrades = useMemo<Array<PaperClosedTrade | TradingClosedTrade>>(
    () =>
      tradingMode === "paper"
        ? summary.closedTrades
        : buildLiveClosedTradeRows(tradingAccounts.closedTrades, sourceLabels),
    [sourceLabels, summary.closedTrades, tradingAccounts.closedTrades, tradingMode],
  );
  const tradingSourceCount = countSourcesWithDashboardOpenPositions(dashboardPositions);
  const monitoredSlotCount = countSourcesByMonitorStatus(monitoredSources, "monitored");
  const waitingSlotCount = countSourcesBySourceStatus(monitoredSources, "waiting_for_slot");
  const updatedAt = latestDateString(summary.updatedAt, tradingAccounts.updatedAt);
  const modeLabel = tradingMode === "paper" ? "Paper trading" : "Live trading";
  const executionStatus: { label: string; tone: Tone } =
    tradingMode === "paper"
      ? {
          label: summary.policy.enabled ? "paper execution enabled" : "paper execution disabled",
          tone: summary.policy.enabled ? "positive" : "warning",
        }
      : liveExecution;

  return (
    <>
      <PageTopPanel
        eyebrow="Execution cockpit"
        icon={BarChart3}
        title="Trading"
        actions={
          <>
            <TradingModeToggle value={tradingMode} onChange={handleModeChange} />
            <HeaderUpdatedLabel label={`Updated ${formatDate(updatedAt)}`} />
            <StatusPill
              label={executionStatus.label}
              tone={executionStatus.tone}
            />
            {tradingMode === "paper" ? (
              <StatusPill label={marketStatusLabel(summary.marketDataStatus)} tone={marketStatusTone(summary.marketDataStatus)} />
            ) : null}
            {connectionState === "offline" ? <StatusPill label="offline" tone="danger" /> : null}
          </>
        }
        refresh={
          <HeaderRefreshButton
            isRefreshing={connectionState === "refreshing"}
            onRefresh={refresh}
            title="Refresh trading data"
          />
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
          detail={
            tradingMode === "live"
              ? `${formatCurrency(metrics.cashEquity)} allocation equity`
              : `${formatCurrency(metrics.cashEquity)} account capital`
          }
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
          detail={`${formatInteger(dashboardPositions.length)} open`}
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
          detail={
            tradingMode === "paper"
              ? `${formatInteger(monitoredSlotCount)} monitored, ${formatInteger(waitingSlotCount)} waiting`
              : `${formatInteger(monitoredSlotCount)} live sources, ${formatInteger(waitingSlotCount)} waiting`
          }
        />
      </section>

      <section className="grid gap-3 xl:grid-cols-[1fr_0.9fr]">
        <ListPanel title={`${modeLabel} Accounts`} meta={`${formatInteger(dashboardAccounts.length)} accounts`}>
          {dashboardAccounts.length === 0 ? (
            <EmptyState text={`No ${tradingMode} trading accounts available.`} />
          ) : (
            dashboardAccounts.map((account) => (
              <AccountRow
                key={account.key}
                account={account}
                isResetting={resettingAccountKey === account.key}
                onReset={handleResetAccount}
              />
            ))
          )}
        </ListPanel>

        <ListPanel
          title={tradingMode === "paper" ? "Paper Policy" : "Live Execution"}
          meta={`Updated ${formatDate(updatedAt)}`}
        >
          <PolicyRow
            lastRefreshAt={lastRefreshAt}
            liveExecution={liveExecution}
            mode={tradingMode}
            summary={summary}
            tradingAccounts={tradingAccounts}
          />
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
                mode={tradingMode}
                onCloseSource={handleCloseSource}
                source={source}
              />
            ))
          )}
        </ListPanel>

        <ListPanel title="Open Positions" meta={`${formatInteger(dashboardPositions.length)} open`}>
          {dashboardPositions.length === 0 ? (
            <EmptyState text="No open positions." />
          ) : (
            dashboardPositions.map((position) => (
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
          emptyText={tradingMode === "paper" ? "No closed paper trades yet." : "No complete closed live trades yet."}
          getKey={(trade) => `${tradingMode}:${trade.id}`}
          items={closedTrades}
          meta={`${formatInteger(closedTrades.length)} closed trades`}
          renderItem={(trade) =>
            tradingMode === "paper" ? (
              <ClosedTradeRow trade={trade as PaperClosedTrade} />
            ) : (
              <LiveClosedTradeRow trade={trade as TradingClosedTrade} />
            )
          }
          title={tradingMode === "paper" ? "Closed Paper Trades" : "Closed Live Trades"}
        />
      </section>

      <section>
        <PaginatedListPanel
          emptyText="No execution activity recorded yet."
          getKey={(fill) => fill.id}
          items={dashboardFills}
          meta={`${formatInteger(dashboardFills.length)} recent items`}
          renderItem={(fill) => <FillRow fill={fill} />}
          title="Recent Execution Activity"
        />
      </section>
    </>
  );
}

function TradingModeToggle({
  onChange,
  value,
}: {
  onChange: (value: TradingMode) => void;
  value: TradingMode;
}) {
  return (
    <div className="inline-flex h-9 overflow-hidden rounded-md border border-line bg-[#f7f9fb] p-0.5">
      {(["paper", "live"] as TradingMode[]).map((mode) => {
        const isActive = value === mode;
        return (
          <button
            key={mode}
            type="button"
            onClick={() => onChange(mode)}
            aria-pressed={isActive}
            className={`inline-flex min-w-24 items-center justify-center rounded-[5px] px-3 text-xs font-semibold transition ${
              isActive
                ? "bg-white text-ink shadow-sm"
                : "text-[#5b6770] hover:bg-white/70 hover:text-ink"
            }`}
          >
            {mode === "paper" ? "Paper" : "Live"}
          </button>
        );
      })}
    </div>
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
  account: DashboardAccount;
  isResetting: boolean;
  onReset: (account: PaperTradingAccount) => void;
}) {
  const paperAccount = account.paperAccount;
  return (
    <ListRow>
      <div className="grid gap-2 sm:grid-cols-[1.2fr_repeat(5,minmax(0,1fr))_auto] sm:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink">{account.label}</p>
            <StatusPill label={account.accountType} tone={account.accountType === "live" ? "positive" : "neutral"} />
            <StatusPill label={account.statusLabel} tone={account.statusTone} />
          </div>
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{account.key}</p>
        </div>
        <RowStat label="Equity" value={formatCurrency(account.equityUsd)} />
        <RowStat label="Total" value={formatCurrency(account.totalPnlUsd)} tone={account.totalPnlUsd >= 0 ? "positive" : "danger"} />
        <RowStat label="Realized" value={formatCurrency(account.realizedPnlUsd)} tone={account.realizedPnlUsd >= 0 ? "positive" : "danger"} />
        <RowStat
          label="Unrealized"
          value={account.unrealizedPnlUsd === null ? "-" : formatCurrency(account.unrealizedPnlUsd)}
          tone={(account.unrealizedPnlUsd ?? 0) >= 0 ? "positive" : "danger"}
        />
        <RowStat label="Open" value={`${formatCurrency(account.openMarginUsd)} / ${formatInteger(account.openPositionCount)}`} />
        {paperAccount ? (
          <button
            type="button"
            onClick={() => onReset(paperAccount)}
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
        ) : (
          <span aria-hidden="true" />
        )}
      </div>
    </ListRow>
  );
}

function PolicyRow({
  lastRefreshAt,
  liveExecution,
  mode,
  summary,
  tradingAccounts,
}: {
  lastRefreshAt: Date | null;
  liveExecution: ReturnType<typeof buildLiveExecutionStatus>;
  mode: TradingMode;
  summary: PaperTradingSummaryResponse;
  tradingAccounts: TradingAccountsResponse;
}) {
  if (mode === "live") {
    const liveAccounts = tradingAccounts.accounts.filter((account) => account.accountType === "live");
    const enabledCount = liveAccounts.filter((account) => account.status === "enabled").length;
    const exitOnlyCount = liveAccounts.filter((account) => account.status === "exit_only").length;
    const network = firstString(liveAccounts.map((account) => account.network)) ?? "-";
    const capitalMode = firstString(liveAccounts.map((account) => account.capitalMode)) ?? "-";
    return (
      <ListRow>
        <div className="grid gap-2 sm:grid-cols-3">
          <RowStat label="Execution" value={liveExecution.label} />
          <RowStat label="Live accounts" value={formatInteger(liveAccounts.length)} />
          <RowStat label="Enabled" value={formatInteger(enabledCount)} />
          <RowStat label="Exit only" value={formatInteger(exitOnlyCount)} />
          <RowStat label="Open positions" value={formatInteger(tradingAccounts.positions.length)} />
          <RowStat label="Recent fills" value={formatInteger(tradingAccounts.recentFills.length)} />
          <RowStat label="Recent orders" value={formatInteger(tradingAccounts.recentOrders.length)} />
          <RowStat label="Capital mode" value={formatCapitalMode(capitalMode)} />
          <RowStat label="Network" value={network} />
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#5b6770]">
          <Clock className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Last refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
          <span className="font-mono">{formatInteger(TRADING_REFRESH_MS)} ms polling</span>
        </div>
      </ListRow>
    );
  }

  return (
    <ListRow>
      <div className="grid gap-2 sm:grid-cols-3">
        <RowStat label="Wallets" value={formatInteger(summary.policy.topWalletCount)} />
        <RowStat label="Pocket" value={formatPercent(summary.policy.standardAllocationPct)} />
        <RowStat label="Total cap" value={formatPercent(summary.policy.maxTotalAllocationPct)} />
        <RowStat label="Min order" value={formatCurrency(summary.policy.minOrderNotionalUsd)} />
        <RowStat
          label="Min adjust"
          value={summary.policy.adjustSmallOrdersToMinOrder ? "enabled" : "disabled"}
        />
        <RowStat label="Fee" value={formatPercent(summary.policy.feeRate)} />
        <RowStat label="Slippage" value={formatBps(summary.policy.slippageBps)} />
        <RowStat label="Latency" value={`${formatInteger(summary.policy.latencyMs)} ms`} />
        <RowStat label="Max adverse drift" value={formatBps(summary.policy.maxPriceDriftBps)} />
        <RowStat
          label="Price cache"
          value={summary.policy.marketPriceCacheEnabled ? "enabled" : "disabled"}
          detail={`${formatInteger(summary.policy.marketPriceCacheStaleSeconds)}s stale`}
        />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[#5b6770]">
        <Clock className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Last refresh {lastRefreshAt ? formatDate(lastRefreshAt.toISOString()) : "-"}</span>
        <span className="font-mono">{formatInteger(TRADING_REFRESH_MS)} ms polling</span>
      </div>
    </ListRow>
  );
}

function SourceRow({
  isClosing,
  mode,
  onCloseSource,
  source,
}: {
  isClosing: boolean;
  mode: TradingMode;
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
  const sourceMetaParts = [
    formatPoolRank(source.poolRank),
    `${formatScore(source.score)} score`,
    mode === "paper"
      ? `${formatInteger(source.accountCount)} paper accounts`
      : `${formatInteger(source.enabledLiveAccountCount)} live accounts`,
    mode === "live" && source.recentLiveFillCount > 0
      ? `${formatInteger(source.recentLiveFillCount)} live fills`
      : null,
    mode === "live" && source.recentLiveOrderCount > 0
      ? `${formatInteger(source.recentLiveOrderCount)} live orders`
      : null,
  ].filter(Boolean);
  const sourceMeta = sourceMetaParts.join(", ");
  const sourceDetail = sourceStatusDetail(source, mode);
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
            {sourceDetail !== "active slot" && sourceDetail !== "active live source" ? (
              <p className="mt-0.5 whitespace-normal break-words text-[11px] leading-4 text-[#5b6770]">
                {sourceDetail}
              </p>
            ) : null}
          </div>
          {mode === "paper" && source.openPaperPositionCount > 0 ? (
            <button
              type="button"
              onClick={() => onCloseSource(source)}
              disabled={isClosing}
              title="Close open paper positions for this source"
              className="inline-flex min-h-7 shrink-0 items-center justify-center gap-1.5 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-2 py-1 text-xs font-semibold text-danger shadow-sm hover:bg-[#ffe6e2] disabled:cursor-not-allowed disabled:border-line disabled:bg-[#f7f9fb] disabled:text-[#98a2b3]"
            >
              {isClosing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Close paper
            </button>
          ) : null}
        </div>
        <div className="grid min-w-0 gap-x-4 gap-y-1 sm:grid-cols-2 xl:grid-cols-5">
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
          <CompactSourceStat
            label="Monitored"
            value={formatMonitoringDuration(source.monitoredSeconds)}
            detail={formatMonitoringPnlPerHour(source.totalPnlPerMonitoredHourUsd)}
            tone={monitoringTone(source.totalPnlPerMonitoredHourUsd)}
          />
          {mode === "paper" ? (
            <>
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
            </>
          ) : (
            <>
              <SourcePocketStat
                label="Allocation"
                remaining={formatCurrency(source.remainingAllocationUsd)}
                tone={usedPct >= 0.9 ? "danger" : usedPct >= 0.7 ? "warning" : "positive"}
                used={formatCurrency(source.openMarginUsd)}
                usedPct={usedPct}
                value={formatPercent(source.pocketUsedPct)}
              />
              <CompactSourceStat
                label="Activity"
                value={`${formatInteger(source.recentLiveFillCount)} fills`}
                detail={`${formatCurrency(source.openNotionalUsd)} notional, ${formatInteger(source.recentLiveOrderCount)} orders`}
              />
            </>
          )}
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
  label = "Allocation",
  remaining,
  tone,
  used,
  usedPct,
  value,
}: {
  label?: string;
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
          {label}
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
  onClose: (position: DashboardPosition) => void;
  position: DashboardPosition;
}) {
  const paperPosition = position.paperPosition;
  const livePosition = position.livePosition;
  const canClose =
    livePosition !== null || (paperPosition !== null && position.markPrice !== null);
  const unrealizedPnl = numberValue(position.unrealizedPnlUsd ?? 0);
  const realizedPnl = numberValue(position.realizedPnlUsd ?? 0);
  const sourceName = sourceDisplayName(position.sourceLabel, position.sourceWallet);
  const closeTitle =
    livePosition !== null
      ? "Close live position"
      : canClose
        ? "Close paper position"
        : "Execution price unavailable";
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.15fr_repeat(7,minmax(0,0.72fr))_auto] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{position.coin}</p>
            <StatusPill label={position.accountType} tone={position.accountType === "live" ? "positive" : "neutral"} />
            <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
            <span className="font-mono text-xs text-[#5b6770]">{formatLeverage(position.leverage)}</span>
          </div>
          {isLiveExchangeSource(position.sourceWallet) ? (
            <p className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
              {sourceName}
            </p>
          ) : (
            <Link
              href={`/wallets/${position.sourceWallet}`}
              className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
            >
              {sourceName}
            </Link>
          )}
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {isLiveExchangeSource(position.sourceWallet) ? "exchange" : shortAddress(position.sourceWallet)} | {position.accountKey}
          </p>
        </div>
        <RowStat
          label="Unrealized"
          value={position.unrealizedPnlUsd === null ? "-" : formatCurrency(position.unrealizedPnlUsd)}
          detail={position.unrealizedPnlPct === null ? undefined : formatPercent(position.unrealizedPnlPct)}
          tone={unrealizedPnl >= 0 ? "positive" : "danger"}
        />
        <RowStat
          label="Realized"
          value={formatCurrency(position.realizedPnlUsd)}
          tone={realizedPnl >= 0 ? "positive" : "danger"}
        />
        <RowStat
          label="Fills"
          value={`${formatInteger(position.addFillCount)} add`}
          detail={`${formatInteger(position.closeFillCount)} close`}
        />
        <RowStat label="Margin" value={formatCurrency(position.marginUsd)} detail={`${formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)} notional`} />
        <RowStat label="Entry" value={formatPrice(position.entryPrice)} detail={`size ${formatSize(position.size)}`} />
        <RowStat
          label="Execution"
          value={formatExecutionMs(position.entryExecutionDelayMs)}
          detail={
            position.accountType === "live" &&
            isLiveExchangeSource(position.sourceWallet) &&
            position.entryExecutionDelayMs !== null
              ? "source to exchange"
              : position.accountType === "paper" || position.entryExecutionDelayMs !== null
              ? "source to open"
              : "live position"
          }
        />
        <RowStat label="Mark" value={formatPrice(position.markPrice)} detail={formatDate(position.priceUpdatedAt ?? position.updatedAt)} />
        {paperPosition || livePosition ? (
          <button
            type="button"
            onClick={() => onClose(position)}
            disabled={!canClose || isClosing}
            title={closeTitle}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-2.5 text-xs font-semibold text-danger shadow-sm hover:bg-[#ffe6e2] disabled:cursor-not-allowed disabled:border-line disabled:bg-[#f7f9fb] disabled:text-[#98a2b3]"
          >
            {isClosing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <XCircle className="h-3.5 w-3.5" aria-hidden="true" />}
            Close
          </button>
        ) : (
          <span className="text-xs text-[#5b6770]">Live</span>
        )}
      </div>
    </ListRow>
  );
}

function WalletHistoryRow({ wallet }: { wallet: WalletPerformanceRow }) {
  const totalPnl = numberValue(wallet.totalPnlUsd);
  const isMonitored = wallet.monitorStatus === "monitored";
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_repeat(6,minmax(0,0.75fr))] xl:items-center">
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
        <RowStat
          label="Monitored"
          value={formatMonitoringDuration(wallet.monitoredSeconds)}
          detail={formatMonitoringPnlPerHour(wallet.totalPnlPerMonitoredHourUsd)}
          tone={monitoringTone(wallet.totalPnlPerMonitoredHourUsd)}
        />
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

function LiveClosedTradeRow({ trade }: { trade: TradingClosedTrade }) {
  const netPnl = numberValue(trade.netPnlUsd);
  const sourceName = sourceDisplayName(trade.sourceLabel, trade.sourceWallet);
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_0.85fr_0.8fr_0.85fr_0.85fr_0.75fr] xl:items-center">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{trade.coin}</p>
            <StatusPill label="live" tone="positive" />
            <StatusPill label="closed trade" tone="neutral" />
            <StatusPill label={trade.side} tone={trade.side === "long" ? "positive" : "warning"} />
          </div>
          {isLiveExchangeSource(trade.sourceWallet) ? (
            <p className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
              {sourceName}
            </p>
          ) : (
            <Link
              href={`/wallets/${trade.sourceWallet}`}
              className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
            >
              {sourceName}
            </Link>
          )}
          <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
            {isLiveExchangeSource(trade.sourceWallet) ? "exchange" : shortAddress(trade.sourceWallet)} | {trade.accountKey}
          </p>
        </div>
        <RowStat label="Net PnL" value={formatCurrency(trade.netPnlUsd)} detail={`${formatCurrency(trade.realizedPnlUsd)} realized`} tone={netPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Closed" value={formatShortDateTime(trade.closedAt)} detail={formatTradeDuration(trade.durationMs)} />
        <RowStat label="Entry" value={formatPrice(trade.entryPrice)} detail={formatShortDateTime(trade.openedAt)} />
        <RowStat label="Exit" value={formatPrice(trade.exitPrice)} detail={`size ${formatSize(trade.size)}`} />
        <RowStat label="Notional" value={formatCurrency(trade.exitNotionalUsd)} detail={`${formatInteger(trade.openFillCount)} open, ${formatInteger(trade.closeFillCount)} close fills`} />
        <RowStat label="Fee" value={formatCurrency(trade.feeUsd)} />
      </div>
    </ListRow>
  );
}

function FillRow({ fill }: { fill: ExecutionActivityItem }) {
  return (
    <ListRow>
      <div className="grid gap-2 xl:grid-cols-[1.05fr_0.7fr_0.85fr_0.85fr_0.85fr_0.9fr] xl:items-center">
        <RowIdentityBlock identity={fill.identity} pills={fill.pills} />
        {fill.stats.map((stat) => (
          <RowStat
            key={stat.label}
            detail={stat.detail}
            label={stat.label}
            tone={stat.tone}
            value={stat.value}
          />
        ))}
      </div>
    </ListRow>
  );
}

function RowIdentityBlock({
  identity,
  pills,
}: {
  identity: RowIdentity;
  pills: RowPill[];
}) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1">
        {identity.href ? (
          <Link
            href={identity.href}
            className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-[#297c73]"
          >
            {identity.label}
          </Link>
        ) : (
          <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink">
            {identity.label}
          </p>
        )}
        {pills.map((pill) => (
          <StatusPill key={`${pill.label}:${pill.tone}`} label={pill.label} tone={pill.tone} />
        ))}
      </div>
      <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">{identity.meta}</p>
    </div>
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

function fillNotionalDetail(fill: PaperCopyFill) {
  const parts = [
    fill.minOrderAdjusted && fill.originalNotionalUsd
      ? `adjusted from ${formatCurrency(fill.originalNotionalUsd)}`
      : null,
    `${formatCurrency(fill.marginUsd)} margin`,
  ].filter(Boolean);
  return parts.join(" | ");
}

function fillSkipDetail(fill: PaperCopyFill) {
  const parts = [
    fill.priceDriftBps ? `adverse drift ${formatBps(fill.priceDriftBps)}` : null,
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
      <p className={`mt-0.5 whitespace-normal break-words font-mono text-xs font-semibold ${valueClass}`}>{value}</p>
      {detail ? <p className="mt-0.5 whitespace-normal break-words text-[11px] text-[#5b6770]">{detail}</p> : null}
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

function buildPaperDashboardAccounts(summary: PaperTradingSummaryResponse): DashboardAccount[] {
  return summary.accounts.map<DashboardAccount>((account) => ({
    accountType: "paper",
    equityUsd: accountNetEquity(account),
    key: account.key,
    label: account.label,
    openMarginUsd: numberValue(account.openMarginUsd),
    openNotionalUsd: numberValue(account.openNotionalUsd),
    openPositionCount: account.openPositionCount,
    paperAccount: account,
    realizedPnlUsd: numberValue(account.realizedPnlUsd),
    statusLabel: account.enabled ? "enabled" : "disabled",
    statusTone: account.enabled ? "positive" : "warning",
    totalPnlUsd: numberValue(account.totalPnlUsd),
    unrealizedPnlUsd: numberValue(account.unrealizedPnlUsd),
  }));
}

function buildLiveDashboardAccounts(
  tradingAccounts: TradingAccountsResponse,
  livePositions: TradingPosition[],
): DashboardAccount[] {
  const livePositionsByAccount = new Map<string, TradingPosition[]>();
  for (const position of livePositions) {
    livePositionsByAccount.set(position.accountKey, [
      ...(livePositionsByAccount.get(position.accountKey) ?? []),
      position,
    ]);
  }

  return tradingAccounts.accounts
    .filter((account) => account.accountType === "live")
    .map<DashboardAccount>((account) => {
      const positions = livePositionsByAccount.get(account.key) ?? [];
      const liveUnrealizedPnl = sumNumbers(
        positions.map((position) => position.unrealizedPnlUsd),
      );
      return {
        accountType: "live",
        equityUsd: liveAccountEquity(account),
        key: account.key,
        label: account.label,
        openMarginUsd: sumNumbers(positions.map((position) => position.marginUsd)),
        openNotionalUsd: sumNumbers(
          positions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
        ),
        openPositionCount: positions.length,
        paperAccount: null,
        realizedPnlUsd: numberValue(account.realizedPnlUsd),
        statusLabel: formatLiveAccountStatus(account.status),
        statusTone: account.status === "enabled" ? "positive" : account.status === "exit_only" ? "warning" : "neutral",
        totalPnlUsd: numberValue(account.realizedPnlUsd) + liveUnrealizedPnl,
        unrealizedPnlUsd: liveUnrealizedPnl,
      };
    });
}

function buildPaperDashboardPositions(paperPositions: PaperPosition[]): DashboardPosition[] {
  return paperPositions.map<DashboardPosition>((position) => ({
    accountKey: position.accountKey,
    accountType: "paper",
    coin: position.coin,
    currentNotionalUsd: position.currentNotionalUsd,
    entryExecutionDelayMs: position.entryExecutionDelayMs,
    entryPrice: position.entryPrice,
    id: position.id,
    leverage: position.leverage,
    marginUsd: position.marginUsd,
    markPrice: position.markPrice,
    notionalUsd: position.notionalUsd,
    paperPosition: position,
    livePosition: null,
    priceUpdatedAt: position.priceUpdatedAt,
    realizedPnlUsd: position.realizedPnlUsd,
    side: position.side,
    size: position.size,
    sourceLabel: position.sourceLabel,
    sourceWallet: position.sourceWallet,
    addFillCount: position.addFillCount,
    closeFillCount: position.closeFillCount,
    unrealizedPnlPct: position.unrealizedPnlPct,
    unrealizedPnlUsd: position.unrealizedPnlUsd,
    updatedAt: position.updatedAt,
  }));
}

function buildLiveDashboardPositions(
  livePositions: TradingPosition[],
  sourceLabels: Map<string, string>,
): DashboardPosition[] {
  return livePositions.map<DashboardPosition>((position) => ({
    accountKey: position.accountKey,
    accountType: "live",
    coin: position.coin,
    currentNotionalUsd: position.currentNotionalUsd ?? position.notionalUsd,
    entryExecutionDelayMs: position.entryExecutionDelayMs,
    entryPrice: position.entryPrice,
    id: position.id,
    leverage: position.leverage,
    marginUsd: position.marginUsd,
    markPrice: position.markPrice,
    notionalUsd: position.notionalUsd,
    paperPosition: null,
    livePosition: position,
    priceUpdatedAt: position.priceUpdatedAt ?? position.lastReconciledAt,
    realizedPnlUsd: position.realizedPnlUsd,
    side: position.side,
    size: position.size,
    sourceLabel: isLiveExchangePosition(position)
      ? "Exchange position"
      : sourceLabels.get(position.sourceWallet.toLowerCase()) ?? null,
    sourceWallet: position.sourceWallet,
    addFillCount: position.addFillCount,
    closeFillCount: position.closeFillCount,
    unrealizedPnlPct: position.unrealizedPnlPct,
    unrealizedPnlUsd: position.unrealizedPnlUsd,
    updatedAt: position.updatedAt,
  })).sort((left, right) => dateMs(right.updatedAt) - dateMs(left.updatedAt));
}

function buildPaperDashboardFills(paperFills: PaperCopyFill[]): ExecutionActivityItem[] {
  return paperFills
    .map<ExecutionActivityItem>((fill) => {
      const realizedPnl = numberValue(fill.realizedPnlUsd);
      const actionTone: Tone =
        fill.action === "skip" ? "warning" : fill.action.includes("close") ? "neutral" : "positive";
      return {
        identity: {
          href: `/wallets/${fill.sourceWallet}`,
          label: sourceDisplayName(fill.sourceLabel, fill.sourceWallet),
          meta: `${shortAddress(fill.sourceWallet)} | ${fill.accountKey}`,
        },
        id: `paper:${fill.id}`,
        pills: [
          { label: "paper", tone: "neutral" },
          { label: fill.action, tone: actionTone },
          ...(fill.side
            ? [{ label: fill.side, tone: fill.side === "long" ? "positive" as Tone : "warning" as Tone }]
            : []),
          ...(fill.minOrderAdjusted ? [{ label: "min order adjusted", tone: "warning" as Tone }] : []),
        ],
        sortAt: fill.filledAt,
        stats: [
          { label: "Market", value: fill.coin, detail: formatDate(fill.filledAt) },
          { label: "Notional", value: formatCurrency(fill.notionalUsd), detail: fillNotionalDetail(fill) },
          {
            label: "Realized",
            value: formatCurrency(fill.realizedPnlUsd),
            tone: realizedPnl >= 0 ? "positive" : "danger",
          },
          { label: "Price", value: formatPrice(fill.price), detail: fillPriceDetail(fill) },
          {
            label: "Result",
            value: fill.skippedReason ? reasonLabel(fill.skippedReason) : "filled",
            detail: fill.skippedReason
              ? fillSkipDetail(fill)
              : `fee ${formatCurrency(fill.feeUsd)} | ${formatLeverage(fill.leverage)}`,
            tone: fill.skippedReason ? "danger" : "positive",
          },
        ],
      };
    })
    .slice(0, 100);
}

function buildLiveDashboardFills(
  liveFills: TradingFill[],
  liveOrders: TradingOrder[],
  sourceLabels: Map<string, string>,
): ExecutionActivityItem[] {
  const liveRows = liveFills.map<ExecutionActivityItem>((fill) => buildLiveFillActivity(fill, sourceLabels));
  const liveFillOrderIds = new Set(
    liveFills
      .map((fill) => fill.orderId)
      .filter((value): value is string => Boolean(value)),
  );
  const liveFillSourceKeys = new Set(liveFills.map(liveActivitySourceKey));
  const liveOrderRows = liveOrders
    .filter(
      (order) =>
        !liveFillOrderIds.has(order.id) &&
        !liveFillSourceKeys.has(liveActivitySourceKey(order)),
    )
    .map<ExecutionActivityItem>((order) => buildLiveOrderActivity(order, sourceLabels));
  return [...liveRows, ...liveOrderRows]
    .sort((left, right) => dateMs(right.sortAt) - dateMs(left.sortAt))
    .slice(0, 100);
}

function buildLiveFillActivity(
  fill: TradingFill,
  sourceLabels: Map<string, string>,
): ExecutionActivityItem {
  const realizedPnl = numberValue(fill.realizedPnlUsd);
  const actionTone: Tone = fill.action.includes("close") || fill.action.includes("reduce")
    ? "neutral"
    : "positive";
  const isExchange = isLiveExchangeSource(fill.sourceWallet);
  return {
    identity: {
      href: isExchange ? null : `/wallets/${fill.sourceWallet}`,
      label: isExchange
        ? "Exchange fill"
        : sourceDisplayName(sourceLabels.get(fill.sourceWallet.toLowerCase()), fill.sourceWallet),
      meta: `${isExchange ? "exchange" : shortAddress(fill.sourceWallet)} | ${fill.accountKey}`,
    },
    id: `live:${fill.id}`,
    pills: [
      { label: "live", tone: "positive" },
      { label: fill.action, tone: actionTone },
      { label: fill.side, tone: fill.side === "long" ? "positive" : "warning" },
    ],
    sortAt: fill.filledAt,
    stats: [
      { label: "Market", value: fill.coin, detail: formatDate(fill.filledAt) },
      { label: "Notional", value: formatCurrency(fill.notionalUsd), detail: `size ${formatSize(fill.size)}` },
      {
        label: "Realized",
        value: formatCurrency(fill.realizedPnlUsd),
        tone: realizedPnl >= 0 ? "positive" : "danger",
      },
      { label: "Price", value: formatPrice(fill.price), detail: `fee ${formatCurrency(fill.feeUsd)}` },
      {
        label: "Result",
        value: "filled",
        detail: fill.sourceFillId ? `source ${shortIdentifier(fill.sourceFillId)}` : "reconciled live fill",
        tone: "positive",
      },
    ],
  };
}

function buildLiveOrderActivity(
  order: TradingOrder,
  sourceLabels: Map<string, string>,
): ExecutionActivityItem {
  const error = order.error?.trim();
  const isSkip = order.orderType === "skip" || error?.startsWith("skip:");
  const isExchange = isLiveExchangeSource(order.sourceWallet);
  const actionTone: Tone = order.action.includes("close") || order.action.includes("reduce")
    ? "neutral"
    : "positive";
  const resultDetail = order.exchangeOrderId
    ? `exchange ${shortIdentifier(order.exchangeOrderId)}`
    : isSkip
      ? `source ${shortIdentifier(order.sourceFillId)}`
      : `client ${shortIdentifier(order.clientOrderId)}`;
  return {
    identity: {
      href: isExchange ? null : `/wallets/${order.sourceWallet}`,
      label: isExchange
        ? "Exchange order"
        : sourceDisplayName(sourceLabels.get(order.sourceWallet.toLowerCase()), order.sourceWallet),
      meta: `${isExchange ? "exchange" : shortAddress(order.sourceWallet)} | ${order.accountKey}`,
    },
    id: `live-order:${order.id}`,
    pills: [
      { label: order.orderType === "skip" ? "live skip" : "live order", tone: order.orderType === "skip" ? "warning" : "neutral" },
      { label: order.action, tone: actionTone },
      { label: order.status, tone: liveOrderStatusTone(order.status) },
    ],
    sortAt: order.orderType === "skip" ? order.createdAt : order.filledAt ?? order.updatedAt ?? order.createdAt,
    stats: [
      { label: "Market", value: order.coin, detail: formatDate(order.createdAt) },
      {
        label: "Requested",
        value: formatCurrency(order.requestedNotionalUsd),
        detail: `size ${formatSize(order.requestedSize)}`,
      },
      {
        label: "Filled",
        value: formatCurrency(order.filledNotionalUsd),
        detail: `size ${formatSize(order.filledSize)}`,
      },
      {
        label: "Price",
        value: formatPrice(order.limitPrice),
        detail: order.averageFillPrice
          ? `avg ${formatPrice(order.averageFillPrice)}`
          : formatLeverage(order.leverage),
      },
      {
        label: "Result",
        value: error ? reasonLabel(error.replace(/^skip:/, "")) : reasonLabel(order.status),
        detail: resultDetail,
        tone: error ? "danger" : "neutral",
      },
    ],
  };
}

function liveActivitySourceKey(item: TradingFill | TradingOrder) {
  return [
    item.accountKey,
    item.sourceWallet,
    item.sourceFillId ?? "",
    item.sequenceIndex ?? "",
  ].join(":");
}

function buildPaperMetrics(summary: PaperTradingSummaryResponse) {
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

function buildLiveMetrics(
  tradingAccounts: TradingAccountsResponse,
  livePositions: TradingPosition[],
) {
  const liveEquity = tradingAccounts.accounts
    .filter((account) => account.accountType === "live")
    .reduce((total, account) => total + liveAccountEquity(account), 0);
  const liveRealizedPnl = tradingAccounts.accounts
    .filter((account) => account.accountType === "live")
    .reduce((total, account) => total + numberValue(account.realizedPnlUsd), 0);
  const liveFees = tradingAccounts.accounts
    .filter((account) => account.accountType === "live")
    .reduce((total, account) => total + numberValue(account.feeUsd), 0);
  const liveOpenMargin = sumNumbers(livePositions.map((position) => position.marginUsd));
  const liveOpenNotional = sumNumbers(
    livePositions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
  );
  const liveUnrealizedPnl = sumNumbers(
    livePositions.map((position) => position.unrealizedPnlUsd),
  );
  return {
    cashEquity: liveEquity,
    fees: liveFees,
    netEquity: liveEquity,
    openMargin: liveOpenMargin,
    openNotional: liveOpenNotional,
    realizedPnl: liveRealizedPnl,
    totalPnl: liveRealizedPnl + liveUnrealizedPnl,
    unrealizedPnl: liveUnrealizedPnl,
  };
}

function buildLiveExecutionStatus(tradingAccounts: TradingAccountsResponse): {
  copyingAccountCount: number;
  label: string;
  tone: Tone;
} {
  const liveAccounts = tradingAccounts.accounts.filter(
    (account) => account.accountType === "live",
  );
  const enabledLiveAccounts = liveAccounts.filter((account) => account.status === "enabled");
  const exitOnlyLiveAccounts = liveAccounts.filter((account) => account.status === "exit_only");

  if (!tradingAccounts.liveTradingEnabled) {
    return {
      copyingAccountCount: 0,
      label: liveAccounts.length > 0 ? "live execution disabled" : "no live accounts",
      tone: liveAccounts.length > 0 ? "warning" : "neutral",
    };
  }
  if (!tradingAccounts.liveCopyEnabled) {
    return {
      copyingAccountCount: 0,
      label: "live copy disabled",
      tone: "warning",
    };
  }
  if (enabledLiveAccounts.length > 0) {
    return {
      copyingAccountCount: enabledLiveAccounts.length,
      label: "live execution enabled",
      tone: "positive",
    };
  }
  if (exitOnlyLiveAccounts.length > 0) {
    return {
      copyingAccountCount: 0,
      label: "live exit only",
      tone: "warning",
    };
  }
  return {
    copyingAccountCount: 0,
    label: "no live accounts enabled",
    tone: "warning",
  };
}

function buildPaperMonitoredSources(summary: PaperTradingSummaryResponse): MonitoredSource[] {
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

  const sources = new Set([
    ...allocationsBySource.keys(),
    ...positionsBySource.keys(),
  ]);
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
      const openPositionCount = openPositions.length;
      const sourceStatus = resolveSourceStatus(allocations, openPositionCount);
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
        enabledLiveAccountCount: 0,
        openLivePositionCount: 0,
        openPaperPositionCount: openPositions.length,
        openPositionCount,
        allocationPct: firstNumber(allocations.map((allocation) => allocation.allocationPct)),
        allocationUsd,
        openMarginUsd,
        openNotionalUsd: sumNumbers(openPositions.map((position) => position.currentNotionalUsd ?? position.notionalUsd)),
        remainingAllocationUsd,
        pocketUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
        recentLiveFillCount: 0,
        recentLiveOrderCount: 0,
        realizedPnlUsd: wallet?.realizedPnlUsd ?? "0",
        unrealizedPnlUsd: wallet?.unrealizedPnlUsd ?? "0",
        totalPnlUsd: wallet?.totalPnlUsd ?? "0",
        monitoredSeconds: wallet?.monitoredSeconds ?? 0,
        monitoredHours: wallet?.monitoredHours ?? "0",
        realizedPnlPerMonitoredHourUsd: wallet?.realizedPnlPerMonitoredHourUsd ?? null,
        totalPnlPerMonitoredHourUsd: wallet?.totalPnlPerMonitoredHourUsd ?? null,
        firstMonitoredAt: wallet?.firstMonitoredAt ?? null,
        currentMonitoringStartedAt: wallet?.currentMonitoringStartedAt ?? null,
        lastMonitoredAt: wallet?.lastMonitoredAt ?? null,
      };
    })
    .sort((left, right) => {
      if (left.sourceStatus !== right.sourceStatus) {
        return statusOrder(left.sourceStatus) - statusOrder(right.sourceStatus);
      }
      return (left.poolRank ?? left.rank ?? 9999) - (right.poolRank ?? right.rank ?? 9999);
    });
}

function buildLiveMonitoredSources(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  enabledLiveAccountCount: number,
  livePositions: TradingPosition[],
): MonitoredSource[] {
  const metadataBySource = buildSourceMetadata(summary, tradingAccounts);
  const liveCopyReady =
    tradingAccounts.liveTradingEnabled &&
    tradingAccounts.liveCopyEnabled &&
    enabledLiveAccountCount > 0;
  const liveAllocationCapital = tradingAccounts.accounts
    .filter(
      (account) =>
        account.accountType === "live" &&
        (account.status === "enabled" || account.status === "exit_only"),
    )
    .reduce((total, account) => total + liveAccountEquity(account), 0);
  const allocationsBySource = new Map<string, PaperCopyAllocation[]>();
  for (const allocation of summary.allocations) {
    const source = allocation.sourceWallet.toLowerCase();
    allocationsBySource.set(source, [...(allocationsBySource.get(source) ?? []), allocation]);
  }
  const livePositionsBySource = groupLivePositionsBySource(livePositions);
  const liveFillsBySource = groupLiveFillsBySource(tradingAccounts.recentFills);
  const liveOrdersBySource = groupLiveOrdersBySource(tradingAccounts.recentOrders);
  const liveVisibleAllocationSources = Array.from(allocationsBySource.entries())
    .filter(([, allocations]) => liveAllocationSourceVisible(allocations, liveCopyReady))
    .map(([source]) => source);
  const sources = new Set([
    ...liveVisibleAllocationSources,
    ...livePositionsBySource.keys(),
  ]);

  return Array.from(sources)
    .map((source) => {
      const allocations = allocationsBySource.get(source) ?? [];
      const liveOpenPositions = livePositionsBySource.get(source) ?? [];
      const liveFills = liveFillsBySource.get(source) ?? [];
      const liveOrders = liveOrdersBySource.get(source) ?? [];
      const metadata = metadataBySource.get(source);
      const canOpenNewPositions = liveSourceCanOpenNewPositions(allocations, liveCopyReady);
      const openPositionCount = liveOpenPositions.length;
      const hasRealtimeSlot =
        allocations.some((allocation) => allocation.hasRealtimeSlot) ||
        (allocations.length === 0 && openPositionCount > 0);
      const sourceStatus = resolveLiveSourceStatus({
        canOpenNewPositions,
        hasRealtimeSlot,
        openPositionCount,
      });
      const monitorStatus: MonitoredSource["monitorStatus"] = hasRealtimeSlot
        ? "monitored"
        : "waiting";
      const realizedPnl = sumNumbers(liveFills.map((fill) => fill.realizedPnlUsd));
      const unrealizedPnl = sumNumbers(liveOpenPositions.map((position) => position.unrealizedPnlUsd));
      const allocationPct =
        metadata?.allocationPct ??
        firstNumber(allocations.map((allocation) => allocation.allocationPct)) ??
        0;
      const allocationUsd =
        liveAllocationCapital > 0 && allocationPct > 0
          ? liveAllocationCapital * allocationPct
          : 0;
      const openMarginUsd = sumNumbers(liveOpenPositions.map((position) => position.marginUsd));
      const accounts = new Set([
        ...liveOpenPositions.map((position) => position.accountKey),
        ...liveFills.map((fill) => fill.accountKey),
        ...liveOrders.map((order) => order.accountKey),
      ]);
      return {
        sourceWallet: source,
        sourceLabel:
          metadata?.label ??
          firstString(allocations.map((allocation) => allocation.sourceLabel)) ??
          null,
        rank: metadata?.rank ?? minNumber(allocations.map((allocation) => allocation.rank)),
        poolRank: metadata?.poolRank ?? minNumber(allocations.map((allocation) => allocation.poolRank)),
        score: metadata?.score ?? firstString(allocations.map((allocation) => allocation.score)),
        monitorStatus,
        sourceStatus,
        sourceStatusReason: resolveLiveSourceStatusReason({
          allocations,
          canOpenNewPositions,
          openPositionCount,
          tradingAccounts,
        }),
        hasRealtimeSlot,
        canOpenNewPositions,
        accountCount: accounts.size,
        enabledLiveAccountCount: liveCopyReady ? enabledLiveAccountCount : 0,
        openLivePositionCount: openPositionCount,
        openPaperPositionCount: 0,
        openPositionCount,
        allocationPct: allocationPct > 0 ? allocationPct : null,
        allocationUsd,
        openMarginUsd,
        openNotionalUsd: sumNumbers(
          liveOpenPositions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
        ),
        remainingAllocationUsd: Math.max(allocationUsd - openMarginUsd, 0),
        pocketUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
        recentLiveFillCount: liveFills.length,
        recentLiveOrderCount: liveOrders.length,
        realizedPnlUsd: String(realizedPnl),
        unrealizedPnlUsd: String(unrealizedPnl),
        totalPnlUsd: String(realizedPnl + unrealizedPnl),
        monitoredSeconds: metadata?.monitoredSeconds ?? 0,
        monitoredHours: metadata?.monitoredHours ?? "0",
        realizedPnlPerMonitoredHourUsd: metadata?.realizedPnlPerMonitoredHourUsd ?? null,
        totalPnlPerMonitoredHourUsd: metadata?.totalPnlPerMonitoredHourUsd ?? null,
        firstMonitoredAt: metadata?.firstMonitoredAt ?? null,
        currentMonitoringStartedAt: metadata?.currentMonitoringStartedAt ?? null,
        lastMonitoredAt: metadata?.lastMonitoredAt ?? null,
      };
    })
    .filter(
      (source) =>
        source.canOpenNewPositions ||
        source.hasRealtimeSlot ||
        source.sourceStatus === "waiting_for_slot" ||
        source.openPositionCount > 0 ||
        source.recentLiveFillCount > 0 ||
        source.recentLiveOrderCount > 0,
    )
    .sort((left, right) => {
      if (left.sourceStatus !== right.sourceStatus) {
        return statusOrder(left.sourceStatus) - statusOrder(right.sourceStatus);
      }
      return (left.poolRank ?? left.rank ?? 9999) - (right.poolRank ?? right.rank ?? 9999);
    });
}

function buildWalletHistory(wallets: WalletPerformanceRow[]) {
  return wallets
    .filter(
      (wallet) =>
        wallet.openPositionCount > 0 ||
        wallet.copiedFillCount > 0 ||
        wallet.skippedFillCount > 0 ||
        wallet.monitoredSeconds > 0 ||
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

function buildLiveWalletHistory(
  liveFills: TradingFill[],
  liveOrders: TradingOrder[],
  livePositions: TradingPosition[],
  sourceMetadata: Map<string, SourceMetadata>,
): WalletPerformanceRow[] {
  const positionsBySource = groupLivePositionsBySource(livePositions);
  const fillsBySource = groupLiveFillsBySource(liveFills);
  const ordersBySource = groupLiveOrdersBySource(liveOrders);
  const sources = new Set([
    ...positionsBySource.keys(),
    ...fillsBySource.keys(),
    ...ordersBySource.keys(),
  ]);
  return Array.from(sources)
    .map<WalletPerformanceRow>((source) => {
      const positions = positionsBySource.get(source) ?? [];
      const fills = fillsBySource.get(source) ?? [];
      const orders = ordersBySource.get(source) ?? [];
      const realizedPnl = sumNumbers(fills.map((fill) => fill.realizedPnlUsd));
      const unrealizedPnl = sumNumbers(positions.map((position) => position.unrealizedPnlUsd));
      const accountKeys = new Set([
        ...positions.map((position) => position.accountKey),
        ...fills.map((fill) => fill.accountKey),
        ...orders.map((order) => order.accountKey),
      ]);
      const metadata = sourceMetadata.get(source);
      return {
        sourceWallet: source,
        sourceLabel: metadata?.label ?? null,
        rank: metadata?.rank ?? null,
        poolRank: metadata?.poolRank ?? null,
        score: metadata?.score ?? null,
        allocationPct: metadata?.allocationPct === null || metadata?.allocationPct === undefined
          ? null
          : String(metadata.allocationPct),
        active: positions.length > 0,
        monitorStatus: positions.length > 0 ? "monitored" : "history",
        accountCount: accountKeys.size,
        openPositionCount: positions.length,
        copiedFillCount: fills.length,
        skippedFillCount: orders.filter((order) => liveOrderStatusTone(order.status) === "danger").length,
        realizedPnlUsd: String(realizedPnl),
        unrealizedPnlUsd: String(unrealizedPnl),
        totalPnlUsd: String(realizedPnl + unrealizedPnl),
        monitoredSeconds: metadata?.monitoredSeconds ?? 0,
        monitoredHours: metadata?.monitoredHours ?? "0",
        realizedPnlPerMonitoredHourUsd: metadata?.realizedPnlPerMonitoredHourUsd ?? null,
        totalPnlPerMonitoredHourUsd: metadata?.totalPnlPerMonitoredHourUsd ?? null,
        firstMonitoredAt: metadata?.firstMonitoredAt ?? null,
        currentMonitoringStartedAt: metadata?.currentMonitoringStartedAt ?? null,
        lastMonitoredAt: metadata?.lastMonitoredAt ?? null,
        feeUsd: String(sumNumbers(fills.map((fill) => fill.feeUsd))),
        openNotionalUsd: String(sumNumbers(positions.map((position) => position.currentNotionalUsd ?? position.notionalUsd))),
        openMarginUsd: String(sumNumbers(positions.map((position) => position.marginUsd))),
        lastFillAt: latestDateStringFromValues([
          ...fills.map((fill) => fill.filledAt),
          ...orders.map((order) => order.updatedAt ?? order.createdAt),
        ]),
      };
    })
    .filter(
      (wallet) =>
        wallet.openPositionCount > 0 ||
        wallet.copiedFillCount > 0 ||
        wallet.skippedFillCount > 0 ||
        numberValue(wallet.totalPnlUsd) !== 0,
    )
    .sort((left, right) => dateMs(right.lastFillAt) - dateMs(left.lastFillAt));
}

function buildLiveClosedTradeRows(
  liveClosedTrades: TradingClosedTrade[],
  sourceLabels: Map<string, string>,
): TradingClosedTrade[] {
  return liveClosedTrades
    .map((trade) => ({
      ...trade,
      sourceLabel:
        trade.sourceLabel ?? sourceLabels.get(trade.sourceWallet.toLowerCase()) ?? null,
    }))
    .sort((left, right) => dateMs(right.closedAt) - dateMs(left.closedAt));
}

function countSourcesWithDashboardOpenPositions(positions: DashboardPosition[]) {
  return new Set(
    positions
      .filter((position) => !isLiveExchangeSource(position.sourceWallet))
      .map((position) => position.sourceWallet.toLowerCase()),
  ).size;
}

function buildSourceLabels(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const labels = new Map<string, string>();
  for (const [source, metadata] of buildSourceMetadata(summary, tradingAccounts)) {
    if (metadata.label) {
      labels.set(source, metadata.label);
    }
  }
  return labels;
}

function buildSourceMetadata(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const metadata = new Map<string, SourceMetadata>();
  const ensureMetadata = (wallet: string): SourceMetadata => {
    const source = wallet.toLowerCase();
    const existing = metadata.get(source);
    if (existing) {
      return existing;
    }
    const item: SourceMetadata = {
      allocationPct: null,
      label: null,
      poolRank: null,
      rank: null,
      score: null,
      monitoredSeconds: 0,
      monitoredHours: "0",
      realizedPnlPerMonitoredHourUsd: null,
      totalPnlPerMonitoredHourUsd: null,
      firstMonitoredAt: null,
      currentMonitoringStartedAt: null,
      lastMonitoredAt: null,
    };
    metadata.set(source, item);
    return item;
  };

  for (const allocation of summary.allocations) {
    const item = ensureMetadata(allocation.sourceWallet);
    item.allocationPct ??= firstNumber([allocation.allocationPct]);
    item.label ??= allocation.sourceLabel;
    item.poolRank = minNumber([item.poolRank, allocation.poolRank]);
    item.rank = minNumber([item.rank, allocation.rank]);
    item.score ??= allocation.score;
  }
  for (const wallet of summary.walletPerformance) {
    const item = ensureMetadata(wallet.sourceWallet);
    item.allocationPct ??= firstNumber([wallet.allocationPct]);
    item.label ??= wallet.sourceLabel;
    item.poolRank = minNumber([item.poolRank, wallet.poolRank]);
    item.rank = minNumber([item.rank, wallet.rank]);
    item.score ??= wallet.score;
    item.monitoredSeconds = Math.max(item.monitoredSeconds, wallet.monitoredSeconds);
    item.monitoredHours = wallet.monitoredHours;
    item.realizedPnlPerMonitoredHourUsd = wallet.realizedPnlPerMonitoredHourUsd;
    item.totalPnlPerMonitoredHourUsd = wallet.totalPnlPerMonitoredHourUsd;
    item.firstMonitoredAt ??= wallet.firstMonitoredAt;
    item.currentMonitoringStartedAt = wallet.currentMonitoringStartedAt;
    item.lastMonitoredAt = wallet.lastMonitoredAt;
  }
  for (const position of summary.positions) {
    ensureMetadata(position.sourceWallet).label ??= position.sourceLabel;
  }
  for (const fill of summary.recentFills) {
    ensureMetadata(fill.sourceWallet).label ??= fill.sourceLabel;
  }
  for (const trade of summary.closedTrades) {
    ensureMetadata(trade.sourceWallet).label ??= trade.sourceLabel;
  }
  for (const source of tradingAccounts?.sourceMetadata ?? []) {
    const item = ensureMetadata(source.sourceWallet);
    item.allocationPct ??= firstNumber([source.allocationPct]);
    item.label ??= source.sourceLabel;
    item.poolRank = minNumber([item.poolRank, source.poolRank]);
    item.rank = minNumber([item.rank, source.rank]);
    item.score ??= source.score;
  }
  return metadata;
}

function groupLivePositionsBySource(positions: TradingPosition[]) {
  const grouped = new Map<string, TradingPosition[]>();
  for (const position of positions) {
    if (isLiveExchangePosition(position)) {
      continue;
    }
    const source = position.sourceWallet.toLowerCase();
    grouped.set(source, [...(grouped.get(source) ?? []), position]);
  }
  return grouped;
}

function groupLiveFillsBySource(fills: TradingFill[]) {
  const grouped = new Map<string, TradingFill[]>();
  for (const fill of fills) {
    if (isLiveExchangeSource(fill.sourceWallet)) {
      continue;
    }
    const source = fill.sourceWallet.toLowerCase();
    grouped.set(source, [...(grouped.get(source) ?? []), fill]);
  }
  return grouped;
}

function groupLiveOrdersBySource(orders: TradingOrder[]) {
  const grouped = new Map<string, TradingOrder[]>();
  for (const order of orders) {
    if (isLiveExchangeSource(order.sourceWallet)) {
      continue;
    }
    const source = order.sourceWallet.toLowerCase();
    grouped.set(source, [...(grouped.get(source) ?? []), order]);
  }
  return grouped;
}

function latestDateStringFromValues(values: Array<string | null | undefined>) {
  let latest: string | null = null;
  for (const value of values) {
    if (!value) {
      continue;
    }
    if (latest === null || dateMs(value) > dateMs(latest)) {
      latest = value;
    }
  }
  return latest;
}

function accountNetEquity(account: { equityUsd: string; unrealizedPnlUsd: string }) {
  return numberValue(account.equityUsd) + numberValue(account.unrealizedPnlUsd);
}

function liveAccountEquity(account: TradingAccount) {
  return numberValue(
    account.equityUsd ??
      account.perpEquityUsd ??
      account.tradableEquityUsd ??
      account.cashBalanceUsd ??
      0,
  );
}

export function displayLivePositions(positions: TradingPosition[]) {
  const accountKeysWithExchangePositions = new Set(
    positions
      .filter((position) => isLiveExchangePosition(position))
      .map((position) => position.accountKey),
  );
  return positions.filter(
    (position) =>
      isLiveExchangePosition(position) ||
      !accountKeysWithExchangePositions.has(position.accountKey),
  );
}

function isLiveExchangePosition(position: TradingPosition) {
  return isLiveExchangeSource(position.sourceWallet);
}

function isLiveExchangeSource(sourceWallet: string) {
  return sourceWallet === LIVE_EXCHANGE_SOURCE;
}

function formatLiveAccountStatus(status: TradingAccount["status"]) {
  if (status === "exit_only") {
    return "exit only";
  }
  return status;
}

function formatCapitalMode(value: string) {
  if (value === "standard_per_dex") {
    return "standard per dex";
  }
  if (value === "unified") {
    return "unified";
  }
  return value;
}

function latestDateString(left: string, right: string) {
  return dateMs(right) > dateMs(left) ? right : left;
}

function dateMs(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
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
  if (openPositionCount > 0 && canOpenNewPositions) {
    return "trading";
  }
  if (openPositionCount > 0) {
    return "retained";
  }
  if (!hasRealtimeSlot) {
    return "waiting_for_slot";
  }
  return "waiting_for_trades";
}

function liveSourceCanOpenNewPositions(
  allocations: PaperCopyAllocation[],
  liveCopyReady: boolean,
) {
  if (!liveCopyReady) {
    return false;
  }
  if (allocations.some((allocation) => allocation.canOpenNewPositions)) {
    return true;
  }
  return allocations.some(
    (allocation) =>
      allocation.hasRealtimeSlot &&
      allocation.sourceStatusReason === "paper_account_disabled",
  );
}

function liveAllocationSourceVisible(
  allocations: PaperCopyAllocation[],
  liveCopyReady: boolean,
) {
  if (!liveCopyReady) {
    return false;
  }
  return allocations.some(
    (allocation) =>
      allocation.canOpenNewPositions ||
      allocation.hasRealtimeSlot ||
      allocation.sourceStatus === "waiting_for_slot",
  );
}

function resolveLiveSourceStatus({
  canOpenNewPositions,
  hasRealtimeSlot,
  openPositionCount,
}: {
  canOpenNewPositions: boolean;
  hasRealtimeSlot: boolean;
  openPositionCount: number;
}): MonitoredSource["sourceStatus"] {
  if (openPositionCount > 0 && canOpenNewPositions) {
    return "trading";
  }
  if (openPositionCount > 0) {
    return "retained";
  }
  if (!hasRealtimeSlot) {
    return "waiting_for_slot";
  }
  return canOpenNewPositions ? "waiting_for_trades" : "waiting_for_slot";
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

function resolveLiveSourceStatusReason({
  allocations,
  canOpenNewPositions,
  openPositionCount,
  tradingAccounts,
}: {
  allocations: PaperCopyAllocation[];
  canOpenNewPositions: boolean;
  openPositionCount: number;
  tradingAccounts: TradingAccountsResponse;
}) {
  if (canOpenNewPositions) {
    return "live_copy_ready";
  }
  if (!tradingAccounts.liveTradingEnabled) {
    return "live_trading_disabled";
  }
  if (!tradingAccounts.liveCopyEnabled) {
    return "live_copy_disabled";
  }
  if (openPositionCount > 0) {
    return "live_existing_exposure_only";
  }
  if (!tradingAccounts.accounts.some((account) => account.accountType === "live" && account.status === "enabled")) {
    return "live_no_enabled_accounts";
  }
  return resolveSourceStatusReason(allocations);
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

function sourceStatusDetail(source: MonitoredSource, mode: TradingMode) {
  const reason = formatSourceStatusReasonForMode(source.sourceStatusReason, mode);
  if (source.sourceStatus === "trading") {
    return mode === "live" ? "active live source" : "active slot";
  }
  if (source.sourceStatus === "retained") {
    if (mode === "live") {
      return reason === "live exposure only" ? reason : `${reason}, live exposure only`;
    }
    return `${reason}, existing exposure only`;
  }
  if (source.sourceStatus === "waiting_for_trades") {
    return mode === "live" ? "ready for live entries" : "ready for new entries";
  }
  return reason;
}

function formatSourceStatusReasonForMode(reason: string | null, mode: TradingMode) {
  if (mode === "live" && reason === "paper_account_disabled") {
    return "copy source inactive";
  }
  return formatSourceStatusReason(reason);
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
  if (reason === "live_copy_ready") {
    return "live copy ready";
  }
  if (reason === "live_trading_disabled") {
    return "live trading disabled";
  }
  if (reason === "live_copy_disabled") {
    return "live copy disabled";
  }
  if (reason === "live_existing_exposure_only") {
    return "live exposure only";
  }
  if (reason === "live_no_enabled_accounts") {
    return "no enabled live accounts";
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

function formatMonitoringDuration(value: number | null | undefined) {
  if (!value || value <= 0) {
    return "-";
  }
  const totalMinutes = Math.max(Math.round(value / 60), 1);
  if (totalMinutes < 60) {
    return `${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 48) {
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `${days}d ${restHours}h` : `${days}d`;
}

function formatMonitoringPnlPerHour(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return undefined;
  }
  return `${formatCurrency(value)}/h`;
}

function monitoringTone(value: string | number | null | undefined): Tone {
  if (value === null || value === undefined) {
    return "neutral";
  }
  return numberValue(value) >= 0 ? "positive" : "danger";
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

function liveOrderStatusTone(status: string): Tone {
  if (status === "filled" || status === "accepted" || status === "submitted") {
    return "positive";
  }
  if (status === "planned" || status === "partially_filled") {
    return "warning";
  }
  if (status === "rejected" || status === "failed" || status === "canceled") {
    return "danger";
  }
  return "neutral";
}

function reasonLabel(value: string) {
  return value.replaceAll("_", " ");
}

function sourceDisplayName(label: string | null | undefined, address: string) {
  if (isLiveExchangeSource(address)) {
    return label?.trim() || "Exchange position";
  }
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
  if (isLiveExchangeSource(address)) {
    return "exchange";
  }
  if (address.length <= 14) {
    return address;
  }
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function shortIdentifier(value: string) {
  if (value.length <= 16) {
    return value;
  }
  return `${value.slice(0, 8)}...${value.slice(-6)}`;
}
