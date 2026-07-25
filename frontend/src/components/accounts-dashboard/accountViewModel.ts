import {
  Activity,
  BarChart3,
  Clock,
  Layers,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
} from "lucide-react";

import type { AccountPerformancePoint } from "@/components/AccountPerformanceChart";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
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

import type {
  AccountClosedTradeRow,
  AccountExecutionRow,
  AccountMetrics,
  AccountOption,
  AccountPositionRow,
  AccountView,
  MarketRow,
  SourceRow,
  Tone,
} from "./types";

type SourceMetadata = {
  allocationPct: number | null;
  label: string | null;
  poolRank: number | null;
  rank: number | null;
  score: string | null;
};

export function formatLiveAccountStatus(status: TradingAccount["status"]) {
  if (status === "exit_only") {
    return "exit only";
  }
  return status;
}

function formatCapitalMode(mode: TradingAccount["capitalMode"]) {
  if (mode === "standard_per_dex") {
    return "standard per DEX";
  }
  if (mode === "unified") {
    return "unified";
  }
  return "unknown";
}

export function lastUpdatedAt(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
) {
  return dateMs(tradingAccounts.updatedAt) > dateMs(summary.updatedAt)
    ? tradingAccounts.updatedAt
    : summary.updatedAt;
}

export function buildSelectedAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  selectedAccount: AccountOption | null,
): AccountView | null {
  if (!selectedAccount) {
    return null;
  }
  return selectedAccount.accountType === "paper"
    ? buildPaperAccountView(summary, selectedAccount.paper)
    : buildLiveAccountView(summary, tradingAccounts, selectedAccount.live);
}

function buildPaperAccountView(
  summary: PaperTradingSummaryResponse,
  account: PaperTradingAccount,
): AccountView {
  const allocations = summary.allocations.filter((item) => item.accountKey === account.key);
  const positions = summary.positions.filter((item) => item.accountKey === account.key);
  const closedTrades = summary.closedTrades.filter((item) => item.accountKey === account.key);
  const recentFills = summary.recentFills.filter((item) => item.accountKey === account.key);
  const sourceRows = buildSourceRows({
    allocations,
    closedTrades,
    positions,
    recentFills,
  });
  const metrics = buildAccountMetrics({
    account,
    allocations,
    closedTrades,
    recentFills,
  });
  const netEquityUsd = accountNetEquity(account);
  const startingBalance = decimal(account.startingBalanceUsd);
  const cashBalance = decimal(account.cashBalanceUsd);
  const openMargin = decimal(account.openMarginUsd);
  const unrealizedPnl = decimal(account.unrealizedPnlUsd);
  const totalPnl = decimal(account.totalPnlUsd);
  const realizedPnl = decimal(account.realizedPnlUsd);
  const balanceScale = Math.max(startingBalance, cashBalance, netEquityUsd, openMargin, 1);

  return {
    accountType: "paper",
    allocations,
    balanceLines: [
      {
        label: "Starting balance",
        tone: "neutral",
        value: startingBalance / balanceScale,
        valueLabel: formatCurrency(startingBalance),
      },
      {
        label: "Cash balance",
        tone: "neutral",
        value: cashBalance / balanceScale,
        valueLabel: formatCurrency(cashBalance),
      },
      {
        label: "Net equity",
        tone: netEquityUsd >= startingBalance ? "positive" : "danger",
        value: netEquityUsd / balanceScale,
        valueLabel: formatCurrency(netEquityUsd),
      },
      {
        label: "Open margin",
        tone: "warning",
        value: openMargin / balanceScale,
        valueLabel: formatCurrency(openMargin),
      },
      {
        label: "Unrealized PnL",
        tone: unrealizedPnl >= 0 ? "positive" : "danger",
        value: Math.abs(unrealizedPnl) / balanceScale,
        valueLabel: formatCurrency(unrealizedPnl),
      },
    ],
    capitalBalances: [],
    closedTrades: closedTrades.map(paperClosedTradeRow),
    detailSections: [
      {
        icon: WalletCards,
        title: "Account Details",
        rows: [
          { label: "Status", value: account.enabled ? "enabled" : "disabled" },
          { label: "Created", value: formatDate(account.createdAt) },
          { label: "Updated", value: formatDate(account.updatedAt) },
          { label: "Open positions", value: formatInteger(account.openPositionCount) },
        ],
      },
    ],
    marketRows: buildMarketRows(positions),
    metrics,
    metricTiles: [
      {
        detail: `${formatCurrency(account.cashBalanceUsd)} cash`,
        icon: WalletCards,
        label: "Net equity",
        value: formatCurrency(metrics.netEquityUsd),
      },
      {
        detail: "Return on starting capital",
        icon: totalPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Account return",
        tone: totalPnl >= 0 ? "positive" : "danger",
        value: formatPercent(metrics.returnPct),
      },
      {
        detail: `${formatCurrency(realizedPnl)} realized`,
        icon: totalPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Total PnL",
        tone: totalPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(totalPnl),
      },
      {
        detail: `${formatCurrency(account.feeUsd)} fees`,
        icon: realizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Realized",
        tone: realizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(realizedPnl),
      },
      {
        detail: `${formatInteger(account.openPositionCount)} open`,
        icon: unrealizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Unrealized",
        tone: unrealizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(unrealizedPnl),
      },
      {
        detail: `${formatPercent(metrics.exposureRatio)} of net equity`,
        icon: Target,
        label: "Open notional",
        value: formatCurrency(account.openNotionalUsd),
      },
      {
        detail: `${formatPercent(metrics.allocationUsedPct)} of allocation`,
        icon: Layers,
        label: "Open margin",
        value: formatCurrency(metrics.openMarginUsd),
      },
      {
        detail: `${formatInteger(closedTrades.length)} loaded trades`,
        icon: BarChart3,
        label: "Win rate",
        tone:
          metrics.winRate === null
            ? "neutral"
            : metrics.winRate >= 0.5
              ? "positive"
              : "warning",
        value: formatPercent(metrics.winRate),
      },
    ],
    positions: positions.map(paperPositionRow),
    recentActivity: recentFills.map(paperExecutionRow),
    sourceRows,
    timeline: buildAccountPerformanceTimeline(closedTrades.map(paperClosedTradeRow)),
  };
}


function buildLiveAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  account: TradingAccount,
): AccountView {
  const sourceMetadata = buildSourceMetadataMap(summary, tradingAccounts);
  const sourceLabels = buildSourceLabelMap(summary, tradingAccounts);
  const allPositions = tradingAccounts.positions.filter((item) => item.accountKey === account.key);
  const displayPositions = displayAccountLivePositions(allPositions);
  const sourcePositions = allPositions.filter((position) => !isLiveExchangeSource(position.sourceWallet));
  const sourcePerformancePositions = sourcePositions.length > 0 ? sourcePositions : displayPositions;
  const closedTrades = tradingAccounts.closedTrades.filter((item) => item.accountKey === account.key);
  const recentFills = tradingAccounts.recentFills.filter((item) => item.accountKey === account.key);
  const recentOrders = tradingAccounts.recentOrders.filter((item) => item.accountKey === account.key);
  const sourceRows = buildLiveSourceRows({
    closedTrades,
    positions: sourcePerformancePositions,
    recentFills,
    recentOrders,
    sourceMetadata,
    sourceLabels,
    allocationCapitalUsd: liveAccountEquity(account),
  });
  const metrics = buildLiveAccountMetrics({
    account,
    closedTrades,
    positions: displayPositions,
    recentFills,
    recentOrders,
  });
  const equity = liveAccountEquity(account);
  const cash = decimal(account.cashBalanceUsd);
  const tradable = decimal(account.tradableEquityUsd);
  const perpEquity = decimal(account.perpEquityUsd);
  const openMargin = sumNumbers(displayPositions.map((position) => position.marginUsd));
  const balanceScale = Math.max(equity, cash, tradable, perpEquity, openMargin, 1);
  const realizedPnl = decimal(account.realizedPnlUsd);
  const feeUsd = decimal(account.feeUsd);
  const timeWeightedReturn =
    account.timeWeightedReturnPct === null ? null : decimal(account.timeWeightedReturnPct);
  const capitalMode = formatCapitalMode(account.capitalMode);
  const reconciliationLabel =
    account.reconciliationStatus === "complete"
      ? "synced"
      : account.reconciliationStatus === "never"
        ? "pending"
        : account.reconciliationStatus;
  const reconciliationDetail =
    account.incompleteReconciliationComponents.length > 0
      ? `${account.incompleteReconciliationComponents.join(", ")} incomplete`
      : formatDate(account.reconciliationAttemptedAt ?? account.lastReconciledAt);
  const reconciliationTone: Tone =
    account.reconciliationStatus === "complete"
      ? "positive"
      : account.reconciliationStatus === "failed"
        ? "danger"
        : "warning";

  return {
    accountType: "live",
    allocations: [],
    balanceLines: [
      {
        label: "Equity",
        tone: "neutral",
        value: equity / balanceScale,
        valueLabel: formatCurrency(equity),
      },
      {
        label: "Tradable",
        tone: "positive",
        value: tradable / balanceScale,
        valueLabel: formatCurrency(tradable),
      },
      {
        label: "Cash balance",
        tone: "neutral",
        value: cash / balanceScale,
        valueLabel: formatCurrency(cash),
      },
      {
        label: "Perp equity",
        tone: "neutral",
        value: perpEquity / balanceScale,
        valueLabel: formatCurrency(perpEquity),
      },
      {
        label: "Open margin",
        tone: "warning",
        value: openMargin / balanceScale,
        valueLabel: formatCurrency(openMargin),
      },
    ],
    capitalBalances: account.capitalBalances,
    closedTrades: closedTrades.map((trade) => liveClosedTradeRow(trade, sourceLabels)),
    detailSections: [
      {
        icon: WalletCards,
        title: "Account Details",
        rows: [
          { label: "Status", value: formatLiveAccountStatus(account.status) },
          { label: "Status reason", value: account.statusReason ?? "none" },
          { label: "Status changed", value: formatDate(account.statusChangedAt) },
          { label: "Network", value: account.network },
          { label: "Capital mode", value: capitalMode },
          { label: "Abstraction", value: account.userAbstraction ?? "unknown" },
          { label: "Created", value: formatDate(account.createdAt) },
          { label: "Updated", value: formatDate(account.updatedAt) },
          {
            label: "Performance tracking",
            value: formatDate(account.performanceTrackingStartedAt),
          },
          {
            label: "Net external flows",
            value: formatCurrency(account.netExternalFlowsUsd),
          },
          {
            label: "Trading PnL",
            value: formatCurrency(account.tradingPnlUsd),
          },
        ],
      },
      {
        icon: Activity,
        title: "Exchange Routing",
        rows: [
          { label: "Wallet address", value: account.walletAddress ?? "config wallet" },
          { label: "Vault address", value: account.vaultAddress ?? "none" },
          { label: "Internal key", value: account.key },
          { label: "Reconciliation", value: account.reconciliationStatus },
          { label: "Last complete", value: formatDate(account.lastReconciledAt) },
          { label: "Last attempt", value: formatDate(account.reconciliationAttemptedAt) },
          {
            label: "Incomplete",
            value:
              account.incompleteReconciliationComponents.length > 0
                ? account.incompleteReconciliationComponents.join(", ")
                : "none",
          },
        ],
      },
    ],
    marketRows: buildMarketRows(displayPositions),
    metrics,
    metricTiles: [
      {
        detail: `${formatLiveAccountStatus(account.status)} on ${account.network}, ${capitalMode}`,
        icon: WalletCards,
        label: "Equity",
        value: formatCurrency(equity),
      },
      {
        detail: account.performanceTrackingStartedAt
          ? `TWR since ${formatDate(account.performanceTrackingStartedAt)}`
          : "Starts after a complete reconciliation",
        icon:
          timeWeightedReturn !== null && timeWeightedReturn < 0
            ? TrendingDown
            : TrendingUp,
        label: "Account return",
        tone:
          timeWeightedReturn === null
            ? "neutral"
            : timeWeightedReturn >= 0
              ? "positive"
              : "danger",
        value: formatPercent(timeWeightedReturn),
      },
      {
        detail: `${formatCurrency(account.netExternalFlowsUsd)} net flows`,
        icon:
          decimal(account.tradingPnlUsd) >= 0 ? TrendingUp : TrendingDown,
        label: "Trading PnL",
        tone:
          decimal(account.tradingPnlUsd) >= 0 ? "positive" : "danger",
        value: formatCurrency(account.tradingPnlUsd),
      },
      {
        detail: `${formatCurrency(feeUsd)} fees`,
        icon: realizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Realized",
        tone: realizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(realizedPnl),
      },
      {
        detail: `${formatInteger(displayPositions.length)} open`,
        icon:
          metrics.unrealizedPnlUsd >= 0 ? TrendingUp : TrendingDown,
        label: "Unrealized",
        tone: metrics.unrealizedPnlUsd >= 0 ? "positive" : "danger",
        value: formatCurrency(metrics.unrealizedPnlUsd),
      },
      {
        detail: `${formatPercent(metrics.exposureRatio)} of equity`,
        icon: Target,
        label: "Open notional",
        value: formatCurrency(metrics.openNotionalUsd),
      },
      {
        detail: `${formatMultiple(
          metrics.openMarginUsd > 0
            ? metrics.openNotionalUsd / metrics.openMarginUsd
            : null,
        )} average leverage`,
        icon: Layers,
        label: "Open margin",
        value: formatCurrency(metrics.openMarginUsd),
      },
      {
        detail: reconciliationDetail,
        icon: Clock,
        label: "Reconciled",
        tone: reconciliationTone,
        value: reconciliationLabel,
      },
    ],
    positions: displayPositions.map((position) => livePositionRow(position, sourceLabels)),
    recentActivity: buildLiveAccountExecutionRows(recentFills, recentOrders, sourceLabels),
    sourceRows,
    timeline: buildAccountPerformanceTimeline(
      closedTrades.map((trade) => liveClosedTradeRow(trade, sourceLabels)),
    ),
  };
}


