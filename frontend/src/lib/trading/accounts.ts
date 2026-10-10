import {
  dateMs,
  decimal,
  formatBps,
  formatPrice,
  formatSize,
  formatUsd,
  toNumber,
} from "@/lib/format";
import type {
  PaperClosedTrade,
  PaperCopyFill,
  PaperPosition,
  PaperTradingAccount,
  PaperTradingSummaryResponse,
} from "@/types/paper";
import type {
  TradingAccount,
  TradingAccountsResponse,
  TradingClosedTrade,
  TradingPosition,
} from "@/types/trading";

import {
  actionLabel,
  formatCloseType,
  isLiveExchangeSource,
  reasonTitle,
  sourceDisplayName,
  walletHref,
} from "./labels";
import {
  buildLiveAccountNotice,
  buildLiveExecutionRows,
  displayLivePositions,
  liveAccountEquity,
  liveReconciliationIsStale,
} from "./live";
import { buildLiveSourceRows, buildPaperSourceRows, buildSourceLabels, sumNumbers } from "./sources";
import type {
  AccountNotice,
  AccountOption,
  AccountSummary,
  AccountView,
  ClosedTradeRow,
  ExecutionRow,
  MarketRow,
  PositionRow,
} from "./types";

/** Paper accounts first, then live accounts that are not mirrors of a paper account. */
export function buildAccountOptions(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
): AccountOption[] {
  const paperKeys = new Set(
    tradingAccounts.accounts.filter((account) => account.accountType === "paper").map((account) => account.key),
  );
  return [
    ...summary.accounts.map<AccountOption>((account) => ({
      accountType: "paper",
      key: account.key,
      label: account.label,
      paper: account,
    })),
    ...tradingAccounts.accounts
      .filter((account) => account.accountType === "live" && !paperKeys.has(account.key))
      .map<AccountOption>((account) => ({ accountType: "live", key: account.key, label: account.label, live: account })),
  ];
}

export function paperAccountSummary(account: PaperTradingAccount): AccountSummary {
  const grossRealized = decimal(account.realizedPnlUsd);
  const fees = decimal(account.feeUsd);
  const funding = decimal(account.fundingUsd);
  const netRealized = grossRealized - fees + funding;
  const unrealized = decimal(account.unrealizedPnlUsd);
  const startingBalance = decimal(account.startingBalanceUsd);
  const totalPnl = toNumber(account.totalPnlUsd) ?? netRealized + unrealized;
  const state = account.exitOnlyAt ? "exit_only" : account.enabled ? "trading" : "stopped";
  return {
    accountType: "paper",
    equityUsd: decimal(account.equityUsd) + unrealized,
    feeUsd: fees,
    fundingUsd: funding,
    grossRealizedPnlUsd: grossRealized,
    key: account.key,
    label: account.label,
    netRealizedPnlUsd: netRealized,
    openMarginUsd: decimal(account.openMarginUsd),
    openNotionalUsd: decimal(account.openNotionalUsd),
    openPositionCount: account.openPositionCount,
    returnLabel: "Return on starting balance",
    returnPct: toNumber(account.totalPnlPct) ?? (startingBalance > 0 ? totalPnl / startingBalance : null),
    state,
    stateDetail: account.exitOnlyAt
      ? `Loss limit reached${account.exitOnlyReason ? `: ${account.exitOnlyReason}` : ""}. Positions can close, no new entries.`
      : account.enabled
        ? "Copying new trades from the active sources."
        : "Stopped. No trades are copied until the account is started.",
    stateLabel: state === "exit_only" ? "Exit only" : state === "trading" ? "Trading" : "Stopped",
    stateTone: state === "exit_only" ? "warning" : state === "trading" ? "positive" : "neutral",
    totalPnlUsd: totalPnl,
    unrealizedPnlUsd: unrealized,
  };
}

