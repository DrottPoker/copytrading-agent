import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OperationStatus } from "@/types/operation";

import { OperationStatusStrip } from "./OperationStatusStrip";

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
    runId: "discovery-run",
    stage: "source_import",
    stageLabel: "Importing candidates",
    stageDetail: "Fetching source 2 of 4: hyperdash.",
    progressPercent: 42,
    inserted: 18,
    skipped: 7,
    poolInserted: 3,
  },
};

describe("OperationStatusStrip", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows compact progress and safely requests cancellation", async () => {
    const canceledRequest: OperationStatus = {
      ...runningDiscovery,
      payload: {
        ...runningDiscovery.payload,
        cancelRequested: true,
        stage: "cancel_requested",
        stageLabel: "Stopping",
        stageDetail: "Finishing the current safe checkpoint before stopping.",
      },
    };
    let cancelRequested = false;
    const fetchMock = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/operations/status")) {
        return {
          ok: true,
          json: async () => ({ items: [cancelRequested ? canceledRequest : runningDiscovery] }),
        };
      }
      if (url.endsWith("/operations/discovery_import/cancel")) {
        cancelRequested = true;
        return {
          ok: true,
          json: async () => canceledRequest,
        };
      }
      throw new Error(`Unexpected URL: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<OperationStatusStrip initialItems={[runningDiscovery]} />);

    expect(
      screen.getByText("Fetching source 2 of 4: hyperdash.", { exact: false }),
    ).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Discovery import progress" })).toHaveAttribute(
      "aria-valuenow",
      "42",
    );
    expect(screen.getByText("42%")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Cancel Discovery import" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/backend/operations/discovery_import/cancel",
        {
          cache: "no-store",
          method: "POST",
        },
      );
    });
    expect(screen.getAllByText("Stopping").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Cancel Discovery import" })).toBeDisabled();
  });
});
