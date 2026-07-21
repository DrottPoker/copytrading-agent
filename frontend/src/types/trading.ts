export type TradingCapitalBalance = {
  key: string;
  label: string;
  equityUsd: string;
  availableUsd: string | null;
  tradable: boolean;
  stale: boolean;
  error: string | null;
};

export type TradingAccount = {
  key: string;
  accountType: "paper" | "live";
  label: string;
  status: "disabled" | "enabled" | "exit_only";
  network: "mainnet" | "testnet";
  walletAddress: string | null;
  vaultAddress: string | null;
  startingBalanceUsd: string | null;
  cashBalanceUsd: string | null;
  equityUsd: string | null;
  realizedPnlUsd: string;
  feeUsd: string;
  lastReconciledAt: string | null;
  lifecycleVersion: number;
  statusChangedAt: string | null;
  statusReason: string | null;
  archivedAt: string | null;
  capitalMode: "unified" | "standard_per_dex" | null;
  userAbstraction: string | null;
  tradableEquityUsd: string | null;
  perpEquityUsd: string | null;
  spotUsdcBalanceUsd: string | null;
  spotUsdcAvailableUsd: string | null;
  capitalBalances: TradingCapitalBalance[];
  reconciliationStatus: "never" | "complete" | "partial" | "failed";
  reconciliationAttemptedAt: string | null;
  incompleteReconciliationComponents: string[];
  reconciliationErrors: Record<string, string>;
  createdAt: string;
  updatedAt: string;
};

export type TradingPosition = {
  id: string;
  accountKey: string;
  accountType: "paper" | "live";
  sourceWallet: string;
  coin: string;
  side: "long" | "short";
  size: string;
  entryPrice: string;
  notionalUsd: string;
  leverage: string;
  marginMode: "cross" | "isolated";
  marginUsd: string;
  currentNotionalUsd: string | null;
  markPrice: string | null;
  unrealizedPnlUsd: string | null;
  unrealizedPnlPct: string | null;
  priceUpdatedAt: string | null;
  realizedPnlUsd: string;
  feeUsd: string;
  addFillCount: number;
  closeFillCount: number;
  openedAt: string;
  entryExecutionDelayMs: number | null;
  lastReconciledAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type TradingFill = {
  id: string;
  orderId: string | null;
  accountKey: string;
  accountType: "paper" | "live";
  sourceWallet: string;
  sourceFillId: string | null;
  sequenceIndex: number | null;
  exchangeFillId: string | null;
  coin: string;
  action: string;
  side: "long" | "short";
  price: string;
  size: string;
  notionalUsd: string;
  feeUsd: string;
  realizedPnlUsd: string;
  filledAt: string;
  createdAt: string;
};

export type TradingOrder = {
  id: string;
  accountKey: string;
  accountType: "paper" | "live";
  sourceWallet: string;
  sourceFillId: string;
  sequenceIndex: number;
  clientOrderId: string;
  exchangeOrderId: string | null;
  coin: string;
  action: string;
  side: string;
  isBuy: boolean;
  reduceOnly: boolean;
  orderType: string;
  status: string;
  requestedSize: string;
  requestedNotionalUsd: string;
  marginUsd: string | null;
  leverage: string | null;
  marginMode: "cross" | "isolated";
  limitPrice: string | null;
  averageFillPrice: string | null;
  filledSize: string;
  filledNotionalUsd: string;
  feeUsd: string;
  error: string | null;
  submittedAt: string | null;
  acceptedAt: string | null;
  filledAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type TradingClosedTrade = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  coin: string;
  side: "long" | "short";
  entryPrice: string | null;
  exitPrice: string | null;
  size: string;
  entryNotionalUsd: string;
  exitNotionalUsd: string;
  feeUsd: string;
  realizedPnlUsd: string;
  netPnlUsd: string;
  openedAt: string;
  closedAt: string;
  durationMs: number | null;
  openFillCount: number;
  closeFillCount: number;
};

export type TradingSourceMetadata = {
  sourceWallet: string;
  sourceLabel: string | null;
  rank: number | null;
  poolRank: number | null;
  score: string | null;
  allocationPct: string | null;
  liveRealizedPnlUsd: string;
  liveFillCount: number;
  monitoredSeconds: number;
  firstMonitoredAt: string | null;
  currentMonitoringStartedAt: string | null;
  lastMonitoredAt: string | null;
};

export type LiveRiskLimits = {
  maxWeeklyLossPct: string;
  maxOrdersPerMinute: number;
  reconciliationMaxSnapshotAgeSeconds: number;
  entryIntentTtlSeconds: number;
  reduceOnlyWhenStopped: boolean;
};

export type LiveCopyProcessingOrigin =
  | "realtime"
  | "snapshot_recovery"
  | "startup_recovery"
  | "periodic_recovery";

export type LiveCopyDecision = {
  accountKey: string;
  sourceWallet: string;
  sourceFillId: string;
  sequenceIndex: number;
  coin: string;
  plannedAction: "open" | "add" | "reduce" | "close" | "flip_close" | "flip_open";
  side: "long" | "short";
  outcome: "pending" | "retryable" | "order" | "terminal_skip" | "baseline_ignored";
  reason: string | null;
  attemptCount: number;
  origin: LiveCopyProcessingOrigin;
  sourceTimestampMs: number;
  observedAt: string | null;
  firstObservedAt: string | null;
  executionClaimedAt: string | null;
  processingStartedAt: string | null;
  decisionAt: string | null;
  lastAttemptAt: string | null;
  nextAttemptAt: string | null;
  tradingOrderId: string | null;
  orderRecordId: string | null;
  logicalOrderStatus: string | null;
  logicalOrderError: string | null;
  latestDispatchAttemptNumber: number | null;
  latestDispatchClientOrderId: string | null;
  latestDispatchStatus: string | null;
  latestExchangeStatus: string | null;
  latestExchangeErrorCode: string | null;
  latestExchangeErrorMessage: string | null;
  latestExchangeResponse: Record<string, unknown> | null;
  submitAttemptCount: number;
  statusLookupCount: number;
  lastStatusLookupAt: string | null;
  lastStatusLookupError: string | null;
  updatedAt: string;
};

export type TradingAccountsResponse = {
  accounts: TradingAccount[];
  liveTradingEnabled: boolean;
  riskLimits: LiveRiskLimits;
  positions: TradingPosition[];
  recentFills: TradingFill[];
  recentOrders: TradingOrder[];
  recentLiveCopyDecisions: LiveCopyDecision[];
  closedTrades: TradingClosedTrade[];
  sourceMetadata: TradingSourceMetadata[];
  updatedAt: string;
};
