export type HighFillLowScoreWalletCandidate = {
  address: string;
  label: string | null;
  fillCount: number;
  score: string;
  lastPolledAt: string | null;
  lastSeenFillAt: string | null;
};

export type HighFillLowScorePruneResponse = {
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  deletedWallets: number;
  deletedFills: number;
  minFills: number;
  scoreThreshold: string;
  scoreOperator: "lte" | "gte";
  items: HighFillLowScoreWalletCandidate[];
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
  accountValueUsd: string | null;
  totalUnrealizedPnlUsd: string | null;
  detail: string | null;
};

export type WalletPruneRuleResult = {
  key: string;
  label: string;
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  deletedWallets: number;
  deletedFills: number;
  rule: string;
  items: WalletPruneCandidate[];
};

export type WalletPruneAllResponse = {
  dryRun: boolean;
  scannedWallets: number;
  candidateWallets: number;
  deletedWallets: number;
  deletedFills: number;
  rules: WalletPruneRuleResult[];
};
