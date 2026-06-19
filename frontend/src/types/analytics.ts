export type AnalyticsOverview = {
  poolWalletCount: number;
  enabledWalletCount: number;
  scoredWalletCount: number;
  scoringCoveragePct: string | null;
  averageScore: string | null;
  activeSourceCount: number;
  openPaperSourceCount: number;
  openPaperPositionCount: number;
  paperRealizedPnlUsd: string;
  paperFeeUsd: string;
  paperOpenMarginUsd: string;
  paperSkipRatePct: string | null;
};

export type AnalyticsScoreAverages = {
  score: string | null;
  profitabilityScore: string | null;
  consistencyScore: string | null;
  riskScore: string | null;
  copyabilityScore: string | null;
  recencyScore: string | null;
  penaltyScore: string | null;
};

export type AnalyticsBucket = {
  label: string;
  count: number;
  pct: string | null;
};

export type AnalyticsWalletRow = {
  walletAddress: string;
  label: string | null;
  poolRank: number | null;
  score: string | null;
  tradeCount: number;
  copyablePnlUsd: string;
  winRate: string | null;
  profitFactor: string | null;
  maxDrawdownPct: string | null;
  currentDrawdownPct: string | null;
  marginStressPct: string | null;
  currentDrawdownStatus: string;
  lastSeenFillAt: string | null;
};

export type AnalyticsSourcePerformanceRow = {
  sourceWallet: string;
  sourceLabel: string | null;
  poolRank: number | null;
  score: string | null;
  closedTradeCount: number;
  winRate: string | null;
  netPnlUsd: string;
  feeUsd: string;
  entryNotionalUsd: string;
  roiPct: string | null;
  averageDurationHours: string | null;
  lastClosedAt: string | null;
};

export type AnalyticsCoinPerformanceRow = {
  coin: string;
  closedTradeCount: number;
  winRate: string | null;
  netPnlUsd: string;
  feeUsd: string;
  entryNotionalUsd: string;
  roiPct: string | null;
  averageDurationHours: string | null;
};

export type AnalyticsPaperSourceRow = {
  sourceWallet: string;
  sourceLabel: string | null;
  copiedFillCount: number;
  skippedFillCount: number;
  skipRatePct: string | null;
  realizedPnlUsd: string;
  feeUsd: string;
  openPositionCount: number;
  openMarginUsd: string;
  lastFillAt: string | null;
};

export type AnalyticsSkipReasonRow = {
  reason: string;
  count: number;
  pct: string | null;
  lastSeenAt: string | null;
};

export type AnalyticsDiscoverySourceRow = {
  source: string;
  total: number;
  discovered: number;
  accepted: number;
  rejected: number;
  promoted: number;
  backfillSucceeded: number;
  averageRoiPct: string | null;
  averageAccountValueUsd: string | null;
  lastSeenAt: string | null;
};

export type AnalyticsFreshness = {
  latestWalletFillAt: string | null;
  latestScoringAt: string | null;
  latestPositionSnapshotAt: string | null;
  staleEnabledWalletCount: number;
  currentDrawdownUnavailableCount: number;
  generatedAt: string;
};

export type AnalyticsResponse = {
  overview: AnalyticsOverview;
  scoreAverages: AnalyticsScoreAverages;
  scoreBuckets: AnalyticsBucket[];
  drawdownStatusBuckets: AnalyticsBucket[];
  opportunityWallets: AnalyticsWalletRow[];
  riskWatchlist: AnalyticsWalletRow[];
  sourcePerformance: AnalyticsSourcePerformanceRow[];
  coinPerformance: AnalyticsCoinPerformanceRow[];
  paperSources: AnalyticsPaperSourceRow[];
  skipReasons: AnalyticsSkipReasonRow[];
  discoverySources: AnalyticsDiscoverySourceRow[];
  freshness: AnalyticsFreshness;
};