function buildAccountMetrics({
  account,
  allocations,
  closedTrades,
  recentFills,
}: {
  account: PaperTradingAccount;
  allocations: PaperCopyAllocation[];
  closedTrades: PaperClosedTrade[];
  recentFills: PaperCopyFill[];
}): AccountMetrics {
  const netEquityUsd = accountNetEquity(account);
  const allocationUsd = sumNumbers(allocations.map((allocation) => allocation.allocationUsd));
  const openMarginUsd = decimal(account.openMarginUsd);
  const remainingAllocationUsd = sumNumbers(
    allocations.map((allocation) => allocation.remainingAllocationUsd),
  );
  const closedNetPnlUsd = sumNumbers(closedTrades.map((trade) => trade.netPnlUsd));
  const winningClosedTradeCount = closedTrades.filter((trade) => decimal(trade.netPnlUsd) > 0).length;
  const copiedFillCount = recentFills.filter((fill) => fill.action !== "skip").length;
  const skippedFillCount = recentFills.filter((fill) => fill.action === "skip").length;
  const startingBalance = decimal(account.startingBalanceUsd);

  return {
    allocationUsd,
    allocationUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
    averageClosedPnlUsd: closedTrades.length > 0 ? closedNetPnlUsd / closedTrades.length : 0,
    closedNetPnlUsd,
    copiedFillCount,
    exposureRatio: netEquityUsd > 0 ? decimal(account.openNotionalUsd) / netEquityUsd : null,
    feeUsd: decimal(account.feeUsd),
    netEquityUsd,
    openMarginUsd,
    openNotionalUsd: decimal(account.openNotionalUsd),
    realizedPnlUsd: decimal(account.realizedPnlUsd),
    remainingAllocationUsd,
    returnPct:
      account.totalPnlPct !== null
        ? decimal(account.totalPnlPct)
        : startingBalance > 0
          ? decimal(account.totalPnlUsd) / startingBalance
          : null,
    skippedFillCount,
    unrealizedPnlUsd: decimal(account.unrealizedPnlUsd),
    winRate: closedTrades.length > 0 ? winningClosedTradeCount / closedTrades.length : null,
  };
}

