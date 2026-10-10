import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { refreshMock } = vi.hoisted(() => ({ refreshMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: refreshMock }),
}));

import { ScoreWalletsButton } from "./walletActions";

describe("ScoreWalletsButton", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    refreshMock.mockReset();
  });

  it("starts scoring in the background instead of holding the request open", async () => {
    const fetchMock = vi.fn(async () =>
      Response.json({
        completedAt: null,
        durationMs: null,
        key: "wallet_scoring",
        label: "Wallet pool scoring",
        lastError: null,
        lastSuccessAt: "2026-07-26T02:56:00Z",
        payload: { stage: "queued" },
        startedAt: "2026-08-02T00:13:00Z",
        status: "running",
        updatedAt: "2026-08-02T00:13:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<ScoreWalletsButton />);
    fireEvent.click(screen.getByRole("button", { name: "Score wallets" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/backend/scores/recalculate/start",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByRole("button", { name: "Scoring" })).toBeDisabled();
  });

  it("shows the backend error when scoring cannot start", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "Scoring is already running." }, { status: 409 })));

    render(<ScoreWalletsButton />);
    fireEvent.click(screen.getByRole("button", { name: "Score wallets" }));

    expect(await screen.findByText("Scoring is already running.")).toBeInTheDocument();
  });
});
