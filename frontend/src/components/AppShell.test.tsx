import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/trading",
}));

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("groups navigation, marks the current route, and reports the fetched execution mode", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({ mode: "paper" }),
        ok: true,
      }),
    );

    render(
      <AppShell>
        <p>Route content</p>
      </AppShell>,
    );

    expect(screen.getByText("Execution")).toBeInTheDocument();
    expect(screen.getByText("Intelligence")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Trading" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(await screen.findByText("Paper")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("id", "main-content");
  });
});
