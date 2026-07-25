import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TradingCashFlowsResponse } from "@/types/trading";

import { AccountTransactionsPanel } from "./AccountTransactionsPanel";

const cashFlows: TradingCashFlowsResponse = {
  accountKey: "live-main",
  depositsUsd: "250",
  items: [
    {
      accountKey: "live-main",
      amountUsd: "-41",
      exchangeEventId: "withdrawal-1",
      feeUsd: "1",
      flowType: "withdrawal",
      id: "flow-2",
      occurredAt: "2026-07-25T12:00:00Z",
    },
    {
      accountKey: "live-main",
      amountUsd: "250",
      exchangeEventId: "deposit-1",
      feeUsd: "0",
      flowType: "deposit",
      id: "flow-1",
      occurredAt: "2026-07-24T12:00:00Z",
    },
  ],
  netExternalFlowsUsd: "209",
  updatedAt: "2026-07-25T12:00:00Z",
  withdrawalsUsd: "41",
};

describe("AccountTransactionsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows every deposit and withdrawal with complete ledger totals", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => cashFlows,
      ok: true,
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
      />,
    );

    expect(await screen.findByText("Deposit")).toBeInTheDocument();
    expect(screen.getByText("Withdrawal")).toBeInTheDocument();
    expect(screen.getByText("deposit-1")).toBeInTheDocument();
    expect(screen.getByText("withdrawal-1 | fee 1,00 US$")).toBeInTheDocument();
    expect(screen.getByText("2 transactions")).toBeInTheDocument();
    expect(screen.getAllByText("250,00 US$")).toHaveLength(2);
    expect(screen.getAllByText("−41,00 US$")).toHaveLength(2);
    expect(screen.getByText("209,00 US$")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backend/trading/accounts/live-main/cash-flows",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("reconciles the account before refreshing the transaction ledger", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => cashFlows,
      ok: true,
    });
    const onReconcile = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
        onReconcile={onReconcile}
      />,
    );

    const refreshButton = await screen.findByRole("button", {
      name: "Reconcile and refresh transactions",
    });
    await waitFor(() => expect(refreshButton).toBeEnabled());
    fireEvent.click(refreshButton);

    await waitFor(() => expect(onReconcile).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("offers historical reconciliation when the imported ledger is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({
          ...cashFlows,
          depositsUsd: "0",
          items: [],
          netExternalFlowsUsd: "0",
          withdrawalsUsd: "0",
        }),
        ok: true,
      }),
    );
    const onReconcile = vi.fn().mockResolvedValue(undefined);

    render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
        onReconcile={onReconcile}
      />,
    );

    const reconcileButton = await screen.findByRole("button", {
      name: "Reconcile history",
    });
    fireEvent.click(reconcileButton);

    await waitFor(() => expect(onReconcile).toHaveBeenCalledTimes(1));
  });

  it("shows a bounded error state when the ledger cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        json: async () => ({}),
        ok: false,
        status: 503,
      }),
    );

    render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
      />,
    );

    expect(
      await screen.findByText("Transaction history request failed with 503."),
    ).toBeInTheDocument();
    expect(screen.getByText("Ledger unavailable")).toBeInTheDocument();
  });
});
