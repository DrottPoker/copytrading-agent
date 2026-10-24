import { dateMs, decimal } from "@/lib/format";
import type { PaperCopyAllocation, PaperTradingSummaryResponse, PaperWalletPerformance } from "@/types/paper";
import type {
  TradingAccount,
  TradingAccountsResponse,
  TradingFill,
  TradingOrder,
  TradingPosition,
} from "@/types/trading";

import { formatSourceStatusReason, isLiveExchangeSource } from "./labels";
import { liveAccountEquity } from "./live";
import type { CopySourceRow, SourceMonitorStatus, SourceState } from "./types";

type RealtimeMonitoring = PaperTradingSummaryResponse["realtimeMonitoring"];

export type SourceMetadata = {
  allocationPct: number | null;
  firstMonitoredAt: string | null;
  label: string | null;
  lastMonitoredAt: string | null;
  liveFillCount: number | null;
  liveRealizedPnlUsd: string | null;
  monitoredSeconds: number;
  poolRank: number | null;
  rank: number | null;
  score: string | null;
};

/** Collects labels, ranks, and monitoring time for every source the API mentions. */
export function buildSourceMetadata(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const metadata = new Map<string, SourceMetadata>();
  const ensure = (wallet: string): SourceMetadata => {
    const source = wallet.toLowerCase();
    const existing = metadata.get(source);
    if (existing) {
      return existing;
    }
    const item: SourceMetadata = {
      allocationPct: null,
      firstMonitoredAt: null,
      label: null,
      lastMonitoredAt: null,
      liveFillCount: null,
      liveRealizedPnlUsd: null,
      monitoredSeconds: 0,
      poolRank: null,
      rank: null,
      score: null,
    };
    metadata.set(source, item);
    return item;
  };
  const addLabel = (wallet: string, label: string | null | undefined) => {
    const trimmed = label?.trim();
    if (trimmed) {
      ensure(wallet).label ??= trimmed;
    }
  };

  for (const allocation of summary.allocations) {
    const item = ensure(allocation.sourceWallet);
    addLabel(allocation.sourceWallet, allocation.sourceLabel);
    item.allocationPct ??= toNullableNumber(allocation.allocationPct);
    item.poolRank = minNullable(item.poolRank, allocation.poolRank);
    item.rank = minNullable(item.rank, allocation.rank);
    item.score ??= allocation.score;
  }
  for (const wallet of summary.walletPerformance) {
    const item = ensure(wallet.sourceWallet);
    addLabel(wallet.sourceWallet, wallet.sourceLabel);
    item.allocationPct ??= toNullableNumber(wallet.allocationPct);
    item.poolRank = minNullable(item.poolRank, wallet.poolRank);
    item.rank = minNullable(item.rank, wallet.rank);
    item.score ??= wallet.score;
    if (wallet.monitoredSeconds >= item.monitoredSeconds) {
      item.monitoredSeconds = wallet.monitoredSeconds;
      item.firstMonitoredAt = wallet.firstMonitoredAt;
      item.lastMonitoredAt = wallet.lastMonitoredAt;
    }
  }
  for (const position of summary.positions) {
    addLabel(position.sourceWallet, position.sourceLabel);
  }
  for (const fill of summary.recentFills) {
    addLabel(fill.sourceWallet, fill.sourceLabel);
  }
  for (const source of tradingAccounts?.sourceMetadata ?? []) {
    const item = ensure(source.sourceWallet);
    addLabel(source.sourceWallet, source.sourceLabel);
    item.allocationPct ??= toNullableNumber(source.allocationPct);
    item.poolRank = minNullable(item.poolRank, source.poolRank);
    item.rank = minNullable(item.rank, source.rank);
    item.score ??= source.score;
    item.liveRealizedPnlUsd = source.liveRealizedPnlUsd;
    item.liveFillCount = source.liveFillCount;
    if (source.monitoredSeconds >= item.monitoredSeconds) {
      item.monitoredSeconds = source.monitoredSeconds;
      item.firstMonitoredAt = source.firstMonitoredAt;
      item.lastMonitoredAt = source.lastMonitoredAt;
    }
  }
  return metadata;
}