export function liveAccountSummary(
  account: TradingAccount,
  positions: TradingPosition[],
  tradingAccounts: Pick<TradingAccountsResponse, "liveTradingEnabled" | "riskLimits" | "updatedAt">,
): AccountSummary {
  const display = displayLivePositions(positions.filter((position) => position.accountKey === account.key));
  const grossRealized = decimal(account.realizedPnlUsd);
  const fees = decimal(account.feeUsd);
  const funding = decimal(account.fundingUsd);
  const netRealized = grossRealized - fees + funding;
  const unrealized = sumNumbers(display.map((position) => position.unrealizedPnlUsd));
  const stale = liveReconciliationIsStale(
    account,
    tradingAccounts.riskLimits.reconciliationMaxSnapshotAgeSeconds,
    tradingAccounts.updatedAt,
  );
  const paused = account.status === "enabled" && (account.reconciliationStatus !== "complete" || stale);
  const state =
    account.status === "exit_only"
      ? "exit_only"
      : account.status === "disabled"
        ? "disabled"
        : paused
          ? "entries_paused"
          : "trading";
  return {
    accountType: "live",
    equityUsd: liveAccountEquity(account),
    feeUsd: fees,
    fundingUsd: funding,
    grossRealizedPnlUsd: grossRealized,
    key: account.key,
    label: account.label,
    netRealizedPnlUsd: netRealized,
    openMarginUsd: sumNumbers(display.map((position) => position.marginUsd)),
    openNotionalUsd: sumNumbers(display.map((position) => position.currentNotionalUsd ?? position.notionalUsd)),
    openPositionCount: display.length,
    returnLabel: account.performanceTrackingStartedAt
      ? "Time-weighted return since tracking started"
      : "Starts after the first complete reconciliation",
    returnPct: toNumber(account.timeWeightedReturnPct),
    state,
    stateDetail: !tradingAccounts.liveTradingEnabled
      ? "Live trading is turned off on the server (LIVE_TRADING_ENABLED=false)."
      : state === "trading"
        ? "Sending real orders for new copy trades."
        : state === "entries_paused"
          ? "New entries paused until the exchange snapshot is complete and fresh."
          : state === "exit_only"
            ? "Positions can close, no new entries."
            : "Disabled. No orders are sent.",
    stateLabel:
      state === "trading"
        ? "Trading"
        : state === "entries_paused"
          ? "Entries paused"
          : state === "exit_only"
            ? "Exit only"
            : "Disabled",
    stateTone: state === "trading" ? "positive" : state === "disabled" ? "neutral" : "warning",
    totalPnlUsd: netRealized + unrealized,
    unrealizedPnlUsd: unrealized,
  };
}

export function accountSummaryFor(option: AccountOption, tradingAccounts: TradingAccountsResponse) {
  return option.accountType === "paper"
    ? paperAccountSummary(option.paper)
    : liveAccountSummary(option.live, tradingAccounts.positions, tradingAccounts);
}

export function buildAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  option: AccountOption,
): AccountView {
  return option.accountType === "paper"
    ? buildPaperAccountView(summary, option.paper)
    : buildLiveAccountView(summary, tradingAccounts, option.live);
}

