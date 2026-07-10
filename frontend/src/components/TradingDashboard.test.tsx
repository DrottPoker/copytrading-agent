import { describe, expect, it } from "vitest";

import type { PaperCopyAllocation } from "@/types/paper";
import type { TradingPosition } from "@/types/trading";

import {
  collectLiveCopySourceWallets,
  displayLivePositions,
  liveAllocationSourceVisible,
  resolveCurrentMonitorStatus,
  resolveCurrentSourceStatus,
  summarizeCopySourceStatuses,
} from "./TradingDashboard";

function position(overrides: Partial<TradingPosition>): TradingPosition {
  return {
    id: "position-1",
    accountKey: "live-test",
    accountType: "live",
    sourceWallet: "0xsource",
    coin: "BTC",
    side: "long",
    size: "1",
    entryPrice: "100",
    notionalUsd: "100",
    leverage: "1",
    marginMode: "cross",
    marginUsd: "100",
    currentNotionalUsd: "100",
    markPrice: "100",
    unrealizedPnlUsd: "0",
    unrealizedPnlPct: "0",
    priceUpdatedAt: null,
    realizedPnlUsd: "0",
    feeUsd: "0",
    addFillCount: 1,
    closeFillCount: 0,
    openedAt: "2026-01-01T00:00:00Z",
    entryExecutionDelayMs: null,
    lastReconciledAt: null,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function allocation(overrides: Partial<PaperCopyAllocation>): PaperCopyAllocation {
  return {
    id: "allocation-1",
    accountKey: "paper-test",
    sourceWallet: "0xsource",
    sourceLabel: null,
    rank: 11,
    poolRank: 11,
    score: "75",
    allocationPct: "0.2",
    allocationUsd: "20",
    openMarginUsd: "0",
    remainingAllocationUsd: "20",
    pocketUsedPct: "0",
    maxTotalAllocationPct: "0.8",
    active: false,
    hasRealtimeSlot: false,
    isRealtimeMonitored: false,
    canOpenNewPositions: false,
    monitorStatus: "waiting",
    sourceStatus: "retained",
    sourceStatusReason: "outside_copy_top_wallet_count",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("displayLivePositions", () => {
  it("only hides a source position when the same exchange coin exists", () => {
    const exchangeBtc = position({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const sourceBtc = position({ id: "source-btc" });
    const sourceEth = position({ id: "source-eth", coin: "ETH" });

    expect(displayLivePositions([exchangeBtc, sourceBtc, sourceEth])).toEqual([
      exchangeBtc,
      sourceEth,
    ]);
  });
});

describe("resolveCurrentSourceStatus", () => {
  it("reports entries paused when the source owns a slot but no account accepts entries", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: false,
        hasRealtimeSlot: true,
        openPositionCount: 0,
      }),
    ).toBe("entries_paused");
  });

  it("reports waiting for slot only when no realtime slot exists", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: false,
        hasRealtimeSlot: false,
        openPositionCount: 0,
      }),
    ).toBe("waiting_for_slot");
  });

  it("retains open exposure instead of reporting it as ready without a slot", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: true,
        hasRealtimeSlot: false,
        openPositionCount: 1,
      }),
    ).toBe("retained");
  });

  it("reports a monitored open source as trading while new entries are paused", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: false,
        hasRealtimeSlot: true,
        openPositionCount: 1,
      }),
    ).toBe("trading");
  });
});

describe("summarizeCopySourceStatuses", () => {
  it("counts the same current source states rendered by Copy Sources", () => {
    expect(
      summarizeCopySourceStatuses([
        { monitorStatus: "monitored", sourceStatus: "trading" },
        { monitorStatus: "monitored", sourceStatus: "waiting_for_trades" },
        { monitorStatus: "waiting", sourceStatus: "waiting_for_slot" },
      ]),
    ).toEqual({ connecting: 0, monitored: 2, offline: 0, trading: 1, waiting: 1 });
  });

  it("counts all monitored sources even when only two manage open exposure", () => {
    expect(
      summarizeCopySourceStatuses(
        Array.from({ length: 10 }, (_, index) => ({
          monitorStatus: "monitored" as const,
          sourceStatus: index < 2 ? "trading" as const : "entries_paused" as const,
        })),
      ),
    ).toEqual({ connecting: 0, monitored: 10, offline: 0, trading: 2, waiting: 0 });
  });
});

describe("resolveCurrentMonitorStatus", () => {
  const realtimeMonitoring = {
    status: "connecting" as const,
    desiredWallets: ["0xconnected", "0xpending"],
    monitoredWallets: ["0xconnected"],
    workerRole: "trading",
    workerInstanceId: "worker-1",
    updatedAt: "2026-01-01T00:00:00Z",
  };

  it("shows monitored only after the wallet subscription is acknowledged", () => {
    expect(
      resolveCurrentMonitorStatus({
        hasRealtimeSlot: true,
        realtimeMonitoring,
        sourceWallet: "0xconnected",
      }),
    ).toBe("monitored");
    expect(
      resolveCurrentMonitorStatus({
        hasRealtimeSlot: true,
        realtimeMonitoring,
        sourceWallet: "0xpending",
      }),
    ).toBe("connecting");
  });

  it("distinguishes an assigned offline source from a source waiting for a slot", () => {
    expect(
      resolveCurrentMonitorStatus({
        hasRealtimeSlot: true,
        realtimeMonitoring,
        sourceWallet: "0xoffline",
      }),
    ).toBe("offline");
    expect(
      resolveCurrentMonitorStatus({
        hasRealtimeSlot: false,
        realtimeMonitoring,
        sourceWallet: "0xwaiting",
      }),
    ).toBe("waiting");
  });
});

describe("liveAllocationSourceVisible", () => {
  it("hides a paper-only retained source from Live Copy Sources", () => {
    expect(liveAllocationSourceVisible([allocation({})], true)).toBe(false);
  });

  it("keeps a current top candidate that is waiting for a live slot visible", () => {
    expect(
      liveAllocationSourceVisible(
        [allocation({ sourceStatus: "waiting_for_slot" })],
        true,
      ),
    ).toBe(true);
  });
});

describe("collectLiveCopySourceWallets", () => {
  it("keeps authoritative realtime sources visible when live entries are paused", () => {
    const monitoredWallets = Array.from(
      { length: 10 },
      (_, index) => `0xsource${index + 1}`,
    );

    expect(
      collectLiveCopySourceWallets({
        allocations: [],
        liveCopyReady: false,
        livePositionSources: monitoredWallets.slice(0, 2),
        realtimeMonitoring: {
          status: "connected",
          desiredWallets: monitoredWallets,
          monitoredWallets,
          workerRole: "trading",
          workerInstanceId: "worker-1",
          updatedAt: "2026-01-01T00:00:00Z",
        },
      }),
    ).toEqual(monitoredWallets);
  });
});