export function buildSourceLabels(summary: PaperTradingSummaryResponse, tradingAccounts?: TradingAccountsResponse) {
  const labels = new Map<string, string>();
  for (const [source, metadata] of buildSourceMetadata(summary, tradingAccounts)) {
    if (metadata.label) {
      labels.set(source, metadata.label);
    }
  }
  return labels;
}

/** Monitored only after the realtime subscription is acknowledged by the worker. */
export function resolveCurrentMonitorStatus({
  hasRealtimeSlot,
  realtimeMonitoring,
  sourceWallet,
}: {
  hasRealtimeSlot: boolean;
  realtimeMonitoring: RealtimeMonitoring;
  sourceWallet: string;
}): SourceMonitorStatus {
  const source = sourceWallet.toLowerCase();
  if (realtimeMonitoring.monitoredWallets.some((wallet) => wallet.toLowerCase() === source)) {
    return "monitored";
  }
  if (!hasRealtimeSlot) {
    return "waiting";
  }
  if (realtimeMonitoring.desiredWallets.some((wallet) => wallet.toLowerCase() === source)) {
    return "connecting";
  }
  return "offline";
}

export function resolveCurrentSourceStatus({
  canOpenNewPositions,
  hasRealtimeSlot,
  openPositionCount,
}: {
  canOpenNewPositions: boolean;
  hasRealtimeSlot: boolean;
  openPositionCount: number;
}): SourceState {
  if (openPositionCount > 0) {
    return canOpenNewPositions && hasRealtimeSlot ? "trading" : "retained";
  }
  if (!hasRealtimeSlot) {
    return "waiting_for_slot";
  }
  return canOpenNewPositions ? "waiting_for_trades" : "entries_paused";
}

export function summarizeCopySourceStatuses(
  sources: Array<Pick<CopySourceRow, "monitorStatus" | "sourceState">>,
) {
  return {
    connecting: sources.filter((source) => source.monitorStatus === "connecting").length,
    monitored: sources.filter((source) => source.monitorStatus === "monitored").length,
    offline: sources.filter((source) => source.monitorStatus === "offline").length,
    trading: sources.filter((source) => source.sourceState === "trading").length,
    waiting: sources.filter((source) => source.sourceState === "waiting_for_slot").length,
  };
}

export const SOURCE_STATE_LABELS: Record<SourceState, string> = {
  entries_paused: "Entries paused",
  retained: "Reduce only",
  trading: "Trading",
  waiting_for_slot: "Waiting for slot",
  waiting_for_trades: "Ready",
};

export const MONITOR_STATUS_LABELS: Record<SourceMonitorStatus, string> = {
  connecting: "Connecting",
  monitored: "Live",
  offline: "Offline",
  waiting: "No slot",
};