function buildPaperAccountView(summary: PaperTradingSummaryResponse, account: PaperTradingAccount): AccountView {
  const positions = summary.positions.filter((position) => position.accountKey === account.key);
  const notices: AccountNotice[] = [];
  if (account.exitOnlyAt) {
    notices.push({
      detail: `${account.exitOnlyReason ? `Reason: ${account.exitOnlyReason}. ` : ""}Open positions still follow their sources out. Reset or restart the account to resume entries.`,
      title: "Exit only: a loss limit stopped new entries",
      tone: "warning",
    });
  }
  if (summary.marketDataStatus === "partial" || summary.marketDataStatus === "unavailable") {
    notices.push({
      detail: "Unrealized PnL and closing prices may be missing or stale for some positions.",
      title: summary.marketDataStatus === "partial" ? "Some market prices are missing" : "Market prices are unavailable",
      tone: "warning",
    });
  }
  const paperAccounts = summary.accounts.length;
  return {
    accountKey: account.key,
    accountType: "paper",
    capitalBalances: [],
    cashFlowsVersion: null,
    closedTrades: summary.closedTrades
      .filter((trade) => trade.accountKey === account.key)
      .map(paperClosedTradeRow)
      .sort((left, right) => dateMs(right.closedAt) - dateMs(left.closedAt)),
    executions: summary.recentFills
      .filter((fill) => fill.accountKey === account.key)
      .map(paperExecutionRow)
      .sort((left, right) => dateMs(right.at) - dateMs(left.at)),
    marketRows: buildMarketRows(positions),
    notices,
    positions: positions.map(paperPositionRow).sort(compareRowsOldestFirst),
    sources: buildPaperSourceRows(summary, account.key),
    sourcesCombineAccounts: paperAccounts > 1,
    summary: paperAccountSummary(account),
  };
}

function buildLiveAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  account: TradingAccount,
): AccountView {
  const sourceLabels = buildSourceLabels(summary, tradingAccounts);
  const accountPositions = tradingAccounts.positions.filter((position) => position.accountKey === account.key);
  const display = displayLivePositions(accountPositions);
  const fills = tradingAccounts.recentFills.filter((fill) => fill.accountKey === account.key);
  const orders = tradingAccounts.recentOrders.filter((order) => order.accountKey === account.key);
  const notices: AccountNotice[] = [];
  if (!tradingAccounts.liveTradingEnabled) {
    notices.push({
      detail: "Set LIVE_TRADING_ENABLED=true in .env and restart the services before starting a live account.",
      title: "Live trading is turned off on the server",
      tone: "neutral",
    });
  }
  const notice = buildLiveAccountNotice(
    account,
    tradingAccounts.riskLimits.reconciliationMaxSnapshotAgeSeconds,
    tradingAccounts.updatedAt,
  );
  if (notice) {
    notices.push(notice);
  }
  return {
    accountKey: account.key,
    accountType: "live",
    capitalBalances: account.capitalBalances,
    cashFlowsVersion: account.lastReconciledAt ?? account.updatedAt,
    closedTrades: tradingAccounts.closedTrades
      .filter((trade) => trade.accountKey === account.key)
      .map((trade) => liveClosedTradeRow(trade, sourceLabels))
      .sort((left, right) => dateMs(right.closedAt) - dateMs(left.closedAt)),
    executions: buildLiveExecutionRows(fills, orders, sourceLabels),
    marketRows: buildMarketRows(display),
    notices,
    positions: display.map((position) => livePositionRow(position, sourceLabels)).sort(compareRowsOldestFirst),
    sources: buildLiveSourceRows({
      account,
      fills,
      liveTradingEnabled: tradingAccounts.liveTradingEnabled,
      orders,
      positions: accountPositions,
      summary,
      tradingAccounts,
    }),
    sourcesCombineAccounts: false,
    summary: liveAccountSummary(account, tradingAccounts.positions, tradingAccounts),
  };
}

function paperPositionRow(position: PaperPosition): PositionRow {
  return {
    accountKey: position.accountKey,
    accountType: "paper",
    addFillCount: position.addFillCount,
    closeDisabledReason: position.markPrice === null ? "No market price yet, so the position cannot be closed." : null,
    closeFillCount: position.closeFillCount,
    coin: position.coin,
    entryExecutionDelayMs: position.entryExecutionDelayMs,
    entryPrice: position.entryPrice,
    fundingUsd: toNumber(position.fundingUsd),
    id: position.id,
    isExchange: false,
    leverage: position.leverage,
    marginMode: position.marginMode ?? null,
    marginUsd: decimal(position.marginUsd),
    markPrice: position.markPrice,
    notionalUsd: decimal(position.currentNotionalUsd ?? position.notionalUsd),
    openedAt: position.openedAt,
    priceUpdatedAt: position.priceUpdatedAt ?? position.updatedAt,
    realizedPnlUsd: decimal(position.realizedPnlUsd),
    side: position.side,
    size: position.size,
    sourceHref: walletHref(position.sourceWallet),
    sourceLabel: sourceDisplayName(position.sourceLabel, position.sourceWallet),
    sourceWallet: position.sourceWallet,
    unrealizedPnlPct: toNumber(position.unrealizedPnlPct),
    unrealizedPnlUsd: toNumber(position.unrealizedPnlUsd),
  };
}

