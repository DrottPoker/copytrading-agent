import { describe, expect, it } from "vitest";

import { liveAccount, livePosition, paperAccount, paperSummary, tradingAccounts } from "@/test/tradingFixtures";

import {
  buildAccountOptions,
  buildAccountView,
  compareRowsOldestFirst,
  liveAccountSummary,
  liveClosedTradeRow,
  paperAccountSummary,
} from "./accounts";
import { buildPerformanceTimeline, calculatePerformance } from "./performance";
import type { ClosedTradeRow } from "./types";

describe("paper account summary", () => {
  it("nets fees and funding out of realized PnL", () => {
    const summary = paperAccountSummary(paperAccount());

    expect(summary.grossRealizedPnlUsd).toBe(20);
    expect(summary.netRealizedPnlUsd).toBe(10);
    expect(summary.totalPnlUsd).toBe(15);
    expect(summary.equityUsd).toBe(1015);
    expect(summary.returnPct).toBeCloseTo(0.015);
    expect(summary.stateLabel).toBe("Trading");
  });

  it("reports a stopped loss limit as exit only", () => {
    const summary = paperAccountSummary(
      paperAccount({ enabled: true, exitOnlyAt: "2026-01-01T00:00:00Z", exitOnlyReason: "daily loss limit" }),
    );

    expect(summary.state).toBe("exit_only");
    expect(summary.stateTone).toBe("warning");
    expect(summary.stateDetail).toContain("daily loss limit");
  });
});

describe("live account summary", () => {
  it("uses the same net PnL semantics as paper and the attributed exchange positions", () => {
    const account = liveAccount({
      equityUsd: "1000",
      feeUsd: "1.64",
      fundingUsd: "-3.96",
      realizedPnlUsd: "70.94",
    });
    const positions = [
      livePosition({
        accountKey: account.key,
        currentNotionalUsd: "500",
        marginUsd: "250",
        sourceWallet: "__exchange__",
        unrealizedPnlUsd: "0.63",
      }),
    ];

    const summary = liveAccountSummary(account, positions, tradingAccounts({ updatedAt: "2026-01-02T00:00:30Z" }));

    expect(summary.netRealizedPnlUsd).toBeCloseTo(65.34);
    expect(summary.totalPnlUsd).toBeCloseTo(65.97);
    expect(summary.openMarginUsd).toBe(250);
    expect(summary.openNotionalUsd).toBe(500);
    expect(summary.returnPct).toBeCloseTo(0.02);
    expect(summary.stateLabel).toBe("Trading");
  });

  it("pauses entries while reconciliation is incomplete", () => {
    const summary = liveAccountSummary(
      liveAccount({ reconciliationStatus: "partial" }),
      [],
      tradingAccounts(),
    );

    expect(summary.state).toBe("entries_paused");
    expect(summary.stateLabel).toBe("Entries paused");
  });

  it("explains that the server switch is off", () => {
    const summary = liveAccountSummary(liveAccount({ status: "disabled" }), [], tradingAccounts({ liveTradingEnabled: false }));

    expect(summary.stateDetail).toContain("LIVE_TRADING_ENABLED=false");
  });
});

