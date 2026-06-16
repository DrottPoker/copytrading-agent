export type DiscoverySource = {
  key: string;
  label: string;
  provider: string;
  enabled: boolean;
  configured: boolean;
  notes: string | null;
};

export type DiscoverySourceListResponse = {
  items: DiscoverySource[];
};

export type DiscoveryCandidate = {
  id: string;
  walletAddress: string;
  source: string;
  sourceRank: number | null;
  sourceLabel: string | null;
  sourceCohort: string | null;
  accountValue: string | null;
  sourcePnl: string | null;
  sourceRoi: string | null;
  sourceCopyScore: string | null;
  accountRole: string;
  parentAddress: string | null;
  subaccountName: string | null;
  status: string;
  failReason: string | null;
  backfillStatus: string;
  backfillError: string | null;
  lastBackfilledAt: string | null;
  backfillFetchedCount: number;
  backfillInsertedCount: number;
  backfillDuplicateCount: number;
  fillCount: number;
  closedTradeCount: number;
  openTradeCount: number;
  ignoredFillCount: number;
  netPnlUsd: string | null;
  profitFactor: string | null;
  winRate: string | null;
  maxDrawdownPct: string | null;
  averageTradeNotionalUsd: string | null;
  lastTradeTimeMs: number | null;
  firstSeenAt: string;
  lastSeenAt: string;
  createdAt: string;
  updatedAt: string;
};

export type DiscoveryCandidateListResponse = {
  items: DiscoveryCandidate[];
  total: number;
  limit: number;
  offset: number;
};

export type DiscoveryImportRun = {
  id: string;
  source: string;
  status: string;
  requestedLimit: number;
  fetchedCount: number;
  candidateCount: number;
  insertedCount: number;
  updatedCount: number;
  skippedCount: number;
  error: string | null;
  startedAt: string;
  finishedAt: string | null;
};

export type DiscoveryImportRunListResponse = {
  items: DiscoveryImportRun[];
  total: number;
  limit: number;
  offset: number;
};

export type DiscoveryPrefilterResponse = {
  evaluated: number;
  accepted: number;
  rejected: number;
  unchanged: number;
  rejectReasons: Record<string, number>;
  candidates: DiscoveryCandidate[];
};

export type DiscoveryBackfillItem = {
  walletAddress: string;
  source: string;
  status: string;
  failReason: string | null;
  poolAction: string | null;
  fetched: number;
  inserted: number;
  duplicate: number;
  fillCount: number;
  closedTradeCount: number;
  openTradeCount: number;
  ignoredFillCount: number;
  netPnlUsd: string | null;
  profitFactor: string | null;
  winRate: string | null;
  maxDrawdownPct: string | null;
  error: string | null;
};

export type DiscoveryBackfillResponse = {
  scanned: number;
  backfilled: number;
  accepted: number;
  rejected: number;
  promoted: number;
  poolInserted: number;
  poolDuplicate: number;
  failed: number;
  skipped: number;
  rejectReasons: Record<string, number>;
  items: DiscoveryBackfillItem[];
};

export type DiscoveryPromoteItem = {
  walletAddress: string;
  source: string;
  action: string;
  label: string | null;
  alreadyInPool: boolean;
  reason: string | null;
};

export type DiscoveryPromoteResponse = {
  scanned: number;
  promoted: number;
  inserted: number;
  duplicate: number;
  skipped: number;
  items: DiscoveryPromoteItem[];
};

export type DiscoveryImportResponse = {
  requestedSources: string[];
  limit: number;
  runs: DiscoveryImportRun[];
  candidates: DiscoveryCandidate[];
  fetched: number;
  candidateCount: number;
  inserted: number;
  updated: number;
  skipped: number;
  skipReasons: Record<string, number>;
  failedSources: number;
  prefilter: DiscoveryPrefilterResponse | null;
  backfill: DiscoveryBackfillResponse | null;
};

export type DiscoveryActionResponse =
  | DiscoveryImportResponse
  | DiscoveryPrefilterResponse
  | DiscoveryBackfillResponse
  | DiscoveryPromoteResponse;