function livePositionRow(position: TradingPosition, sourceLabels: Map<string, string>): PositionRow {
  const isExchange = isLiveExchangeSource(position.sourceWallet);
  return {
    accountKey: position.accountKey,
    accountType: "live",
    addFillCount: position.addFillCount,
    closeDisabledReason: null,
    closeFillCount: position.closeFillCount,
    coin: position.coin,
    entryExecutionDelayMs: position.entryExecutionDelayMs,
    entryPrice: position.entryPrice,
    fundingUsd: decimal(position.fundingUsd),
    id: position.id,
    isExchange,
    leverage: position.leverage,
    marginMode: position.marginMode,
    marginUsd: decimal(position.marginUsd),
    markPrice: position.markPrice,
    notionalUsd: decimal(position.currentNotionalUsd ?? position.notionalUsd),
    openedAt: position.openedAt,
    priceUpdatedAt: position.priceUpdatedAt ?? position.lastReconciledAt,
    realizedPnlUsd: decimal(position.realizedPnlUsd),
    side: position.side,
    size: position.size,
    sourceHref: walletHref(position.sourceWallet),
    sourceLabel: isExchange
      ? "No attributed source"
      : sourceDisplayName(sourceLabels.get(position.sourceWallet.toLowerCase()), position.sourceWallet),
    sourceWallet: position.sourceWallet,
    unrealizedPnlPct: toNumber(position.unrealizedPnlPct),
    unrealizedPnlUsd: toNumber(position.unrealizedPnlUsd),
  };
}

function paperClosedTradeRow(trade: PaperClosedTrade): ClosedTradeRow {
  return {
    closeTypeLabel: formatCloseType(trade.closeType),
    closedAt: trade.closedAt,
    coin: trade.coin,
    durationMs: trade.durationMs,
    entryPrice: null,
    exitPrice: trade.exitPrice,
    feeUsd: decimal(trade.feeUsd),
    flags: [
      ...(trade.isSourceLiquidation ? [{ label: "Source liquidated", tone: "negative" as const }] : []),
      ...(trade.isPaperLiquidation ? [{ label: "Paper liquidation", tone: "negative" as const }] : []),
    ],
    fundingUsd: null,
    id: trade.id,
    netPnlUsd: decimal(trade.netPnlUsd),
    notionalUsd: toNumber(trade.notionalUsd),
    openedAt: trade.openedAt,
    realizedPnlUsd: decimal(trade.realizedPnlUsd),
    side: trade.side,
    size: trade.size,
    sourceHref: walletHref(trade.sourceWallet),
    sourceLabel: sourceDisplayName(trade.sourceLabel, trade.sourceWallet),
    sourceWallet: trade.sourceWallet,
  };
}

export function liveClosedTradeRow(trade: TradingClosedTrade, sourceLabels: Map<string, string>): ClosedTradeRow {
  const isExchange = isLiveExchangeSource(trade.sourceWallet);
  return {
    closeTypeLabel: "Close",
    closedAt: trade.closedAt,
    coin: trade.coin,
    durationMs: trade.durationMs,
    entryPrice: trade.entryPrice,
    exitPrice: trade.exitPrice,
    feeUsd: decimal(trade.feeUsd),
    flags: trade.isLiquidation ? [{ label: "Liquidated", tone: "negative" }] : [],
    fundingUsd: decimal(trade.fundingUsd),
    id: trade.id,
    netPnlUsd: decimal(trade.netPnlUsd),
    notionalUsd: toNumber(trade.exitNotionalUsd),
    openedAt: trade.openedAt,
    realizedPnlUsd: decimal(trade.realizedPnlUsd),
    side: trade.side,
    size: trade.size,
    sourceHref: walletHref(trade.sourceWallet),
    sourceLabel: isExchange
      ? "Exchange position"
      : sourceDisplayName(trade.sourceLabel ?? sourceLabels.get(trade.sourceWallet.toLowerCase()), trade.sourceWallet),
    sourceWallet: trade.sourceWallet,
  };
}

