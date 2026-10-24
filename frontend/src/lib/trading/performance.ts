import { dateMs, decimal, toNumber } from "@/lib/format";
import type { PaperAccountPerformanceResponse, PaperTradeStats } from "@/types/paper";

import type { ClosedTradeRow } from "./types";

export type PerformancePoint = {
  closedAt: string;
  coin: string;
  id: string;
  timestamp: number;
  tradeValue: number;
  value: number;
};

/** One marked-to-market equity sample with its PnL and its distance below the running peak. */
export type EquityPoint = {
  at: string;
  drawdownPct: number;
  drawdownUsd: number;
  equityUsd: number;
  isLive: boolean;
  pnlUsd: number;
  timestamp: number;
};

/** Win and loss sums and streaks of closed trades. */
export type TradeSummary = {
  bestTradeUsd: number;
  currentStreak: { count: number; kind: "win" | "loss" | null };
  grossLossUsd: number;
  grossProfitUsd: number;
  longestLossStreak: number;
  longestWinStreak: number;
  lossCount: number;
  netPnlUsd: number;
  tradeCount: number;
  winCount: number;
  worstTradeUsd: number;
};

export type PerformanceAnalytics = TradeSummary & {
  averageLossUsd: number;
  averageWinUsd: number;
  currentDrawdownPct: number | null;
  currentDrawdownUsd: number | null;
  /** Equity drawdown for paper accounts, cumulative closed trade PnL for live accounts. */
  drawdownBasis: "equity" | "closed_trades";
  expectancyUsd: number;
  maxDrawdownPct: number | null;
  maxDrawdownUsd: number;
  payoffRatio: number | null;
  profitFactor: number | null;
  recoveryFactor: number | null;
  winRate: number | null;
};

/** Orders closed trades by close time and accumulates their net PnL. */
export function buildPerformanceTimeline(trades: ClosedTradeRow[]): PerformancePoint[] {
  let cumulative = 0;
  return [...trades]
    .sort((left, right) => dateMs(left.closedAt) - dateMs(right.closedAt))
    .map((trade) => {
      cumulative += trade.netPnlUsd;
      return {
        closedAt: trade.closedAt,
        coin: trade.coin,
        id: trade.id,
        timestamp: dateMs(trade.closedAt),
        tradeValue: trade.netPnlUsd,
        value: cumulative,
      };
    });
}

export function summarizeTrades(values: number[]): TradeSummary {
  const wins = values.filter((value) => value > 0);
  const losses = values.filter((value) => value < 0);
  let direction = 0;
  let streak = 0;
  let longestWinStreak = 0;
  let longestLossStreak = 0;
  for (const value of values) {
    const current = value > 0 ? 1 : value < 0 ? -1 : 0;
    if (current === 0) {
      continue;
    }
    streak = current === direction ? streak + 1 : 1;
    direction = current;
    if (current > 0) {
      longestWinStreak = Math.max(longestWinStreak, streak);
    } else {
      longestLossStreak = Math.max(longestLossStreak, streak);
    }
  }
  return {
    bestTradeUsd: values.length > 0 ? Math.max(...values) : 0,
    currentStreak: { count: direction === 0 ? 0 : streak, kind: direction > 0 ? "win" : direction < 0 ? "loss" : null },
    grossLossUsd: Math.abs(losses.reduce((total, value) => total + value, 0)),
    grossProfitUsd: wins.reduce((total, value) => total + value, 0),
    longestLossStreak,
    longestWinStreak,
    lossCount: losses.length,
    netPnlUsd: values.reduce((total, value) => total + value, 0),
    tradeCount: values.length,
    winCount: wins.length,
    worstTradeUsd: values.length > 0 ? Math.min(...values) : 0,
  };
}

export function tradeSummaryFromStats(stats: PaperTradeStats): TradeSummary {
  return {
    bestTradeUsd: decimal(stats.bestTradeUsd),
    currentStreak: { count: stats.currentStreakCount, kind: stats.currentStreakKind },
    grossLossUsd: decimal(stats.grossLossUsd),
    grossProfitUsd: decimal(stats.grossProfitUsd),
    longestLossStreak: stats.longestLossStreak,
    longestWinStreak: stats.longestWinStreak,
    lossCount: stats.lossCount,
    netPnlUsd: decimal(stats.netPnlUsd),
    tradeCount: stats.tradeCount,
    winCount: stats.winCount,
    worstTradeUsd: decimal(stats.worstTradeUsd),
  };
}

