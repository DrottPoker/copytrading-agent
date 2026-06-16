export type PaperTradingAccount = {
  key: string;
  label: string;
  startingBalanceUsd: string;
  cashBalanceUsd: string;
  equityUsd: string;
  realizedPnlUsd: string;
  feeUsd: string;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
};

export type PaperCopyAllocation = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  rank: number;
  score: string | null;
  allocationPct: string;
  allocationUsd: string;
  maxTotalAllocationPct: string;
  active: boolean;
  updatedAt: string;
};

export type PaperPosition = {
  id: string;
  accountKey: string;
  sourceWallet: string;
  coin: string;
  side: "long" | "short";
  size: string;
  entryPrice: string;
  notionalUsd: string;
  leverage: string;
  marginUsd: string;
  realizedPnlUsd: string;
  feeUsd: string;
  openedAt: string;
  createdAt: string;
  updatedAt: string;
};

export type PaperCopyFill = {
  id: string;
  accountKey: string;
  sourceWallet: string;
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
  sourceSize: string | null;
  sourceNotionalUsd: string | null;
  sourceAccountValueUsd: string | null;
  sourceExposurePct: string | null;
  allocationPct: string | null;
  allocationUsd: string | null;
  skippedReason: string | null;
  filledAt: string;
  createdAt: string;
};

export type PaperTradingPolicy = {
  enabled: boolean;
  topWalletCount: number;
  topTierWalletCount: number;
  topTierAllocationPct: string;
  standardAllocationPct: string;
  maxTotalAllocationPct: string;
  minOrderNotionalUsd: string;
  feeRate: string;
  slippageBps: string;
  latencyMs: number;
  maxPriceDriftBps: string;
  useLiveMidPrice: boolean;
};

export type PaperTradingSummaryResponse = {
  policy: PaperTradingPolicy;
  accounts: PaperTradingAccount[];
  allocations: PaperCopyAllocation[];
  positions: PaperPosition[];
  recentFills: PaperCopyFill[];
};