function paperExecutionRow(fill: PaperCopyFill): ExecutionRow {
  const skipped = fill.action === "skip" || Boolean(fill.skippedReason);
  const details = [
    fill.skippedReason ? reasonTitle(fill.skippedReason) : null,
    fill.priceDriftBps
      ? `adverse drift ${formatBps(fill.priceDriftBps)}${fill.maxPriceDriftBps ? ` (max ${formatBps(fill.maxPriceDriftBps)})` : ""}`
      : null,
    fill.minOrderAdjusted && fill.originalNotionalUsd
      ? `raised to the minimum order from ${formatUsd(fill.originalNotionalUsd)}`
      : null,
  ].filter(Boolean);
  return {
    action: actionLabel(fill.action),
    at: fill.filledAt,
    coin: fill.coin,
    detail: details.length > 0 ? details.join(", ") : null,
    feeUsd: decimal(fill.feeUsd),
    id: `paper:${fill.id}`,
    notionalDetail: fill.marginUsd ? `${formatUsd(fill.marginUsd)} margin` : null,
    notionalUsd: fill.notionalUsd,
    price: fill.price ?? fill.observedPrice,
    priceDetail: [
      fill.sourcePrice ? `source ${formatPrice(fill.sourcePrice)}` : null,
      fill.size ? `size ${formatSize(fill.size)}` : null,
    ]
      .filter(Boolean)
      .join(", ") || null,
    realizedPnlUsd: skipped ? null : decimal(fill.realizedPnlUsd),
    result: skipped
      ? { label: "Skipped", tone: "warning" }
      : fill.minOrderAdjusted
        ? { label: "Filled, min size", tone: "positive" }
        : { label: "Filled", tone: "positive" },
    side: fill.side,
    sourceHref: walletHref(fill.sourceWallet),
    sourceLabel: sourceDisplayName(fill.sourceLabel, fill.sourceWallet),
    sourceWallet: fill.sourceWallet,
  };
}

export function buildMarketRows(positions: Array<PaperPosition | TradingPosition>): MarketRow[] {
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
    const notional = decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.positionCount += 1;
    row.marginUsd += decimal(position.marginUsd);
    row.notionalUsd += notional;
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    if (position.side === "long") {
      row.longCount += 1;
      row.longNotionalUsd += notional;
    } else {
      row.shortCount += 1;
      row.shortNotionalUsd += notional;
    }
    rows.set(position.coin, row);
  }
  return Array.from(rows.values()).sort((left, right) => right.notionalUsd - left.notionalUsd);
}

export function compareRowsOldestFirst(
  left: Pick<PositionRow, "accountKey" | "coin" | "id" | "openedAt" | "side" | "sourceWallet">,
  right: Pick<PositionRow, "accountKey" | "coin" | "id" | "openedAt" | "side" | "sourceWallet">,
) {
  return (
    dateMs(left.openedAt) - dateMs(right.openedAt) ||
    [left.accountKey, left.sourceWallet, left.coin, left.side, left.id]
      .join("|")
      .localeCompare([right.accountKey, right.sourceWallet, right.coin, right.side, right.id].join("|"))
  );
}

export function latestUpdatedAt(summary: PaperTradingSummaryResponse, tradingAccounts: TradingAccountsResponse) {
  return dateMs(tradingAccounts.updatedAt) > dateMs(summary.updatedAt) ? tradingAccounts.updatedAt : summary.updatedAt;
}
