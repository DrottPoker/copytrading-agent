import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OperationStatus } from "@/types/operation";

import { JobsPanel } from "./JobsPanel";

const runningDiscovery: OperationStatus = {
  key: "discovery_import",
  label: "Discovery import",
  status: "running",
  startedAt: "2026-08-02T04:16:00Z",
  completedAt: null,
  updatedAt: "2026-08-02T04:17:00Z",
  lastSuccessAt: "2026-08-02T00:21:00Z",
  durationMs: null,
  lastError: null,
  payload: {
    stage: "source_import",
    stageLabel: "Importing candidates",
    stageDetail: "Fetching source 2 of 4: hyperdash.",
    progressPercent: 42,
  },
};

const canceledScoring: OperationStatus = {
  ...runningDiscovery,
  completedAt: "2026-08-02T04:20:00Z",
  durationMs: 60_000,
  key: "wallet_scoring",
  label: "Wallet scoring",
  payload: { cancelRequested: true, scoredWallets: 12, totalWallets: 80 },
  status: "canceled",
};

describe("JobsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows progress and safely requests cancellation", async () => {
    let cancelRequested = false;
    const stopping: OperationStatus = {
      ...runningDiscovery,
      payload: { ...runningDiscovery.payload, cancelRequested: true, stageLabel: "Stopping" },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/operations/status")) {
        return Response.json({ items: [cancelRequested ? stopping : runningDiscovery, canceledScoring] });
      }
      if (url.endsWith("/operations/discovery_import/cancel")) {
        cancelRequested = true;
        return Response.json(stopping);
      }
      throw new Error(`Unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<JobsPanel initialItems={[runningDiscovery, canceledScoring]} />);

    expect(screen.getByText("Importing candidates: Fetching source 2 of 4: hyperdash.")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Discovery import progress" })).toHaveAttribute("aria-valuenow", "42");

    fireEvent.click(screen.getByRole("button", { name: "Cancel Discovery import" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/backend/operations/discovery_import/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel Discovery import" })).toBeDisabled());
  });

  it("shows a canceled job as canceled, not as stopping", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));

    render(<JobsPanel initialItems={[canceledScoring]} />);

    expect(screen.getByText("Canceled")).toBeInTheDocument();
    expect(screen.queryByText("Stopping")).not.toBeInTheDocument();
    expect(screen.getByText("12 of 80 wallets scored")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Wallet scoring" })).toBeEnabled();
  });
});
