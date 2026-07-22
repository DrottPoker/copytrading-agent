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

import { DashboardMetric } from "@/components/DashboardSurface";
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
  LiveCopyDecision,
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
  monitorStatus: "monitored" | "connecting" | "offline" | "waiting";
  sourceStatus:
    | "trading"
    | "retained"
    | "entries_paused"
    | "waiting_for_trades"
    | "waiting_for_slot";
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
  feeUsd: number;
  fundingUsd: number;
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
  marginMode: "cross" | "isolated" | null;
  marginUsd: string | number | null;
  notionalUsd: string | number | null;
  currentNotionalUsd: string | number | null;
  entryPrice: string | number | null;
  size: string | number | null;
  realizedPnlUsd: string | number | null;
  fundingUsd: string | number | null;
  unrealizedPnlUsd: string | number | null;
  unrealizedPnlPct: string | number | null;
  addFillCount: number;
  closeFillCount: number;
  openedAt: string;
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
  liveRealizedPnlUsd: string | null;
  liveFillCount: number | null;
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
  const liveCopyDecisionRows = useMemo(
    () =>
      tradingMode === "live"
        ? buildLiveCopyDecisionActivities(tradingAccounts.recentLiveCopyDecisions, sourceLabels)
        : [],
    [sourceLabels, tradingAccounts.recentLiveCopyDecisions, tradingMode],
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
            summary.realtimeMonitoring,
          ),
    [
      liveSourcePositions,
      sourceMetadata,
      summary.realtimeMonitoring,
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
  const sourceStatusCounts = summarizeCopySourceStatuses(monitoredSources);
  const tradingSourceCount = sourceStatusCounts.trading;
  const sourceMonitorMeta = formatSourceMonitorMeta(sourceStatusCounts);
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
        <div className="rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm font-medium text-danger">
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
          detail={`${formatCurrency(metrics.fees)} fees | ${formatSignedCurrency(metrics.funding)} funding`}
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
          detail={sourceMonitorMeta}
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
          meta={sourceMonitorMeta}
        >
          {monitoredSources.length === 0 ? (
            <EmptyState text="No copy sources." />
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

      {tradingMode === "live" ? (
        <section>
          <PaginatedListPanel
            emptyText="No live copy decisions recorded yet."
            getKey={(decision) => decision.id}
            items={liveCopyDecisionRows}
            meta={`${formatInteger(liveCopyDecisionRows.length)} recent decisions`}
            renderItem={(decision) => <FillRow fill={decision} />}
            title="Copy Decisions"
          />
        </section>
      ) : null}
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
    <div className="inline-flex h-9 overflow-hidden rounded-md border border-line bg-subtle p-0.5">
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
                : "text-muted hover:bg-white/70 hover:text-ink"
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
  return (
    <DashboardMetric compact detail={detail} icon={Icon} label={label} tone={tone} value={value} />
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
    <section className="ui-panel overflow-hidden">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-1.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {meta ? <p className="text-xs text-muted">{meta}</p> : null}
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
        className="ui-button-secondary h-8 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-50"
      >
        Previous
      </button>
      <p className="text-xs text-muted">
        Page {formatInteger(page + 1)} of {formatInteger(pageCount)}
      </p>
      <button
        type="button"
        onClick={onNext}
        disabled={page >= pageCount - 1}
        className="ui-button-secondary h-8 px-3 text-xs disabled:cursor-not-allowed disabled:opacity-50"
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[1.2fr_repeat(5,minmax(0,1fr))_auto] xl:items-center">
        <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
          <div className="flex flex-wrap items-center gap-1">
            <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink">{account.label}</p>
            <StatusPill label={account.accountType} tone={account.accountType === "live" ? "positive" : "neutral"} />
            <StatusPill label={account.statusLabel} tone={account.statusTone} />
          </div>
          <p className="mt-1 truncate font-mono text-xs text-muted">{account.key}</p>
        </div>
        <RowStat label="Equity" value={formatCurrency(account.equityUsd)} />
        <RowStat label="Total" value={formatCurrency(account.totalPnlUsd)} tone={account.totalPnlUsd >= 0 ? "positive" : "danger"} />
        <RowStat
          label="Net realized"
          value={formatCurrency(account.realizedPnlUsd)}
          detail={`${formatCurrency(account.feeUsd)} fees | ${formatSignedCurrency(account.fundingUsd)} funding`}
          tone={account.realizedPnlUsd >= 0 ? "positive" : "danger"}
        />
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
            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-white text-secondary hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-60"
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
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
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
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
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
  const monitorTone = source.monitorStatus === "monitored"
    ? "positive"
    : source.monitorStatus === "connecting"
      ? "warning"
      : source.monitorStatus === "offline"
        ? "danger"
        : "neutral";
  const sourceTone =
    source.sourceStatus === "trading"
      ? "positive"
      : source.sourceStatus === "retained" || source.sourceStatus === "entries_paused"
        ? "warning"
        : "neutral";
  const sourceMetaParts = [
    formatPoolRank(source.poolRank),
    `${formatScore(source.score)} score`,
    mode === "paper"
      ? `${formatInteger(source.accountCount)} paper accounts`
      : `${formatInteger(source.enabledLiveAccountCount)} entry-enabled live accounts`,
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
                className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold leading-5 text-ink hover:text-brand"
              >
                {sourceDisplayName(source.sourceLabel, source.sourceWallet)}
              </Link>
              <CompactSourcePill label={source.monitorStatus} tone={monitorTone} />
              <CompactSourcePill label={formatSourceStatus(source.sourceStatus)} tone={sourceTone} />
            </div>
            <p className="mt-0.5 whitespace-normal break-words text-[11px] leading-4 text-muted">
              {sourceMeta}
            </p>
            {sourceDetail !== "active slot" && sourceDetail !== "active live source" ? (
              <p className="mt-0.5 whitespace-normal break-words text-[11px] leading-4 text-muted">
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
              className="ui-button-danger h-auto min-h-7 shrink-0 gap-1.5 px-2 py-1 text-xs disabled:cursor-not-allowed disabled:border-line disabled:bg-subtle disabled:text-faint"
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
  positive: "border-positive/25 bg-positive-soft text-positive",
  warning: "border-warning/25 bg-warning-soft text-warning",
  danger: "border-danger/25 bg-danger-soft text-danger",
  neutral: "border-line bg-subtle text-secondary",
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
        <p className="text-[10px] font-medium uppercase leading-4 text-muted">
          {label}
        </p>
        <p className={`whitespace-normal break-words font-mono text-xs font-semibold leading-4 ${valueClass}`}>
          {value}
        </p>
      </div>
      {detail ? (
        <p className="whitespace-normal break-words text-[11px] leading-4 text-muted">
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
        <p className="text-[10px] font-medium uppercase leading-4 text-muted">
          {label}
        </p>
        <p className="whitespace-normal break-words font-mono text-xs font-semibold leading-4 text-ink">
          {value}
        </p>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-line">
        <div
          className={`h-full ${barClass}`}
          style={{ width: `${Math.min(usedPct * 100, 100)}%` }}
        />
      </div>
      <p className="whitespace-normal break-words text-[11px] leading-4 text-muted">
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
  const fundingUsd = numberValue(position.fundingUsd ?? 0);
  const closeTitle =
    livePosition !== null
      ? "Close live position"
      : canClose
        ? "Close paper position"
        : "Execution price unavailable";
  return (
    <ListRow>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[1.15fr_repeat(8,minmax(0,0.72fr))_auto] xl:items-center">
        <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{position.coin}</p>
            <StatusPill label={position.accountType} tone={position.accountType === "live" ? "positive" : "neutral"} />
            <StatusPill label={position.side} tone={position.side === "long" ? "positive" : "warning"} />
            <span className="font-mono text-xs text-muted">
              {formatLeverage(position.leverage, position.marginMode)}
            </span>
          </div>
          <PositionOwnerWallet
            sourceLabel={position.sourceLabel}
            sourceWallet={position.sourceWallet}
          />
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
          label="Funding"
          value={formatSignedCurrency(position.fundingUsd)}
          tone={fundingUsd >= 0 ? "positive" : "danger"}
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
            className="ui-button-danger col-span-2 h-7 gap-1.5 px-2 text-xs disabled:cursor-not-allowed disabled:border-line disabled:bg-subtle disabled:text-faint sm:col-span-3 xl:col-span-1"
          >
            {isClosing ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <XCircle className="h-3.5 w-3.5" aria-hidden="true" />}
            Close
          </button>
        ) : (
          <span className="text-xs text-muted">Live</span>
        )}
      </div>
    </ListRow>
  );
}

export function PositionOwnerWallet({
  sourceLabel,
  sourceWallet,
}: {
  sourceLabel: string | null;
  sourceWallet: string;
}) {
  const isExchange = isLiveExchangeSource(sourceWallet);
  const sourceName = sourceDisplayName(sourceLabel, sourceWallet);

  return (
    <div className="mt-0.5 flex min-w-0 items-baseline gap-1.5 text-xs">
      <span className="shrink-0 text-[10px] font-medium uppercase text-muted">Owner</span>
      {isExchange ? (
        <span className="min-w-0 truncate font-semibold text-ink">No attributed source wallet</span>
      ) : (
        <Link
          href={`/wallets/${sourceWallet}`}
          aria-label={`Open owner wallet ${sourceName}`}
          title={sourceWallet}
          className="min-w-0 truncate rounded-sm font-semibold text-ink hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
        >
          {sourceName}
        </Link>
      )}
    </div>
  );
}

function WalletHistoryRow({ wallet }: { wallet: WalletPerformanceRow }) {
  const totalPnl = numberValue(wallet.totalPnlUsd);
  const isMonitored = wallet.monitorStatus === "monitored";
  return (
    <ListRow>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[1.05fr_repeat(6,minmax(0,0.75fr))] xl:items-center">
        <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
          <div className="flex flex-wrap items-center gap-1">
            <Link
              href={`/wallets/${wallet.sourceWallet}`}
              className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-brand"
            >
              {sourceDisplayName(wallet.sourceLabel, wallet.sourceWallet)}
            </Link>
            <StatusPill
              label={isMonitored ? "monitored" : "history"}
              tone={isMonitored ? "positive" : "neutral"}
            />
          </div>
          <p className="mt-0.5 text-[11px] leading-4 text-muted">
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[1.05fr_0.85fr_0.8fr_0.85fr_0.85fr_0.75fr] xl:items-center">
        <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{trade.coin}</p>
            {trade.side ? <StatusPill label={trade.side} tone={trade.side === "long" ? "positive" : "warning"} /> : null}
            {trade.isSourceLiquidation ? <StatusPill label="liquidation" tone="danger" /> : null}
            <span className="text-xs text-muted">{formatCloseType(trade.closeType)}</span>
          </div>
          <Link
            href={`/wallets/${trade.sourceWallet}`}
            className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
          >
            {sourceDisplayName(trade.sourceLabel, trade.sourceWallet)}
          </Link>
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
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[1.05fr_repeat(7,minmax(0,0.8fr))] xl:items-center">
        <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
          <div className="flex flex-wrap items-center gap-1">
            <p className="font-semibold text-ink">{trade.coin}</p>
            <StatusPill label={trade.side} tone={trade.side === "long" ? "positive" : "warning"} />
          </div>
          {isLiveExchangeSource(trade.sourceWallet) ? (
            <p className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
              {sourceName}
            </p>
          ) : (
            <Link
              href={`/wallets/${trade.sourceWallet}`}
              className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
            >
              {sourceName}
            </Link>
          )}
        </div>
        <RowStat label="Net PnL" value={formatCurrency(trade.netPnlUsd)} detail={`${formatCurrency(trade.realizedPnlUsd)} realized`} tone={netPnl >= 0 ? "positive" : "danger"} />
        <RowStat label="Closed" value={formatShortDateTime(trade.closedAt)} detail={formatTradeDuration(trade.durationMs)} />
        <RowStat label="Entry" value={formatPrice(trade.entryPrice)} detail={formatShortDateTime(trade.openedAt)} />
        <RowStat label="Exit" value={formatPrice(trade.exitPrice)} detail={`size ${formatSize(trade.size)}`} />
        <RowStat label="Notional" value={formatCurrency(trade.exitNotionalUsd)} detail={`${formatInteger(trade.openFillCount)} open, ${formatInteger(trade.closeFillCount)} close fills`} />
        <RowStat label="Fee" value={formatCurrency(trade.feeUsd)} />
        <RowStat
          label="Funding"
          value={formatSignedCurrency(trade.fundingUsd)}
          tone={numberValue(trade.fundingUsd) >= 0 ? "positive" : "danger"}
        />
      </div>
    </ListRow>
  );
}

function FillRow({ fill }: { fill: ExecutionActivityItem }) {
  return (
    <ListRow>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 sm:grid-cols-3 xl:grid-cols-[minmax(190px,1.25fr)_repeat(auto-fit,minmax(105px,1fr))] xl:items-start">
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
    <div className="col-span-2 min-w-0 sm:col-span-3 xl:col-span-1">
      <div className="flex flex-wrap items-center gap-1">
        {identity.href ? (
          <Link
            href={identity.href}
            className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-brand"
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
      <p className="mt-1 truncate font-mono text-xs text-muted">{identity.meta}</p>
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
  return <div className="border-b border-line px-3 py-1 last:border-b-0">{children}</div>;
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
      <p className="truncate text-[10px] font-medium uppercase leading-3 text-muted">{label}</p>
      <p className={`whitespace-normal break-words font-mono text-xs font-semibold leading-4 ${valueClass}`}>{value}</p>
      {detail ? <p className="whitespace-normal break-words text-[11px] leading-4 text-muted">{detail}</p> : null}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="px-3 py-6 text-center text-sm text-muted">
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
    feeUsd: numberValue(account.feeUsd),
    fundingUsd: 0,
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
        feeUsd: numberValue(account.feeUsd),
        fundingUsd: numberValue(account.fundingUsd),
        realizedPnlUsd:
          numberValue(account.realizedPnlUsd) -
          numberValue(account.feeUsd) +
          numberValue(account.fundingUsd),
        statusLabel: formatLiveAccountStatus(account.status),
        statusTone: account.status === "enabled" ? "positive" : account.status === "exit_only" ? "warning" : "neutral",
        totalPnlUsd:
          numberValue(account.realizedPnlUsd) -
          numberValue(account.feeUsd) +
          numberValue(account.fundingUsd) +
          liveUnrealizedPnl,
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
    marginMode: null,
    marginUsd: position.marginUsd,
    markPrice: position.markPrice,
    notionalUsd: position.notionalUsd,
    paperPosition: position,
    livePosition: null,
    priceUpdatedAt: position.priceUpdatedAt,
    realizedPnlUsd: position.realizedPnlUsd,
    fundingUsd: 0,
    side: position.side,
    size: position.size,
    sourceLabel: position.sourceLabel,
    sourceWallet: position.sourceWallet,
    addFillCount: position.addFillCount,
    closeFillCount: position.closeFillCount,
    openedAt: position.openedAt,
    unrealizedPnlPct: position.unrealizedPnlPct,
    unrealizedPnlUsd: position.unrealizedPnlUsd,
    updatedAt: position.updatedAt,
  })).sort(compareDashboardPositionsOldestFirst);
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
    marginMode: position.marginMode,
    marginUsd: position.marginUsd,
    markPrice: position.markPrice,
    notionalUsd: position.notionalUsd,
    paperPosition: null,
    livePosition: position,
    priceUpdatedAt: position.priceUpdatedAt ?? position.lastReconciledAt,
    realizedPnlUsd: position.realizedPnlUsd,
    fundingUsd: position.fundingUsd,
    side: position.side,
    size: position.size,
    sourceLabel: isLiveExchangePosition(position)
      ? "Exchange position"
      : sourceLabels.get(position.sourceWallet.toLowerCase()) ?? null,
    sourceWallet: position.sourceWallet,
    addFillCount: position.addFillCount,
    closeFillCount: position.closeFillCount,
    openedAt: position.openedAt,
    unrealizedPnlPct: position.unrealizedPnlPct,
    unrealizedPnlUsd: position.unrealizedPnlUsd,
    updatedAt: position.updatedAt,
  })).sort(compareDashboardPositionsOldestFirst);
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

export function buildLiveCopyDecisionActivities(
  decisions: LiveCopyDecision[],
  sourceLabels: Map<string, string>,
): ExecutionActivityItem[] {
  return decisions
    .map<ExecutionActivityItem>((decision) => buildLiveCopyDecisionActivity(decision, sourceLabels))
    .sort((left, right) => dateMs(right.sortAt) - dateMs(left.sortAt));
}

export function buildLiveCopyDecisionActivity(
  decision: LiveCopyDecision,
  sourceLabels: Map<string, string>,
): ExecutionActivityItem {
  const isExchange = isLiveExchangeSource(decision.sourceWallet);
  const statusPills = liveCopyDecisionStatusPills(decision);
  const pipeline = liveCopyDecisionPipeline(decision);
  return {
    identity: {
      href: isExchange ? null : `/wallets/${decision.sourceWallet}`,
      label: isExchange
        ? "Copy decision"
        : sourceDisplayName(sourceLabels.get(decision.sourceWallet.toLowerCase()), decision.sourceWallet),
      meta: `${isExchange ? "exchange" : shortAddress(decision.sourceWallet)} | ${decision.accountKey} | source ${shortIdentifier(decision.sourceFillId)}`,
    },
    id: `copy-decision:${decision.accountKey}:${decision.sourceWallet}:${decision.sourceFillId}:${decision.sequenceIndex}`,
    pills: [
      { label: "copy decision", tone: "neutral" },
      { label: liveCopyDecisionOriginLabel(decision.origin), tone: "neutral" },
      {
        label: reasonLabel(decision.plannedAction),
        tone: decision.plannedAction.includes("close") || decision.plannedAction === "reduce"
          ? "neutral"
          : "positive",
      },
      { label: decision.side, tone: decision.side === "long" ? "positive" : "warning" },
      ...statusPills,
    ],
    sortAt: decision.decisionAt ?? decision.updatedAt,
    stats: [
      {
        label: "Market",
        value: decision.coin,
        detail: `sequence ${formatInteger(decision.sequenceIndex)}`,
      },
      {
        label: "Reason",
        value: decision.latestExchangeErrorCode
          ? reasonLabel(decision.latestExchangeErrorCode)
          : decision.reason
            ? reasonLabel(decision.reason)
            : "-",
        detail: decision.latestExchangeErrorMessage ?? decision.logicalOrderError ?? undefined,
        tone: decision.latestExchangeErrorCode || decision.outcome === "terminal_skip"
          ? "danger"
          : "neutral",
      },
      {
        label: "Prerequisite retries",
        value: formatInteger(decision.attemptCount),
        detail: decision.nextAttemptAt
          ? `next ${formatShortDateTime(decision.nextAttemptAt)}`
          : decision.lastAttemptAt
            ? `last ${formatShortDateTime(decision.lastAttemptAt)}`
            : "not attempted",
      },
      {
        label: "Exchange attempts",
        value: formatInteger(decision.submitAttemptCount),
        detail: [
          decision.latestDispatchAttemptNumber
            ? `attempt ${formatInteger(decision.latestDispatchAttemptNumber)}`
            : "not submitted",
          decision.latestDispatchClientOrderId
            ? `CLOID ${shortIdentifier(decision.latestDispatchClientOrderId)}`
            : null,
          decision.statusLookupCount > 0
            ? `status lookups ${formatInteger(decision.statusLookupCount)}`
            : "no status lookup",
        ].filter(Boolean).join(" | "),
        tone: liveCopyDecisionExchangeTone(decision),
      },
      {
        label: "First observed",
        value: formatShortDateTime(decision.firstObservedAt),
        detail: [
          `source ${formatShortDateTime(sourceTimestampDate(decision.sourceTimestampMs))}`,
          `ingest ${formatIngestLag(decision)}`,
        ].join(" | "),
      },
      {
        label: "Pipeline",
        value: pipeline.label,
        detail: `${pipeline.detail} | age ${formatDecisionAge(decision)} | queue ${formatQueueLag(decision)} | prep ${formatPreparationLag(decision)} | work ${formatProcessingLag(decision)}`,
        tone: pipeline.tone,
      },
    ],
  };
}

export function liveCopyDecisionStatusPills(decision: LiveCopyDecision): RowPill[] {
  const orderRecordId = decision.orderRecordId ?? decision.tradingOrderId;
  const outcomePill: RowPill =
    decision.outcome === "pending"
      ? { label: "waiting/pending", tone: "warning" }
      : decision.outcome === "retryable"
        ? { label: decision.nextAttemptAt ? "retry scheduled" : "retry pending", tone: "warning" }
        : decision.outcome === "baseline_ignored"
          ? { label: "baseline ignored", tone: "neutral" }
          : decision.outcome === "terminal_skip" && decision.reason === "live_source_fill_too_old"
            ? { label: "stale no-order", tone: "danger" }
          : decision.outcome === "terminal_skip"
            ? { label: "no-order blocked", tone: "danger" }
            : orderRecordId
              ? { label: "order recorded", tone: "neutral" }
              : { label: "record missing", tone: "danger" };

  const exchangeStatus = decision.latestExchangeStatus ?? decision.logicalOrderStatus;
  if (!exchangeStatus) {
    return [outcomePill];
  }
  return [
    outcomePill,
    { label: reasonLabel(exchangeStatus), tone: liveOrderStatusTone(exchangeStatus) },
  ];
}

function liveCopyDecisionExchangeTone(decision: LiveCopyDecision): Tone {
  return liveOrderStatusTone(
    decision.latestExchangeStatus
      ?? decision.logicalOrderStatus
      ?? decision.latestDispatchStatus
      ?? "unknown",
  );
}

function liveCopyDecisionPipeline(decision: LiveCopyDecision): {
  label: string;
  detail: string;
  tone: Tone;
} {
  const logicalStatus = decision.logicalOrderStatus;
  const exchangeStatus = decision.latestExchangeStatus;
  const orderRecordId = decision.orderRecordId ?? decision.tradingOrderId;
  if (!orderRecordId) {
    return {
      label: decision.outcome === "terminal_skip" ? "pre-submit skip" : "decision only",
      detail: "no logical order record",
      tone: decision.outcome === "terminal_skip" ? "danger" : "warning",
    };
  }
  if (exchangeStatus === "filled" || logicalStatus === "filled") {
    return {
      label: "filled",
      detail: `order ${shortIdentifier(orderRecordId)}`,
      tone: "positive",
    };
  }
  if (exchangeStatus === "accepted" || logicalStatus === "accepted") {
    return {
      label: "accepted",
      detail: `order ${shortIdentifier(orderRecordId)}`,
      tone: "positive",
    };
  }
  if (exchangeStatus === "rejected" || logicalStatus === "rejected") {
    return {
      label: decision.nextAttemptAt ? "exchange rejected, retry scheduled" : "exchange rejected",
      detail: decision.latestExchangeErrorMessage ?? decision.logicalOrderError ?? "exchange rejected",
      tone: "danger",
    };
  }
  if (logicalStatus === "failed" || logicalStatus === "canceled") {
    return {
      label: decision.submitAttemptCount === 0 ? "pre-submit skip" : logicalStatus,
      detail: decision.logicalOrderError ?? `order ${shortIdentifier(orderRecordId)}`,
      tone: "danger",
    };
  }
  if (logicalStatus === "uncertain" || decision.latestDispatchStatus === "uncertain") {
    return {
      label: "status unknown",
      detail: decision.lastStatusLookupError ?? "awaiting exchange status lookup",
      tone: "warning",
    };
  }
  return {
    label: logicalStatus ?? "order recorded",
    detail: `order ${shortIdentifier(orderRecordId)}${
      decision.latestDispatchClientOrderId
        ? ` | CLOID ${shortIdentifier(decision.latestDispatchClientOrderId)}`
        : ""
    }`,
    tone: liveCopyDecisionExchangeTone(decision),
  };
}

function liveCopyDecisionOriginLabel(origin: LiveCopyDecision["origin"]) {
  return origin.replaceAll("_", " ");
}

function sourceTimestampDate(sourceTimestampMs: number | null | undefined) {
  if (sourceTimestampMs === null || sourceTimestampMs === undefined || !Number.isFinite(sourceTimestampMs) || sourceTimestampMs <= 0) {
    return null;
  }
  return new Date(sourceTimestampMs).toISOString();
}

function formatDecisionAge(decision: LiveCopyDecision) {
  return formatElapsedMs(decisionTimestampMs(decision) - decision.sourceTimestampMs);
}

function formatIngestLag(decision: LiveCopyDecision) {
  const firstObservedTimestamp = decision.firstObservedAt ?? decision.observedAt;
  if (!firstObservedTimestamp) {
    return "-";
  }
  return formatElapsedMs(dateMs(firstObservedTimestamp) - decision.sourceTimestampMs);
}

function formatQueueLag(decision: LiveCopyDecision) {
  const firstObservedTimestamp = decision.firstObservedAt ?? decision.observedAt;
  const claimedTimestamp = decision.executionClaimedAt ?? decision.processingStartedAt;
  if (!firstObservedTimestamp || !claimedTimestamp) {
    return "-";
  }
  return formatElapsedMs(dateMs(claimedTimestamp) - dateMs(firstObservedTimestamp));
}

function formatProcessingLag(decision: LiveCopyDecision) {
  const firstProcessingTimestamp = decision.processingStartedAt ?? decision.executionClaimedAt;
  if (!firstProcessingTimestamp) {
    return "-";
  }
  return formatElapsedMs(decisionTimestampMs(decision) - dateMs(firstProcessingTimestamp));
}

function formatPreparationLag(decision: LiveCopyDecision) {
  if (!decision.executionClaimedAt || !decision.processingStartedAt) {
    return "-";
  }
  return formatElapsedMs(dateMs(decision.processingStartedAt) - dateMs(decision.executionClaimedAt));
}

function decisionTimestampMs(decision: LiveCopyDecision) {
  return dateMs(decision.decisionAt ?? decision.updatedAt);
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
    sortAt: order.filledAt ?? order.updatedAt ?? order.createdAt,
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
          : formatLeverage(order.leverage, order.marginMode),
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
    funding: 0,
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
  const liveFunding = tradingAccounts.accounts
    .filter((account) => account.accountType === "live")
    .reduce((total, account) => total + numberValue(account.fundingUsd), 0);
  const liveNetRealizedPnl = liveRealizedPnl - liveFees + liveFunding;
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
    funding: liveFunding,
    netEquity: liveEquity,
    openMargin: liveOpenMargin,
    openNotional: liveOpenNotional,
    realizedPnl: liveNetRealizedPnl,
    totalPnl: liveNetRealizedPnl + liveUnrealizedPnl,
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
      const monitorStatus = resolveCurrentMonitorStatus({
        hasRealtimeSlot,
        realtimeMonitoring: summary.realtimeMonitoring,
        sourceWallet: source,
      });
      const openPositionCount = openPositions.length;
      const sourceStatus = resolveSourceStatus(
        allocations,
        openPositionCount,
        monitorStatus === "monitored",
      );
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
    .sort(compareCopySourcesByAllocationUsed);
}

function buildLiveMonitoredSources(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  enabledLiveAccountCount: number,
  livePositions: TradingPosition[],
): MonitoredSource[] {
  const metadataBySource = buildSourceMetadata(summary, tradingAccounts);
  const liveCopyReady = tradingAccounts.liveTradingEnabled && enabledLiveAccountCount > 0;
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
  const paperPositionsBySource = groupPaperPositionsBySource(summary.positions);
  const liveFillsBySource = groupLiveFillsBySource(tradingAccounts.recentFills);
  const liveOrdersBySource = groupLiveOrdersBySource(tradingAccounts.recentOrders);
  const realtimeSlotSources = new Set(
    [
      ...summary.realtimeMonitoring.desiredWallets,
      ...summary.realtimeMonitoring.monitoredWallets,
    ].map((source) => source.toLowerCase()),
  );
  const sources = new Set(
    collectLiveCopySourceWallets({
      allocations: summary.allocations,
      liveCopyReady,
      livePositionSources: livePositionsBySource.keys(),
      realtimeMonitoring: summary.realtimeMonitoring,
    }),
  );

  return Array.from(sources)
    .map((source) => {
      const allocations = allocationsBySource.get(source) ?? [];
      const liveOpenPositions = livePositionsBySource.get(source) ?? [];
      const paperOpenPositions = paperPositionsBySource.get(source) ?? [];
      const liveFills = liveFillsBySource.get(source) ?? [];
      const liveOrders = liveOrdersBySource.get(source) ?? [];
      const metadata = metadataBySource.get(source);
      const canOpenNewPositions = liveSourceCanOpenNewPositions(allocations, liveCopyReady);
      const openPositionCount = liveOpenPositions.length;
      const hasRealtimeSlot =
        allocations.some((allocation) => allocation.hasRealtimeSlot) ||
        realtimeSlotSources.has(source) ||
        (allocations.length === 0 && openPositionCount > 0);
      const monitorStatus = resolveCurrentMonitorStatus({
        hasRealtimeSlot,
        realtimeMonitoring: summary.realtimeMonitoring,
        sourceWallet: source,
      });
      const sourceStatus = resolveCurrentSourceStatus({
        canOpenNewPositions,
        hasRealtimeSlot: hasRealtimeSlot || monitorStatus === "monitored",
        openPositionCount,
      });
      const realizedPnl = metadata?.liveRealizedPnlUsd !== null && metadata?.liveRealizedPnlUsd !== undefined
        ? numberValue(metadata.liveRealizedPnlUsd)
        : sumNumbers(liveFills.map((fill) => fill.realizedPnlUsd));
      const unrealizedPnl = sumNumbers(liveOpenPositions.map((position) => position.unrealizedPnlUsd));
      const totalPnl = realizedPnl + unrealizedPnl;
      const monitoredSeconds = metadata?.monitoredSeconds ?? 0;
      const realizedPnlPerHour = pnlPerMonitoredHour(realizedPnl, monitoredSeconds);
      const totalPnlPerHour = pnlPerMonitoredHour(totalPnl, monitoredSeconds);
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
        openPaperPositionCount: paperOpenPositions.length,
        openPositionCount,
        allocationPct: allocationPct > 0 ? allocationPct : null,
        allocationUsd,
        openMarginUsd,
        openNotionalUsd: sumNumbers(
          liveOpenPositions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
        ),
        remainingAllocationUsd: Math.max(allocationUsd - openMarginUsd, 0),
        pocketUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
        recentLiveFillCount: metadata?.liveFillCount ?? liveFills.length,
        recentLiveOrderCount: liveOrders.length,
        realizedPnlUsd: String(realizedPnl),
        unrealizedPnlUsd: String(unrealizedPnl),
        totalPnlUsd: String(totalPnl),
        monitoredSeconds,
        monitoredHours: String(monitoredSeconds / 3600),
        realizedPnlPerMonitoredHourUsd:
          realizedPnlPerHour === null ? null : String(realizedPnlPerHour),
        totalPnlPerMonitoredHourUsd: totalPnlPerHour === null ? null : String(totalPnlPerHour),
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
    .sort(compareCopySourcesByAllocationUsed);
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
    .sort(compareWalletHistoryByPnl);
}

function buildLiveWalletHistory(
  liveFills: TradingFill[],
  liveOrders: TradingOrder[],
  livePositions: TradingPosition[],
  sourceMetadata: Map<string, SourceMetadata>,
  realtimeMonitoring: PaperTradingSummaryResponse["realtimeMonitoring"],
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
      const isRealtimeMonitored = realtimeMonitoring.monitoredWallets.some(
        (wallet) => wallet.toLowerCase() === source,
      );
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
        monitorStatus: isRealtimeMonitored ? "monitored" : "history",
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
    .sort(compareWalletHistoryByPnl);
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
      liveRealizedPnlUsd: null,
      liveFillCount: null,
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
    item.liveRealizedPnlUsd = source.liveRealizedPnlUsd;
    item.liveFillCount = source.liveFillCount;
    if (source.monitoredSeconds >= item.monitoredSeconds) {
      item.monitoredSeconds = source.monitoredSeconds;
      item.monitoredHours = String(source.monitoredSeconds / 3600);
      item.firstMonitoredAt = source.firstMonitoredAt;
      item.currentMonitoringStartedAt = source.currentMonitoringStartedAt;
      item.lastMonitoredAt = source.lastMonitoredAt;
    }
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

function groupPaperPositionsBySource(positions: PaperPosition[]) {
  const grouped = new Map<string, PaperPosition[]>();
  for (const position of positions) {
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
  const sourcePositionsByMarket = new Map<string, TradingPosition[]>();
  for (const position of positions) {
    if (isLiveExchangePosition(position)) {
      continue;
    }
    const key = livePositionAttributionKey(position);
    sourcePositionsByMarket.set(key, [
      ...(sourcePositionsByMarket.get(key) ?? []),
      position,
    ]);
  }
  const exchangePositionKeys = new Set(
    positions
      .filter((position) => isLiveExchangePosition(position))
      .map((position) => livePositionKey(position)),
  );
  return positions
    .filter(
      (position) =>
        isLiveExchangePosition(position) ||
        !exchangePositionKeys.has(livePositionKey(position)),
    )
    .map((position) => {
      if (!isLiveExchangePosition(position)) {
        return position;
      }
      const attributedPositions = sourcePositionsByMarket.get(
        livePositionAttributionKey(position),
      ) ?? [];
      if (attributedPositions.length !== 1) {
        return position;
      }
      return {
        ...position,
        sourceWallet: attributedPositions[0].sourceWallet,
      };
    });
}

function livePositionKey(position: TradingPosition) {
  return `${position.accountKey.toLowerCase()}:${position.coin.toUpperCase()}`;
}

function livePositionAttributionKey(position: TradingPosition) {
  return `${livePositionKey(position)}:${position.side}`;
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

export function summarizeCopySourceStatuses(
  sources: Array<Pick<MonitoredSource, "monitorStatus" | "sourceStatus">>,
) {
  return {
    trading: sources.filter((source) => source.sourceStatus === "trading").length,
    monitored: sources.filter((source) => source.monitorStatus === "monitored").length,
    connecting: sources.filter((source) => source.monitorStatus === "connecting").length,
    offline: sources.filter((source) => source.monitorStatus === "offline").length,
    waiting: sources.filter((source) => source.sourceStatus === "waiting_for_slot").length,
  };
}

export function resolveCurrentMonitorStatus({
  hasRealtimeSlot,
  realtimeMonitoring,
  sourceWallet,
}: {
  hasRealtimeSlot: boolean;
  realtimeMonitoring: PaperTradingSummaryResponse["realtimeMonitoring"];
  sourceWallet: string;
}): MonitoredSource["monitorStatus"] {
  const normalizedSource = sourceWallet.toLowerCase();
  if (
    realtimeMonitoring.monitoredWallets.some(
      (wallet) => wallet.toLowerCase() === normalizedSource,
    )
  ) {
    return "monitored";
  }
  if (!hasRealtimeSlot) {
    return "waiting";
  }
  if (
    realtimeMonitoring.desiredWallets.some(
      (wallet) => wallet.toLowerCase() === normalizedSource,
    )
  ) {
    return "connecting";
  }
  return "offline";
}

function formatSourceMonitorMeta(
  counts: ReturnType<typeof summarizeCopySourceStatuses>,
) {
  return [
    `${formatInteger(counts.monitored)} monitored`,
    counts.connecting > 0 ? `${formatInteger(counts.connecting)} connecting` : null,
    counts.offline > 0 ? `${formatInteger(counts.offline)} offline` : null,
    `${formatInteger(counts.waiting)} waiting for slot`,
  ]
    .filter(Boolean)
    .join(", ");
}

function resolveSourceStatus(
  allocations: PaperCopyAllocation[],
  openPositionCount: number,
  isRealtimeMonitored: boolean,
): MonitoredSource["sourceStatus"] {
  const hasRealtimeSlot = allocations.some((allocation) => allocation.hasRealtimeSlot);
  const canOpenNewPositions = allocations.some((allocation) => allocation.canOpenNewPositions);
  return resolveCurrentSourceStatus({
    canOpenNewPositions,
    hasRealtimeSlot: hasRealtimeSlot || isRealtimeMonitored,
    openPositionCount,
  });
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

export function liveAllocationSourceVisible(
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

export function collectLiveCopySourceWallets({
  allocations,
  liveCopyReady,
  livePositionSources,
  realtimeMonitoring,
}: {
  allocations: PaperCopyAllocation[];
  liveCopyReady: boolean;
  livePositionSources: Iterable<string>;
  realtimeMonitoring: PaperTradingSummaryResponse["realtimeMonitoring"];
}) {
  const sources = new Set<string>();
  const addSource = (source: string) => {
    const normalized = source.trim().toLowerCase();
    if (normalized) {
      sources.add(normalized);
    }
  };

  for (const source of realtimeMonitoring.desiredWallets) {
    addSource(source);
  }
  for (const source of realtimeMonitoring.monitoredWallets) {
    addSource(source);
  }
  for (const source of livePositionSources) {
    addSource(source);
  }

  const allocationsBySource = new Map<string, PaperCopyAllocation[]>();
  for (const allocation of allocations) {
    const source = allocation.sourceWallet.toLowerCase();
    allocationsBySource.set(source, [...(allocationsBySource.get(source) ?? []), allocation]);
  }
  for (const [source, sourceAllocations] of allocationsBySource) {
    if (liveAllocationSourceVisible(sourceAllocations, liveCopyReady)) {
      addSource(source);
    }
  }

  return Array.from(sources);
}

export function resolveCurrentSourceStatus({
  canOpenNewPositions,
  hasRealtimeSlot,
  openPositionCount,
}: {
  canOpenNewPositions: boolean;
  hasRealtimeSlot: boolean;
  openPositionCount: number;
}): MonitoredSource["sourceStatus"] {
  if (openPositionCount > 0) {
    return canOpenNewPositions && hasRealtimeSlot ? "trading" : "retained";
  }
  if (!hasRealtimeSlot) {
    return "waiting_for_slot";
  }
  return canOpenNewPositions ? "waiting_for_trades" : "entries_paused";
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
  if (openPositionCount > 0) {
    return "live_existing_exposure_only";
  }
  if (!tradingAccounts.accounts.some((account) => account.accountType === "live" && account.status === "enabled")) {
    return "live_no_enabled_accounts";
  }
  return resolveSourceStatusReason(allocations);
}

function formatSourceStatus(status: MonitoredSource["sourceStatus"]) {
  if (status === "retained") {
    return "reduce only";
  }
  if (status === "entries_paused") {
    return "entries paused";
  }
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
    if (!source.canOpenNewPositions) {
      return mode === "live"
        ? "managing open live exposure, new entries paused"
        : "managing open paper exposure, new entries paused";
    }
    return mode === "live" ? "active live source" : "active slot";
  }
  if (source.sourceStatus === "retained") {
    if (mode === "live") {
      const retainedExposure = source.openLivePositionCount > 0
        ? source.openPaperPositionCount > 0
          ? "retained for open paper and live exposure"
          : "retained for open live exposure"
        : source.openPaperPositionCount > 0
          ? "retained for open paper exposure"
          : null;
      return retainedExposure && reason !== "live exposure only"
        ? `${reason}, ${retainedExposure}`
        : retainedExposure ?? reason;
    }
    return source.openPaperPositionCount > 0 && reason !== "existing exposure"
      ? `${reason}, existing exposure only`
      : reason;
  }
  if (source.sourceStatus === "waiting_for_trades") {
    return mode === "live" ? "ready for live entries" : "ready for new entries";
  }
  if (source.sourceStatus === "entries_paused") {
    return reason;
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
  if (status === "entries_paused") {
    return 3;
  }
  return 4;
}

type CopySourceSortValue = Pick<
  MonitoredSource,
  | "openMarginUsd"
  | "pocketUsedPct"
  | "poolRank"
  | "rank"
  | "realizedPnlUsd"
  | "sourceStatus"
  | "sourceWallet"
>;

export function compareCopySourcesByAllocationUsed(
  left: CopySourceSortValue,
  right: CopySourceSortValue,
) {
  const usedPctDiff = (right.pocketUsedPct ?? 0) - (left.pocketUsedPct ?? 0);
  if (usedPctDiff !== 0) {
    return usedPctDiff;
  }
  const usedUsdDiff = right.openMarginUsd - left.openMarginUsd;
  if (usedUsdDiff !== 0) {
    return usedUsdDiff;
  }
  const realizedDiff = numberValue(right.realizedPnlUsd) - numberValue(left.realizedPnlUsd);
  if (realizedDiff !== 0) {
    return realizedDiff;
  }
  const statusDiff = statusOrder(left.sourceStatus) - statusOrder(right.sourceStatus);
  if (statusDiff !== 0) {
    return statusDiff;
  }
  const rankDiff = (left.poolRank ?? left.rank ?? 9999) - (right.poolRank ?? right.rank ?? 9999);
  if (rankDiff !== 0) {
    return rankDiff;
  }
  return left.sourceWallet.localeCompare(right.sourceWallet);
}

type WalletHistorySortValue = Pick<
  WalletPerformanceRow,
  "poolRank" | "realizedPnlUsd" | "sourceWallet" | "totalPnlUsd"
>;

export function compareWalletHistoryByPnl(
  left: WalletHistorySortValue,
  right: WalletHistorySortValue,
) {
  const totalDiff = numberValue(right.totalPnlUsd) - numberValue(left.totalPnlUsd);
  if (totalDiff !== 0) {
    return totalDiff;
  }
  const realizedDiff = numberValue(right.realizedPnlUsd) - numberValue(left.realizedPnlUsd);
  if (realizedDiff !== 0) {
    return realizedDiff;
  }
  const rankDiff = (left.poolRank ?? 9999) - (right.poolRank ?? 9999);
  if (rankDiff !== 0) {
    return rankDiff;
  }
  return left.sourceWallet.localeCompare(right.sourceWallet);
}

type DashboardPositionSortValue = Pick<
  DashboardPosition,
  "accountKey" | "coin" | "id" | "openedAt" | "side" | "sourceWallet"
>;

export function compareDashboardPositionsOldestFirst(
  left: DashboardPositionSortValue,
  right: DashboardPositionSortValue,
) {
  const openedDiff = dateMs(left.openedAt) - dateMs(right.openedAt);
  if (openedDiff !== 0) {
    return openedDiff;
  }
  return [left.accountKey, left.sourceWallet, left.coin, left.side, left.id]
    .join("|")
    .localeCompare([right.accountKey, right.sourceWallet, right.coin, right.side, right.id].join("|"));
}

export function pnlPerMonitoredHour(pnlUsd: number, monitoredSeconds: number): number | null {
  if (!Number.isFinite(pnlUsd) || monitoredSeconds <= 0) {
    return null;
  }
  return (pnlUsd * 3600) / monitoredSeconds;
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

function formatSignedCurrency(value: string | number | null | undefined) {
  const resolved = numberValue(value ?? 0);
  if (resolved > 0) {
    return `+${formatCurrency(resolved)}`;
  }
  return formatCurrency(resolved);
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
  const amount = numberValue(value);
  return `${new Intl.NumberFormat("sv-SE", {
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: Math.abs(amount) < 1 ? 4 : 2,
    style: "currency",
  }).format(amount)}/h`;
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

function formatElapsedMs(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "-";
  }
  const elapsedMs = Math.max(0, value);
  if (elapsedMs < 1000) {
    return `${formatInteger(elapsedMs)} ms`;
  }
  const totalSeconds = elapsedMs / 1000;
  if (totalSeconds < 60) {
    return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(totalSeconds)} s`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    const seconds = Math.floor(totalSeconds % 60);
    return seconds > 0 ? `${totalMinutes}m ${seconds}s` : `${totalMinutes}m`;
  }
  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 48) {
    const minutes = totalMinutes % 60;
    return minutes > 0 ? `${totalHours}h ${minutes}m` : `${totalHours}h`;
  }
  const days = Math.floor(totalHours / 24);
  const hours = totalHours % 24;
  return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )} bps`;
}

function formatLeverage(
  value: string | number | null | undefined,
  marginMode?: "cross" | "isolated" | null,
) {
  if (value === null || value === undefined) {
    return "-";
  }
  const leverage = `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )}x`;
  return marginMode ? `${leverage} ${marginMode}` : leverage;
}

function formatCloseType(value: string) {
  if (value === "flip_close") {
    return "flip close";
  }
  return value;
}

function liveOrderStatusTone(status: string): Tone {
  if (status === "filled" || status === "accepted") {
    return "positive";
  }
  if (
    status === "planned" ||
    status === "ready" ||
    status === "submitting" ||
    status === "uncertain" ||
    status === "submitted" ||
    status === "partially_filled"
  ) {
    return "warning";
  }
  if (status === "rejected" || status === "failed" || status === "canceled") {
    return "danger";
  }
  return "neutral";
}

function reasonLabel(value: string) {
  const labels: Record<string, string> = {
    live_exit_market_owned_by_other_source:
      "exit ignored: position owned by another source wallet",
    live_market_reserved_by_other_source:
      "entry blocked: market owned by another source wallet",
    live_source_attribution_ambiguous:
      "ownership could not be proven from live fill history",
  };
  if (labels[value]) {
    return labels[value];
  }
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

export async function responseError(response: Response, fallback: string) {
  const requestId = response.headers.get("x-request-id")?.trim();
  const requestSuffix = requestId ? ` Request ID: ${requestId}.` : "";
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return `${payload.detail}${requestSuffix}`;
    }
  } catch {
    return `${fallback} with HTTP ${response.status}.${requestSuffix}`;
  }
  return `${fallback} with HTTP ${response.status}.${requestSuffix}`;
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
