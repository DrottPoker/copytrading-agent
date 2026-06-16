export type Wallet = {
  id: string;
  address: string;
  label: string | null;
  enabled: boolean;
  eligible: boolean;
  copyEnabled: boolean;
  pollingTier: "pool" | "candidate" | "active" | "exit_only" | "cooldown";
  cooldownUntil: string | null;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
  notes: string | null;
  score: WalletScore | null;
  createdAt: string;
  updatedAt: string;
};

export type WalletScore = {
  id: string;
  walletAddress: string;
  score: string;
  pnlScore: string;
  copyabilityScore: string;
  riskScore: string;
  consistencyScore: string;
  recencyScore: string;
  penaltyScore: string;
  copyablePnlUsd: string;
  winRate: string | null;
  profitFactor: string | null;
  maxDrawdownPct: string | null;
  tradeCount: number;
  last24hScore: string | null;
  last7dScore: string | null;
  last30dScore: string | null;
  updatedAt: string;
};

export type WalletScorePenaltyItem = {
  key: string;
  label: string;
  value: string;
  maxValue: string;
  active: boolean;
  detail: string;
};

export type WalletScoreDetail = {
  walletAddress: string;
  windowDays: number;
  minTrades: number;
  fillCount: number;
  tradeCount: number;
  ignoredFillCount: number;
  openTradeCount: number;
  liquidationCount: number;
  liquidationFillCount: number;
  liquidationEventCount: number;
  recencyScore: string;
  netPnlUsd: string;
  grossProfitUsd: string;
  penaltyScore: string;
  penaltyItems: WalletScorePenaltyItem[];
};

export type WalletScoreRunResponse = {
  totalWallets: number;
  scoredWallets: number;
  skippedWallets: number;
  windowDays: number;
  minFills: number;
  minTrades: number;
  updatedAt: string;
};

export type WalletListResponse = {
  items: Wallet[];
  total: number;
  limit: number;
  offset: number;
};

export type WalletFill = {
  id: string;
  walletAddress: string;
  externalFillId: string;
  coin: string;
  side: "buy" | "sell" | "long" | "short";
  price: string;
  size: string;
  notionalUsd: string | null;
  feeUsd: string | null;
  pnlUsd: string | null;
  timestampMs: number;
  sourceTimestampMs: number | null;
  receivedAt: string;
  processedAt: string | null;
  ingestLatencyMs: number | null;
  isSnapshot: boolean;
  rawJson: Record<string, unknown>;
  createdAt: string;
};

export type WalletFillListResponse = {
  items: WalletFill[];
  total: number;
  limit: number;
  offset: number;
};

export type SourceTrade = {
  id: string;
  walletAddress: string;
  coin: string;
  side: "long" | "short";
  status: "open" | "closed";
  openedAtMs: number;
  closedAtMs: number | null;
  durationMs: number | null;
  entrySize: string;
  closedSize: string;
  remainingSize: string;
  entryNotionalUsd: string;
  closeNotionalUsd: string;
  averageEntryPrice: string | null;
  averageExitPrice: string | null;
  realizedPnlUsd: string;
  feeUsd: string;
  netPnlUsd: string;
  entryFillCount: number;
  closeFillCount: number;
};

export type SourceTradeSummary = {
  closedTradeCount: number;
  openTradeCount: number;
  unmatchedCloseFillCount: number;
  preexistingOpenFillCount: number;
  totalEntryNotionalUsd: string;
  realizedPnlUsd: string;
  feeUsd: string;
  netPnlUsd: string;
};

export type SourceTradeListResponse = {
  items: SourceTrade[];
  total: number;
  limit: number;
  offset: number;
  days: number;
  summary: SourceTradeSummary;
};

export type WalletFillImportResponse = {
  walletAddress: string;
  fetched: number;
  rawFetched: number;
  pagesFetched: number;
  inserted: number;
  duplicate: number;
  targetFills: number;
  startTimeMs: number;
  endTimeMs: number;
  latestFillTimeMs: number | null;
};

export type WalletWindowStats = {
  label: string;
  fillCount: number;
  notionalUsd: string;
  pnlUsd: string;
  feeUsd: string;
};

export type WalletCoinStats = {
  coin: string;
  fillCount: number;
  buyCount: number;
  sellCount: number;
  notionalUsd: string;
  pnlUsd: string;
  feeUsd: string;
  lastFillTimeMs: number | null;
};

export type WalletPerpPositionStats = {
  coin: string;
  side: "long" | "short" | "flat";
  size: string;
  entryPrice: string | null;
  positionValueUsd: string | null;
  unrealizedPnlUsd: string | null;
  returnOnEquity: string | null;
  marginUsedUsd: string | null;
  liquidationPrice: string | null;
  leverageType: string | null;
  leverageValue: number | null;
};

export type WalletSpotBalanceStats = {
  coin: string;
  token: number | null;
  total: string;
  hold: string;
  entryNotionalUsd: string;
};

export type WalletCurrentStateStats = {
  stateTimeMs: number | null;
  accountValueUsd: string;
  withdrawableUsd: string;
  totalPositionNotionalUsd: string;
  totalMarginUsedUsd: string;
  totalUnrealizedPnlUsd: string;
  openPositionCount: number;
  spotBalanceCount: number;
  spotEntryNotionalUsd: string;
  spotUsdcBalance: string;
  positions: WalletPerpPositionStats[];
  spotBalances: WalletSpotBalanceStats[];
  error: string | null;
};

export type WalletStats = {
  walletAddress: string;
  fillCount: number;
  snapshotFillCount: number;
  realtimeFillCount: number;
  uniqueCoinCount: number;
  buyCount: number;
  sellCount: number;
  profitableFillCount: number;
  losingFillCount: number;
  winRate: string | null;
  totalNotionalUsd: string;
  averageFillNotionalUsd: string;
  totalPnlUsd: string;
  totalFeeUsd: string;
  averageIngestLatencyMs: string | null;
  maxIngestLatencyMs: number | null;
  firstFillTimeMs: number | null;
  lastFillTimeMs: number | null;
  windows: WalletWindowStats[];
  topCoins: WalletCoinStats[];
  currentState: WalletCurrentStateStats | null;
};

export type CopyTrade = {
  id: string;
  mode: "paper" | "live_small" | "full_live";
  sourceWallet: string;
  coin: string;
  side: "long" | "short";
  status: "open" | "closing" | "closed" | "cancelled" | "error";
  sourceEntryPrice: string | null;
  ourEntryPrice: string | null;
  sourceExitPrice: string | null;
  ourExitPrice: string | null;
  sizeUsd: string;
  riskUsd: string | null;
  pnlUsd: string | null;
  pnlPct: string | null;
  entrySignalId: string | null;
  exitSignalId: string | null;
  openedAt: string | null;
  closedAt: string | null;
  createdAt: string;
  updatedAt: string;
};

export type CopyTradeListResponse = {
  items: CopyTrade[];
  total: number;
  limit: number;
  offset: number;
};