/** Paper copy sources for one account, with PnL net of fees and funding since the account's last reset. */
export function buildPaperSourceRows(summary: PaperTradingSummaryResponse, accountKey: string): CopySourceRow[] {
  const performanceBySource = new Map(
    summary.walletPerformance
      .filter((wallet) => wallet.accountKey === accountKey)
      .map((wallet) => [wallet.sourceWallet.toLowerCase(), wallet]),
  );
  const allocationsBySource = groupBy(
    summary.allocations.filter((allocation) => allocation.accountKey === accountKey),
    (allocation) => allocation.sourceWallet.toLowerCase(),
  );
  const positionsBySource = groupBy(
    summary.positions.filter((position) => position.accountKey === accountKey),
    (position) => position.sourceWallet.toLowerCase(),
  );
  const fillsBySource = groupBy(
    summary.recentFills.filter((fill) => fill.accountKey === accountKey),
    (fill) => fill.sourceWallet.toLowerCase(),
  );

  const sources = new Set([...allocationsBySource.keys(), ...positionsBySource.keys()]);
  return Array.from(sources)
    .map((source) => {
      const allocations = allocationsBySource.get(source) ?? [];
      const positions = positionsBySource.get(source) ?? [];
      const fills = fillsBySource.get(source) ?? [];
      const performance = performanceBySource.get(source);
      const hasRealtimeSlot = allocations.some((allocation) => allocation.hasRealtimeSlot);
      const canOpenNewPositions = allocations.some((allocation) => allocation.canOpenNewPositions);
      const monitorStatus = resolveCurrentMonitorStatus({
        hasRealtimeSlot,
        realtimeMonitoring: summary.realtimeMonitoring,
        sourceWallet: source,
      });
      const sourceState = resolveCurrentSourceStatus({
        canOpenNewPositions,
        hasRealtimeSlot: hasRealtimeSlot || monitorStatus === "monitored",
        openPositionCount: positions.length,
      });
      const allocationUsd = sumNumbers(allocations.map((allocation) => allocation.allocationUsd));
      const openMarginUsd = sumNumbers(allocations.map((allocation) => allocation.openMarginUsd));
      const realizedPnlUsd = decimal(performance?.netRealizedPnlUsd);
      const unrealizedPnlUsd = sumNumbers(positions.map((position) => position.unrealizedPnlUsd));
      const monitoredSeconds = performance?.monitoredSeconds ?? 0;
      return {
        allocationPct: firstNumber(allocations.map((allocation) => allocation.allocationPct)),
        allocationUsd,
        canOpenNewPositions,
        closedTradeCount: performance?.closedTradeCount ?? 0,
        copiedFillCount: performance?.copiedFillCount ?? fills.filter((fill) => fill.action !== "skip").length,
        firstMonitoredAt: performance?.firstMonitoredAt ?? null,
        hasRealtimeSlot,
        lastActivityAt: latestDate([
          performance?.lastFillAt,
          ...fills.map((fill) => fill.filledAt),
          ...positions.map((position) => position.updatedAt),
        ]),
        monitorStatus,
        monitoredSeconds,
        openMarginUsd,
        openNotionalUsd: sumNumbers(positions.map((position) => position.currentNotionalUsd ?? position.notionalUsd)),
        openPaperPositionCount: positions.length,
        openPositionCount: positions.length,
        poolRank: minNumber(allocations.map((allocation) => allocation.poolRank)) ?? performance?.poolRank ?? null,
        rank: minNumber(allocations.map((allocation) => allocation.rank)) ?? performance?.rank ?? null,
        realizedPnlPerHourUsd: pnlPerMonitoredHour(realizedPnlUsd, monitoredSeconds),
        realizedPnlUsd,
        remainingAllocationUsd: sumNumbers(allocations.map((allocation) => allocation.remainingAllocationUsd)),
        score: firstString(allocations.map((allocation) => allocation.score)) ?? performance?.score ?? null,
        skippedFillCount: performance?.skippedFillCount ?? fills.filter((fill) => fill.action === "skip").length,
        sourceLabel:
          firstString(allocations.map((allocation) => allocation.sourceLabel)) ??
          performance?.sourceLabel ??
          firstString(positions.map((position) => position.sourceLabel)),
        sourceState,
        sourceStateDetail: sourceStateDetail({
          canOpenNewPositions,
          mode: "paper",
          openLivePositionCount: 0,
          openPaperPositionCount: positions.length,
          reason: resolveSourceStatusReason(allocations),
          sourceState,
        }),
        sourceWallet: source,
        totalPnlUsd: realizedPnlUsd + unrealizedPnlUsd,
        unrealizedPnlUsd,
        usedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
      } satisfies CopySourceRow;
    })
    .sort(compareCopySourcesByAllocationUsed);
}

