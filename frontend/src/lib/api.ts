import "server-only";

import type { AnalyticsResponse } from "@/types/analytics";
import type { LiveEventListResponse } from "@/types/event";
import type { DatabaseStatsResponse } from "@/types/database";
import type {
  DiscoveryCandidateListResponse,
  DiscoveryImportRunListResponse,
  DiscoverySourceListResponse,
} from "@/types/discovery";
import type { HealthResponse } from "@/types/health";
import type { OperationStatusListResponse } from "@/types/operation";
import type { OpsHealthResponse } from "@/types/ops";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type {
  CopyTradeListResponse,
  SourceTradeListResponse,
  Wallet,
  WalletFillListResponse,
  WalletListResponse,
  WalletScoreDetail,
  WalletStats,
} from "@/types/wallet";

import { frontendConfig } from "./config";

export function getApiBaseUrl() {
  return frontendConfig.serverApiBaseUrl;
}

function getBackendAuthHeaders(): HeadersInit {
  const username = process.env.DASHBOARD_AUTH_USERNAME ?? "admin";
  const password = process.env.DASHBOARD_AUTH_PASSWORD ?? "change-me";
  if (!username || !password) {
    return {};
  }

  const token = Buffer.from(`${username}:${password}`).toString("base64");
  return { Authorization: `Basic ${token}` };
}

function backendGet(url: string) {
  return fetch(url, {
    cache: "no-store",
    next: { revalidate: 0 },
    headers: getBackendAuthHeaders(),
  });
}

export async function getHealth(): Promise<HealthResponse | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/health`);

    if (!response.ok && response.status !== 503) {
      return null;
    }

    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

export async function getDatabaseStats(): Promise<DatabaseStatsResponse | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/database/stats`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as DatabaseStatsResponse;
  } catch {
    return null;
  }
}

export async function getOpsHealth(): Promise<OpsHealthResponse | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/ops/health`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as OpsHealthResponse;
  } catch {
    return null;
  }
}

export async function getAnalytics(): Promise<AnalyticsResponse | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/analytics`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as AnalyticsResponse;
  } catch {
    return null;
  }
}

export async function getDiscoverySources(): Promise<DiscoverySourceListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/discovery/sources`);

    if (!response.ok) {
      return { items: [] };
    }

    return (await response.json()) as DiscoverySourceListResponse;
  } catch {
    return { items: [] };
  }
}

export async function getDiscoveryCandidates({
  limit = 500,
  offset = 0,
  query,
  source,
  status,
}: {
  limit?: number;
  offset?: number;
  query?: string;
  source?: string;
  status?: string;
} = {}): Promise<DiscoveryCandidateListResponse> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (query?.trim()) {
    params.set("q", query.trim());
  }
  if (source?.trim()) {
    params.set("source", source.trim());
  }
  if (status?.trim()) {
    params.set("status", status.trim());
  }

  try {
    const response = await backendGet(`${getApiBaseUrl()}/discovery/candidates?${params.toString()}`);

    if (!response.ok) {
      return { items: [], total: 0, limit, offset };
    }

    return (await response.json()) as DiscoveryCandidateListResponse;
  } catch {
    return { items: [], total: 0, limit, offset };
  }
}

export async function getDiscoveryRuns(limit = 25): Promise<DiscoveryImportRunListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/discovery/runs?limit=${limit}`);

    if (!response.ok) {
      return { items: [], total: 0, limit, offset: 0 };
    }

    return (await response.json()) as DiscoveryImportRunListResponse;
  } catch {
    return { items: [], total: 0, limit, offset: 0 };
  }
}

export async function getWallets(query?: string): Promise<WalletListResponse> {
  const params = new URLSearchParams({ limit: "250" });
  if (query?.trim()) {
    params.set("q", query.trim());
  }

  try {
    const response = await backendGet(`${getApiBaseUrl()}/wallets?${params.toString()}`);

    if (!response.ok) {
      return { items: [], total: 0, limit: 250, offset: 0 };
    }

    return (await response.json()) as WalletListResponse;
  } catch {
    return { items: [], total: 0, limit: 250, offset: 0 };
  }
}

