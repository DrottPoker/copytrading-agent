"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import { formatCurrency, formatInteger } from "@/lib/format";

export type AccountPerformancePoint = {
  coin: string;
  id: string;
  label: string;
  timestamp: number;
  tradeValue: number;
  value: number;
};

export type AccountPerformanceAnalytics = {
  averageLossUsd: number;
  averageWinUsd: number;
  bestTradeUsd: number;
  currentStreak: string;
  expectancyUsd: number;
  grossLossUsd: number;
  grossProfitUsd: number;
  longestLossStreak: number;
  longestWinStreak: number;
  maxDrawdownUsd: number;
  payoffRatio: number | null;
  profitFactor: number | null;
  recoveryFactor: number | null;
  tradeCount: number;
  winRate: number | null;
  worstTradeUsd: number;
};

type ChartMode = "cumulative" | "trade";
type ChartRange = 25 | 50 | 100 | "all";

const CHART_WIDTH = 900;
const CHART_HEIGHT = 260;
const CHART_PADDING = { bottom: 34, left: 68, right: 20, top: 18 };
const CHART_RANGES: ChartRange[] = [25, 50, 100, "all"];

export function calculateAccountPerformance(
  points: AccountPerformancePoint[],
): AccountPerformanceAnalytics {
  const tradeValues = points.map((point) => point.tradeValue);
  const wins = tradeValues.filter((value) => value > 0);
  const losses = tradeValues.filter((value) => value < 0);
  const grossProfitUsd = wins.reduce((total, value) => total + value, 0);
  const grossLossUsd = Math.abs(losses.reduce((total, value) => total + value, 0));
  const netPnlUsd = tradeValues.reduce((total, value) => total + value, 0);
  const averageWinUsd = wins.length > 0 ? grossProfitUsd / wins.length : 0;
  const averageLossUsd = losses.length > 0 ? -grossLossUsd / losses.length : 0;
  let peak = 0;
  let maxDrawdownUsd = 0;
  let runningPnl = 0;
  let currentDirection = 0;
  let currentStreakCount = 0;
  let longestWinStreak = 0;
  let longestLossStreak = 0;

  for (const value of tradeValues) {
    runningPnl += value;
    peak = Math.max(peak, runningPnl);
    maxDrawdownUsd = Math.max(maxDrawdownUsd, peak - runningPnl);

    const direction = value > 0 ? 1 : value < 0 ? -1 : 0;
    if (direction === 0) {
      continue;
    }
    if (direction === currentDirection) {
      currentStreakCount += 1;
    } else {
      currentDirection = direction;
      currentStreakCount = 1;
    }
    if (direction > 0) {
      longestWinStreak = Math.max(longestWinStreak, currentStreakCount);
    } else {
      longestLossStreak = Math.max(longestLossStreak, currentStreakCount);
    }
  }

  return {
    averageLossUsd,
    averageWinUsd,
    bestTradeUsd: tradeValues.length > 0 ? Math.max(...tradeValues) : 0,
    currentStreak:
      currentDirection > 0
        ? `${formatInteger(currentStreakCount)} W`
        : currentDirection < 0
          ? `${formatInteger(currentStreakCount)} L`
          : "-",
    expectancyUsd: tradeValues.length > 0 ? netPnlUsd / tradeValues.length : 0,
    grossLossUsd,
    grossProfitUsd,
    longestLossStreak,
    longestWinStreak,
    maxDrawdownUsd,
    payoffRatio:
      averageLossUsd < 0 ? averageWinUsd / Math.abs(averageLossUsd) : null,
    profitFactor:
      grossLossUsd > 0 ? grossProfitUsd / grossLossUsd : grossProfitUsd > 0 ? Infinity : null,
    recoveryFactor: maxDrawdownUsd > 0 ? netPnlUsd / maxDrawdownUsd : null,
    tradeCount: tradeValues.length,
    winRate: tradeValues.length > 0 ? wins.length / tradeValues.length : null,
    worstTradeUsd: tradeValues.length > 0 ? Math.min(...tradeValues) : 0,
  };
}

