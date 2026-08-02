import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { refreshMock } = vi.hoisted(() => ({
  refreshMock: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: refreshMock }),
}));

import { ScoreWalletsButton } from "./ScoreWalletsButton";

describe("ScoreWalletsButton", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    refreshMock.mockReset();
  });

  it("starts scoring in the background instead of holding the request open", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => ({
        key: "wallet_scoring",
        label: "Wallet pool scoring",
        status: "running",
        startedAt: "2026-08-02T00:13:00Z",
        completedAt: null,
        updatedAt: "2026-08-02T00:13:00Z",
        lastSuccessAt: "2026-07-26T02:56:00Z",
        durationMs: null,
        lastError: null,
        payload: { stage: "queued" },
      }),
      ok: true,
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<ScoreWalletsButton />);
    fireEvent.click(screen.getByRole("button", { name: "Score wallets" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/backend/scores/recalculate/start",
        { method: "POST" },
      );
    });
    expect(screen.getByRole("button", { name: "Scoring" })).toBeDisabled();
  });
});
