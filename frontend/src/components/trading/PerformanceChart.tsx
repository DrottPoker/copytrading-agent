"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";

import { Segmented } from "@/components/ui/client";
import { cx } from "@/lib/cx";
import { formatDateTime, formatInteger, formatSignedUsd, formatUsd } from "@/lib/format";
import type { PerformancePoint } from "@/lib/trading/performance";

type ChartMode = "cumulative" | "trade";
type ChartRange = 25 | 50 | 100 | "all";

const HEIGHT = 220;
const PADDING = { bottom: 24, left: 56, right: 12, top: 12 };

/** Cumulative or per trade net PnL of closed trades, inspectable with pointer and arrow keys. */
export function PerformanceChart({ points }: { points: PerformancePoint[] }) {
  const [mode, setMode] = useState<ChartMode>("cumulative");
  const [range, setRange] = useState<ChartRange>("all");
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const [width, setWidth] = useState(800);
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gradientId = useId().replaceAll(":", "");
  const visible = useMemo(() => (range === "all" ? points : points.slice(-range)), [points, range]);

  useEffect(() => {
    setActiveIndex(null);
  }, [mode, range, points]);

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

  if (points.length === 0) {
    return (
      <div className="flex h-[220px] items-center justify-center rounded-md border border-dashed border-line px-4 text-center text-sm text-fg-3">
        The curve appears after the account closes its first trade.
      </div>
    );
  }

  // The cumulative curve of a shorter range starts from the value before the range.
  const offset = range === "all" || visible.length === points.length ? 0 : points[points.length - visible.length - 1].value;
  const values = visible.map((point) => (mode === "cumulative" ? point.value - offset : point.tradeValue));
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const span = maxValue - minValue || 1;
  const plotWidth = Math.max(width - PADDING.left - PADDING.right, 10);
  const plotHeight = HEIGHT - PADDING.top - PADDING.bottom;
  const xFor = (index: number) =>
    visible.length === 1 ? PADDING.left + plotWidth / 2 : PADDING.left + (index / (visible.length - 1)) * plotWidth;
  const yFor = (value: number) => PADDING.top + ((maxValue - value) / span) * plotHeight;
  const zeroY = yFor(0);
  const line = values.map((value, index) => `${index === 0 ? "M" : "L"}${xFor(index).toFixed(1)} ${yFor(value).toFixed(1)}`).join(" ");
  const area = `${line} L${xFor(values.length - 1).toFixed(1)} ${zeroY.toFixed(1)} L${xFor(0).toFixed(1)} ${zeroY.toFixed(1)} Z`;
  const ticks = niceTicks(minValue, maxValue);
  const resolved = activeIndex ?? visible.length - 1;
  const activePoint = visible[resolved];
  const activeValue = values[resolved];
  const endPositive = values[values.length - 1] >= 0;
  const stroke = mode === "cumulative" ? (endPositive ? "var(--positive)" : "var(--negative)") : "var(--fg-3)";
  const barWidth = Math.max(2, Math.min(12, (plotWidth / visible.length) * 0.6));

  const selectFromClientX = (clientX: number) => {
    const bounds = svgRef.current?.getBoundingClientRect();
    if (!bounds) {
      return;
    }
    const x = ((clientX - bounds.left) / Math.max(bounds.width, 1)) * width;
    const ratio = Math.max(0, Math.min(1, (x - PADDING.left) / plotWidth));
    setActiveIndex(Math.round(ratio * Math.max(visible.length - 1, 0)));
  };

  const handleKeyDown = (event: ReactKeyboardEvent<SVGSVGElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") {
      return;
    }
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    setActiveIndex((current) => Math.max(0, Math.min(visible.length - 1, (current ?? visible.length - 1) + step)));
  };

  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-fg-3">
            {mode === "cumulative" ? "Cumulative net PnL" : "Net PnL of the trade"}
            {range === "all" ? "" : `, last ${range} trades`}
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
            {activePoint.coin}, closed {formatDateTime(activePoint.closedAt)}, trade{" "}
            {formatSignedUsd(activePoint.tradeValue)}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented<ChartMode>
            label="Chart mode"
            value={mode}
            onChange={setMode}
            options={[
              { label: "Cumulative", value: "cumulative" },
              { label: "Per trade", value: "trade" },
            ]}
          />
          <Segmented<ChartRange>
            label="Trade range"
            value={range}
            onChange={setRange}
            options={[
              { label: "25", value: 25 },
              { label: "50", value: 50 },
              { label: "100", value: 100 },
              { label: "All", value: "all" },
            ]}
          />
        </div>
      </div>

      <div ref={containerRef} className="mt-2">
        <svg
          ref={svgRef}
          aria-label={`Chart of ${formatInteger(visible.length)} closed trades. Use the arrow keys to inspect trades.`}
          className="block h-[220px] w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-info/40"
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
                fontSize="10"
                textAnchor="end"
                x={PADDING.left - 8}
                y={yFor(tick) + 3}
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {formatUsd(tick, { compact: true, decimals: Math.abs(tick) < 10 ? 2 : 0 })}
              </text>
            </g>
          ))}
          <line stroke="var(--chart-axis)" strokeOpacity="0.6" x1={PADDING.left} x2={width - PADDING.right} y1={zeroY} y2={zeroY} />
          {mode === "cumulative" ? (
            <>
              <path d={area} fill={`url(#${gradientId})`} />
              <path d={line} fill="none" stroke={stroke} strokeLinejoin="round" strokeWidth="1.75" />
            </>
          ) : (
            values.map((value, index) => (
              <rect
                key={visible[index].id}
                fill={value >= 0 ? "var(--positive)" : "var(--negative)"}
                height={Math.max(Math.abs(zeroY - yFor(value)), 1)}
                opacity={index === resolved ? 1 : 0.55}
                rx="1"
                width={barWidth}
                x={xFor(index) - barWidth / 2}
                y={Math.min(yFor(value), zeroY)}
              />
            ))
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
          <text fill="var(--chart-axis)" fontSize="10" x={PADDING.left} y={HEIGHT - 6}>
            {formatDateTime(visible[0].closedAt)}
          </text>
          <text fill="var(--chart-axis)" fontSize="10" textAnchor="end" x={width - PADDING.right} y={HEIGHT - 6}>
            {formatDateTime(visible[visible.length - 1].closedAt)}
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