export async function getWallet(address: string): Promise<Wallet | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/wallets/${address}`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as Wallet;
  } catch {
    return null;
  }
}

export async function getWalletFills(address: string): Promise<WalletFillListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/wallets/${address}/fills?limit=100`);

    if (!response.ok) {
      return { items: [], total: 0, limit: 100, offset: 0 };
    }

    return (await response.json()) as WalletFillListResponse;
  } catch {
    return { items: [], total: 0, limit: 100, offset: 0 };
  }
}

export async function getWalletStats(address: string): Promise<WalletStats | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/wallets/${address}/stats`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as WalletStats;
  } catch {
    return null;
  }
}

export async function getWalletScoreDetail(address: string): Promise<WalletScoreDetail | null> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/scores/${address}/detail`);

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as WalletScoreDetail;
  } catch {
    return null;
  }
}

export async function getWalletCopyTrades(address: string): Promise<CopyTradeListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/wallets/${address}/copy-trades?limit=100`);

    if (!response.ok) {
      return { items: [], total: 0, limit: 100, offset: 0 };
    }

    return (await response.json()) as CopyTradeListResponse;
  } catch {
    return { items: [], total: 0, limit: 100, offset: 0 };
  }
}

export async function getWalletSourceTrades(address: string): Promise<SourceTradeListResponse> {
  try {
    const response = await backendGet(
      `${getApiBaseUrl()}/wallets/${address}/source-trades?limit=5000`,
    );

    if (!response.ok) {
      return emptySourceTrades();
    }

    return (await response.json()) as SourceTradeListResponse;
  } catch {
    return emptySourceTrades();
  }
}

function emptySourceTrades(): SourceTradeListResponse {
  return {
    items: [],
    total: 0,
    limit: 5000,
    offset: 0,
    days: null,
    summary: {
      closedTradeCount: 0,
      openTradeCount: 0,
      unmatchedCloseFillCount: 0,
      preexistingOpenFillCount: 0,
      totalEntryNotionalUsd: "0",
      realizedPnlUsd: "0",
      feeUsd: "0",
      netPnlUsd: "0",
      liquidationTradeCount: 0,
      liquidationNotionalUsd: "0",
    },
    windows: [],
  };
}

export async function getLiveEvents(limit = 100): Promise<LiveEventListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/events/recent?limit=${limit}`);

    if (!response.ok) {
      return { items: [], total: 0 };
    }

    return (await response.json()) as LiveEventListResponse;
  } catch {
    return { items: [], total: 0 };
  }
}

export async function getOperationStatuses(): Promise<OperationStatusListResponse> {
  try {
    const response = await backendGet(`${getApiBaseUrl()}/operations/status`);

    if (!response.ok) {
      return { items: [] };
    }

    return (await response.json()) as OperationStatusListResponse;
  } catch {
    return { items: [] };
  }
}

type PaperTradingSummaryOptions = {
  closedTradeLimit?: number;
  recentFillLimit?: number;
};

export async function getPaperTradingSummary(
  options: PaperTradingSummaryOptions = {},
): Promise<PaperTradingSummaryResponse> {
  try {
    const url = new URL(`${getApiBaseUrl()}/paper-trading`);
    if (options.closedTradeLimit) {
      url.searchParams.set("closed_trade_limit", String(options.closedTradeLimit));
    }
    if (options.recentFillLimit) {
      url.searchParams.set("recent_fill_limit", String(options.recentFillLimit));
    }
    const response = await backendGet(url.toString());

    if (!response.ok) {
      return emptyPaperTradingSummary();
    }

    return (await response.json()) as PaperTradingSummaryResponse;
  } catch {
    return emptyPaperTradingSummary();
  }
}

function emptyPaperTradingSummary(): PaperTradingSummaryResponse {
  return {
    policy: {
      enabled: false,
      topWalletCount: 10,
      topTierWalletCount: 3,
      topTierAllocationPct: "0.05",
      standardAllocationPct: "0.03",
      maxTotalAllocationPct: "0.30",
      minOrderNotionalUsd: "10",
      adjustSmallOrdersToMinOrder: true,
      feeRate: "0.00045",
      slippageBps: "5",
      latencyMs: 250,
      maxPriceDriftBps: "50",
      useLiveMidPrice: true,
      marketPriceCacheEnabled: true,
      marketPriceCacheStaleSeconds: 2,
    },
    accounts: [],
    allocations: [],
    positions: [],
    walletPerformance: [],
    closedTrades: [],
    recentFills: [],
    updatedAt: new Date(0).toISOString(),
    marketDataStatus: "unavailable",
  };
}
