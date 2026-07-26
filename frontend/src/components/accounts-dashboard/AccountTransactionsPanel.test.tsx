import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TradingCashFlowsResponse } from "@/types/trading";

import { AccountTransactionsPanel } from "./AccountTransactionsPanel";

const cashFlows: TradingCashFlowsResponse = {
  accountKey: "live-main",
  depositsUsd: "1249.20",
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
    {
      accountKey: "live-main",
      amountUsd: "999.20",
      exchangeEventId: "send-in-1",
      feeUsd: "0",
      flowType: "send_in",
      id: "flow-3",
      occurredAt: "2026-07-23T12:00:00Z",
    },
    {
      accountKey: "live-main",
      amountUsd: "-99.20",
      exchangeEventId: "send-out-1",
      feeUsd: "0.20",
      flowType: "send_out",
      id: "flow-4",
      occurredAt: "2026-07-22T12:00:00Z",
    },
  ],
  netExternalFlowsUsd: "1109",
  updatedAt: "2026-07-25T12:00:00Z",
  withdrawalsUsd: "140.20",
};

describe("AccountTransactionsPanel", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows deposits, withdrawals, and external transfers with complete totals", async () => {
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
    expect(screen.getByText("Transfer in")).toBeInTheDocument();
    expect(screen.getByText("Transfer out")).toBeInTheDocument();
    expect(screen.getByText("deposit-1")).toBeInTheDocument();
    expect(screen.getByText("withdrawal-1 | fee 1,00 US$")).toBeInTheDocument();
    expect(screen.getByText("send-in-1")).toBeInTheDocument();
    expect(screen.getByText("send-out-1 | fee 0,20 US$")).toBeInTheDocument();
    expect(screen.getByText("4 transactions")).toBeInTheDocument();
    expect(screen.getByText("1 249,20 US$")).toBeInTheDocument();
    expect(screen.getByText("−140,20 US$")).toBeInTheDocument();
    expect(screen.getByText("1 109,00 US$")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/backend/trading/accounts/live-main/cash-flows",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("reloads automatically when account reconciliation advances", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      json: async () => cashFlows,
      ok: true,
    });
    vi.stubGlobal("fetch", fetchMock);

    const { rerender } = render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
      />,
    );

    expect(await screen.findByText("4 transactions")).toBeInTheDocument();
    rerender(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:04Z"
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("keeps the empty state passive while automatic reconciliation owns imports", async () => {
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

    render(
      <AccountTransactionsPanel
        accountKey="live-main"
        cashFlowsVersion="2026-07-25T12:00:00Z"
      />,
    );

    expect(
      await screen.findByText(
        "No external cash flows have been recorded by automatic reconciliation.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
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