function buildLiveAccountMetrics({
  account,
  closedTrades,
  positions,
  recentFills,
  recentOrders,
}: {
  account: TradingAccount;
  closedTrades: TradingClosedTrade[];
  positions: TradingPosition[];
  recentFills: TradingFill[];
  recentOrders: TradingOrder[];
}): AccountMetrics {
  const netEquityUsd = liveAccountEquity(account);
  const openMarginUsd = sumNumbers(positions.map((position) => position.marginUsd));
  const openNotionalUsd = sumNumbers(
    positions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
  );
  const closedNetPnlUsd = sumNumbers(closedTrades.map((trade) => trade.netPnlUsd));
  const winningClosedTradeCount = closedTrades.filter((trade) => decimal(trade.netPnlUsd) > 0).length;
  const skippedFillCount = recentOrders.filter((order) => order.orderType === "skip").length;

  return {
    allocationUsd: netEquityUsd,
    allocationUsedPct: netEquityUsd > 0 ? openMarginUsd / netEquityUsd : null,
    averageClosedPnlUsd: closedTrades.length > 0 ? closedNetPnlUsd / closedTrades.length : 0,
    closedNetPnlUsd,
    copiedFillCount: recentFills.length,
    exposureRatio: netEquityUsd > 0 ? openNotionalUsd / netEquityUsd : null,
    feeUsd: decimal(account.feeUsd),
    netEquityUsd,
    openMarginUsd,
    openNotionalUsd,
    realizedPnlUsd: decimal(account.realizedPnlUsd),
    remainingAllocationUsd: Math.max(netEquityUsd - openMarginUsd, 0),
    returnPct:
      account.timeWeightedReturnPct === null
        ? null
        : decimal(account.timeWeightedReturnPct),
    skippedFillCount,
    unrealizedPnlUsd: sumNumbers(
      positions.map((position) => position.unrealizedPnlUsd),
    ),
    winRate: closedTrades.length > 0 ? winningClosedTradeCount / closedTrades.length : null,
  };
}


function paperPositionRow(position: PaperPosition): AccountPositionRow {
  return {
    accountType: "paper",
    coin: position.coin,
    detail: `opened ${formatDate(position.openedAt)}`,
    entryDetail: `mark ${formatPrice(position.markPrice)}`,
    entryPrice: position.entryPrice,
    executionDetail: "source to open",
    executionValue: formatExecutionMs(position.entryExecutionDelayMs),
    id: position.id,
    leverage: position.leverage,
    marginMode: null,
    notionalUsd: position.currentNotionalUsd ?? position.notionalUsd,
    side: position.side,
    sourceHref: `/wallets/${position.sourceWallet}`,
    sourceLabel: sourceDisplayName(position.sourceLabel, position.sourceWallet),
    unrealizedPnlUsd: position.unrealizedPnlUsd,
  };
}

