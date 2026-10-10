import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PerformancePoint } from "@/lib/trading/performance";

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
    render(<PerformanceChart points={points([10, -4, 6])} />);

    expect(screen.getByText("Cumulative net PnL")).toBeInTheDocument();
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

  it("shows an explicit empty state before the first closed trade", () => {
    render(<PerformanceChart points={[]} />);

    expect(screen.getByText("The curve appears after the account closes its first trade.")).toBeInTheDocument();
  });
});
