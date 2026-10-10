import { describe, expect, it } from "vitest";

import { copyDecision, liveAccount, livePosition } from "@/test/tradingFixtures";
import type { TradingFill } from "@/types/trading";

import {
  buildLiveAccountNotice,
  buildLiveCopyDecisionActivities,
  buildLiveFillExecution,
  displayLivePositions,
  liveCopyDecisionStatusPills,
  liveOrderResultLabel,
} from "./live";

describe("live liquidation activity", () => {
  it("shows a proven liquidation and its attributed source wallet", () => {
    const sourceWallet = "0x1234567890abcdef1234567890abcdef12345678";
    const fill: TradingFill = {
      id: "fill-1",
      orderId: null,
      accountKey: "live-test",
      accountType: "live",
      sourceWallet,
      sourceFillId: "exchange-fill-1",
      sequenceIndex: null,
      exchangeFillId: "exchange-fill-1",
      coin: "CASHCAT",
      action: "close",
      side: "short",
      price: "0.1553",
      size: "267",
      notionalUsd: "41.47",
      feeUsd: "0.02",
      realizedPnlUsd: "-12.20",
      isLiquidation: true,
      filledAt: "2026-08-06T00:56:00Z",
      createdAt: "2026-08-06T00:56:01Z",
    };

    const row = buildLiveFillExecution(fill, new Map([[sourceWallet.toLowerCase(), "Hyperdash profitable #81"]]));

    expect(row.sourceLabel).toBe("Hyperdash profitable #81");
    expect(row.sourceHref).toBe(`/wallets/${sourceWallet}`);
    expect(row.result).toEqual({ label: "Liquidated", tone: "negative" });
    expect(row.detail).toBe("Forced exchange liquidation");
    expect(row.realizedPnlUsd).toBeCloseTo(-12.2);
  });

  it("labels an unattributed exchange liquidation without a wallet link", () => {
    const row = buildLiveFillExecution(
      {
        id: "fill-2",
        orderId: null,
        accountKey: "live-test",
        accountType: "live",
        sourceWallet: "__exchange__",
        sourceFillId: null,
        sequenceIndex: null,
        exchangeFillId: "x",
        coin: "BTC",
        action: "close",
        side: "long",
        price: "1",
        size: "1",
        notionalUsd: "1",
        feeUsd: "0",
        realizedPnlUsd: "0",
        isLiquidation: true,
        filledAt: "2026-08-06T00:56:00Z",
        createdAt: "2026-08-06T00:56:00Z",
      },
      new Map(),
    );

    expect(row.sourceLabel).toBe("Unattributed liquidation");
    expect(row.sourceHref).toBeNull();
  });
});

describe("live order result messages", () => {
  it("clarifies legacy expiry rows that already reached the exchange", () => {
    expect(
      liveOrderResultLabel("Live entry intent expired before exchange submission.", "2026-07-25T00:04:00Z"),
    ).toBe("Live entry retry window expired after an earlier exchange submission attempt.");
  });

  it("keeps the pre-submission message when no exchange attempt occurred", () => {
    expect(liveOrderResultLabel("Live entry intent expired before exchange submission.", null)).toBe(
      "Live entry intent expired before exchange submission.",
    );
  });
});

