export type DependencyStatus = {
  status: "ok" | "error" | "not_configured" | "unknown";
  detail?: string;
};

export type HealthResponse = {
  status: "ok" | "degraded";
  service: string;
  version: string;
  environment: string;
  mode: "monitor" | "paper" | "live_small";
  paperTradingEnabled: boolean;
  liveTradingEnabled: boolean;
  workerRunInApiProcess: boolean;
  hyperliquidNetwork: "mainnet" | "testnet";
  activeCopyWallets: number;
  maxRealtimeWallets: number;
  dependencies: {
    postgres: DependencyStatus;
    redis: DependencyStatus;
  };
};
