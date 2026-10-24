import { describe, expect, it } from "vitest";

import { paperPerformance } from "@/test/tradingFixtures";

import { buildEquitySeries, calculatePaperPerformance, summarizeTrades, tradeSummaryFromStats } from "./performance";

describe("trade summary", () => {
  it("matches the server statistics for the same trades", () => {
    const fromValues = summarizeTrades([10, -4, 6, 15, -6]);
    const fromServer = tradeSummaryFromStats({
      bestTradeUsd: "15",
      currentStreakCount: 1,
      currentStreakKind: "loss",
      grossLossUsd: "10",
      grossProfitUsd: "31",
      longestLossStreak: 1,
      longestWinStreak: 2,
      lossCount: 2,
      netPnlUsd: "21",
      tradeCount: 5,
      winCount: 3,
      worstTradeUsd: "-6",
    });

    expect(fromServer).toEqual(fromValues);
  });
});

describe("equity series", () => {
  it("tracks PnL from the starting balance and drawdown from the running peak", () => {
    const series = buildEquitySeries(paperPerformance(), null);

    expect(series.map((point) => point.pnlUsd)).toEqual([0, 50, 0, 80]);
    expect(series.map((point) => point.drawdownUsd)).toEqual([0, 0, 50, 0]);
    expect(series[2].drawdownPct).toBeCloseTo(50 / 1050, 10);
  });

  it("appends the live mark only when it is newer than the last snapshot", () => {
    const newer = buildEquitySeries(paperPerformance(), { at: "2026-01-01T00:16:00Z", equityUsd: 1040 });
    const older = buildEquitySeries(paperPerformance(), { at: "2026-01-01T00:14:00Z", equityUsd: 1040 });

    expect(newer).toHaveLength(5);
    expect(newer[4]).toMatchObject({ drawdownUsd: 40, isLive: true, pnlUsd: 40 });
    expect(older).toHaveLength(4);
  });
});

describe("paper performance analytics", () => {
  it("uses the server statistics and the snapshot drawdown", () => {
    const analytics = calculatePaperPerformance(paperPerformance(), { currentEquityUsd: 1070, totalPnlUsd: 70 });

    expect(analytics.drawdownBasis).toBe("equity");
    expect(analytics.tradeCount).toBe(5);
    expect(analytics.winRate).toBeCloseTo(0.6);
    expect(analytics.profitFactor).toBeCloseTo(3);
    expect(analytics.expectancyUsd).toBeCloseTo(4);
    expect(analytics.maxDrawdownUsd).toBe(50);
    expect(analytics.maxDrawdownPct).toBeCloseTo(0.047619);
    expect(analytics.currentDrawdownUsd).toBe(10);
    expect(analytics.currentDrawdownPct).toBeCloseTo(10 / 1080, 10);
    expect(analytics.recoveryFactor).toBeCloseTo(70 / 50);
  });

  it("reports the live drawdown as the max when it is deeper than every snapshot", () => {
    const analytics = calculatePaperPerformance(paperPerformance(), { currentEquityUsd: 972, totalPnlUsd: -28 });

    expect(analytics.currentDrawdownUsd).toBe(108);
    expect(analytics.maxDrawdownUsd).toBe(108);
    expect(analytics.maxDrawdownPct).toBeCloseTo(0.1, 10);
  });

  it("keeps a profit factor without losses unbounded", () => {
    const performance = paperPerformance();
    const analytics = calculatePaperPerformance(
      { ...performance, tradeStats: { ...performance.tradeStats, grossLossUsd: "0", lossCount: 0 } },
      { currentEquityUsd: 1080, totalPnlUsd: 80 },
    );

    expect(analytics.profitFactor).toBe(Infinity);
    expect(analytics.payoffRatio).toBeNull();
  });
});
