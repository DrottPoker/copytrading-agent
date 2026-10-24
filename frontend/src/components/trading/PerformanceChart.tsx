"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import { Segmented } from "@/components/ui/client";
import { cx } from "@/lib/cx";
import { formatDateTime, formatInteger, formatPercent, formatSignedUsd, formatUsd } from "@/lib/format";
import type { EquityPoint, PerformancePoint } from "@/lib/trading/performance";

type ChartMode = "pnl" | "drawdown" | "cumulative" | "trade";
type TimeRange = "1d" | "7d" | "30d" | "all";
type TradeRange = 25 | 50 | 100 | "all";

const HEIGHT = 240;
const PADDING = { bottom: 28, left: 66, right: 12, top: 12 };
const DAY_MS = 24 * 60 * 60 * 1000;
const TIME_RANGE_MS: Record<TimeRange, number> = { "1d": DAY_MS, "7d": 7 * DAY_MS, "30d": 30 * DAY_MS, all: Infinity };

type Sample = { id: string; label: string; detail: string; timestamp: number; value: number };

/**
 * Account performance, inspectable with pointer and arrow keys. With an equity
 * series it shows marked-to-market PnL and drawdown over time; closed trades are
 * always available as a per trade view.
 */
export function PerformanceChart({ equity, trades }: { equity: EquityPoint[] | null; trades: PerformancePoint[] }) {
  const hasEquity = equity !== null;
  const [mode, setMode] = useState<ChartMode>(hasEquity ? "pnl" : "cumulative");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");
  const [tradeRange, setTradeRange] = useState<TradeRange>("all");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [width, setWidth] = useState(800);
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gradientId = useId().replaceAll(":", "");
  const resolvedMode: ChartMode = hasEquity
    ? mode === "cumulative"
      ? "pnl"
      : mode
    : mode === "pnl" || mode === "drawdown"
      ? "cumulative"
      : mode;
  const timeBased = resolvedMode === "pnl" || resolvedMode === "drawdown";

  const samples = useMemo<Sample[]>(() => {
    if (timeBased) {
      const points = equity ?? [];
      const end = points.length > 0 ? points[points.length - 1].timestamp : 0;
      const visible = points.filter((point) => point.timestamp >= end - TIME_RANGE_MS[timeRange]);
      return visible.map((point) => ({
        detail:
          resolvedMode === "pnl"
            ? `equity ${formatUsd(point.equityUsd)}${point.isLive ? ", live mark" : ""}`
            : `${formatPercent(-point.drawdownPct, { decimals: 2 })} from the peak`,
        id: point.at,
        label: formatDateTime(point.at),
        timestamp: point.timestamp,
        value: resolvedMode === "pnl" ? point.pnlUsd : -point.drawdownUsd,
      }));
    }
    const visible = tradeRange === "all" ? trades : trades.slice(-tradeRange);
    // The cumulative curve of a shorter range starts from the value before the range.
    const offset = visible.length === trades.length ? 0 : trades[trades.length - visible.length - 1].value;
    return visible.map((point) => ({
      detail: `trade ${formatSignedUsd(point.tradeValue)}`,
      id: point.id,
      label: `${point.coin}, closed ${formatDateTime(point.closedAt)}`,
      timestamp: point.timestamp,
      value: resolvedMode === "cumulative" ? point.value - offset : point.tradeValue,
    }));
  }, [equity, resolvedMode, timeBased, timeRange, tradeRange, trades]);

  useEffect(() => {
    setActiveIndex(null);
  }, [samples]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const update = () => {
      const next = Math.floor(container.getBoundingClientRect().width);
      if (next > 0) {
        setWidth(next);
      }
    };
    update();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(update);
    observer.observe(container);
    return () => observer.disconnect();
  }, []);

  const modeOptions: Array<{ label: string; value: ChartMode }> = hasEquity
    ? [
        { label: "PnL", value: "pnl" },
        { label: "Drawdown", value: "drawdown" },
        { label: "Per trade", value: "trade" },
      ]
    : [
        { label: "Cumulative", value: "cumulative" },
        { label: "Per trade", value: "trade" },
      ];
  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented<ChartMode> label="Chart mode" value={resolvedMode} onChange={setMode} options={modeOptions} />
      {timeBased ? (
        <Segmented<TimeRange>
          label="Time range"
          value={timeRange}
          onChange={setTimeRange}
          options={[
            { label: "1D", value: "1d" },
            { label: "7D", value: "7d" },
            { label: "30D", value: "30d" },
            { label: "All", value: "all" },
          ]}
        />
      ) : (
        <Segmented<TradeRange>
          label="Trade range"
          value={tradeRange}
          onChange={setTradeRange}
          options={[
            { label: "25", value: 25 },
            { label: "50", value: 50 },
            { label: "100", value: 100 },
            { label: "All", value: "all" },
          ]}
        />
      )}
    </div>
  );

  if (samples.length === 0) {
    return (
      <div>
        <div className="flex justify-end">{controls}</div>
        <div className="mt-2 flex h-[240px] items-center justify-center rounded-md border border-dashed border-line px-4 text-center text-sm text-fg-3">
          {timeBased
            ? "The equity curve appears after the first equity snapshot of the account."
            : "The curve appears after the account closes its first trade."}
        </div>
      </div>
    );
  }

  const values = samples.map((sample) => sample.value);
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const span = maxValue - minValue || 1;
  const plotWidth = Math.max(width - PADDING.left - PADDING.right, 10);
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const firstTime = samples[0].timestamp;
  const timeSpan = samples[samples.length - 1].timestamp - firstTime;
  const xFor = (index: number) => {
    if (samples.length === 1) {
      return PADDING.left + plotWidth / 2;
    }
    const ratio =
      timeBased && timeSpan > 0 ? (samples[index].timestamp - firstTime) / timeSpan : index / (samples.length - 1);
    return PADDING.left + ratio * plotWidth;
  };
  const yFor = (value: number) => PADDING.top + ((maxValue - value) / span) * plotHeight;
  const zeroY = yFor(0);
  const line = values.map((value, index) => `${index === 0 ? "M" : "L"}${xFor(index).toFixed(1)} ${yFor(value).toFixed(1)}`).join(" ");
  const area = `${line} L${xFor(values.length - 1).toFixed(1)} ${zeroY.toFixed(1)} L${xFor(0).toFixed(1)} ${zeroY.toFixed(1)} Z`;
  const ticks = niceTicks(minValue, maxValue);
  const resolved = activeIndex ?? samples.length - 1;
  const activeSample = samples[resolved];
  const activeValue = values[resolved];
  const endPositive = values[values.length - 1] >= 0;
  const stroke =
    resolvedMode === "trade"
      ? "var(--fg-3)"
      : resolvedMode === "drawdown" || !endPositive
        ? "var(--negative)"
        : "var(--positive)";
  const barWidth = Math.max(2, Math.min(12, (plotWidth / samples.length) * 0.6));
  const caption =
    resolvedMode === "pnl"
      ? "Equity PnL, marked to market"
      : resolvedMode === "drawdown"
        ? "Drawdown from the equity peak"
        : resolvedMode === "cumulative"
          ? "Cumulative net PnL of closed trades"
          : "Net PnL of the trade";
  const rangeCaption = timeBased
    ? timeRange === "all"
      ? ""
      : `, last ${timeRange.toUpperCase()}`
    : tradeRange === "all"
      ? ""
      : `, last ${tradeRange} trades`;

  const selectFromClientX = (clientX: number) => {
    const bounds = svgRef.current?.getBoundingClientRect();
    if (!bounds) {
      return;
    }
    const x = ((clientX - bounds.left) / Math.max(bounds.width, 1)) * width;
    let nearest = 0;
    for (let index = 1; index < samples.length; index += 1) {
      if (Math.abs(xFor(index) - x) < Math.abs(xFor(nearest) - x)) {
        nearest = index;
      }
    }
    setActiveIndex(nearest);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<SVGSVGElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    setActiveIndex((current) => Math.max(0, Math.min(samples.length - 1, (current ?? samples.length - 1) + step)));
  };

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-fg-3">
            {caption}
            {rangeCaption}
          </p>
          <p
            className={cx(
              "text-xl font-semibold tabular-nums",
              activeValue > 0 ? "text-positive" : activeValue < 0 ? "text-negative" : "text-fg",
            )}
          >
            {formatSignedUsd(activeValue)}
          </p>
          <p className="text-xs text-fg-3">
            {activeSample.label}, {activeSample.detail}
          </p>
        </div>
        {controls}
      </div>

      <div ref={containerRef} className="mt-2">
        <svg
          ref={svgRef}
          aria-label={
            timeBased
              ? `Chart of ${formatInteger(samples.length)} equity samples. Use the arrow keys to inspect them.`
              : `Chart of ${formatInteger(samples.length)} closed trades. Use the arrow keys to inspect trades.`
          }
          className="block h-[240px] w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-info/40"
          onKeyDown={handleKeyDown}
          onPointerLeave={() => setActiveIndex(null)}
          onPointerMove={(event: ReactPointerEvent<SVGSVGElement>) => selectFromClientX(event.clientX)}
          role="img"
          tabIndex={0}
          viewBox={`0 0 ${width} ${HEIGHT}`}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity="0.18" />
              <stop offset="100%" stopColor={stroke} stopOpacity="0" />
            </linearGradient>
          </defs>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                stroke="var(--chart-grid)"
                strokeWidth="1"
                x1={PADDING.left}
                x2={width - PADDING.right}
                y1={yFor(tick)}
                y2={yFor(tick)}
              />
              <text
                fill="var(--chart-axis)"
                fontSize="12"
                textAnchor="end"
                x={PADDING.left - 8}
                y={yFor(tick) + 4}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {formatUsd(tick, { compact: true, decimals: Math.abs(tick) < 10 ? 2 : 0 })}
              </text>
            </g>
          ))}
          <line stroke="var(--chart-axis)" strokeOpacity="0.6" x1={PADDING.left} x2={width - PADDING.right} y1={zeroY} y2={zeroY} />
          {resolvedMode === "trade" ? (
            values.map((value, index) => (
              <rect
                key={samples[index].id}
                fill={value >= 0 ? "var(--positive)" : "var(--negative)"}
                height={Math.max(Math.abs(zeroY - yFor(value)), 1)}
                opacity={index === resolved ? 1 : 0.55}
                rx="1"
                width={barWidth}
                x={xFor(index) - barWidth / 2}
                y={Math.min(yFor(value), zeroY)}
              />
            ))
          ) : (
            <>
              <path d={area} fill={`url(#${gradientId})`} />
              <path d={line} fill="none" stroke={stroke} strokeLinejoin="round" strokeWidth="1.75" />
            </>
          )}
          <line
            stroke="var(--fg-3)"
            strokeDasharray="2 3"
            x1={xFor(resolved)}
            x2={xFor(resolved)}
            y1={PADDING.top}
            y2={HEIGHT - PADDING.bottom}
          />
          <circle cx={xFor(resolved)} cy={yFor(activeValue)} fill="var(--surface)" r="3.5" stroke={stroke} strokeWidth="2" />
          <text fill="var(--chart-axis)" fontSize="12" x={PADDING.left} y={HEIGHT - 8}>
            {formatDateTime(samples[0].timestamp)}
          </text>
          <text fill="var(--chart-axis)" fontSize="12" textAnchor="end" x={width - PADDING.right} y={HEIGHT - 8}>
            {formatDateTime(samples[samples.length - 1].timestamp)}
          </text>
        </svg>
      </div>
    </div>
  );
}

/** Three to five round tick values that cover the range and include zero. */
function niceTicks(min: number, max: number) {
  const span = max - min || 1;
  const rough = span / 4;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((factor) => factor * magnitude).find((value) => value >= rough) ?? rough;
  const ticks: number[] = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step / 1000; value += step) {
    ticks.push(Number(value.toFixed(10)));
  }
  return ticks.length > 0 ? ticks : [0];
}
