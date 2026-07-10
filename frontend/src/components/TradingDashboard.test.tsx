import { describe, expect, it } from "vitest";

import type { TradingPosition } from "@/types/trading";

import {
  displayLivePositions,
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
  it("never reports waiting for slot when the source owns a realtime slot", () => {
    expect(
      resolveCurrentSourceStatus({
        canOpenNewPositions: false,
        hasRealtimeSlot: true,
        openPositionCount: 0,
      }),
    ).toBe("retained");
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
});

describe("summarizeCopySourceStatuses", () => {
  it("counts the same current source states rendered by Copy Sources", () => {
    expect(
      summarizeCopySourceStatuses([
        { monitorStatus: "monitored", sourceStatus: "trading" },
        { monitorStatus: "monitored", sourceStatus: "waiting_for_trades" },
        { monitorStatus: "waiting", sourceStatus: "waiting_for_slot" },
      ]),
    ).toEqual({ monitored: 2, trading: 1, waiting: 1 });
  });
});
