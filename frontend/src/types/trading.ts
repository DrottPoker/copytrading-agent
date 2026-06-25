export type TradingCapitalBalance = {
  key: string;
  label: string;
  equityUsd: string;
  availableUsd: string | null;
  tradable: boolean;
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
  capitalMode: "unified" | "standard_per_dex" | null;
  userAbstraction: string | null;
  tradableEquityUsd: string | null;
  perpEquityUsd: string | null;
  spotUsdcBalanceUsd: string | null;
  spotUsdcAvailableUsd: string | null;
  capitalBalances: TradingCapitalBalance[];
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
  marginUsd: string;
  realizedPnlUsd: string;
  feeUsd: string;
  openedAt: string;
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

export type TradingAccountsResponse = {
  accounts: TradingAccount[];
  liveTradingEnabled: boolean;
  liveCopyEnabled: boolean;
  positions: TradingPosition[];
  recentFills: TradingFill[];
  updatedAt: string;
};
