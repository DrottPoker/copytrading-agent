"use client";

import { Play, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { TimeAgo } from "@/components/ui/client";
import { buttonClass, Meter, Panel, Spinner, StatusDot } from "@/components/ui/primitives";
import { statusTone } from "@/components/ui/tone";
import { formatDuration, formatInteger, humanize } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import {
  numericPayload,
  operationIsStopping,
  operationProgress,
  operationStatusLabel,
} from "@/lib/operations";
import type { DiscoverySource } from "@/types/discovery";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

const SKIP_LABELS: Record<string, string> = {
  already_in_candidates: "Already a candidate",
  already_in_pool: "Already in the pool",
  duplicate_in_source: "Duplicate in source",
  insert_conflict: "Insert conflict",
  invalid_address: "Invalid address",
  invalid_source_row: "Invalid row",
  missing_address: "Missing address",
  missing_or_invalid_address: "Missing or invalid address",
};

/** Starts the discovery pipeline and shows its stage-by-stage progress. */
export function DiscoveryRunPanel({
  initialOperation,
  sources,
}: {
  initialOperation: OperationStatus | null;
  sources: DiscoverySource[];
}) {
  const router = useRouter();
  const [operation, setOperation] = useState(initialOperation);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wasRunning = useRef(initialOperation?.status === "running");
  const running = operation?.status === "running";
  const stopping = operationIsStopping(operation);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await apiRequest<OperationStatusListResponse>("/operations/status", { signal });
      setOperation(data.items.find((item) => item.key === "discovery_import") ?? null);
    } catch {
      // The next poll retries.
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const intervalId = window.setInterval(() => void refresh(controller.signal), running ? 2000 : 10_000);
    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [refresh, running]);

  // Reload the server data once a run finishes so the tables show the new candidates.
  useEffect(() => {
    if (wasRunning.current && !running) {
      router.refresh();
    }
    wasRunning.current = running;
  }, [router, running]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      const started = await apiRequest<OperationStatus>("/discovery/import/start", {
        fallback: "Could not start discovery",
        method: "POST",
        params: source ? { sources: source } : {},
      });
      setOperation(started);
    } catch (startError) {
      setError(errorMessage(startError, "Could not start discovery."));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    setBusy(true);
    setError(null);
    try {
      setOperation(await apiRequest<OperationStatus>("/operations/discovery_import/cancel", { method: "POST" }));
    } catch (cancelError) {
      setError(errorMessage(cancelError, "Cancel failed."));
    } finally {
      setBusy(false);
    }
  };

  const payload = operation?.payload ?? {};
  const progress = operationProgress(operation);
  const steps = [
    { label: "Fetched", value: count(payload.fetched) },
    { label: "New candidates", value: count(payload.inserted) },
    { label: "Skipped", value: count(payload.skipped) },
    {
      label: "Prefilter passed / rejected",
      value:
        numericPayload(payload.prefilterAccepted) === null && numericPayload(payload.prefilterRejected) === null
          ? "-"
          : `${count(payload.prefilterAccepted)} / ${count(payload.prefilterRejected)}`,
    },
    { label: "History imported", value: backfillText(payload) },
    { label: "Added to pool", value: count(payload.poolInserted) },
  ];
  const skipReasons = Object.entries((payload.skipReasons as Record<string, unknown> | undefined) ?? {})
    .map(([key, value]) => ({ key, value: numericPayload(value) ?? 0 }))
    .filter((item) => item.value > 0)
    .sort((left, right) => right.value - left.value)
    .slice(0, 8);
  const configured = sources.filter((item) => item.configured);

  return (
    <Panel
      title="Discovery run"
      meta={
        <StatusDot
          tone={stopping ? "warning" : statusTone(operation?.status)}
          pulse={running && !stopping}
          label={`${operationStatusLabel(operation)}${running && progress.percent !== null ? ` ${progress.percent}%` : ""}`}
        />
      }
      actions={
        <>
          <select
            aria-label="Sources to import from"
            className="field h-7 text-xs"
            disabled={running || busy}
            onChange={(event) => setSource(event.target.value)}
            value={source}
          >
            <option value="">All enabled sources</option>
            {configured.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          {running ? (
            <button
              type="button"
              className={buttonClass({ size: "sm", variant: "danger" })}
              disabled={busy || stopping}
              onClick={() => void cancel()}
            >
              {stopping || busy ? <Spinner /> : <Square className="h-3 w-3" aria-hidden="true" />}
              {stopping ? "Stopping" : "Cancel"}
            </button>
          ) : (
            <button
              type="button"
              className={buttonClass({ size: "sm", variant: "primary" })}
              disabled={busy}
              onClick={() => void start()}
            >
              {busy ? <Spinner /> : <Play className="h-3 w-3" aria-hidden="true" />}
              Run discovery
            </button>
          )}
        </>
      }
    >
      <div className="px-3.5 py-3">
        <p className="text-sm text-fg">
          {running
            ? [progress.stageLabel, progress.stageDetail].filter(Boolean).join(": ") || "Running"
            : operation
              ? (
                  <>
                    Last run {operationStatusLabel(operation).toLowerCase()}{" "}
                    <TimeAgo value={operation.completedAt ?? operation.updatedAt} />
                    {operation.durationMs !== null ? `, took ${formatDuration(operation.durationMs)}` : ""}.
                  </>
                )
              : "Discovery has not run yet. A run imports new wallets, filters them, imports their trade history, and admits the good ones."}
        </p>
        {running ? (
          <div className="mt-2">
            {progress.percent !== null ? (
              <Meter value={Math.max(progress.percent, 2) / 100} tone={stopping ? "warning" : "info"} label="Discovery progress" />
            ) : (
              <div className="relative h-1 overflow-hidden rounded-full bg-surface-3">
                <div className="animate-indeterminate absolute inset-y-0 w-1/3 rounded-full bg-info" />
              </div>
            )}
            {payload.currentSource ? (
              <p className="mt-1 text-xs text-fg-3">
                Source {formatInteger(numericPayload(payload.sourceIndex) ?? 0)} of{" "}
                {formatInteger(numericPayload(payload.sourceCount) ?? 0)}: {String(payload.currentSource)}
              </p>
            ) : null}
          </div>
        ) : null}
        {operation?.lastError ? <p className="mt-2 text-xs text-negative">{operation.lastError}</p> : null}
        {error ? <p className="mt-2 text-xs text-negative">{error}</p> : null}
      </div>
      {running || steps.some((step) => step.value !== "-") ? (
        <div className="overflow-hidden border-t border-line">
          <dl className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
            {steps.map((step) => (
              <div key={step.label} className="border-b border-r border-line px-3.5 py-2">
                <dt className="truncate text-xs text-fg-3">{step.label}</dt>
                <dd className="text-sm font-medium tabular-nums">{step.value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {skipReasons.length > 0 ? (
        <p className="border-t border-line px-3.5 py-2 text-xs text-fg-3">
          Skipped because:{" "}
          {skipReasons
            .map((item) => `${SKIP_LABELS[item.key] ?? humanize(item.key)} ${formatInteger(item.value)}`)
            .join(", ")}
        </p>
      ) : null}
    </Panel>
  );
}

function count(value: unknown) {
  const parsed = numericPayload(value);
  return parsed === null ? "-" : formatInteger(parsed);
}

function backfillText(payload: Record<string, unknown>) {
  const processed = numericPayload(payload.backfillProcessed);
  const total = numericPayload(payload.backfillTotal);
  if (processed !== null || total !== null) {
    return `${formatInteger(processed ?? 0)} / ${formatInteger(total ?? 0)}`;
  }
  const done = numericPayload(payload.backfilled);
  const failed = numericPayload(payload.backfillFailed);
  if (done === null && failed === null) {
    return "-";
  }
  return `${formatInteger(done ?? 0)}, ${formatInteger(failed ?? 0)} failed`;
}