export function liveAllocationSourceVisible(allocations: PaperCopyAllocation[], liveCopyReady: boolean) {
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
  realtimeMonitoring: RealtimeMonitoring;
}) {
  const sources = new Set<string>();
  const add = (source: string) => {
    const normalized = source.trim().toLowerCase();
    if (normalized) {
      sources.add(normalized);
    }
  };
  realtimeMonitoring.desiredWallets.forEach(add);
  realtimeMonitoring.monitoredWallets.forEach(add);
  for (const source of livePositionSources) {
    add(source);
  }
  for (const [source, sourceAllocations] of groupBy(allocations, (allocation) => allocation.sourceWallet.toLowerCase())) {
    if (liveAllocationSourceVisible(sourceAllocations, liveCopyReady)) {
      add(source);
    }
  }
  return Array.from(sources);
}

function liveSourceCanOpenNewPositions(allocations: PaperCopyAllocation[], liveCopyReady: boolean) {
  if (!liveCopyReady) {
    return false;
  }
  if (allocations.some((allocation) => allocation.canOpenNewPositions)) {
    return true;
  }
  // The paper allocation decides source eligibility; a stopped paper account must not block live.
  return allocations.some(
    (allocation) => allocation.hasRealtimeSlot && allocation.sourceStatusReason === "paper_account_disabled",
  );
}

