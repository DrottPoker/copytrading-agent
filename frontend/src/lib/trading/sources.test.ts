import { describe, expect, it } from "vitest";

import { allocation, paperSummary } from "@/test/tradingFixtures";

import {
  buildLiveSourceHistory,
  buildPaperSourceRows,
  collectLiveCopySourceWallets,
  compareCopySourcesByAllocationUsed,
  compareWalletHistoryByPnl,
  liveAllocationSourceVisible,
  pnlPerMonitoredHour,
  resolveCurrentMonitorStatus,
  resolveCurrentSourceStatus,
  summarizeCopySourceStatuses,
  type SourceMetadata,
} from "./sources";

describe("copy source performance", () => {
  it("calculates PnL per hour from the PnL and the time followed", () => {
    expect(pnlPerMonitoredHour(0.79, 150 * 3600)).toBeCloseTo(0.0052666667, 10);
    expect(pnlPerMonitoredHour(0.79, 0)).toBeNull();
  });

  it("sorts copy sources by allocation used before PnL and status", () => {
    const sources = [
      { openMarginUsd: 10, poolRank: 1, rank: 1, realizedPnlUsd: 0, sourceState: "trading" as const, sourceWallet: "0xzero", usedPct: 0.1 },
      {
        openMarginUsd: 0,
        poolRank: 9,
        rank: 9,
        realizedPnlUsd: 7.17,
        sourceState: "waiting_for_trades" as const,
        sourceWallet: "0xhighest",
        usedPct: 0,
      },
      {
        openMarginUsd: 25,
        poolRank: 10,
        rank: 10,
        realizedPnlUsd: 0.79,
        sourceState: "waiting_for_trades" as const,
        sourceWallet: "0xmiddle",
        usedPct: 0.25,
      },
    ];

    expect(sources.sort(compareCopySourcesByAllocationUsed).map((source) => source.sourceWallet)).toEqual([
      "0xmiddle",
      "0xzero",
      "0xhighest",
    ]);
  });

  it("sorts source history by realized PnL descending", () => {
    const wallets = [
      { poolRank: 1, realizedPnlUsd: 7.17, sourceWallet: "0xlow", totalPnlUsd: 5 },
      { poolRank: 10, realizedPnlUsd: 0.79, sourceWallet: "0xhigh", totalPnlUsd: 6 },
    ];

    expect(wallets.sort(compareWalletHistoryByPnl).map((wallet) => wallet.sourceWallet)).toEqual(["0xlow", "0xhigh"]);
  });

  it("uses all-time live source metadata for source history", () => {
    const metadata = new Map<string, SourceMetadata>([
      [
        "0xhistorical",
        {
          allocationPct: null,
          firstMonitoredAt: "2026-01-01T00:00:00Z",
          label: "Historical winner",
          lastMonitoredAt: "2026-01-11T00:00:00Z",
          liveFillCount: 10,
          liveRealizedPnlUsd: "39.30",
          monitoredSeconds: 10 * 24 * 3600,
          poolRank: 5,
          rank: 5,
          score: "84.1",
        },
      ],
    ]);

    const rows = buildLiveSourceHistory({
      fills: [],
      metadata,
      orders: [],
      positions: [],
      realtimeMonitoring: paperSummary().realtimeMonitoring,
    });

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ copiedFillCount: 10, realizedPnlUsd: 39.3, sourceWallet: "0xhistorical", totalPnlUsd: 39.3 });
    expect(rows[0].realizedPnlPerHourUsd).toBeCloseTo(39.3 / 240, 10);
  });
});

describe("paper source rows", () => {
  it("only uses the allocations and positions of the selected account", () => {
    const summary = paperSummary({
      allocations: [
        allocation({ accountKey: "paper-a", allocationUsd: "200", hasRealtimeSlot: true, canOpenNewPositions: true, openMarginUsd: "50", sourceWallet: "0xa" }),
        allocation({ accountKey: "paper-b", allocationUsd: "999", sourceWallet: "0xb" }),
      ],
      realtimeMonitoring: { ...paperSummary().realtimeMonitoring, desiredWallets: ["0xa"], monitoredWallets: ["0xa"] },
    });

    const rows = buildPaperSourceRows(summary, "paper-a");

    expect(rows.map((row) => row.sourceWallet)).toEqual(["0xa"]);
    expect(rows[0]).toMatchObject({ allocationUsd: 200, monitorStatus: "monitored", sourceState: "waiting_for_trades", usedPct: 0.25 });
    expect(rows[0].sourceStateDetail).toContain("Waiting for the wallet to open a trade");
  });
});

