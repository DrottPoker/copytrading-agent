import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AccountPerformanceChart,
  calculateAccountPerformance,
  type AccountPerformancePoint,
} from "./AccountPerformanceChart";
import { buildAccountPerformanceTimeline } from "./accounts-dashboard/accountViewModel";

function performancePoints(values: number[]): AccountPerformancePoint[] {
  let cumulativeValue = 0;
  return values.map((tradeValue, index) => {
    cumulativeValue += tradeValue;
    return {
      coin: index % 2 === 0 ? "BTC" : "ETH",
      id: `trade-${index}`,
      label: `2026-01-${String(index + 1).padStart(2, "0")}`,
      timestamp: Date.UTC(2026, 0, index + 1),
      tradeValue,
      value: cumulativeValue,
    };
  });
}

describe("account performance analytics", () => {
  it("orders closed trades chronologically and builds cumulative PnL", () => {
    const timeline = buildAccountPerformanceTimeline([
      {
        badges: [],
        closedAt: "2026-01-03T00:00:00Z",
        coin: "ETH",
        detail: "second",
        exitDetail: "",
        exitPrice: "110",
        id: "trade-2",
        netPnlUsd: "-4",
        sourceHref: null,
        sourceLabel: "Source",
      },
      {
        badges: [],
        closedAt: "2026-01-01T00:00:00Z",
        coin: "BTC",
        detail: "first",
        exitDetail: "",
        exitPrice: "100",
        id: "trade-1",
        netPnlUsd: "10",
        sourceHref: null,
        sourceLabel: "Source",
      },
    ]);

    expect(timeline.map((point) => point.id)).toEqual(["trade-1", "trade-2"]);
    expect(timeline.map((point) => point.tradeValue)).toEqual([10, -4]);
    expect(timeline.map((point) => point.value)).toEqual([10, 6]);
  });

  it("calculates professional trade statistics from the loaded closed trades", () => {
    const analytics = calculateAccountPerformance(
      performancePoints([10, -4, 6, -12, 8]),
    );

    expect(analytics.tradeCount).toBe(5);
    expect(analytics.winRate).toBeCloseTo(0.6);
    expect(analytics.grossProfitUsd).toBe(24);
    expect(analytics.grossLossUsd).toBe(16);
    expect(analytics.profitFactor).toBeCloseTo(1.5);
    expect(analytics.maxDrawdownUsd).toBe(12);
    expect(analytics.averageWinUsd).toBe(8);
    expect(analytics.averageLossUsd).toBe(-8);
    expect(analytics.payoffRatio).toBe(1);
    expect(analytics.expectancyUsd).toBeCloseTo(1.6);
    expect(analytics.bestTradeUsd).toBe(10);
    expect(analytics.worstTradeUsd).toBe(-12);
    expect(analytics.currentStreak).toBe("1 W");
  });
});

describe("account performance chart", () => {
  it("switches between cumulative and per-trade views and supports keyboard inspection", () => {
    render(<AccountPerformanceChart points={performancePoints([10, -4, 6])} />);

    expect(screen.getByText("Cumulative net PnL")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Per trade" }));
    expect(screen.getByText("Net PnL per trade")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "25" }));
    expect(screen.getByRole("button", { name: "25" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    const chart = screen.getByRole("img", {
      name: "Interactive chart with 3 closed trades",
    });
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(screen.getByText("ETH | 2026-01-02")).toBeInTheDocument();
  });

  it("shows an explicit empty state before the first closed trade", () => {
    render(<AccountPerformanceChart points={[]} />);

    expect(
      screen.getByText(
        "The performance curve appears after this account has closed trades.",
      ),
    ).toBeInTheDocument();
  });
});
