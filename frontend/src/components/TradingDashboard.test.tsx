import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PaperCopyAllocation } from "@/types/paper";
import type { LiveCopyDecision, TradingPosition } from "@/types/trading";

import {
  buildLiveCopyDecisionActivities,
  collectLiveCopySourceWallets,
  compareDashboardPositionsOldestFirst,
  compareCopySourcesByAllocationUsed,
  compareWalletHistoryByPnl,
  displayLivePositions,
  liveAllocationSourceVisible,
  liveCopyDecisionStatusPills,
  liveOrderResultLabel,
  pnlPerMonitoredHour,
  PositionOwnerWallet,
  responseError,
  resolveCurrentMonitorStatus,
  resolveCurrentSourceStatus,
  summarizeCopySourceStatuses,
} from "./TradingDashboard";

describe("live order result messages", () => {
  it("clarifies legacy expiry rows that already reached the exchange", () => {
    expect(
      liveOrderResultLabel(
        "Live entry intent expired before exchange submission.",
        "2026-07-25T00:04:00Z",
      ),
    ).toBe(
      "Live entry retry window expired after an earlier exchange submission attempt.",
    );
  });

  it("keeps the pre-submission message when no exchange attempt occurred", () => {
    expect(
      liveOrderResultLabel(
        "Live entry intent expired before exchange submission.",
        null,
      ),
    ).toBe("Live entry intent expired before exchange submission.");
  });
});

describe("open position owner wallet", () => {
  it("links the source name without printing the full wallet address", () => {
    const sourceWallet = "0x1234567890abcdef1234567890abcdef12345678";

    render(
      <PositionOwnerWallet
        sourceLabel="Profitable vault"
        sourceWallet={sourceWallet}
      />,
    );

    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.queryByText(sourceWallet)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open owner wallet Profitable vault" }))
      .toHaveAttribute("href", `/wallets/${sourceWallet}`);
    expect(screen.getByRole("link", { name: "Open owner wallet Profitable vault" }))
      .toHaveAttribute("title", sourceWallet);
  });

  it("does not create a wallet link for an unattributed exchange position", () => {
    render(
      <PositionOwnerWallet
        sourceLabel="Exchange position"
        sourceWallet="__exchange__"
      />,
    );

    expect(screen.getByText("No attributed source wallet")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

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

  it("sorts Wallet PnL History by total PnL descending", () => {
    const wallets = [
      { sourceWallet: "0xlow", poolRank: 1, realizedPnlUsd: "7.17", totalPnlUsd: "5" },
      { sourceWallet: "0xhigh", poolRank: 10, realizedPnlUsd: "0.79", totalPnlUsd: "6" },
    ];

    expect(wallets.sort(compareWalletHistoryByPnl).map((wallet) => wallet.sourceWallet)).toEqual([
      "0xhigh",
      "0xlow",
    ]);
  });

  it("keeps open positions oldest first with a deterministic tie break", () => {
    const positions = [
      {
        id: "newest",
        accountKey: "live-test",
        sourceWallet: "0xsource",
        coin: "ETH",
        side: "long" as const,
        openedAt: "2026-01-03T00:00:00Z",
      },
      {
        id: "old-b",
        accountKey: "live-test",
        sourceWallet: "0xsource",
        coin: "ETH",
        side: "long" as const,
        openedAt: "2026-01-01T00:00:00Z",
      },
      {
        id: "old-a",
        accountKey: "live-test",
        sourceWallet: "0xsource",
        coin: "BTC",
        side: "long" as const,
        openedAt: "2026-01-01T00:00:00Z",
      },
    ];

    expect(
      positions.sort(compareDashboardPositionsOldestFirst).map((position) => position.id),
    ).toEqual(["old-a", "old-b", "newest"]);
  });
});

describe("dashboard mutation errors", () => {
  it("shows the proxy request id for a non-JSON server failure", async () => {
    const response = new Response("Internal Server Error", {
      status: 500,
      headers: { "X-Request-ID": "close-request-123" },
    });

    await expect(responseError(response, "Manual close failed")).resolves.toBe(
      "Manual close failed with HTTP 500. Request ID: close-request-123.",
    );
  });
});

describe("live copy decisions", () => {
  it("explains known ownership conflicts separately from ambiguous history", () => {
    const ownedByOtherSource = buildLiveCopyDecisionActivities(
      [
        copyDecision({
          outcome: "baseline_ignored",
          plannedAction: "close",
          reason: "live_exit_market_owned_by_other_source",
        }),
      ],
      new Map(),
    )[0];
    const ambiguousHistory = buildLiveCopyDecisionActivities(
      [copyDecision({ outcome: "retryable", reason: "live_source_attribution_ambiguous" })],
      new Map(),
    )[0];

    expect(ownedByOtherSource.stats.find((stat) => stat.label === "Reason")?.value).toBe(
      "exit ignored: position owned by another source wallet",
    );
    expect(ambiguousHistory.stats.find((stat) => stat.label === "Reason")?.value).toBe(
      "ownership could not be proven from live fill history",
    );
  });

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
    fundingUsd: "0",
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
  it("keeps exchange metrics while attaching the matching source wallet", () => {
    const exchangeBtc = position({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const sourceBtc = position({ id: "source-btc" });
    const sourceEth = position({ id: "source-eth", coin: "ETH" });

    expect(displayLivePositions([exchangeBtc, sourceBtc, sourceEth])).toEqual([
      { ...exchangeBtc, sourceWallet: sourceBtc.sourceWallet },
      sourceEth,
    ]);
  });

  it("does not guess an owner when source attribution is ambiguous", () => {
    const exchangeBtc = position({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const firstSource = position({ id: "source-a", sourceWallet: "0xsource-a" });
    const secondSource = position({ id: "source-b", sourceWallet: "0xsource-b" });

    expect(displayLivePositions([exchangeBtc, firstSource, secondSource])).toEqual([
      exchangeBtc,
    ]);
  });

  it("does not attach a source wallet from the opposite side", () => {
    const exchangeBtc = position({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const staleSource = position({
      id: "source-short",
      side: "short",
      sourceWallet: "0xsource-short",
    });

    expect(displayLivePositions([exchangeBtc, staleSource])).toEqual([exchangeBtc]);
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

  it("reports an entry-ineligible monitored source with exposure as reduce only", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: false,
        hasRealtimeSlot: true,
        openPositionCount: 1,
      }),
    ).toBe("retained");
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
