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

  it("renders nothing without budget data from an older backend", () => {
    const { container } = render(<HyperliquidBudgetPanel budget={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });
});
