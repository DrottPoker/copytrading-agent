export type LowScoreWalletCandidate = {
  address: string;
  label: string | null;
  fillCount: number;
  closedTradeCount: number;
  score: string;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
};

export type LowScorePruneResponse = {
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  deletedWallets: number;
  deletedFills: number;
  minClosedTrades: number;
  scoreThreshold: string;
  scoreOperator: "lt" | "lte" | "gt" | "gte";
  items: LowScoreWalletCandidate[];
};

export type ZeroFillWalletCandidate = {
  address: string;
  label: string | null;
  fillCount: number;
  score: string | null;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
};

export type ZeroFillPruneResponse = {
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  deletedWallets: number;
  deletedFills: number;
  items: ZeroFillWalletCandidate[];
};

export type WalletPruneCandidate = {
  address: string;
  label: string | null;
  fillCount: number | null;
  closedTradeCount: number | null;
  score: string | null;
  maxDrawdownPct: string | null;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
  perpEquityUsd?: string | null;
  accountValueUsd: string | null;
  totalUnrealizedPnlUsd: string | null;
  detail: string | null;
  error: string | null;
};

export type WalletPruneRuleResult = {
  key: string;
  label: string;
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  erroredWallets: number;
  deletedWallets: number;
  deletedFills: number;
  rule: string;
  items: WalletPruneCandidate[];
};

export type WalletPruneAllResponse = {
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  erroredWallets: number;
  deletedWallets: number;
  deletedFills: number;
  rules: WalletPruneRuleResult[];
};
