import { dateMs } from "@/lib/format";

import type { ClosedTradeRow } from "./types";

export type PerformancePoint = {
  closedAt: string;
  coin: string;
  id: string;
  timestamp: number;
  tradeValue: number;
  value: number;
};

export type PerformanceAnalytics = {
  averageLossUsd: number;
  averageWinUsd: number;
  bestTradeUsd: number;
  currentStreak: { count: number; kind: "win" | "loss" | null };
  expectancyUsd: number;
  grossLossUsd: number;
  grossProfitUsd: number;
  longestLossStreak: number;
  longestWinStreak: number;
  maxDrawdownUsd: number;
  netPnlUsd: number;
  payoffRatio: number | null;
  profitFactor: number | null;
  recoveryFactor: number | null;
  tradeCount: number;
  winRate: number | null;
  worstTradeUsd: number;
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

export function calculatePerformance(points: PerformancePoint[]): PerformanceAnalytics {
  const values = points.map((point) => point.tradeValue);
  const wins = values.filter((value) => value > 0);
  const losses = values.filter((value) => value < 0);
  const grossProfitUsd = wins.reduce((total, value) => total + value, 0);
  const grossLossUsd = Math.abs(losses.reduce((total, value) => total + value, 0));
  const netPnlUsd = values.reduce((total, value) => total + value, 0);
  const averageWinUsd = wins.length > 0 ? grossProfitUsd / wins.length : 0;
  const averageLossUsd = losses.length > 0 ? -grossLossUsd / losses.length : 0;
  let peak = 0;
  let running = 0;
  let maxDrawdownUsd = 0;
  let direction = 0;
  let streak = 0;
  let longestWinStreak = 0;
  let longestLossStreak = 0;

  for (const value of values) {
    running += value;
    peak = Math.max(peak, running);
    maxDrawdownUsd = Math.max(maxDrawdownUsd, peak - running);
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
    averageLossUsd,
    averageWinUsd,
    bestTradeUsd: values.length > 0 ? Math.max(...values) : 0,
    currentStreak: { count: direction === 0 ? 0 : streak, kind: direction > 0 ? "win" : direction < 0 ? "loss" : null },
    expectancyUsd: values.length > 0 ? netPnlUsd / values.length : 0,
    grossLossUsd,
    grossProfitUsd,
    longestLossStreak,
    longestWinStreak,
    maxDrawdownUsd,
    netPnlUsd,
    payoffRatio: averageLossUsd < 0 ? averageWinUsd / Math.abs(averageLossUsd) : null,
    profitFactor: grossLossUsd > 0 ? grossProfitUsd / grossLossUsd : grossProfitUsd > 0 ? Infinity : null,
    recoveryFactor: maxDrawdownUsd > 0 ? netPnlUsd / maxDrawdownUsd : null,
    tradeCount: values.length,
    winRate: values.length > 0 ? wins.length / values.length : null,
    worstTradeUsd: values.length > 0 ? Math.min(...values) : 0,
  };
}
