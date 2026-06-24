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
  createdAt: string;
  updatedAt: string;
};

export type TradingAccountsResponse = {
  accounts: TradingAccount[];
  updatedAt: string;
};