function tradeRatios(summary: TradeSummary) {
  const averageWinUsd = summary.winCount > 0 ? summary.grossProfitUsd / summary.winCount : 0;
  const averageLossUsd = summary.lossCount > 0 ? -summary.grossLossUsd / summary.lossCount : 0;
  return {
    averageLossUsd,
    averageWinUsd,
    expectancyUsd: summary.tradeCount > 0 ? summary.netPnlUsd / summary.tradeCount : 0,
    payoffRatio: averageLossUsd < 0 ? averageWinUsd / Math.abs(averageLossUsd) : null,
    profitFactor:
      summary.grossLossUsd > 0
        ? summary.grossProfitUsd / summary.grossLossUsd
        : summary.grossProfitUsd > 0
          ? Infinity
          : null,
    winRate: summary.tradeCount > 0 ? summary.winCount / summary.tradeCount : null,
  };
}

/** Closed trade statistics with the drawdown of the cumulative closed PnL, used by live accounts. */
export function calculatePerformance(points: PerformancePoint[]): PerformanceAnalytics {
  const values = points.map((point) => point.tradeValue);
  const summary = summarizeTrades(values);
  let peak = 0;
  let running = 0;
  let maxDrawdownUsd = 0;
  for (const value of values) {
    running += value;
    peak = Math.max(peak, running);
    maxDrawdownUsd = Math.max(maxDrawdownUsd, peak - running);
  }
  return {
    ...summary,
    ...tradeRatios(summary),
    currentDrawdownPct: null,
    currentDrawdownUsd: null,
    drawdownBasis: "closed_trades",
    maxDrawdownPct: null,
    maxDrawdownUsd,
    recoveryFactor: maxDrawdownUsd > 0 ? summary.netPnlUsd / maxDrawdownUsd : null,
  };
}

/**
 * Equity samples since the last reset plus the current marked equity, with each
 * sample's distance below the highest equity before it.
 */
export function buildEquitySeries(
  performance: PaperAccountPerformanceResponse,
  current: { at: string; equityUsd: number } | null,
): EquityPoint[] {
  const startingBalance = decimal(performance.startingBalanceUsd);
  const samples = performance.equityPoints.map((point) => ({
    at: point.recordedAt,
    equityUsd: decimal(point.equityUsd),
    isLive: false,
  }));
  if (current && (samples.length === 0 || dateMs(current.at) > dateMs(samples[samples.length - 1].at))) {
    samples.push({ ...current, isLive: true });
  }
  let peak = -Infinity;
  return samples.map((sample) => {
    peak = Math.max(peak, sample.equityUsd);
    const drawdownUsd = peak - sample.equityUsd;
    return {
      at: sample.at,
      drawdownPct: peak > 0 ? drawdownUsd / peak : 0,
      drawdownUsd,
      equityUsd: sample.equityUsd,
      isLive: sample.isLive,
      pnlUsd: sample.equityUsd - startingBalance,
      timestamp: dateMs(sample.at),
    };
  });
}

/**
 * Paper statistics: round trip figures from the server over every trade since the
 * last reset, and drawdown from marked-to-market equity instead of closed trades.
 */
export function calculatePaperPerformance(
  performance: PaperAccountPerformanceResponse,
  { currentEquityUsd, totalPnlUsd }: { currentEquityUsd: number; totalPnlUsd: number },
): PerformanceAnalytics {
  const summary = tradeSummaryFromStats(performance.tradeStats);
  const peakEquity = Math.max(toNumber(performance.peakEquityUsd) ?? 0, currentEquityUsd);
  const currentDrawdownUsd = Math.max(peakEquity - currentEquityUsd, 0);
  const currentDrawdownPct = peakEquity > 0 ? currentDrawdownUsd / peakEquity : null;
  const snapshotDrawdownUsd = decimal(performance.maxDrawdownUsd);
  const snapshotDrawdownPct = toNumber(performance.maxDrawdownPct);
  // The live mark can sit below every stored snapshot, so the current drawdown may be the largest.
  const currentIsLargest = (currentDrawdownPct ?? 0) > (snapshotDrawdownPct ?? 0);
  const maxDrawdownUsd = currentIsLargest ? currentDrawdownUsd : snapshotDrawdownUsd;
  return {
    ...summary,
    ...tradeRatios(summary),
    currentDrawdownPct,
    currentDrawdownUsd,
    drawdownBasis: "equity",
    maxDrawdownPct: currentIsLargest ? currentDrawdownPct : snapshotDrawdownPct,
    maxDrawdownUsd,
    recoveryFactor: maxDrawdownUsd > 0 ? totalPnlUsd / maxDrawdownUsd : null,
  };
}
