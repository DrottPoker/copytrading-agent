import { render, screen } from "@testing-library/react";
import { Activity } from "lucide-react";
import { describe, expect, it } from "vitest";

import { MetricCard } from "./MetricCard";

describe("MetricCard", () => {
  it("renders its label, value, and detail", () => {
    render(
      <MetricCard
        icon={Activity}
        label="Worker status"
        value="Healthy"
        detail="All critical loops are reporting."
        tone="positive"
      />,
    );

    expect(screen.getByText("Worker status")).toBeInTheDocument();
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("All critical loops are reporting.")).toBeInTheDocument();
  });
});