function livePositionRow(
  position: TradingPosition,
  sourceLabels: Map<string, string>,
): AccountPositionRow {
  const isExchange = isLiveExchangeSource(position.sourceWallet);
  return {
    accountType: "live",
    coin: position.coin,
    detail: `opened ${formatDate(position.openedAt)}`,
    entryDetail: `mark ${formatPrice(position.markPrice)}`,
    entryPrice: position.entryPrice,
    executionDetail:
      position.entryExecutionDelayMs !== null ? "source to open" : "live position",
    executionValue: formatExecutionMs(position.entryExecutionDelayMs),
    id: position.id,
    leverage: position.leverage,
    marginMode: position.marginMode,
    notionalUsd: position.currentNotionalUsd ?? position.notionalUsd,
    side: position.side,
    sourceHref: isExchange ? null : `/wallets/${position.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange position"
      : sourceDisplayName(sourceLabels.get(position.sourceWallet.toLowerCase()), position.sourceWallet),
    unrealizedPnlUsd: position.unrealizedPnlUsd,
  };
}

function paperClosedTradeRow(trade: PaperClosedTrade): AccountClosedTradeRow {
  return {
    badges: [
      ...(trade.side ? [{ label: trade.side, tone: trade.side === "long" ? "positive" as Tone : "warning" as Tone }] : []),
      ...(trade.isSourceLiquidation ? [{ label: "liquidation", tone: "danger" as Tone }] : []),
    ],
    closedAt: trade.closedAt,
    coin: trade.coin,
    detail: `${formatCloseType(trade.closeType)}, ${formatDuration(trade.durationMs)}`,
    exitDetail: `size ${formatSize(trade.size)}`,
    exitPrice: trade.exitPrice,
    id: trade.id,
    netPnlUsd: trade.netPnlUsd,
    sourceHref: `/wallets/${trade.sourceWallet}`,
    sourceLabel: sourceDisplayName(trade.sourceLabel, trade.sourceWallet),
  };
}

function liveClosedTradeRow(
  trade: TradingClosedTrade,
  sourceLabels: Map<string, string>,
): AccountClosedTradeRow {
  const isExchange = isLiveExchangeSource(trade.sourceWallet);
  return {
    badges: [
      { label: "live", tone: "positive" },
      { label: trade.side, tone: trade.side === "long" ? "positive" : "warning" },
    ],
    closedAt: trade.closedAt,
    coin: trade.coin,
    detail: `closed trade, ${formatDuration(trade.durationMs)}`,
    exitDetail: `size ${formatSize(trade.size)}`,
    exitPrice: trade.exitPrice,
    id: trade.id,
    netPnlUsd: trade.netPnlUsd,
    sourceHref: isExchange ? null : `/wallets/${trade.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange position"
      : sourceDisplayName(sourceLabels.get(trade.sourceWallet.toLowerCase()), trade.sourceWallet),
  };
}

function paperExecutionRow(fill: PaperCopyFill): AccountExecutionRow {
  return {
    badges: [
      { label: "paper", tone: "neutral" },
      { label: fill.action, tone: fill.action === "skip" ? "warning" : "neutral" },
      ...(fill.side ? [{ label: fill.side, tone: fill.side === "long" ? "positive" as Tone : "warning" as Tone }] : []),
      ...(fill.minOrderAdjusted ? [{ label: "min order adjusted", tone: "warning" as Tone }] : []),
    ],
    coin: fill.coin,
    detail: fill.skippedReason ? humanReason(fill.skippedReason) : formatShortDateTime(fill.filledAt),
    id: `paper:${fill.id}`,
    notionalDetail: paperFillNotionalDetail(fill),
    notionalUsd: fill.notionalUsd,
    price: fill.price,
    priceDetail: paperFillPriceDetail(fill),
    realizedPnlUsd: fill.realizedPnlUsd,
    sourceHref: `/wallets/${fill.sourceWallet}`,
    sourceLabel: sourceDisplayName(fill.sourceLabel, fill.sourceWallet),
  };
}

function buildLiveAccountExecutionRows(
  liveFills: TradingFill[],
  liveOrders: TradingOrder[],
  sourceLabels: Map<string, string>,
): AccountExecutionRow[] {
  const fillOrderIds = new Set(
    liveFills.map((fill) => fill.orderId).filter((value): value is string => Boolean(value)),
  );
  const fillRows = liveFills.map((fill) => liveFillExecutionRow(fill, sourceLabels));
  const orderRows = liveOrders
    .filter((order) => !fillOrderIds.has(order.id))
    .map((order) => liveOrderExecutionRow(order, sourceLabels));
  return [...fillRows, ...orderRows]
    .sort((left, right) => dateMs(right.detailDate ?? "") - dateMs(left.detailDate ?? ""))
    .map(accountExecutionRow)
    .slice(0, 100);
}

type DatedAccountExecutionRow = AccountExecutionRow & { detailDate?: string };

function accountExecutionRow(row: DatedAccountExecutionRow): AccountExecutionRow {
  return {
    badges: row.badges,
    coin: row.coin,
    detail: row.detail,
    id: row.id,
    notionalDetail: row.notionalDetail,
    notionalUsd: row.notionalUsd,
    price: row.price,
    priceDetail: row.priceDetail,
    realizedPnlUsd: row.realizedPnlUsd,
    sourceHref: row.sourceHref,
    sourceLabel: row.sourceLabel,
  };
}

function liveFillExecutionRow(
  fill: TradingFill,
  sourceLabels: Map<string, string>,
): DatedAccountExecutionRow {
  const isExchange = isLiveExchangeSource(fill.sourceWallet);
  return {
    badges: [
      { label: "live", tone: "positive" },
      { label: fill.action, tone: fill.action.includes("close") ? "neutral" : "positive" },
      { label: fill.side, tone: fill.side === "long" ? "positive" : "warning" },
    ],
    coin: fill.coin,
    detail: formatShortDateTime(fill.filledAt),
    detailDate: fill.filledAt,
    id: `live:${fill.id}`,
    notionalDetail: `size ${formatSize(fill.size)}`,
    notionalUsd: fill.notionalUsd,
    price: fill.price,
    priceDetail: `fee ${formatCurrency(fill.feeUsd)}`,
    realizedPnlUsd: fill.realizedPnlUsd,
    sourceHref: isExchange ? null : `/wallets/${fill.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange fill"
      : sourceDisplayName(sourceLabels.get(fill.sourceWallet.toLowerCase()), fill.sourceWallet),
  };
}

function liveOrderExecutionRow(
  order: TradingOrder,
  sourceLabels: Map<string, string>,
): DatedAccountExecutionRow {
  const isExchange = isLiveExchangeSource(order.sourceWallet);
  const error = order.error?.trim();
  const sortAt = order.filledAt ?? order.updatedAt ?? order.createdAt;
  return {
    badges: [
      { label: order.orderType === "skip" ? "live skip" : "live order", tone: order.orderType === "skip" ? "warning" : "neutral" },
      { label: order.action, tone: order.action.includes("close") ? "neutral" : "positive" },
      { label: order.status, tone: liveOrderStatusTone(order.status) },
    ],
    coin: order.coin,
    detail: error ? humanReason(error.replace(/^skip:/, "")) : formatShortDateTime(sortAt),
    detailDate: sortAt,
    id: `live-order:${order.id}`,
    notionalDetail: `filled ${formatCurrency(order.filledNotionalUsd)}`,
    notionalUsd: order.requestedNotionalUsd,
    price: order.limitPrice,
    priceDetail: order.averageFillPrice
      ? `avg ${formatPrice(order.averageFillPrice)}`
      : formatLeverage(order.leverage, order.marginMode),
    realizedPnlUsd: "0",
    sourceHref: isExchange ? null : `/wallets/${order.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange order"
      : sourceDisplayName(sourceLabels.get(order.sourceWallet.toLowerCase()), order.sourceWallet),
  };
}


function buildSourceRows({
  allocations,
  closedTrades,
  positions,
  recentFills,
}: {
  allocations: PaperCopyAllocation[];
  closedTrades: PaperClosedTrade[];
  positions: PaperPosition[];
  recentFills: PaperCopyFill[];
}) {
  const rows = new Map<string, SourceRow>();

  const ensureRow = (sourceWallet: string, sourceLabel: string | null): SourceRow => {
    const source = sourceWallet.toLowerCase();
    const existing = rows.get(source);
    if (existing) {
      if (!existing.sourceLabel && sourceLabel) {
        existing.sourceLabel = sourceLabel;
      }
      return existing;
    }
    const row: SourceRow = {
      allocationUsd: 0,
      closedNetPnlUsd: 0,
      closedTradeCount: 0,
      copiedFillCount: 0,
      lastActivityAt: null,
      openMarginUsd: 0,
      openNotionalUsd: 0,
      openPositionCount: 0,
      poolRank: null,
      remainingAllocationUsd: 0,
      score: null,
      skippedFillCount: 0,
      sourceLabel,
      sourceStatus: "history",
      sourceWallet: source,
      totalPnlUsd: 0,
      unrealizedPnlUsd: 0,
      winRate: null,
    };
    rows.set(source, row);
    return row;
  };

  for (const allocation of allocations) {
    const row = ensureRow(allocation.sourceWallet, allocation.sourceLabel);
    row.allocationUsd += decimal(allocation.allocationUsd);
    row.remainingAllocationUsd += decimal(allocation.remainingAllocationUsd);
    row.poolRank = minNullable(row.poolRank, allocation.poolRank);
    row.score = row.score ?? allocation.score;
    row.sourceStatus = allocation.sourceStatus;
    row.lastActivityAt = latestDate(row.lastActivityAt, allocation.updatedAt);
  }

  for (const position of positions) {
    const row = ensureRow(position.sourceWallet, position.sourceLabel);
    row.openPositionCount += 1;
    row.openMarginUsd += decimal(position.marginUsd);
    row.openNotionalUsd += decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, position.updatedAt);
  }

  const winsBySource = new Map<string, number>();
  for (const trade of closedTrades) {
    const row = ensureRow(trade.sourceWallet, trade.sourceLabel);
    row.closedTradeCount += 1;
    row.closedNetPnlUsd += decimal(trade.netPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, trade.closedAt);
    if (decimal(trade.netPnlUsd) > 0) {
      const source = trade.sourceWallet.toLowerCase();
      winsBySource.set(source, (winsBySource.get(source) ?? 0) + 1);
    }
  }

  for (const fill of recentFills) {
    const row = ensureRow(fill.sourceWallet, fill.sourceLabel);
    if (fill.action === "skip") {
      row.skippedFillCount += 1;
    } else {
      row.copiedFillCount += 1;
    }
    row.lastActivityAt = latestDate(row.lastActivityAt, fill.filledAt);
  }

  return Array.from(rows.values())
    .map((row) => ({
      ...row,
      totalPnlUsd: row.closedNetPnlUsd + row.unrealizedPnlUsd,
      winRate: row.closedTradeCount > 0 ? (winsBySource.get(row.sourceWallet) ?? 0) / row.closedTradeCount : null,
    }))
    .sort((left, right) => {
      if (left.openPositionCount !== right.openPositionCount) {
        return right.openPositionCount - left.openPositionCount;
      }
      const pnlDiff = right.totalPnlUsd - left.totalPnlUsd;
      if (pnlDiff !== 0) {
        return pnlDiff;
      }
      return (left.poolRank ?? 9999) - (right.poolRank ?? 9999);
    });
}


function buildLiveSourceRows({
  allocationCapitalUsd,
  closedTrades,
  positions,
  recentFills,
  recentOrders,
  sourceMetadata,
  sourceLabels,
}: {
  allocationCapitalUsd: number;
  closedTrades: TradingClosedTrade[];
  positions: TradingPosition[];
  recentFills: TradingFill[];
  recentOrders: TradingOrder[];
  sourceMetadata: Map<string, SourceMetadata>;
  sourceLabels: Map<string, string>;
}) {
  const rows = new Map<string, SourceRow>();
  const ensureRow = (sourceWallet: string): SourceRow => {
    const source = sourceWallet.toLowerCase();
    const existing = rows.get(source);
    if (existing) {
      return existing;
    }
    const metadata = sourceMetadata.get(source);
    const allocationUsd =
      allocationCapitalUsd > 0 && metadata?.allocationPct
        ? allocationCapitalUsd * metadata.allocationPct
        : 0;
    const row: SourceRow = {
      allocationUsd,
      closedNetPnlUsd: 0,
      closedTradeCount: 0,
      copiedFillCount: 0,
      lastActivityAt: null,
      openMarginUsd: 0,
      openNotionalUsd: 0,
      openPositionCount: 0,
      poolRank: metadata?.poolRank ?? null,
      remainingAllocationUsd: allocationUsd,
      score: metadata?.score ?? null,
      skippedFillCount: 0,
      sourceLabel: isLiveExchangeSource(sourceWallet)
        ? "Exchange"
        : metadata?.label ?? sourceLabels.get(source) ?? null,
      sourceStatus: "history",
      sourceWallet: sourceWallet,
      totalPnlUsd: 0,
      unrealizedPnlUsd: 0,
      winRate: null,
    };
    rows.set(source, row);
    return row;
  };

  for (const position of positions) {
    const row = ensureRow(position.sourceWallet);
    row.openPositionCount += 1;
    row.openMarginUsd += decimal(position.marginUsd);
    row.openNotionalUsd += decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, position.updatedAt);
    row.sourceStatus = "trading";
  }

  const winsBySource = new Map<string, number>();
  for (const trade of closedTrades) {
    const row = ensureRow(trade.sourceWallet);
    row.closedTradeCount += 1;
    row.closedNetPnlUsd += decimal(trade.netPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, trade.closedAt);
    if (decimal(trade.netPnlUsd) > 0) {
      const source = trade.sourceWallet.toLowerCase();
      winsBySource.set(source, (winsBySource.get(source) ?? 0) + 1);
    }
  }

  for (const fill of recentFills) {
    const row = ensureRow(fill.sourceWallet);
    row.copiedFillCount += 1;
    row.lastActivityAt = latestDate(row.lastActivityAt, fill.filledAt);
  }

  for (const order of recentOrders) {
    const row = ensureRow(order.sourceWallet);
    if (order.orderType === "skip") {
      row.skippedFillCount += 1;
    }
    row.lastActivityAt = latestDate(row.lastActivityAt, order.createdAt);
  }

  return Array.from(rows.values())
    .map((row) => ({
      ...row,
      remainingAllocationUsd: Math.max(row.allocationUsd - row.openMarginUsd, 0),
      totalPnlUsd: row.closedNetPnlUsd + row.unrealizedPnlUsd,
      winRate: row.closedTradeCount > 0 ? (winsBySource.get(row.sourceWallet.toLowerCase()) ?? 0) / row.closedTradeCount : null,
    }))
    .sort((left, right) => {
      if (left.openPositionCount !== right.openPositionCount) {
        return right.openPositionCount - left.openPositionCount;
      }
      return right.totalPnlUsd - left.totalPnlUsd;
    });
}