/** Live copy sources for one live account. */
export function buildLiveSourceRows({
  account,
  fills,
  liveTradingEnabled,
  orders,
  positions,
  summary,
  tradingAccounts,
}: {
  account: TradingAccount;
  fills: TradingFill[];
  liveTradingEnabled: boolean;
  orders: TradingOrder[];
  positions: TradingPosition[];
  summary: PaperTradingSummaryResponse;
  tradingAccounts: TradingAccountsResponse;
}): CopySourceRow[] {
  const metadataBySource = buildSourceMetadata(summary, tradingAccounts);
  const liveCopyReady = liveTradingEnabled && account.status === "enabled";
  const allocationCapital = account.status === "enabled" || account.status === "exit_only" ? liveAccountEquity(account) : 0;
  const allocationsBySource = groupBy(summary.allocations, (allocation) => allocation.sourceWallet.toLowerCase());
  const sourcePositions = positions.filter((position) => !isLiveExchangeSource(position.sourceWallet));
  const positionsBySource = groupBy(sourcePositions, (position) => position.sourceWallet.toLowerCase());
  const paperPositionsBySource = groupBy(summary.positions, (position) => position.sourceWallet.toLowerCase());
  const fillsBySource = groupBy(
    fills.filter((fill) => !isLiveExchangeSource(fill.sourceWallet)),
    (fill) => fill.sourceWallet.toLowerCase(),
  );
  const ordersBySource = groupBy(
    orders.filter((order) => !isLiveExchangeSource(order.sourceWallet)),
    (order) => order.sourceWallet.toLowerCase(),
  );
  const realtimeSources = new Set(
    [...summary.realtimeMonitoring.desiredWallets, ...summary.realtimeMonitoring.monitoredWallets].map((wallet) =>
      wallet.toLowerCase(),
    ),
  );
  const sources = collectLiveCopySourceWallets({
    allocations: summary.allocations,
    liveCopyReady,
    livePositionSources: positionsBySource.keys(),
    realtimeMonitoring: summary.realtimeMonitoring,
  });

  return sources
    .map((source) => {
      const allocations = allocationsBySource.get(source) ?? [];
      const openPositions = positionsBySource.get(source) ?? [];
      const sourceFills = fillsBySource.get(source) ?? [];
      const sourceOrders = ordersBySource.get(source) ?? [];
      const metadata = metadataBySource.get(source);
      const canOpenNewPositions = liveSourceCanOpenNewPositions(allocations, liveCopyReady);
      const hasRealtimeSlot =
        allocations.some((allocation) => allocation.hasRealtimeSlot) ||
        realtimeSources.has(source) ||
        (allocations.length === 0 && openPositions.length > 0);
      const monitorStatus = resolveCurrentMonitorStatus({
        hasRealtimeSlot,
        realtimeMonitoring: summary.realtimeMonitoring,
        sourceWallet: source,
      });
      const sourceState = resolveCurrentSourceStatus({
        canOpenNewPositions,
        hasRealtimeSlot: hasRealtimeSlot || monitorStatus === "monitored",
        openPositionCount: openPositions.length,
      });
      const realizedPnlUsd =
        metadata?.liveRealizedPnlUsd !== null && metadata?.liveRealizedPnlUsd !== undefined
          ? decimal(metadata.liveRealizedPnlUsd)
          : sumNumbers(sourceFills.map((fill) => fill.realizedPnlUsd));
      const unrealizedPnlUsd = sumNumbers(openPositions.map((position) => position.unrealizedPnlUsd));
      const monitoredSeconds = metadata?.monitoredSeconds ?? 0;
      const allocationPct =
        metadata?.allocationPct ?? firstNumber(allocations.map((allocation) => allocation.allocationPct)) ?? 0;
      const allocationUsd = allocationCapital > 0 && allocationPct > 0 ? allocationCapital * allocationPct : 0;
      const openMarginUsd = sumNumbers(openPositions.map((position) => position.marginUsd));
      const reason = canOpenNewPositions
        ? "live_copy_ready"
        : !liveTradingEnabled
          ? "live_trading_disabled"
          : openPositions.length > 0
            ? "live_existing_exposure_only"
            : account.status !== "enabled"
              ? "live_no_enabled_accounts"
              : resolveSourceStatusReason(allocations);
      return {
        allocationPct: allocationPct > 0 ? allocationPct : null,
        allocationUsd,
        canOpenNewPositions,
        closedTradeCount: 0,
        copiedFillCount: metadata?.liveFillCount ?? sourceFills.length,
        firstMonitoredAt: metadata?.firstMonitoredAt ?? null,
        hasRealtimeSlot,
        lastActivityAt: latestDate([
          ...sourceFills.map((fill) => fill.filledAt),
          ...sourceOrders.map((order) => order.updatedAt ?? order.createdAt),
        ]),
        monitorStatus,
        monitoredSeconds,
        openMarginUsd,
        openNotionalUsd: sumNumbers(openPositions.map((position) => position.currentNotionalUsd ?? position.notionalUsd)),
        openPaperPositionCount: (paperPositionsBySource.get(source) ?? []).length,
        openPositionCount: openPositions.length,
        poolRank: metadata?.poolRank ?? minNumber(allocations.map((allocation) => allocation.poolRank)),
        rank: metadata?.rank ?? minNumber(allocations.map((allocation) => allocation.rank)),
        realizedPnlPerHourUsd: pnlPerMonitoredHour(realizedPnlUsd, monitoredSeconds),
        realizedPnlUsd,
        remainingAllocationUsd: Math.max(allocationUsd - openMarginUsd, 0),
        score: metadata?.score ?? firstString(allocations.map((allocation) => allocation.score)),
        skippedFillCount: sourceOrders.filter((order) => order.orderType === "skip").length,
        sourceLabel: metadata?.label ?? firstString(allocations.map((allocation) => allocation.sourceLabel)),
        sourceState,
        sourceStateDetail: sourceStateDetail({
          canOpenNewPositions,
          mode: "live",
          openLivePositionCount: openPositions.length,
          openPaperPositionCount: (paperPositionsBySource.get(source) ?? []).length,
          reason,
          sourceState,
        }),
        sourceWallet: source,
        totalPnlUsd: realizedPnlUsd + unrealizedPnlUsd,
        unrealizedPnlUsd,
        usedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
      } satisfies CopySourceRow;
    })
    .filter(
      (source) =>
        source.canOpenNewPositions ||
        source.hasRealtimeSlot ||
        source.sourceState === "waiting_for_slot" ||
        source.openPositionCount > 0 ||
        source.copiedFillCount > 0 ||
        source.skippedFillCount > 0,
    )
    .sort(compareCopySourcesByAllocationUsed);
}

