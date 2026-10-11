"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Stopwatch } from "@/components/ui/client";
import { Spinner, StatusDot } from "@/components/ui/primitives";
import { statusTone, type Tone } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import { formatInteger } from "@/lib/format";
import { apiUrl } from "@/lib/http";
import { operationProgress } from "@/lib/operations";
import type { HealthResponse } from "@/types/health";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

const IDLE_POLL_MS = 10_000;
const BUSY_POLL_MS = 3_000;

type HealthState =
  | { kind: "loading" }
  | { kind: "ok"; health: HealthResponse }
  | { kind: "unreachable" };

/** Thin bar on every page that shows what the system is doing right now. */
export function SystemStatusBar() {
  const [health, setHealth] = useState<HealthState>({ kind: "loading" });
  const [operations, setOperations] = useState<OperationStatus[]>([]);
  const failuresRef = useRef(0);

  const load = useCallback(async (signal?: AbortSignal) => {
    const [healthResult, operationsResult] = await Promise.allSettled([
      fetch(apiUrl("/health"), { cache: "no-store", signal }).then(async (response) => {
        if (!response.ok && response.status !== 503) {
          throw new Error(`HTTP ${response.status}`);
        }
        return (await response.json()) as HealthResponse;
      }),
      fetch(apiUrl("/operations/status"), { cache: "no-store", signal }).then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return (await response.json()) as OperationStatusListResponse;
      }),
    ]);
    if (signal?.aborted) {
      return;
    }
    if (healthResult.status === "fulfilled") {
      failuresRef.current = 0;
      setHealth({ kind: "ok", health: healthResult.value });
    } else {
      failuresRef.current += 1;
      // One missed poll is not an outage; report it after two failures in a row.
      if (failuresRef.current >= 2) {
        setHealth({ kind: "unreachable" });
      }
    }
    if (operationsResult.status === "fulfilled") {
      setOperations(operationsResult.value.items);
    }
  }, []);

  const running = operations.filter((operation) => operation.status === "running");
  const pollMs = running.length > 0 ? BUSY_POLL_MS : IDLE_POLL_MS;

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void load(controller.signal);
      }
    }, pollMs);
    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [load, pollMs]);

  return (
    <div className="flex h-9 items-center gap-4 overflow-x-auto px-3 text-xs text-fg-3 sm:px-5 lg:px-6">
      {health.kind === "loading" ? (
        <span className="text-fg-4">Connecting to backend</span>
      ) : health.kind === "unreachable" ? (
        <StatusDot tone="negative" pulse label={<span className="text-negative">Backend unreachable, retrying</span>} />
      ) : (
        <HealthSummary health={health.health} />
      )}

      <div className="ml-auto flex shrink-0 items-center gap-4">
        {running.map((operation) => (
          <RunningOperation key={operation.key} operation={operation} />
        ))}
      </div>
    </div>
  );
}

function HealthSummary({ health }: { health: HealthResponse }) {
  const mode = modeDisplay(health);
  const services: Array<{ label: string; status: string }> = [
    { label: "API", status: health.status === "ok" ? "ok" : "degraded" },
    { label: "Postgres", status: health.dependencies.postgres.status },
    { label: "Redis", status: health.dependencies.redis.status },
  ];
  const slotsFull = health.activeCopyWallets >= health.maxRealtimeWallets;

  return (
    <div className="flex shrink-0 items-center gap-4">
      <span className="inline-flex items-center gap-1.5">
        <span
          className={cx(
            "rounded px-1.5 py-px text-2xs font-semibold tracking-wide",
            mode.tone === "negative" ? "bg-negative text-white" : "bg-surface-3 text-fg",
          )}
        >
          {mode.label}
        </span>
        <span>{health.hyperliquidNetwork}</span>
      </span>
      <span className="flex items-center gap-3">
        {services.map((service) => (
          <StatusDot
            key={service.label}
            tone={serviceTone(service.status)}
            label={<span className="hidden sm:inline">{service.label}</span>}
            title={`${service.label}: ${service.status}`}
          />
        ))}
      </span>
      <span
        className="hidden items-center gap-1 md:inline-flex"
        title="Wallets followed over a realtime subscription. Hyperliquid allows a limited number."
      >
        Realtime
        <span className={cx("tabular-nums", slotsFull ? "text-fg" : "text-fg-2")}>
          {formatInteger(health.activeCopyWallets)}/{formatInteger(health.maxRealtimeWallets)}
        </span>
      </span>
    </div>
  );
}

function RunningOperation({ operation }: { operation: OperationStatus }) {
  const progress = operationProgress(operation);
  return (
    <Link href="/#jobs" className="inline-flex items-center gap-1.5 text-fg-2 hover:text-fg">
      <Spinner className="text-info" />
      <span>{operation.label}</span>
      {progress.percent !== null ? <span className="tabular-nums text-fg-3">{progress.percent}%</span> : null}
      {operation.startedAt ? <Stopwatch className="text-fg-3" since={operation.startedAt} /> : null}
    </Link>
  );
}

function modeDisplay(health: HealthResponse): { label: string; tone: Tone } {
  if (health.mode === "live_small" || health.liveTradingEnabled) {
    return { label: "LIVE", tone: "negative" };
  }
  if (health.mode === "paper") {
    return { label: "PAPER", tone: "neutral" };
  }
  return { label: "MONITOR", tone: "neutral" };
}

function serviceTone(status: string): Tone {
  if (status === "ok") {
    return "positive";
  }
  if (status === "not_configured" || status === "unknown") {
    return "warning";
  }
  return statusTone(status) === "neutral" ? "negative" : statusTone(status);
}