describe("account options and views", () => {
  it("lists paper accounts first and skips live rows that mirror a paper account", () => {
    const options = buildAccountOptions(
      paperSummary({ accounts: [paperAccount({ key: "paper-test" })] }),
      tradingAccounts({
        accounts: [liveAccount({ key: "live-test" }), liveAccount({ accountType: "paper", key: "paper-test" })],
      }),
    );

    expect(options.map((option) => `${option.accountType}:${option.key}`)).toEqual(["paper:paper-test", "live:live-test"]);
  });

  it("builds a live view with notices when live trading is off", () => {
    const account = liveAccount();
    const view = buildAccountView(
      paperSummary(),
      tradingAccounts({ accounts: [account], liveTradingEnabled: false, positions: [livePosition({ accountKey: account.key })] }),
      { accountType: "live", key: account.key, label: account.label, live: account },
    );

    expect(view.positions).toHaveLength(1);
    expect(view.notices[0].title).toBe("Live trading is turned off on the server");
  });

  it("keeps open positions oldest first with a deterministic tie break", () => {
    const base = { accountKey: "live-test", side: "long" as const, sourceWallet: "0xsource" };
    const positions = [
      { ...base, coin: "ETH", id: "newest", openedAt: "2026-01-03T00:00:00Z" },
      { ...base, coin: "ETH", id: "old-b", openedAt: "2026-01-01T00:00:00Z" },
      { ...base, coin: "BTC", id: "old-a", openedAt: "2026-01-01T00:00:00Z" },
    ];

    expect(positions.sort(compareRowsOldestFirst).map((position) => position.id)).toEqual(["old-a", "old-b", "newest"]);
  });

  it("marks liquidated live trades and links the source wallet", () => {
    const sourceWallet = "0x1234567890abcdef1234567890abcdef12345678";
    const row = liveClosedTradeRow(
      {
        accountKey: "live-test",
        closeFillCount: 1,
        closedAt: "2026-08-06T00:56:00Z",
        coin: "CASHCAT",
        durationMs: 46560000,
        entryNotionalUsd: "29.26",
        entryPrice: "0.109602",
        exitNotionalUsd: "41.47",
        exitPrice: "0.1553",
        feeUsd: "0.02",
        fundingUsd: "0",
        id: "trade-1",
        isLiquidation: true,
        netPnlUsd: "-12.22",
        openFillCount: 1,
        openedAt: "2026-08-05T12:00:00Z",
        realizedPnlUsd: "-12.20",
        side: "short",
        size: "267",
        sourceLabel: "Hyperdash profitable #81",
        sourceWallet,
      },
      new Map(),
    );

    expect(row.flags).toEqual([{ label: "Liquidated", tone: "negative" }]);
    expect(row.sourceLabel).toBe("Hyperdash profitable #81");
    expect(row.sourceHref).toBe(`/wallets/${sourceWallet}`);
  });
});

describe("performance analytics", () => {
  it("orders closed trades chronologically and builds cumulative PnL", () => {
    const timeline = buildPerformanceTimeline([
      trade({ closedAt: "2026-01-03T00:00:00Z", coin: "ETH", id: "trade-2", netPnlUsd: -4 }),
      trade({ closedAt: "2026-01-01T00:00:00Z", coin: "BTC", id: "trade-1", netPnlUsd: 10 }),
    ]);

    expect(timeline.map((point) => point.id)).toEqual(["trade-1", "trade-2"]);
    expect(timeline.map((point) => point.tradeValue)).toEqual([10, -4]);
    expect(timeline.map((point) => point.value)).toEqual([10, 6]);
  });

  it("calculates trade statistics from the loaded closed trades", () => {
    const analytics = calculatePerformance(
      buildPerformanceTimeline(
        [10, -4, 6, -12, 8].map((value, index) =>
          trade({ closedAt: `2026-01-0${index + 1}T00:00:00Z`, id: `trade-${index}`, netPnlUsd: value }),
        ),
      ),
    );

    expect(analytics.tradeCount).toBe(5);
    expect(analytics.winRate).toBeCloseTo(0.6);
    expect(analytics.grossProfitUsd).toBe(24);
    expect(analytics.grossLossUsd).toBe(16);
    expect(analytics.profitFactor).toBeCloseTo(1.5);
    expect(analytics.maxDrawdownUsd).toBe(12);
    expect(analytics.averageWinUsd).toBe(8);
    expect(analytics.averageLossUsd).toBe(-8);
    expect(analytics.payoffRatio).toBe(1);
    expect(analytics.expectancyUsd).toBeCloseTo(1.6);
    expect(analytics.bestTradeUsd).toBe(10);
    expect(analytics.worstTradeUsd).toBe(-12);
    expect(analytics.currentStreak).toEqual({ count: 1, kind: "win" });
    expect(analytics.longestLossStreak).toBe(1);
  });
});

function trade(overrides: Partial<ClosedTradeRow>): ClosedTradeRow {
  return {
    closeTypeLabel: "Close",
    closedAt: "2026-01-01T00:00:00Z",
    coin: "BTC",
    durationMs: null,
    entryPrice: null,
    exitPrice: "100",
    feeUsd: 0,
    flags: [],
    fundingUsd: null,
    id: "trade",
    netPnlUsd: 0,
    notionalUsd: null,
    openedAt: null,
    realizedPnlUsd: 0,
    side: "long",
    size: "1",
    sourceHref: null,
    sourceLabel: "Source",
    sourceWallet: "0xsource",
    ...overrides,
  };
}