function resolveSourceStatusReason(allocations: PaperCopyAllocation[]) {
  return (
    allocations.find((allocation) => allocation.canOpenNewPositions)?.sourceStatusReason ??
    allocations.find((allocation) => allocation.hasRealtimeSlot)?.sourceStatusReason ??
    allocations.find((allocation) => allocation.sourceStatusReason)?.sourceStatusReason ??
    null
  );
}

/** One sentence that explains what the copy engine does with this source right now. */
function sourceStateDetail({
  canOpenNewPositions,
  mode,
  openLivePositionCount,
  openPaperPositionCount,
  reason,
  sourceState,
}: {
  canOpenNewPositions: boolean;
  mode: "paper" | "live";
  openLivePositionCount: number;
  openPaperPositionCount: number;
  reason: string | null;
  sourceState: SourceState;
}) {
  const reasonText =
    mode === "live" && reason === "paper_account_disabled"
      ? "copy source inactive"
      : formatSourceStatusReason(reason);
  switch (sourceState) {
    case "trading":
      return canOpenNewPositions
        ? "Copying new trades and managing open positions."
        : "Managing open positions, new entries are paused.";
    case "retained":
      if (mode === "live" && openLivePositionCount > 0) {
        return `Only closing existing live positions: ${reasonText}.`;
      }
      return openPaperPositionCount > 0
        ? `Only closing existing positions: ${reasonText}.`
        : `Reduce only: ${reasonText}.`;
    case "waiting_for_trades":
      return "Subscribed and ready. Waiting for the wallet to open a trade.";
    case "entries_paused":
      return `New entries paused: ${reasonText}.`;
    case "waiting_for_slot":
      return "Waiting for a realtime slot to free up before copying starts.";
  }
}

const SOURCE_STATE_ORDER: Record<SourceState, number> = {
  entries_paused: 3,
  retained: 1,
  trading: 0,
  waiting_for_slot: 4,
  waiting_for_trades: 2,
};

export function compareCopySourcesByAllocationUsed(
  left: Pick<CopySourceRow, "openMarginUsd" | "usedPct" | "poolRank" | "rank" | "realizedPnlUsd" | "sourceState" | "sourceWallet">,
  right: Pick<CopySourceRow, "openMarginUsd" | "usedPct" | "poolRank" | "rank" | "realizedPnlUsd" | "sourceState" | "sourceWallet">,
) {
  return (
    (right.usedPct ?? 0) - (left.usedPct ?? 0) ||
    right.openMarginUsd - left.openMarginUsd ||
    right.realizedPnlUsd - left.realizedPnlUsd ||
    SOURCE_STATE_ORDER[left.sourceState] - SOURCE_STATE_ORDER[right.sourceState] ||
    (left.poolRank ?? left.rank ?? 9999) - (right.poolRank ?? right.rank ?? 9999) ||
    left.sourceWallet.localeCompare(right.sourceWallet)
  );
}

export type SourceHistoryRow = {
  copiedFillCount: number;
  feeUsd: number;
  fundingUsd: number | null;
  lastFillAt: string | null;
  monitorStatus: "monitored" | "history";
  monitoredSeconds: number;
  openMarginUsd: number;
  openPositionCount: number;
  poolRank: number | null;
  /** Gross realized PnL before fees and funding. */
  realizedPnlUsd: number;
  /** Realized PnL after fees and funding; null when the fees per source are not known (live). */
  netRealizedPnlUsd: number | null;
  /** Per followed hour, from the net realized PnL when known and the gross one otherwise. */
  realizedPnlPerHourUsd: number | null;
  score: string | null;
  skippedFillCount: number;
  sourceLabel: string | null;
  sourceWallet: string;
  /** Net realized PnL when known, else gross realized PnL, plus open PnL. */
  totalPnlUsd: number;
  unrealizedPnlUsd: number;
};