function buildMarketRows(positions: Array<PaperPosition | TradingPosition>): MarketRow[] {
  const rows = new Map<string, MarketRow>();
  for (const position of positions) {
    const row = rows.get(position.coin) ?? {
      coin: position.coin,
      longCount: 0,
      longNotionalUsd: 0,
      marginUsd: 0,
      notionalUsd: 0,
      positionCount: 0,
      shortCount: 0,
      shortNotionalUsd: 0,
      unrealizedPnlUsd: 0,
    };
    const positionNotional = decimal(
      position.currentNotionalUsd ?? position.notionalUsd,
    );
    row.positionCount += 1;
    row.marginUsd += decimal(position.marginUsd);
    row.notionalUsd += positionNotional;
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    if (position.side === "long") {
      row.longCount += 1;
      row.longNotionalUsd += positionNotional;
    } else {
      row.shortCount += 1;
      row.shortNotionalUsd += positionNotional;
    }
    rows.set(position.coin, row);
  }
  return Array.from(rows.values()).sort((left, right) => right.notionalUsd - left.notionalUsd);
}

export function buildAccountPerformanceTimeline(
  closedTrades: AccountClosedTradeRow[],
): AccountPerformancePoint[] {
  let cumulativePnl = 0;
  return [...closedTrades]
    .sort((left, right) => dateMs(left.closedAt) - dateMs(right.closedAt))
    .map((trade) => {
      const tradeValue = decimal(trade.netPnlUsd);
      cumulativePnl += tradeValue;
      return {
        coin: trade.coin,
        id: trade.id,
        label: formatShortDateTime(trade.closedAt),
        timestamp: dateMs(trade.closedAt),
        tradeValue,
        value: cumulativePnl,
      };
    });
}

