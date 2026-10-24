export type PaperTradingAccount = {
  key: string;
  label: string;
  startingBalanceUsd: string;
  cashBalanceUsd: string;
  equityUsd: string;
  realizedPnlUsd: string;
  unrealizedPnlUsd: string;
  totalPnlUsd: string;
  totalPnlPct: string | null;
  openPositionCount: number;
  openNotionalUsd: string;
  openMarginUsd: string;
  feeUsd: string;
  fundingUsd?: string;
  enabled: boolean;
  exitOnlyAt?: string | null;
  exitOnlyReason?: string | null;
  createdAt: string;
  updatedAt: string;
};

export type PaperCopyAllocation = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  rank: number;
  poolRank: number | null;
  score: string | null;
  allocationPct: string;
  allocationUsd: string;
  openMarginUsd: string;
  remainingAllocationUsd: string;
  pocketUsedPct: string | null;
  maxTotalAllocationPct: string;
  active: boolean;
  hasRealtimeSlot: boolean;
  isRealtimeMonitored: boolean;
  canOpenNewPositions: boolean;
  monitorStatus: "monitored" | "connecting" | "offline" | "waiting";
  sourceStatus: "trading" | "retained" | "waiting_for_trades" | "waiting_for_slot";
  sourceStatusReason: string;
  updatedAt: string;
};

export type PaperPosition = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  coin: string;
  side: "long" | "short";
  size: string;
  entryPrice: string;
  notionalUsd: string;
  leverage: string;
  marginUsd: string;
  marginMode?: "cross" | "isolated";
  fundingUsd?: string;
  realizedPnlUsd: string;
  markPrice: string | null;
  currentNotionalUsd: string | null;
  unrealizedPnlUsd: string | null;
  unrealizedPnlPct: string | null;
  priceUpdatedAt: string | null;
  feeUsd: string;
  addFillCount: number;
  closeFillCount: number;
  openedAt: string;
  entryExecutionDelayMs: number;
  createdAt: string;
  updatedAt: string;
};

export type PaperCopyFill = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  sourceFillId: string;
  sequenceIndex: number;
  coin: string;
  action: "open" | "add" | "reduce" | "close" | "flip_close" | "flip_open" | "skip";
  side: "long" | "short" | null;
  price: string | null;
  size: string | null;
  notionalUsd: string | null;
  leverage: string | null;
  marginUsd: string | null;
  feeUsd: string;
  realizedPnlUsd: string;
  sourcePrice: string | null;
  observedPrice: string | null;
  executionPrice: string | null;
  priceDriftBps: string | null;
  priceSource: string | null;
  maxPriceDriftBps: string | null;
  sourceSize: string | null;
  sourceNotionalUsd: string | null;
  sourcePerpEquityUsd: string | null;
  sourceAccountValueUsd: string | null;
  sourceExposurePct: string | null;
  allocationPct: string | null;
  allocationUsd: string | null;
  skippedReason: string | null;
  minOrderAdjusted: boolean;
  originalNotionalUsd: string | null;
  adjustedNotionalUsd: string | null;
  minOrderNotionalUsd: string | null;
  filledAt: string;
  createdAt: string;
};

export type PaperTradingPolicy = {
  enabled: boolean;
  topWalletCount: number;
  sourceAllocationPct: string;
  maxTotalAllocationPct: string;
  maxSourceExposureMultiple: string;
  maxTotalExposureMultiple: string;
  minOrderNotionalUsd: string;
  minOrderRoundUpRatio: string;
  feeRate: string;
  slippageBps: string;
  latencyMs: number;
  maxPriceDriftBps: string;
  useLiveMidPrice: boolean;
  marketPriceCacheEnabled: boolean;
  marketPriceCacheStaleSeconds: number;
};

/** Copy results of one source in one paper account since the account's last balance reset. */
export type PaperWalletPerformance = {
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  rank: number | null;
  poolRank: number | null;
  score: string | null;
  allocationPct: string | null;
  active: boolean;
  monitorStatus: "monitored" | "history";
  openPositionCount: number;
  copiedFillCount: number;
  skippedFillCount: number;
  closedTradeCount: number;
  realizedPnlUsd: string;
  feeUsd: string;
  fundingUsd: string;
  netRealizedPnlUsd: string;
  unrealizedPnlUsd: string;
  totalPnlUsd: string;
  monitoredSeconds: number;
  monitoredHours: string;
  netRealizedPnlPerMonitoredHourUsd: string | null;
  totalPnlPerMonitoredHourUsd: string | null;
  firstMonitoredAt: string | null;
  currentMonitoringStartedAt: string | null;
  lastMonitoredAt: string | null;
  openNotionalUsd: string;
  openMarginUsd: string;
  lastFillAt: string | null;
};

/** One round trip: from the opening fill to the fill that left the position flat. */
export type PaperClosedTrade = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  sourceLabel: string | null;
  sourceFillId: string;
  coin: string;
  closeType: "close" | "flip_close" | "liquidation";
  side: "long" | "short" | null;
  entryPrice: string | null;
  exitPrice: string | null;
  size: string | null;
  entryNotionalUsd: string | null;
  notionalUsd: string | null;
  leverage: string | null;
  marginUsd: string | null;
  feeUsd: string;
  fundingUsd: string;
  realizedPnlUsd: string;
  netPnlUsd: string;
  openFillCount: number;
  closeFillCount: number;
  isSourceLiquidation: boolean;
  isPaperLiquidation?: boolean;
  openedAt: string | null;
  closedAt: string;
  durationMs: number | null;
  createdAt: string;
};

export type PaperTradingSummaryResponse = {
  policy: PaperTradingPolicy;
  accounts: PaperTradingAccount[];
  allocations: PaperCopyAllocation[];
  positions: PaperPosition[];
  walletPerformance: PaperWalletPerformance[];
  recentFills: PaperCopyFill[];
  realtimeMonitoring: {
    status: "idle" | "connecting" | "connected" | "disconnected";
    desiredWallets: string[];
    monitoredWallets: string[];
    workerRole: string | null;
    workerInstanceId: string | null;
    updatedAt: string | null;
  };
  updatedAt: string;
  marketDataStatus: "live" | "partial" | "unavailable" | "no_open_positions";
};

export type PaperEquityPoint = {
  recordedAt: string;
  equityUsd: string;
};

/** Sums and streaks of round trip net PnL; ratios are derived in the dashboard. */
export type PaperTradeStats = {
  tradeCount: number;
  winCount: number;
  lossCount: number;
  grossProfitUsd: string;
  grossLossUsd: string;
  netPnlUsd: string;
  bestTradeUsd: string;
  worstTradeUsd: string;
  longestWinStreak: number;
  longestLossStreak: number;
  currentStreakCount: number;
  currentStreakKind: "win" | "loss" | null;
};

/** Equity history and round trip statistics of one paper account since its last reset. */
export type PaperAccountPerformanceResponse = {
  accountKey: string;
  startingBalanceUsd: string;
  statsStartedAt: string;
  snapshotIntervalSeconds: number;
  equityPoints: PaperEquityPoint[];
  equitySnapshotCount: number;
  peakEquityUsd: string | null;
  maxDrawdownUsd: string;
  maxDrawdownPct: string | null;
  maxDrawdownPeakAt: string | null;
  maxDrawdownTroughAt: string | null;
  tradeStats: PaperTradeStats;
  closedTrades: PaperClosedTrade[];
  updatedAt: string;
};