/** Every source with paper copy activity for one account since its last reset, best net PnL first. */
export function buildPaperSourceHistory(wallets: PaperWalletPerformance[], accountKey: string): SourceHistoryRow[] {
  return wallets
    .filter(
      (wallet) =>
        wallet.accountKey === accountKey &&
        (wallet.openPositionCount > 0 ||
        wallet.copiedFillCount > 0 ||
        wallet.skippedFillCount > 0 ||
        wallet.monitoredSeconds > 0 ||
        decimal(wallet.totalPnlUsd) !== 0),
    )
    .map((wallet) => ({
      copiedFillCount: wallet.copiedFillCount,
      feeUsd: decimal(wallet.feeUsd),
      fundingUsd: decimal(wallet.fundingUsd),
      lastFillAt: wallet.lastFillAt,
      monitorStatus: wallet.monitorStatus,
      monitoredSeconds: wallet.monitoredSeconds,
      openMarginUsd: decimal(wallet.openMarginUsd),
      openPositionCount: wallet.openPositionCount,
      netRealizedPnlUsd: decimal(wallet.netRealizedPnlUsd),
      poolRank: wallet.poolRank,
      realizedPnlPerHourUsd: toNullableNumber(wallet.netRealizedPnlPerMonitoredHourUsd),
      realizedPnlUsd: decimal(wallet.realizedPnlUsd),
      score: wallet.score,
      skippedFillCount: wallet.skippedFillCount,
      sourceLabel: wallet.sourceLabel,
      sourceWallet: wallet.sourceWallet,
      totalPnlUsd: decimal(wallet.totalPnlUsd),
      unrealizedPnlUsd: decimal(wallet.unrealizedPnlUsd),
    }))
    .sort(compareWalletHistoryByPnl);
}

/** Every source with live copy activity for one account, from all-time source metadata. */
export function buildLiveSourceHistory({
  fills,
  metadata,
  orders,
  positions,
  realtimeMonitoring,
}: {
  fills: TradingFill[];
  metadata: Map<string, SourceMetadata>;
  orders: TradingOrder[];
  positions: TradingPosition[];
  realtimeMonitoring: RealtimeMonitoring;
}): SourceHistoryRow[] {
  const positionsBySource = groupBy(
    positions.filter((position) => !isLiveExchangeSource(position.sourceWallet)),
    (position) => position.sourceWallet.toLowerCase(),
  );
  const fillsBySource = groupBy(
    fills.filter((fill) => !isLiveExchangeSource(fill.sourceWallet)),
    (fill) => fill.sourceWallet.toLowerCase(),
  );
  const ordersBySource = groupBy(
    orders.filter((order) => !isLiveExchangeSource(order.sourceWallet)),
    (order) => order.sourceWallet.toLowerCase(),
  );
  const sources = new Set([...positionsBySource.keys(), ...fillsBySource.keys(), ...ordersBySource.keys()]);
  for (const [source, item] of metadata) {
    if ((item.liveFillCount ?? 0) > 0) {
      sources.add(source);
    }
  }
  return Array.from(sources)
    .map((source) => {
      const sourcePositions = positionsBySource.get(source) ?? [];
      const sourceFills = fillsBySource.get(source) ?? [];
      const sourceOrders = ordersBySource.get(source) ?? [];
      const item = metadata.get(source);
      const realizedPnlUsd =
        item?.liveRealizedPnlUsd !== null && item?.liveRealizedPnlUsd !== undefined
          ? decimal(item.liveRealizedPnlUsd)
          : sumNumbers(sourceFills.map((fill) => fill.realizedPnlUsd));
      const unrealizedPnlUsd = sumNumbers(sourcePositions.map((position) => position.unrealizedPnlUsd));
      const monitoredSeconds = item?.monitoredSeconds ?? 0;
      return {
        copiedFillCount: item?.liveFillCount ?? sourceFills.length,
        feeUsd: sumNumbers(sourceFills.map((fill) => fill.feeUsd)),
        fundingUsd: null,
        lastFillAt: latestDate([
          ...sourceFills.map((fill) => fill.filledAt),
          ...sourceOrders.map((order) => order.updatedAt ?? order.createdAt),
        ]),
        monitorStatus: realtimeMonitoring.monitoredWallets.some((wallet) => wallet.toLowerCase() === source)
          ? ("monitored" as const)
          : ("history" as const),
        monitoredSeconds,
        openMarginUsd: sumNumbers(sourcePositions.map((position) => position.marginUsd)),
        netRealizedPnlUsd: null,
        openPositionCount: sourcePositions.length,
        poolRank: item?.poolRank ?? null,
        realizedPnlPerHourUsd: pnlPerMonitoredHour(realizedPnlUsd, monitoredSeconds),
        realizedPnlUsd,
        score: item?.score ?? null,
        skippedFillCount: sourceOrders.filter((order) => order.orderType === "skip").length,
        sourceLabel: item?.label ?? null,
        sourceWallet: source,
        totalPnlUsd: realizedPnlUsd + unrealizedPnlUsd,
        unrealizedPnlUsd,
      };
    })
    .filter(
      (row) =>
        row.openPositionCount > 0 || row.copiedFillCount > 0 || row.skippedFillCount > 0 || row.totalPnlUsd !== 0,
    )
    .sort(compareWalletHistoryByPnl);
}