export function AccountPerformanceChart({
  points,
}: {
  points: AccountPerformancePoint[];
}) {
  const [mode, setMode] = useState<ChartMode>("cumulative");
  const [range, setRange] = useState<ChartRange>(50);
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [svgWidth, setSvgWidth] = useState(CHART_WIDTH);
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gradientId = useId().replaceAll(":", "");
  const visiblePoints = useMemo(
    () => (range === "all" ? points : points.slice(-range)),
    [points, range],
  );

  useEffect(() => {
    setActiveIndex(null);
  }, [mode, range, points]);

  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container) {
      return;
    }
    const updateWidth = () => {
      const width = Math.floor(container.getBoundingClientRect().width);
      if (width > 0) {
        setSvgWidth(width);
      }
    };
    updateWidth();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(updateWidth);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  if (points.length === 0) {
    return (
      <div className="flex min-h-[260px] items-center justify-center rounded-lg border border-dashed border-line bg-subtle/60 px-4 text-center text-sm text-muted">
        The performance curve appears after this account has closed trades.
      </div>
    );
  }

  const values = visiblePoints.map((point) =>
    mode === "cumulative" ? point.value : point.tradeValue,
  );
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const rangeValue = maxValue - minValue || 1;
  const plotWidth = svgWidth - CHART_PADDING.left - CHART_PADDING.right;
  const chartHeight = CHART_HEIGHT - CHART_PADDING.top - CHART_PADDING.bottom;
  const xForIndex = (index: number) =>
    visiblePoints.length === 1
      ? CHART_PADDING.left + plotWidth / 2
      : CHART_PADDING.left + (index / (visiblePoints.length - 1)) * plotWidth;
  const yForValue = (value: number) =>
    CHART_PADDING.top + ((maxValue - value) / rangeValue) * chartHeight;
  const zeroY = yForValue(0);
  const linePath = visiblePoints
    .map((point, index) => {
      const x = xForIndex(index);
      const y = yForValue(point.value);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const areaPath = `${linePath} L ${xForIndex(visiblePoints.length - 1).toFixed(
    2,
  )} ${zeroY.toFixed(2)} L ${xForIndex(0).toFixed(2)} ${zeroY.toFixed(2)} Z`;
  const gridValues = Array.from({ length: 5 }, (_, index) =>
    maxValue - (rangeValue * index) / 4,
  );
  const resolvedIndex = activeIndex ?? visiblePoints.length - 1;
  const activePoint = visiblePoints[resolvedIndex];
  const activeX = xForIndex(resolvedIndex);
  const activeValue = mode === "cumulative" ? activePoint.value : activePoint.tradeValue;
  const activeY = yForValue(activeValue);
  const trendPositive = visiblePoints[visiblePoints.length - 1].value >= 0;
  const lineColor = trendPositive ? "var(--chart-positive)" : "var(--chart-danger)";
  const barWidth = Math.max(3, Math.min(14, (plotWidth / visiblePoints.length) * 0.64));

  const selectIndexFromClientX = (clientX: number) => {
    const svg = svgRef.current;
    if (!svg) {
      return;
    }
    const bounds = svg.getBoundingClientRect();
    const chartX =
      ((clientX - bounds.left) / Math.max(bounds.width, 1)) * svgWidth;
    const ratio = Math.max(
      0,
      Math.min(1, (chartX - CHART_PADDING.left) / plotWidth),
    );
    setActiveIndex(Math.round(ratio * Math.max(visiblePoints.length - 1, 0)));
  };

  const handlePointerMove = (event: ReactPointerEvent<SVGSVGElement>) => {
    selectIndexFromClientX(event.clientX);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<SVGSVGElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    setActiveIndex((current) =>
      Math.max(
        0,
        Math.min(
          visiblePoints.length - 1,
          (current ?? visiblePoints.length - 1) + direction,
        ),
      ),
    );
  };

  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.05em] text-muted">
            {mode === "cumulative" ? "Cumulative net PnL" : "Net PnL per trade"}
          </p>
          <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <p
              className={`font-mono text-2xl font-semibold ${
                activeValue >= 0 ? "text-positive" : "text-danger"
              }`}
            >
              {formatCurrency(activeValue)}
            </p>
            <p className="text-xs text-muted">
              {activePoint.coin} | {activePoint.label}
            </p>
          </div>
          <p className="mt-1 text-xs text-muted">
            Trade {formatCurrency(activePoint.tradeValue)} | Curve{" "}
            {formatCurrency(activePoint.value)}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div
            aria-label="Select chart mode"
            className="inline-flex rounded-lg border border-line bg-subtle p-0.5"
            role="group"
          >
            {(["cumulative", "trade"] as ChartMode[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                onClick={() => setMode(value)}
                className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                  mode === value
                    ? "bg-white text-ink shadow-panel"
                    : "text-muted hover:text-ink"
                }`}
              >
                {value === "cumulative" ? "Cumulative" : "Per trade"}
              </button>
            ))}
          </div>
          <div
            aria-label="Select trade range"
            className="inline-flex rounded-lg border border-line bg-subtle p-0.5"
            role="group"
          >
            {CHART_RANGES.map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={range === value}
                onClick={() => setRange(value)}
                className={`rounded-md px-2 py-1 text-xs font-semibold ${
                  range === value
                    ? "bg-white text-ink shadow-panel"
                    : "text-muted hover:text-ink"
                }`}
              >
                {value === "all" ? "All" : value}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div
        ref={chartContainerRef}
        className="mt-3 overflow-hidden rounded-lg border border-line bg-gradient-to-b from-white to-subtle/70"
      >
        <svg
          ref={svgRef}
          aria-label={`Interactive chart with ${formatInteger(
            visiblePoints.length,
          )} closed trades`}
          className="h-[260px] w-full touch-none"
          onKeyDown={handleKeyDown}
          onPointerLeave={() => setActiveIndex(null)}
          onPointerMove={handlePointerMove}
          role="img"
          tabIndex={0}
          viewBox={`0 0 ${svgWidth} ${CHART_HEIGHT}`}
        >
          <defs>
            <linearGradient id={`${gradientId}-area`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={lineColor} stopOpacity="0.22" />
              <stop offset="100%" stopColor={lineColor} stopOpacity="0.015" />
            </linearGradient>
          </defs>

          {gridValues.map((value) => {
            const y = yForValue(value);
            return (
              <g key={value}>
                <line
                  stroke="var(--chart-grid)"
                  strokeDasharray="3 5"
                  strokeWidth="1"
                  x1={CHART_PADDING.left}
                  x2={svgWidth - CHART_PADDING.right}
                  y1={y}
                  y2={y}
                />
                <text
                  fill="#667085"
                  fontFamily="SFMono-Regular, Cascadia Code, Consolas, monospace"
                  fontSize="10"
                  textAnchor="end"
                  x={CHART_PADDING.left - 9}
                  y={y + 3}
                >
                  {compactCurrency(value)}
                </text>
              </g>
            );
          })}

          <line
            stroke="#98a2b3"
            strokeWidth="1"
            x1={CHART_PADDING.left}
            x2={svgWidth - CHART_PADDING.right}
            y1={zeroY}
            y2={zeroY}
          />

          {mode === "cumulative" ? (
            <>
              <path d={areaPath} fill={`url(#${gradientId}-area)`} />
              <path
                d={linePath}
                fill="none"
                stroke={lineColor}
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth="2.5"
                vectorEffect="non-scaling-stroke"
              />
            </>
          ) : (
            visiblePoints.map((point, index) => {
              const y = yForValue(point.tradeValue);
              return (
                <rect
                  key={point.id}
                  fill={
                    point.tradeValue >= 0
                      ? "var(--chart-positive)"
                      : "var(--chart-danger)"
                  }
                  height={Math.max(Math.abs(zeroY - y), 1)}
                  opacity={index === resolvedIndex ? 1 : 0.52}
                  rx="2"
                  width={barWidth}
                  x={xForIndex(index) - barWidth / 2}
                  y={Math.min(y, zeroY)}
                />
              );
            })
          )}

          <line
            stroke="#667085"
            strokeDasharray="3 3"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
            x1={activeX}
            x2={activeX}
            y1={CHART_PADDING.top}
            y2={CHART_HEIGHT - CHART_PADDING.bottom}
          />
          <circle
            cx={activeX}
            cy={activeY}
            fill="white"
            r="5"
            stroke={activeValue >= 0 ? "var(--chart-positive)" : "var(--chart-danger)"}
            strokeWidth="3"
            vectorEffect="non-scaling-stroke"
          />

          <text
            fill="#667085"
            fontSize="10"
            textAnchor="start"
            x={CHART_PADDING.left}
            y={CHART_HEIGHT - 10}
          >
            {visiblePoints[0]?.label}
          </text>
          <text
            fill="#667085"
            fontSize="10"
            textAnchor="end"
            x={svgWidth - CHART_PADDING.right}
            y={CHART_HEIGHT - 10}
          >
            {visiblePoints[visiblePoints.length - 1]?.label}
          </text>
        </svg>
      </div>
    </div>
  );
}

function compactCurrency(value: number) {
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: Math.abs(value) < 10 ? 1 : 0,
    notation: Math.abs(value) >= 10000 ? "compact" : "standard",
  }).format(value);
}
