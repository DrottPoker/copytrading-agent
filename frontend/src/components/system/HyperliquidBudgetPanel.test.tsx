import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OpsHyperliquidBudget, OpsHyperliquidProcessUsage } from "@/types/ops";

import { HyperliquidBudgetPanel } from "./HyperliquidBudgetPanel";

const maintenance: OpsHyperliquidProcessUsage = {
  process: "maintenance",
  instanceId: "instance-a",
  updatedAt: "2026-10-11T12:00:00Z",
  budgetMode: "local",
  budgetWeightPerMinute: 550,
  weightLastMinute: 540,
  weightPerMinute15m: 512.4,
  executionWeightPerMinute15m: 0,
  backgroundWeightPerMinute15m: 512.4,
  peakWeightPerMinute60m: 560,
  requests15m: 300,
  executionWaitSeconds15m: 0,
  backgroundWaitSeconds15m: 610,
  rateLimited60m: 0,
  topRequestTypes15m: [
    { type: "userFillsByTime", weight: 5200 },
    { type: "portfolio", weight: 1400 },
  ],
};

const budget: OpsHyperliquidBudget = {
  status: "warning",
  ipWeightLimitPerMinute: 1200,
  sharedBudgetEnabled: false,
  sharedWeightPerMinute: 1100,
  executionReserveWeight: 60,
  processWeightPerMinute: 550,
  totalWeightLastMinute: 580,
  totalWeightPerMinute15m: 560.2,
  rateLimited60m: 2,
  processes: [
    maintenance,
    {
      ...maintenance,
      process: "trading",
      instanceId: "instance-b",
      weightLastMinute: 40,
      executionWaitSeconds15m: 3,
      rateLimited60m: 2,
      topRequestTypes15m: [],
    },
  ],
};

describe("HyperliquidBudgetPanel", () => {
  it("shows each process's weight against the per-IP limit", () => {
    render(<HyperliquidBudgetPanel budget={budget} />);

    expect(screen.getByText(/Hyperliquid allows 1,200 per IP address/)).toBeInTheDocument();
    expect(screen.getByText(/560 per minute over 15 minutes, 580 in the last minute/)).toBeInTheDocument();
    expect(screen.getByText("2 rate limited in the last hour")).toHaveClass("text-negative");
    expect(screen.getByText("Maintenance worker")).toBeInTheDocument();
    expect(screen.getByText("Trading worker")).toBeInTheDocument();
    expect(screen.getByText("10m 10s")).toBeInTheDocument();
    expect(screen.getByText("execution 3s")).toBeInTheDocument();
    expect(screen.getByText("userFillsByTime 5,200, portfolio 1,400")).toBeInTheDocument();
  });

  it("explains the shared budget and flags a process that cannot reach Redis", () => {
    render(
      <HyperliquidBudgetPanel
        budget={{
          ...budget,
          sharedBudgetEnabled: true,
          processes: [
            { ...maintenance, budgetMode: "shared", budgetWeightPerMinute: 1100 },
            { ...maintenance, process: "trading", instanceId: "instance-b", sharedFallbacks60m: 2 },
          ],
        }}
      />,
    );

    expect(
      screen.getByText(/All processes share 1,100 per minute, and background work leaves 60 for copy execution/),
    ).toBeInTheDocument();
    expect(screen.getByText("Shared")).toBeInTheDocument();
    expect(screen.getByText("Own, Redis unreachable")).toHaveClass("text-warning");
    expect(screen.getByText("Redis unreachable 2 times")).toBeInTheDocument();
  });

  it("renders nothing without budget data from an older backend", () => {
    const { container } = render(<HyperliquidBudgetPanel budget={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });
});
