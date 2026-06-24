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

export type TradingAccountsResponse = {
  accounts: TradingAccount[];
  updatedAt: string;
};