type HistorySortFields = Pick<
  SourceHistoryRow,
  "netRealizedPnlUsd" | "poolRank" | "realizedPnlUsd" | "sourceWallet" | "totalPnlUsd"
>;

/** Best realized PnL first, net of fees and funding when known. */
export function compareWalletHistoryByPnl(left: HistorySortFields, right: HistorySortFields) {
  return (
    (right.netRealizedPnlUsd ?? right.realizedPnlUsd) - (left.netRealizedPnlUsd ?? left.realizedPnlUsd) ||
    right.totalPnlUsd - left.totalPnlUsd ||
    (left.poolRank ?? 9999) - (right.poolRank ?? 9999) ||
    left.sourceWallet.localeCompare(right.sourceWallet)
  );
}

export function pnlPerMonitoredHour(pnlUsd: number, monitoredSeconds: number): number | null {
  if (!Number.isFinite(pnlUsd) || monitoredSeconds <= 0) {
    return null;
  }
  return (pnlUsd * 3600) / monitoredSeconds;
}

export function groupBy<T>(items: T[], key: (item: T) => string) {
  const grouped = new Map<string, T[]>();
  for (const item of items) {
    const value = key(item);
    const existing = grouped.get(value);
    if (existing) {
      existing.push(item);
    } else {
      grouped.set(value, [item]);
    }
  }
  return grouped;
}

export function sumNumbers(values: Array<string | number | null | undefined>) {
  return values.reduce<number>((total, value) => total + decimal(value), 0);
}

function toNullableNumber(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function firstNumber(values: Array<string | number | null | undefined>) {
  for (const value of values) {
    const parsed = toNullableNumber(value);
    if (parsed !== null) {
      return parsed;
    }
  }
  return null;
}

function firstString(values: Array<string | number | null | undefined>) {
  for (const value of values) {
    if (value !== null && value !== undefined && String(value).trim()) {
      return String(value);
    }
  }
  return null;
}

function minNumber(values: Array<string | number | null | undefined>) {
  const numbers = values.map(toNullableNumber).filter((value): value is number => value !== null);
  return numbers.length > 0 ? Math.min(...numbers) : null;
}

function minNullable(left: number | null, right: number | null) {
  if (left === null) {
    return right;
  }
  if (right === null) {
    return left;
  }
  return Math.min(left, right);
}

export function latestDate(values: Array<string | null | undefined>) {
  let latest: string | null = null;
  for (const value of values) {
    if (value && (latest === null || dateMs(value) > dateMs(latest))) {
      latest = value;
    }
  }
  return latest;
}
