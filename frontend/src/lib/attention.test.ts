import { describe, expect, it } from "vitest";

import { liveAccount, paperAccount, paperSummary, tradingAccounts } from "@/test/tradingFixtures";
import type { OpsHealthResponse } from "@/types/ops";

import { buildAttentionItems } from "./attention";

function ops(overrides: Partial<OpsHealthResponse> = {}): OpsHealthResponse {
  return {
    backup: {
      backupCount: 3,
      directory: "/backups",
      latestAgeSeconds: 3600,
      latestFile: "copyagent.dump",
      latestModifiedAt: "2026-01-01T00:00:00Z",
      latestSizeBytes: 1000,
      note: "",
      staleAfterSeconds: 86400,
      status: "ok",
      totalSizeBytes: 3000,
    },
    config: {
      activeCopyWallets: 10,
      environment: "test",
      hyperliquidNetwork: "mainnet",
      liveTradingEnabled: false,
      maxRealtimeWallets: 10,
      mode: "paper",
      paperTradingEnabled: true,
      workerRunInApiProcess: false,
    },
    database: {
      connectionMax: 100,
      connectionTotal: 10,
      connectionUsagePct: "0.1",
      databaseName: "copyagent",
      databaseSizeBytes: 1,
      databaseSizePretty: "1 B",
      error: null,
      fillCount: 1,
      largestTableName: "wallet_fills",
      largestTableSizeBytes: 1,
      measuredAt: null,
      status: "ok",
      tableCount: 1,
    },
    dependencies: { postgres: { detail: null, status: "ok" }, redis: { detail: null, status: "ok" } },
    disk: { freeBytes: 100, path: "/", status: "ok", totalBytes: 200, usagePct: "0.5", usedBytes: 100 },
    load: { cpuCount: 4, load15m: "0.1", load1m: "0.1", load5m: "0.1", status: "ok" },
    measuredAt: "2026-01-01T00:00:00Z",
    memory: { availableBytes: 1, status: "ok", totalBytes: 2, usagePct: "0.5", usedBytes: 1 },
    metadata: {},
    notes: [],
    operations: [],
    service: "copy agent",
    status: "ok",
    version: "0.1.0",
    workers: [],
    ...overrides,
  };
}

describe("buildAttentionItems", () => {
  it("is empty when everything is healthy", () => {
    expect(buildAttentionItems({ ops: ops(), summary: paperSummary(), tradingAccounts: tradingAccounts() })).toEqual([]);
  });

  it("lists problems before warnings", () => {
    const items = buildAttentionItems({
      ops: ops({
        backup: { ...ops().backup, latestAgeSeconds: null, latestFile: null, status: "missing" },
        dependencies: { postgres: { detail: "connection refused", status: "error" }, redis: { detail: null, status: "ok" } },
      }),
      summary: paperSummary({
        accounts: [paperAccount({ exitOnlyAt: "2026-01-01T00:00:00Z", exitOnlyReason: "daily loss limit" })],
      }),
      tradingAccounts: tradingAccounts(),
    });

    expect(items.map((item) => item.title)).toEqual([
      "Postgres is unreachable",
      "No database backups",
      "Paper test is exit only",
    ]);
    expect(items[2].href).toBe("/trading?account=paper-test");
  });

  it("flags a live account with failed reconciliation", () => {
    const items = buildAttentionItems({
      ops: ops(),
      summary: paperSummary(),
      tradingAccounts: tradingAccounts({ accounts: [liveAccount({ reconciliationStatus: "failed" })] }),
    });

    expect(items[0].title).toBe("Live test: New entries paused: reconciliation failed");
    expect(items[0].tone).toBe("negative");
  });

  it("warns when trading accounts run without the realtime feed", () => {
    const items = buildAttentionItems({
      ops: ops(),
      summary: paperSummary({
        accounts: [paperAccount()],
        realtimeMonitoring: { ...paperSummary().realtimeMonitoring, status: "disconnected" },
      }),
      tradingAccounts: tradingAccounts(),
    });

    expect(items.map((item) => item.title)).toContain("Realtime feed disconnected");
  });
});
