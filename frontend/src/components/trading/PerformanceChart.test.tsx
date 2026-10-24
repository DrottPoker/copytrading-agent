import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildEquitySeries, type PerformancePoint } from "@/lib/trading/performance";
import { paperPerformance } from "@/test/tradingFixtures";

import { PerformanceChart } from "./PerformanceChart";

function points(values: number[]): PerformancePoint[] {
  let cumulative = 0;
  return values.map((tradeValue, index) => {
    cumulative += tradeValue;
    return {
      closedAt: `2026-01-0${index + 1}T00:00:00Z`,
      coin: index % 2 === 0 ? "BTC" : "ETH",
      id: `trade-${index}`,
      timestamp: Date.UTC(2026, 0, index + 1),
      tradeValue,
      value: cumulative,
    };
  });
}

describe("PerformanceChart", () => {
  it("switches between cumulative and per trade views and supports keyboard inspection", () => {
    render(<PerformanceChart equity={null} trades={points([10, -4, 6])} />);

    expect(screen.getByText("Cumulative net PnL of closed trades")).toBeInTheDocument();
    expect(screen.getByText("+$12.00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Per trade" }));
    expect(screen.getByText("Net PnL of the trade")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "25" }));
    expect(screen.getByRole("button", { name: "25" })).toHaveAttribute("aria-pressed", "true");

    const chart = screen.getByRole("img", { name: /Chart of 3 closed trades/ });
    fireEvent.keyDown(chart, { key: "ArrowLeft" });
    expect(screen.getByText(/ETH, closed Jan 2, 00:00/)).toBeInTheDocument();
    expect(screen.getByText("−$4.00")).toBeInTheDocument();
  });

  it("shows marked-to-market PnL and drawdown when equity is available", () => {
    const equity = buildEquitySeries(paperPerformance(), { at: "2026-01-01T00:20:00Z", equityUsd: 1040 });
    render(<PerformanceChart equity={equity} trades={points([10])} />);

    expect(screen.getByText("Equity PnL, marked to market")).toBeInTheDocument();
    expect(screen.getByText("+$40.00")).toBeInTheDocument();
    expect(screen.getByText(/live mark/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cumulative" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Drawdown" }));
    expect(screen.getByText("Drawdown from the equity peak")).toBeInTheDocument();
    expect(screen.getByText("−$40.00")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "7D" }));
    expect(screen.getByRole("img", { name: /Chart of 5 equity samples/ })).toBeInTheDocument();
  });

  it("shows explicit empty states before the first trade and the first snapshot", () => {
    const { rerender } = render(<PerformanceChart equity={null} trades={[]} />);
    expect(screen.getByText("The curve appears after the account closes its first trade.")).toBeInTheDocument();

    rerender(<PerformanceChart equity={[]} trades={[]} />);
    expect(screen.getByText("The equity curve appears after the first equity snapshot of the account.")).toBeInTheDocument();
  });
});