function buildSourceLabelMap(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const labels = new Map<string, string>();
  for (const [source, metadata] of buildSourceMetadataMap(summary, tradingAccounts)) {
    if (metadata.label) {
      labels.set(source, metadata.label);
    }
  }
  return labels;
}

function buildSourceMetadataMap(
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
    };
    metadata.set(source, item);
    return item;
  };
  const addLabel = (wallet: string, label: string | null | undefined) => {
    const trimmed = label?.trim();
    if (trimmed) {
      ensureMetadata(wallet).label = trimmed;
    }
  };
  for (const allocation of summary.allocations) {
    const item = ensureMetadata(allocation.sourceWallet);
    addLabel(allocation.sourceWallet, allocation.sourceLabel);
    item.allocationPct ??= firstNumber([allocation.allocationPct]);
    item.poolRank = minNullable(item.poolRank, allocation.poolRank);
    item.rank = minNullable(item.rank, allocation.rank);
    item.score ??= allocation.score;
  }
  for (const wallet of summary.walletPerformance) {
    const item = ensureMetadata(wallet.sourceWallet);
    addLabel(wallet.sourceWallet, wallet.sourceLabel);
    item.allocationPct ??= firstNumber([wallet.allocationPct]);
    item.poolRank = minNullable(item.poolRank, wallet.poolRank);
    item.rank = minNullable(item.rank, wallet.rank);
    item.score ??= wallet.score;
  }
  for (const position of summary.positions) {
    addLabel(position.sourceWallet, position.sourceLabel);
  }
  for (const fill of summary.recentFills) {
    addLabel(fill.sourceWallet, fill.sourceLabel);
  }
  for (const trade of summary.closedTrades) {
    addLabel(trade.sourceWallet, trade.sourceLabel);
  }
  for (const source of tradingAccounts?.sourceMetadata ?? []) {
    const item = ensureMetadata(source.sourceWallet);
    addLabel(source.sourceWallet, source.sourceLabel);
    item.allocationPct ??= firstNumber([source.allocationPct]);
    item.poolRank = minNullable(item.poolRank, source.poolRank);
    item.rank = minNullable(item.rank, source.rank);
    item.score ??= source.score;
  }
  return metadata;
}


