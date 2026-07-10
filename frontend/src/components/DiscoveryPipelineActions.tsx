"use client";

import { DownloadCloud, Loader2, Play } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import { frontendConfig } from "@/lib/config";
import { formatDate, formatInteger } from "@/lib/format";
import type { DiscoverySource } from "@/types/discovery";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

type ActionKind = "import";

type ActionState = {
  kind: ActionKind;
  title: string;
  detail: string;
};

const actions: {
  kind: ActionKind;
  label: string;
  description: string;
  path: string;
  icon: typeof DownloadCloud;
}[] = [
  {
    kind: "import",
    label: "Import candidates",
    description: "Imports new candidates, filters them, backfills fills and adds approved wallets.",
    path: "/discovery/import",
    icon: DownloadCloud,
  },
];

export function DiscoveryPipelineActions({
  initialOperation,
  sources,
}: {
  initialOperation: OperationStatus | null;
  sources: DiscoverySource[];
}) {
  const [busy, setBusy] = useState<ActionKind | null>(null);
  const [source, setSource] = useState("");
  const [result, setResult] = useState<ActionState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState<OperationStatus | null>(initialOperation);
  const configuredSources = sources.filter((item) => item.configured);
  const operationPayload = useMemo(() => operation?.payload ?? {}, [operation]);
  const operationRunning = operation?.status === "running";

  const refreshOperation = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`${frontendConfig.browserApiBaseUrl}/operations/status`, {
        cache: "no-store",
        signal,
      });
      if (!response.ok) {
        return;
      }
      const payload = (await response.json()) as OperationStatusListResponse;
      setOperation(payload.items.find((item) => item.key === "discovery_import") ?? null);
    } catch {
      return;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refreshOperation(controller.signal);
    const intervalId = window.setInterval(
      () => void refreshOperation(controller.signal),
      Math.min(frontendConfig.operationStatusPollMs, 2000),
    );
    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [refreshOperation]);

  async function runAction(action: (typeof actions)[number]) {
    setBusy(action.kind);
    setError(null);
    setResult({
      kind: action.kind,
      title: "Discovery started",
      detail: "The backend is running the pipeline. Progress will continue to update here.",
    });

    try {
      const params = new URLSearchParams();
      if (source) {
        params.append("sources", source);
      }

      const query = params.toString();
      const response = await fetch(
        `${getPublicApiBaseUrl()}${action.path}/start${query ? `?${query}` : ""}`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => null)) as
        | OperationStatus
        | { detail?: string }
        | null;

      if (!response.ok) {
        setError(errorDetail(payload) ?? `Could not run ${action.label.toLowerCase()}.`);
        return;
      }

      setOperation(payload as OperationStatus);
      await refreshOperation();
    } catch {
      setError("Could not reach backend API.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="ui-panel overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 xl:flex-row xl:items-center xl:justify-between">
        <div>
          <h2 className="text-base font-semibold">Discovery Pipeline</h2>
          <p className="mt-1 text-sm leading-6 text-muted">
            Import only new wallets, filter them, backfill fills and add approved wallets to pool.
          </p>
        </div>
        <label className="flex min-w-[260px] flex-col gap-1 text-sm">
          <span className="font-medium text-muted">Source scope</span>
          <select
            value={source}
            onChange={(event) => setSource(event.target.value)}
            disabled={busy !== null || operationRunning}
            className="ui-control disabled:cursor-not-allowed disabled:opacity-60"
          >
            <option value="">All enabled sources</option>
            {configuredSources.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid gap-2 p-4">
        {actions.map((action) => {
          const Icon = action.icon;
          const isBusy = busy === action.kind || operationRunning;
          return (
            <button
              key={action.kind}
              type="button"
              disabled={busy !== null || operationRunning}
              onClick={() => void runAction(action)}
              className="group flex min-h-24 items-center gap-3 rounded-lg border border-line bg-white p-3 text-left shadow-panel hover:border-brand/30 hover:bg-brand-soft/30 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand">
                {isBusy ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Icon className="h-4 w-4" aria-hidden="true" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold text-ink">{action.label}</span>
                <span className="mt-1 block text-xs leading-5 text-muted">{action.description}</span>
              </span>
              <span className="flex shrink-0 items-center gap-1 text-xs font-semibold text-brand">
                <Play className="h-3.5 w-3.5" aria-hidden="true" />
                {isBusy ? "Running" : "Run"}
              </span>
            </button>
          );
        })}
      </div>

      <PipelineProgress operation={operation} payload={operationPayload} />

      {error ? (
        <div className="mx-4 mb-4 rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm font-medium text-danger">
          {error}
        </div>
      ) : null}

      {result ? (
        <div className="mx-4 mb-4 rounded-md border border-positive/25 bg-positive-soft px-3 py-2 text-sm">
          <p className="font-semibold text-positive">{result.title}</p>
          <p className="mt-1 text-secondary">{result.detail}</p>
        </div>
      ) : null}
    </section>
  );
}

function PipelineProgress({
  operation,
  payload,
}: {
  operation: OperationStatus | null;
  payload: Record<string, unknown>;
}) {
  const progress = progressPercent(operation, payload);
  const stageLabel = stringPayload(payload.stageLabel) ?? fallbackStageLabel(operation);
  const stageDetail = stringPayload(payload.stageDetail) ?? operationTimeText(operation);
  const status = operation?.status ?? "idle";
  const running = status === "running";
  const skipReasonItems = skipReasonEntries(payload);
  const metricItems = [
    { label: "Fetched", value: metricValue(payload, "fetched") },
    { label: "Inserted", value: metricValue(payload, "inserted") },
    { label: "Skipped", value: metricValue(payload, "skipped") },
    { label: "Prefilter", value: prefilterValue(payload) },
    { label: "Backfill", value: backfillValue(payload) },
    { label: "Pool new", value: metricValue(payload, "poolInserted") },
  ];

  return (
    <div className="mx-4 mb-4 rounded-md border border-line bg-white p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin text-brand" aria-hidden="true" />
            ) : null}
            <p className="text-sm font-semibold text-ink">{stageLabel}</p>
          </div>
          <p className="mt-1 text-sm leading-6 text-muted">{stageDetail}</p>
          {operation?.updatedAt ? (
            <p className="mt-1 text-xs text-muted">Updated {formatDate(operation.updatedAt)}</p>
          ) : null}
        </div>
        <div className="shrink-0 text-left sm:text-right">
          <p className="text-xs font-medium uppercase text-muted">Progress</p>
          <p className="mt-1 text-2xl font-semibold text-ink">{formatInteger(progress)}%</p>
        </div>
      </div>

      <div className="mt-4 h-2 overflow-hidden rounded-full bg-line">
        <div
          className="h-full rounded-full bg-brand transition-all duration-500"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {metricItems.map((item) => (
          <div key={item.label} className="ui-data-cell min-w-0">
            <p className="truncate text-[11px] font-medium uppercase text-muted">
              {item.label}
            </p>
            <p className="mt-1 truncate text-sm font-semibold text-ink">{item.value}</p>
          </div>
        ))}
      </div>

      {skipReasonItems.length > 0 ? (
        <div className="mt-4 rounded-md border border-line bg-subtle p-3">
          <p className="text-[11px] font-medium uppercase text-muted">Skip reasons</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {skipReasonItems.map((item) => (
              <span
                key={item.key}
                className="inline-flex h-7 items-center gap-2 rounded-md border border-line bg-white px-2.5 text-xs font-medium text-secondary"
                title={item.key}
              >
                <span>{skipReasonLabel(item.key)}</span>
                <span className="font-mono font-semibold text-ink">
                  {formatInteger(item.value)}
                </span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      {operation?.lastError ? (
        <p className="mt-3 truncate text-xs font-medium text-danger" title={operation.lastError}>
          {operation.lastError}
        </p>
      ) : null}
    </div>
  );
}

function progressPercent(
  operation: OperationStatus | null,
  payload: Record<string, unknown>,
) {
  const progress = numberPayload(payload.progressPercent);
  if (progress !== null) {
    return clampPercent(progress);
  }
  if (operation?.status === "succeeded") {
    return 100;
  }
  return 0;
}

function fallbackStageLabel(operation: OperationStatus | null) {
  if (operation?.status === "running") {
    return "Discovery running";
  }
  if (operation?.status === "succeeded") {
    return "Discovery complete";
  }
  if (operation?.status === "failed") {
    return "Discovery failed";
  }
  return "No discovery run";
}

function operationTimeText(operation: OperationStatus | null) {
  if (!operation) {
    return "No saved discovery progress yet.";
  }
  if (operation.status === "running") {
    return `Started ${formatDate(operation.startedAt)}`;
  }
  if (operation.status === "succeeded") {
    return `Last success ${formatDate(operation.lastSuccessAt ?? operation.completedAt)}`;
  }
  if (operation.status === "failed") {
    return `Failed ${formatDate(operation.completedAt ?? operation.updatedAt)}`;
  }
  return "No saved discovery progress yet.";
}

function metricValue(payload: Record<string, unknown>, key: string) {
  const value = numberPayload(payload[key]);
  return value === null ? "-" : formatInteger(value);
}

function prefilterValue(payload: Record<string, unknown>) {
  const accepted = numberPayload(payload.prefilterAccepted);
  const rejected = numberPayload(payload.prefilterRejected);
  if (accepted === null && rejected === null) {
    return "-";
  }
  return `${formatInteger(accepted ?? 0)} / ${formatInteger(rejected ?? 0)}`;
}

function backfillValue(payload: Record<string, unknown>) {
  const processed = numberPayload(payload.backfillProcessed);
  const total = numberPayload(payload.backfillTotal);
  if (processed !== null || total !== null) {
    return `${formatInteger(processed ?? 0)} / ${formatInteger(total ?? 0)}`;
  }
  const backfilled = numberPayload(payload.backfilled);
  const failed = numberPayload(payload.backfillFailed);
  if (backfilled === null && failed === null) {
    return "-";
  }
  return `${formatInteger(backfilled ?? 0)} done, ${formatInteger(failed ?? 0)} failed`;
}

function skipReasonEntries(payload: Record<string, unknown>) {
  const reasons = payload.skipReasons;
  if (!reasons || typeof reasons !== "object" || Array.isArray(reasons)) {
    return [];
  }
  return Object.entries(reasons)
    .map(([key, value]) => ({ key, value: numberPayload(value) ?? 0 }))
    .filter((item) => item.value > 0)
    .sort((left, right) => right.value - left.value)
    .slice(0, 8);
}

function skipReasonLabel(key: string) {
  const labels: Record<string, string> = {
    already_in_candidates: "Already in candidates",
    already_in_pool: "Already in pool",
    duplicate_in_source: "Duplicate in source",
    insert_conflict: "Insert conflict",
    invalid_address: "Invalid address",
    invalid_source_row: "Invalid row",
    missing_address: "Missing address",
    missing_or_invalid_address: "Missing/invalid address",
  };
  return labels[key] ?? key.split("_").join(" ");
}

function numberPayload(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function stringPayload(value: unknown) {
  return typeof value === "string" && value.trim() ? value : null;
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function errorDetail(value: unknown) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const detail = (value as { detail?: unknown }).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return null;
      })
      .filter(Boolean)
      .join(", ");
  }
  return null;
}
