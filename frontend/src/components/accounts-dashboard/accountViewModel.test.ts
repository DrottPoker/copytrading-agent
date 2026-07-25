import { describe, expect, it } from "vitest";

import { formatCurrency, formatPercent } from "@/lib/format";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type {
  TradingAccount,
  TradingAccountsResponse,
  TradingPosition,
} from "@/types/trading";

import { buildSelectedAccountView } from "./accountViewModel";

describe("live account metric tiles", () => {
  it("matches Trading PnL semantics and prioritizes open margin", () => {
    const account = liveAccount();
    const tradingAccounts = {
      accounts: [account],
      closedTrades: [],
      liveTradingEnabled: true,
      positions: [livePosition()],
      recentFills: [],
      recentLiveCopyDecisions: [],
      recentOrders: [],
      riskLimits: {
        entryIntentTtlSeconds: 30,
        maxOrdersPerMinute: 20,
        maxWeeklyLossPct: "0.1",
        reconciliationMaxSnapshotAgeSeconds: 30,
        reduceOnlyWhenStopped: true,
      },
      sourceMetadata: [],
      updatedAt: "2026-07-26T12:00:00Z",
    } satisfies TradingAccountsResponse;
    const paperSummary = {
      accounts: [],
      allocations: [],
      closedTrades: [],
      positions: [],
      recentFills: [],
      updatedAt: "2026-07-26T12:00:00Z",
      walletPerformance: [],
    } as unknown as PaperTradingSummaryResponse;

    const view = buildSelectedAccountView(
      paperSummary,
      tradingAccounts,
      {
        accountType: "live",
        key: account.key,
        label: account.label,
        live: account,
      },
    );

    expect(view).not.toBeNull();
    expect(view?.metrics.realizedPnlUsd).toBeCloseTo(65.34);
    expect(metric(view, "Total PnL")).toMatchObject({
      detail: "realized + unrealized",
      value: formatCurrency(65.97),
    });
    expect(metric(view, "Realized")).toMatchObject({
      detail: `${formatCurrency(1.64)} fees | ${formatCurrency(-3.96)} funding`,
      value: formatCurrency(65.34),
    });
    expect(view?.metricTiles.map((tile) => tile.label).slice(5, 7)).toEqual([
      "Open margin",
      "Open notional",
    ]);
    expect(metric(view, "Open margin")).toMatchObject({
      detail: `${formatPercent(0.25)} of equity`,
      value: formatCurrency(250),
    });
    expect(metric(view, "Open notional")).toMatchObject({
      detail: "2,00x average leverage",
      value: formatCurrency(500),
    });
    expect(
      view?.detailSections[0].rows.find((row) => row.label === "Cash-flow-adjusted PnL"),
    ).toMatchObject({ value: formatCurrency(65.18) });
  });
});

function metric(
  view: ReturnType<typeof buildSelectedAccountView>,
  label: string,
) {
  const tile = view?.metricTiles.find((item) => item.label === label);
  expect(tile, `${label} metric is missing`).toBeDefined();
  return tile;
}

function liveAccount(): TradingAccount {
  return {
    accountType: "live",
    archivedAt: null,
    capitalBalances: [],
    capitalMode: "unified",
    cashBalanceUsd: "1000",
    createdAt: "2026-06-24T12:00:00Z",
    equityUsd: "1000",
    feeUsd: "1.64",
    fundingUsd: "-3.96",
    incompleteReconciliationComponents: [],
    key: "live-main",
    label: "Main live account",
    lastReconciledAt: "2026-07-26T12:00:00Z",
    lifecycleVersion: 1,
    netExternalFlowsUsd: "999.20",
    network: "mainnet",
    performanceTrackingStartedAt: "2026-06-24T12:00:00Z",
    perpEquityUsd: "1000",
    realizedPnlUsd: "70.94",
    reconciliationAttemptedAt: "2026-07-26T12:00:00Z",
    reconciliationErrors: {},
    reconciliationStatus: "complete",
    spotUsdcAvailableUsd: "0",
    spotUsdcBalanceUsd: "0",
    startingBalanceUsd: null,
    status: "enabled",
    statusChangedAt: "2026-06-24T12:00:00Z",
    statusReason: null,
    timeWeightedReturnPct: "0.08",
    tradableEquityUsd: "1000",
    tradingPnlUsd: "65.18",
    updatedAt: "2026-07-26T12:00:00Z",
    userAbstraction: "unified",
    vaultAddress: null,
    walletAddress: "0x1111111111111111111111111111111111111111",
  };
}

function livePosition(): TradingPosition {
  return {
    accountKey: "live-main",
    accountType: "live",
    addFillCount: 1,
    closeFillCount: 0,
    coin: "BTC",
    createdAt: "2026-07-26T10:00:00Z",
    currentNotionalUsd: "500",
    entryExecutionDelayMs: null,
    entryPrice: "100000",
    feeUsd: "0",
    fundingUsd: "0",
    id: "position-1",
    lastReconciledAt: "2026-07-26T12:00:00Z",
    leverage: "2",
    marginMode: "cross",
    marginUsd: "250",
    markPrice: "100126",
    notionalUsd: "500",
    openedAt: "2026-07-26T10:00:00Z",
    priceUpdatedAt: "2026-07-26T12:00:00Z",
    realizedPnlUsd: "0",
    side: "long",
    size: "0.005",
    sourceWallet: "__exchange__",
    unrealizedPnlPct: "0.00126",
    unrealizedPnlUsd: "0.63",
    updatedAt: "2026-07-26T12:00:00Z",
  };
}
