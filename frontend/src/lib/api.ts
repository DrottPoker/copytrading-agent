import type { LiveEventListResponse } from "@/types/event";
import type { DatabaseStatsResponse } from "@/types/database";
import type {
  DiscoveryCandidateListResponse,
  DiscoveryImportRunListResponse,
  DiscoverySourceListResponse,
} from "@/types/discovery";
import type { HealthResponse } from "@/types/health";
import type { OperationStatusListResponse } from "@/types/operation";
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

export function getPublicApiBaseUrl() {
  return frontendConfig.browserApiBaseUrl;
}

export async function getHealth(): Promise<HealthResponse | null> {
  try {
    const response = await fetch(`${getApiBaseUrl()}/health`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as HealthResponse;
  } catch {
    return null;
  }
}

export async function getDatabaseStats(): Promise<DatabaseStatsResponse | null> {
  try {
    const response = await fetch(`${getApiBaseUrl()}/database/stats`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

    if (!response.ok) {
      return null;
    }

    return (await response.json()) as DatabaseStatsResponse;
  } catch {
    return null;
  }
}

export async function getDiscoverySources(): Promise<DiscoverySourceListResponse> {
  try {
    const response = await fetch(`${getApiBaseUrl()}/discovery/sources`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/discovery/candidates?${params.toString()}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/discovery/runs?limit=${limit}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/wallets?${params.toString()}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/wallets/${address}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/wallets/${address}/fills?limit=100`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/wallets/${address}/stats`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/scores/${address}/detail`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/wallets/${address}/copy-trades?limit=100`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(
      `${getApiBaseUrl()}/wallets/${address}/source-trades?days=30&limit=100`,
      {
        cache: "no-store",
        next: { revalidate: 0 },
      },
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
    limit: 100,
    offset: 0,
    days: 30,
    summary: {
      closedTradeCount: 0,
      openTradeCount: 0,
      unmatchedCloseFillCount: 0,
      preexistingOpenFillCount: 0,
      totalEntryNotionalUsd: "0",
      realizedPnlUsd: "0",
      feeUsd: "0",
      netPnlUsd: "0",
    },
  };
}

export async function getLiveEvents(limit = 100): Promise<LiveEventListResponse> {
  try {
    const response = await fetch(`${getApiBaseUrl()}/events/recent?limit=${limit}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

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
    const response = await fetch(`${getApiBaseUrl()}/operations/status`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });

    if (!response.ok) {
      return { items: [] };
    }

    return (await response.json()) as OperationStatusListResponse;
  } catch {
    return { items: [] };
  }
}