describe("resolveCurrentSourceStatus", () => {
  it("reports entries paused when the source owns a slot but no account accepts entries", () => {
    expect(resolveCurrentSourceStatus({ canOpenNewPositions: false, hasRealtimeSlot: true, openPositionCount: 0 })).toBe(
      "entries_paused",
    );
  });

  it("reports waiting for slot only when no realtime slot exists", () => {
    expect(resolveCurrentSourceStatus({ canOpenNewPositions: false, hasRealtimeSlot: false, openPositionCount: 0 })).toBe(
      "waiting_for_slot",
    );
  });

  it("retains open exposure instead of reporting it as ready without a slot", () => {
    expect(resolveCurrentSourceStatus({ canOpenNewPositions: true, hasRealtimeSlot: false, openPositionCount: 1 })).toBe(
      "retained",
    );
  });

  it("reports an entry-ineligible monitored source with exposure as reduce only", () => {
    expect(resolveCurrentSourceStatus({ canOpenNewPositions: false, hasRealtimeSlot: true, openPositionCount: 1 })).toBe(
      "retained",
    );
  });
});

describe("summarizeCopySourceStatuses", () => {
  it("counts the same source states the table shows", () => {
    expect(
      summarizeCopySourceStatuses([
        { monitorStatus: "monitored", sourceState: "trading" },
        { monitorStatus: "monitored", sourceState: "waiting_for_trades" },
        { monitorStatus: "waiting", sourceState: "waiting_for_slot" },
      ]),
    ).toEqual({ connecting: 0, monitored: 2, offline: 0, trading: 1, waiting: 1 });
  });

  it("counts all monitored sources even when only two manage open exposure", () => {
    expect(
      summarizeCopySourceStatuses(
        Array.from({ length: 10 }, (_, index) => ({
          monitorStatus: "monitored" as const,
          sourceState: index < 2 ? ("trading" as const) : ("entries_paused" as const),
        })),
      ),
    ).toEqual({ connecting: 0, monitored: 10, offline: 0, trading: 2, waiting: 0 });
  });
});

describe("resolveCurrentMonitorStatus", () => {
  const realtimeMonitoring = {
    desiredWallets: ["0xconnected", "0xpending"],
    monitoredWallets: ["0xconnected"],
    status: "connecting" as const,
    updatedAt: "2026-01-01T00:00:00Z",
    workerInstanceId: "worker-1",
    workerRole: "trading",
  };

  it("shows monitored only after the wallet subscription is acknowledged", () => {
    expect(resolveCurrentMonitorStatus({ hasRealtimeSlot: true, realtimeMonitoring, sourceWallet: "0xconnected" })).toBe(
      "monitored",
    );
    expect(resolveCurrentMonitorStatus({ hasRealtimeSlot: true, realtimeMonitoring, sourceWallet: "0xpending" })).toBe(
      "connecting",
    );
  });

  it("distinguishes an assigned offline source from a source waiting for a slot", () => {
    expect(resolveCurrentMonitorStatus({ hasRealtimeSlot: true, realtimeMonitoring, sourceWallet: "0xoffline" })).toBe(
      "offline",
    );
    expect(resolveCurrentMonitorStatus({ hasRealtimeSlot: false, realtimeMonitoring, sourceWallet: "0xwaiting" })).toBe(
      "waiting",
    );
  });
});

describe("live copy source visibility", () => {
  it("hides a paper-only retained source", () => {
    expect(liveAllocationSourceVisible([allocation()], true)).toBe(false);
  });

  it("keeps a current top candidate that is waiting for a live slot visible", () => {
    expect(liveAllocationSourceVisible([allocation({ sourceStatus: "waiting_for_slot" })], true)).toBe(true);
  });

  it("keeps realtime sources visible when live entries are paused", () => {
    const monitoredWallets = Array.from({ length: 10 }, (_, index) => `0xsource${index + 1}`);

    expect(
      collectLiveCopySourceWallets({
        allocations: [],
        liveCopyReady: false,
        livePositionSources: monitoredWallets.slice(0, 2),
        realtimeMonitoring: {
          desiredWallets: monitoredWallets,
          monitoredWallets,
          status: "connected",
          updatedAt: "2026-01-01T00:00:00Z",
          workerInstanceId: "worker-1",
          workerRole: "trading",
        },
      }),
    ).toEqual(monitoredWallets);
  });
});