function displayAccountLivePositions(positions: TradingPosition[]) {
  const exchangeCoins = new Set(
    positions
      .filter((position) => isLiveExchangeSource(position.sourceWallet))
      .map((position) => position.coin),
  );
  return positions.filter(
    (position) =>
      isLiveExchangeSource(position.sourceWallet) ||
      !exchangeCoins.has(position.coin),
  );
}

function accountNetEquity(account: PaperTradingAccount) {
  return decimal(account.equityUsd) + decimal(account.unrealizedPnlUsd);
}

function liveAccountEquity(account: TradingAccount) {
  return decimal(account.equityUsd ?? account.perpEquityUsd ?? account.tradableEquityUsd);
}

function isLiveExchangeSource(sourceWallet: string) {
  return sourceWallet === "__exchange__";
}

function liveOrderStatusTone(status: string): Tone {
  if (status === "filled" || status === "accepted") {
    return "positive";
  }
  if (status === "rejected" || status === "failed" || status === "canceled") {
    return "danger";
  }
  if (
    status === "ready" ||
    status === "submitting" ||
    status === "uncertain" ||
    status === "submitted" ||
    status === "partially_filled"
  ) {
    return "warning";
  }
  return "neutral";
}

function decimal(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return 0;
  }
  const parsed = numberValue(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sumNumbers(values: Array<string | number | null | undefined>): number {
  return values.reduce<number>((total, value) => total + decimal(value), 0);
}

function firstNumber(values: Array<string | number | null | undefined>): number | null {
  for (const value of values) {
    if (value !== null && value !== undefined) {
      return decimal(value);
    }
  }
  return null;
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

function latestDate(left: string | null, right: string | null | undefined) {
  if (!right) {
    return left;
  }
  if (!left || dateMs(right) > dateMs(left)) {
    return right;
  }
  return left;
}

function dateMs(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function sourceDisplayName(label: string | null | undefined, address: string) {
  const trimmed = label?.trim();
  return trimmed || shortAddress(address);
}

function shortAddress(address: string) {
  if (address.length <= 14) {
    return address;
  }
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function formatCloseType(value: string) {
  if (value === "flip_close") {
    return "flip close";
  }
  return value;
}

function formatPrice(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 6,
    minimumFractionDigits: 2,
  }).format(decimal(value));
}

function formatSize(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 6 }).format(decimal(value));
}

function formatLeverage(
  value: string | number | null | undefined,
  marginMode?: "cross" | "isolated" | null,
) {
  if (value === null || value === undefined) {
    return "-";
  }
  const leverage = `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )}x`;
  return marginMode ? `${leverage} ${marginMode}` : leverage;
}

function formatMultiple(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (!Number.isFinite(value)) {
    return "âˆž";
  }
  return `${value.toLocaleString("sv-SE", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  })}x`;
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )} bps`;
}

function paperFillPriceDetail(fill: PaperCopyFill) {
  if (fill.skippedReason && fill.priceDriftBps) {
    const maxDrift = fill.maxPriceDriftBps ? ` | max ${formatBps(fill.maxPriceDriftBps)}` : "";
    return `adverse drift ${formatBps(fill.priceDriftBps)}${maxDrift} | live ${formatPrice(fill.observedPrice)}`;
  }
  const parts = [
    fill.sourcePrice ? `src ${formatPrice(fill.sourcePrice)}` : null,
    fill.observedPrice ? `live ${formatPrice(fill.observedPrice)}` : null,
    `fee ${formatCurrency(fill.feeUsd)}`,
  ].filter(Boolean);
  return parts.join(" | ");
}

function paperFillNotionalDetail(fill: PaperCopyFill) {
  if (!fill.minOrderAdjusted || !fill.originalNotionalUsd) {
    return undefined;
  }
  return `adjusted from ${formatCurrency(fill.originalNotionalUsd)}`;
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

function formatDuration(value: number | null | undefined) {
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

function humanReason(value: string) {
  return value.replaceAll("_", " ");
}
