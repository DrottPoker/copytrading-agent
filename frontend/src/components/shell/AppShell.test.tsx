import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/trading",
}));

const health = {
  activeCopyWallets: 10,
  dependencies: { postgres: { status: "ok" }, redis: { status: "ok" } },
  environment: "development",
  hyperliquidNetwork: "mainnet",
  liveTradingEnabled: false,
  maxRealtimeWallets: 10,
  mode: "paper",
  paperTradingEnabled: true,
  service: "Copy Agent",
  status: "ok",
  version: "0.1.0",
  workerRunInApiProcess: false,
};

const runningScoring = {
  completedAt: null,
  durationMs: null,
  key: "wallet_scoring",
  label: "Wallet scoring",
  lastError: null,
  lastSuccessAt: null,
  payload: { progressPercent: 40 },
  startedAt: "2026-01-01T00:00:00Z",
  status: "running",
  updatedAt: "2026-01-01T00:00:00Z",
};

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("groups navigation, marks the current route, and shows what the system is doing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input).endsWith("/health") ? Response.json(health) : Response.json({ items: [runningScoring] }),
      ),
    );

    render(
      <AppShell>
        <p>Route content</p>
      </AppShell>,
    );

    const navigation = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(navigation).getByText("Monitor")).toBeInTheDocument();
    expect(within(navigation).getByText("Research")).toBeInTheDocument();
    expect(within(navigation).getByRole("link", { name: "Trading" })).toHaveAttribute("aria-current", "page");
    expect(await screen.findByText("PAPER")).toBeInTheDocument();
    expect(screen.getByText("mainnet")).toBeInTheDocument();
    expect(await screen.findByText("Wallet scoring")).toBeInTheDocument();
    expect(screen.getByText("40%")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });

  it("scales the interface with the text size setting and remembers it", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));

    render(
      <AppShell>
        <p>Route content</p>
      </AppShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Larger text size" }));
    expect(document.documentElement.dataset.textSize).toBe("larger");
    expect(window.localStorage.getItem("copyagent.textSize")).toBe("larger");

    fireEvent.click(screen.getByRole("button", { name: "Normal text size" }));
    expect(document.documentElement.dataset.textSize).toBeUndefined();
    expect(window.localStorage.getItem("copyagent.textSize")).toBeNull();
  });

  it("reports an unreachable backend only after repeated failures", async () => {
    vi.useFakeTimers();
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("down", { status: 502 })));

    render(
      <AppShell>
        <p>Route content</p>
      </AppShell>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.queryByText("Backend unreachable, retrying")).not.toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByText("Backend unreachable, retrying")).toBeInTheDocument();
    vi.useRealTimers();
  });
});
