export type DatabaseConnectionStats = {
  total: number;
  active: number;
  idle: number;
  idleInTransaction: number;
  maxConnections: number | null;
  usagePct: string | null;
};

export type DatabaseTableStats = {
  name: string;
  estimatedRows: number;
  deadRows: number;
  tableSizeBytes: number;
  indexSizeBytes: number;
  totalSizeBytes: number;
  seqScanCount: number;
  indexScanCount: number;
  lastVacuumAt: string | null;
  lastAutovacuumAt: string | null;
  lastAnalyzeAt: string | null;
  lastAutoanalyzeAt: string | null;
};

export type DatabaseWalletStats = {
  total: number;
  enabled: number;
  eligible: number;
  copyEnabled: number;
  unpolled: number;
  stale24h: number;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
  tiers: Record<string, number>;
};

export type DatabaseFillStats = {
  total: number;
  snapshot: number;
  realtime: number;
  walletCount: number;
  poolWalletCount: number;
  orphanWalletCount: number;
  coinCount: number;
  totalNotionalUsd: string;
  totalFeeUsd: string;
  totalPnlUsd: string;
  firstFillTimeMs: number | null;
  lastFillTimeMs: number | null;
  lastInsertedAt: string | null;
};

export type DatabaseScoreStats = {
  scoredWallets: number;
  averageScore: string | null;
  bestScore: string | null;
  zeroOrNegative: number;
  above70: number;
  lastScoredAt: string | null;
};

export type DatabaseCopyTradeStats = {
  total: number;
  open: number;
  closed: number;
  error: number;
  totalSizeUsd: string;
  totalPnlUsd: string;
  lastCreatedAt: string | null;
  statuses: Record<string, number>;
  modes: Record<string, number>;
};

export type DatabaseSignalStats = {
  total: number;
  copy: number;
  skip: number;
  exit: number;
  observe: number;
  lastCreatedAt: string | null;
};

export type DatabaseOperationalStats = {
  activeCopyWallets: number;
  realtimeSlotsUsed: number;
  activeCopyStatuses: Record<string, number>;
  sourceTradeLinks: number;
  riskEvents: number;
  auditLogs: number;
  settings: number;
};

export type DatabaseStatsResponse = {
  measuredAt: string;
  databaseName: string;
  databaseSizeBytes: number;
  databaseSizePretty: string;
  tableCount: number;
  connections: DatabaseConnectionStats;
  wallets: DatabaseWalletStats;
  fills: DatabaseFillStats;
  scores: DatabaseScoreStats;
  copyTrades: DatabaseCopyTradeStats;
  signals: DatabaseSignalStats;
  operational: DatabaseOperationalStats;
  tables: DatabaseTableStats[];
};

export type FillRawJsonCompactResponse = {
  dryRun: boolean;
  candidateFills: number;
  processedFills: number;
  remainingCandidates: number | null;
  beforeRawJsonBytes: number;
  afterRawJsonBytes: number;
  savedRawJsonBytes: number;
  keptFields: string[];
  batchSize: number;
  maxRows: number;
  note: string;
};