describe("live copy decisions", () => {
  it("explains known ownership conflicts separately from ambiguous history", () => {
    const [ownedByOtherSource] = buildLiveCopyDecisionActivities(
      [
        copyDecision({
          outcome: "baseline_ignored",
          plannedAction: "close",
          reason: "live_exit_market_owned_by_other_source",
        }),
      ],
      new Map(),
    );
    const [ambiguousHistory] = buildLiveCopyDecisionActivities(
      [copyDecision({ outcome: "retryable", reason: "live_source_attribution_ambiguous" })],
      new Map(),
    );

    expect(ownedByOtherSource.stats.find((stat) => stat.label === "Reason")?.value).toBe(
      "exit ignored: position owned by another source wallet",
    );
    expect(ambiguousHistory.stats.find((stat) => stat.label === "Reason")?.value).toBe(
      "ownership could not be proven from live fill history",
    );
  });

  it("keeps a terminal no-order decision out of fill and order language", () => {
    const decision = copyDecision({ outcome: "terminal_skip", reason: "entry_blocked" });

    expect(liveCopyDecisionStatusPills(decision)).toEqual([{ label: "no-order blocked", tone: "negative" }]);
    const [activity] = buildLiveCopyDecisionActivities([decision], new Map());
    expect(activity.pills.map((pill) => pill.label)).not.toContain("order created");
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.value).toBe("pre-submit skip");
  });

  it("shows retry scheduling and accepted exchange execution separately", () => {
    const retry = copyDecision({ outcome: "retryable", nextAttemptAt: "2026-01-01T00:01:00Z" });
    const created = copyDecision({
      outcome: "order",
      tradingOrderId: "order-1",
      orderRecordId: "order-1",
      logicalOrderStatus: "accepted",
      latestExchangeStatus: "accepted",
    });

    expect(liveCopyDecisionStatusPills(retry)).toEqual([{ label: "retry scheduled", tone: "warning" }]);
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

    const [activity] = buildLiveCopyDecisionActivities([decision], new Map());
    expect(activity.pills).toContainEqual({ label: "rejected", tone: "negative" });
    expect(activity.stats.find((stat) => stat.label === "Pipeline")?.value).toBe("exchange rejected, retry scheduled");
    expect(activity.stats.find((stat) => stat.label === "Exchange attempts")?.value).toBe("1");
    expect(activity.stats.find((stat) => stat.label === "Exchange attempts")?.detail).toContain("CLOID");
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

    expect(liveCopyDecisionStatusPills(decision)).toEqual([{ label: "stale no-order", tone: "negative" }]);
    const [activity] = buildLiveCopyDecisionActivities([decision], new Map());
    const observed = activity.stats.find((stat) => stat.label === "First observed");
    const pipeline = activity.stats.find((stat) => stat.label === "Pipeline");
    expect(activity.pills.map((pill) => pill.label)).toContain("snapshot recovery");
    expect(observed?.value).toBe("Jan 1, 00:00");
    expect(observed?.detail).toContain("source Jan 1, 00:00");
    expect(observed?.detail).toContain("ingest 3 s");
    expect(pipeline?.detail).toContain("age 1m 5s");
    expect(pipeline?.detail).toContain("queue 2 s");
    expect(pipeline?.detail).toContain("prep 3 s");
    expect(pipeline?.detail).toContain("work 57 s");
  });

  it("keeps no-order decisions as copy decisions", () => {
    const decisions = buildLiveCopyDecisionActivities(
      [copyDecision({ outcome: "terminal_skip", reason: "live_source_fill_too_old" })],
      new Map(),
    );

    expect(decisions).toHaveLength(1);
    expect(decisions[0].id).toContain("copy-decision:");
  });
});

describe("displayLivePositions", () => {
  it("keeps exchange metrics while attaching the matching source wallet", () => {
    const exchangeBtc = livePosition({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const sourceBtc = livePosition({ id: "source-btc" });
    const sourceEth = livePosition({ id: "source-eth", coin: "ETH" });

    expect(displayLivePositions([exchangeBtc, sourceBtc, sourceEth])).toEqual([
      { ...exchangeBtc, sourceWallet: sourceBtc.sourceWallet },
      sourceEth,
    ]);
  });

  it("does not guess an owner when source attribution is ambiguous", () => {
    const exchangeBtc = livePosition({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const firstSource = livePosition({ id: "source-a", sourceWallet: "0xsource-a" });
    const secondSource = livePosition({ id: "source-b", sourceWallet: "0xsource-b" });

    expect(displayLivePositions([exchangeBtc, firstSource, secondSource])).toEqual([exchangeBtc]);
  });

  it("does not attach a source wallet from the opposite side", () => {
    const exchangeBtc = livePosition({ id: "exchange-btc", sourceWallet: "__exchange__" });
    const staleSource = livePosition({ id: "source-short", side: "short", sourceWallet: "0xsource-short" });

    expect(displayLivePositions([exchangeBtc, staleSource])).toEqual([exchangeBtc]);
  });
});

describe("live account notices", () => {
  it("pauses entries when the last complete snapshot is older than the limit", () => {
    const notice = buildLiveAccountNotice(
      liveAccount({ lastReconciledAt: "2026-01-02T00:00:00Z" }),
      60,
      "2026-01-02T00:05:00Z",
    );

    expect(notice?.title).toBe("New entries paused: the exchange snapshot is stale");
    expect(notice?.tone).toBe("warning");
  });

  it("reports a failed reconciliation as an error", () => {
    const notice = buildLiveAccountNotice(
      liveAccount({ reconciliationStatus: "failed", reconciliationErrors: { positions: "timeout" } }),
      60,
      "2026-01-02T00:00:10Z",
    );

    expect(notice?.tone).toBe("negative");
    expect(notice?.detail).toContain("positions: timeout");
  });

  it("stays quiet for a healthy enabled account", () => {
    expect(buildLiveAccountNotice(liveAccount(), 120, "2026-01-02T00:00:30Z")).toBeNull();
  });
});
