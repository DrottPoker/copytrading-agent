import { describe, expect, it } from "vitest";

import type { PaperCopyAllocation } from "@/types/paper";
import type { LiveCopyDecision, TradingPosition } from "@/types/trading";

import {
  buildLiveCopyDecisionActivities,
  collectLiveCopySourceWallets,
  compareCopySourcesByAllocationUsed,
  compareWalletHistoryByPnl,
  displayLivePositions,
  liveAllocationSourceVisible,
  liveCopyDecisionStatusPills,
  pnlPerMonitoredHour,
  resolveCurrentMonitorStatus,
  resolveCurrentSourceStatus,
  summarizeCopySourceStatuses,
} from "./TradingDashboard";

function copyDecision(overrides: Partial<LiveCopyDecision>): LiveCopyDecision {
  return {
    accountKey: "live-test",
    sourceWallet: "0xsource",
    sourceFillId: "fill-1",
    sequenceIndex: 0,
    coin: "BTC",
    plannedAction: "open",
    side: "long",
    outcome: "pending",
    reason: null,
    attemptCount: 0,
    origin: "realtime",
    sourceTimestampMs: 1767225600000,
    observedAt: "2026-01-01T00:00:01Z",
    firstObservedAt: "2026-01-01T00:00:00Z",
    executionClaimedAt: "2026-01-01T00:00:01Z",
    processingStartedAt: "2026-01-01T00:00:02Z",
    decisionAt: "2026-01-01T00:00:03Z",
    lastAttemptAt: null,
    nextAttemptAt: null,
    tradingOrderId: null,
    orderRecordId: null,
    logicalOrderStatus: null,
    logicalOrderError: null,
    latestDispatchAttemptNumber: null,
    latestDispatchClientOrderId: null,
    latestDispatchStatus: null,
    latestExchangeStatus: null,
    latestExchangeErrorCode: null,
    latestExchangeErrorMessage: null,
    latestExchangeResponse: null,
    submitAttemptCount: 0,
    statusLookupCount: 0,
    lastStatusLookupAt: null,
    lastStatusLookupError: null,
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("copy source performance", () => {
  it("calculates PnL per hour from the displayed PnL and monitored duration", () => {
    expect(pnlPerMonitoredHour(0.79, 150 * 3600)).toBeCloseTo(0.0052666667, 10);
    expect(pnlPerMonitoredHour(0.79, 0)).toBeNull();
  });

  it("sorts Copy Sources by allocation used before PnL and status", () => {
    const sources = [
      {
        sourceWallet: "0xzero",
        sourceStatus: "trading" as const,
        pocketUsedPct: 0.1,
        openMarginUsd: 10,
        poolRank: 1,
        rank: 1,
        realizedPnlUsd: "0",
      },
      {
        sourceWallet: "0xhighest",
        sourceStatus: "waiting_for_trades" as const,
        pocketUsedPct: 0,
        openMarginUsd: 0,
        poolRank: 9,
        rank: 9,
        realizedPnlUsd: "7.17",
      },
      {
        sourceWallet: "0xmiddle",
        sourceStatus: "waiting_for_trades" as const,
        pocketUsedPct: 0.25,
        openMarginUsd: 25,
        poolRank: 10,
        rank: 10,
        realizedPnlUsd: "0.79",
      },
    ];

    expect(sources.sort(compareCopySourcesByAllocationUsed).map((source) => source.sourceWallet)).toEqual([
      "0xmiddle",
      "0xzero",
      "0xhighest",
    ]);
  });

  it("sorts Wallet PnL History by realized PnL descending", () => {
    const wallets = [
      { sourceWallet: "0xlow", poolRank: 1, realizedPnlUsd: "0.79", totalPnlUsd: "5" },
      { sourceWallet: "0xhigh", poolRank: 10, realizedPnlUsd: "7.17", totalPnlUsd: "6" },
    ];

    expect(wallets.sort(compareWalletHistoryByPnl).map((wallet) => wallet.sourceWallet)).toEqual([
      "0xhigh",
      "0xlow",
    ]);
  });
});

describe("live copy decisions", () => {
  it("keeps a terminal no-order decision out of fill and order language", () => {
    const decision = copyDecision({
      outcome: "terminal_skip",
      reason: "entry_blocked",
    });

    expect(liveCopyDecisionStatusPills(decision)).toEqual([
      { label: "no-order blocked", tone: "danger" },
    ]);
    const activity = buildLiveCopyDecisionActivities([decision], new Map())[0];
    expect(activity.pills.map((pill) => pill.label)).not.toContain("order created");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.value).toBe("pre-submit skip");
  });

  it("shows retry scheduling and accepted exchange execution separately", () => {
    const retry = copyDecision({
      outcome: "retryable",
      nextAttemptAt: "2026-01-01T00:01:00Z",
    });
    const created = copyDecision({
      outcome: "order",
      tradingOrderId: "order-1",
      orderRecordId: "order-1",
      logicalOrderStatus: "accepted",
      latestExchangeStatus: "accepted",
    });

    expect(liveCopyDecisionStatusPills(retry)).toEqual([
      { label: "retry scheduled", tone: "warning" },
    ]);
    expect(liveCopyDecisionStatusPills(created)).toEqual([
      { label: "order recorded", tone: "neutral" },
      { label: "accepted", tone: "positive" },
    ]);
  });

  it("shows a definitive exchange reject as a red retryable execution result", () => {
    const decision = copyDecision({
      outcome: "retryable",
      orderRecordId: "order-1",
      logicalOrderStatus: "rejected",
      logicalOrderError: "exchange_ioc_no_match: Order could not immediately match.",
      latestDispatchAttemptNumber: 1,
      latestDispatchClientOrderId: "0x1234567890abcdef",
      latestDispatchStatus: "completed",
      latestExchangeStatus: "rejected",
      latestExchangeErrorCode: "exchange_ioc_no_match",
      latestExchangeErrorMessage: "Order could not immediately match.",
      submitAttemptCount: 1,
      nextAttemptAt: "2026-01-01T00:01:00Z",
    });

    const activity = buildLiveCopyDecisionActivities([decision], new Map())[0];
    expect(activity.pills).toContainEqual({ label: "rejected", tone: "danger" });
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.value).toBe(
      "exchange rejected, retry scheduled",
    );
    expect(activity.stats.find((stat) => stat.label === "Exchange attempts")?.value).toBe("1");
    expect(activity.stats.find((stat) => stat.label === "Exchange attempts")?.detail).toContain(
      "CLOID",
    );
  });

  it("shows stale no-order timing and recovery origin diagnostics", () => {
    const decision = copyDecision({
      outcome: "terminal_skip",
      reason: "live_source_fill_too_old",
      origin: "snapshot_recovery",
      sourceTimestampMs: Date.parse("2026-01-01T00:00:00Z"),
      observedAt: null,
      firstObservedAt: "2026-01-01T00:00:03Z",
      executionClaimedAt: "2026-01-01T00:00:05Z",
      processingStartedAt: "2026-01-01T00:00:08Z",
      decisionAt: "2026-01-01T00:01:05Z",
      updatedAt: "2026-01-01T00:01:05Z",
    });

    expect(liveCopyDecisionStatusPills(decision)).toEqual([
      { label: "stale no-order", tone: "danger" },
    ]);
    const activity = buildLiveCopyDecisionActivities([decision], new Map())[0];
    expect(activity.pills.map((pill) => pill.label)).toContain("snapshot recovery");
    expect(activity.stats.find((stat) => stat.label === "First observed")).toMatchObject({
      value: "01/01 01:00",
      detail: expect.stringContaining("source 01/01 01:00"),
    });
    expect(activity.stats.find((stat) => stat.label === "First observed")?.detail).toContain("ingest 3 s");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.detail).toContain("age 1m 5s");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.detail).toContain("queue 2 s");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.detail).toContain("prep 3 s");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.detail).toContain("work 57 s");
  });

  it("keeps no-order decisions in Copy Decisions instead of execution activity", () => {
    const decision = copyDecision({
      outcome: "terminal_skip",
      reason: "live_source_fill_too_old",
    });

    expect(buildLiveCopyDecisionActivities([decision], new Map())).toHaveLength(1);
    expect(buildLiveCopyDecisionActivities([decision], new Map())[0].id).toContain("copy-decision:");
  });
});

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
