"use client";

import {
  Activity,
  CheckCircle2,
  Clock3,
  CircleStop,
  Loader2,
  Play,
  RefreshCcw,
  ShieldCheck,
  Trophy,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { frontendConfig } from "@/lib/config";
import { formatDate, formatInteger } from "@/lib/format";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

type OperationMetric = {
  key: string;
  label: string;
  suffix?: string;
};

const OPERATION_CARDS: Array<{
  icon: LucideIcon;
  key: string;
  manualEndpoint: string;
  manualLabel: string;
  metrics: OperationMetric[];
  title: string;
}> = [
  {
    icon: Trophy,
    key: "discovery_import",
    manualEndpoint: "/discovery/import/start",
    manualLabel: "Run discovery import",
    title: "Discovery import",
    metrics: [
      { label: "New", key: "inserted" },
      { label: "Skipped", key: "skipped" },
      { label: "Pool new", key: "poolInserted" },
    ],
  },
  {
    icon: RefreshCcw,
    key: "pool_fill_import",
    manualEndpoint: "/wallets/fills/import-pool",
    manualLabel: "Run pool reimport",
    title: "Pool reimport",
    metrics: [
      { label: "Wallets", key: "importedWallets" },
      { label: "New fills", key: "inserted" },
      { label: "Failed", key: "failed" },
    ],
  },
  {
    icon: ShieldCheck,
    key: "wallet_scoring",
    manualEndpoint: "/scores/recalculate/start",
    manualLabel: "Run wallet scoring",
    title: "Wallet scoring",
    metrics: [
      { label: "Scored", key: "scoredWallets" },
      { label: "Total", key: "totalWallets" },
      { label: "Window", key: "windowDays", suffix: "d" },
    ],
  },
];

export function OperationStatusStrip({ initialItems }: { initialItems: OperationStatus[] }) {
  const [items, setItems] = useState(initialItems);
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set());
  const [cancelPendingKeys, setCancelPendingKeys] = useState<Set<string>>(new Set());
  const [manualErrors, setManualErrors] = useState<Record<string, string>>({});

  const refreshStatuses = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(`${frontendConfig.browserApiBaseUrl}/operations/status`, {
        cache: "no-store",
        signal,
      });
      if (!response.ok) {
        return;
      }
      const data = (await response.json()) as OperationStatusListResponse;
      setItems(data.items);
    } catch {
      return;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void refreshStatuses(controller.signal);
    const intervalId = window.setInterval(
      () => void refreshStatuses(controller.signal),
      frontendConfig.operationStatusPollMs,
    );
    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [refreshStatuses]);

  useEffect(() => {
    setManualErrors((current) => {
      let next = current;
      for (const item of items) {
        if (
          (item.status === "running" ||
            item.status === "succeeded" ||
            item.status === "canceled") &&
          next[item.key]
        ) {
          next = omitKey(next, item.key);
        }
      }
      return next;
    });
  }, [items]);

  const runOperation = useCallback(
    async (card: (typeof OPERATION_CARDS)[number]) => {
      const now = new Date().toISOString();
      setManualErrors((current) => omitKey(current, card.key));
      setPendingKeys((current) => addSetKey(current, card.key));
      setItems((current) => optimisticRunningStatus(current, card, now));

      try {
        const response = await fetch(manualOperationUrl(card), {
          cache: "no-store",
          method: "POST",
        });
        if (!response.ok) {
          throw new Error(await responseErrorMessage(response));
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : "Manual run failed.";
        if (!message.toLowerCase().includes("canceled")) {
          setManualErrors((current) => ({
            ...current,
            [card.key]: message,
          }));
        }
      } finally {
        setPendingKeys((current) => removeSetKey(current, card.key));
        await refreshStatuses();
      }
    },
    [refreshStatuses],
  );

  const cancelOperation = useCallback(
    async (card: (typeof OPERATION_CARDS)[number]) => {
      setManualErrors((current) => omitKey(current, card.key));
      setCancelPendingKeys((current) => addSetKey(current, card.key));
      setItems((current) => optimisticStoppingStatus(current, card.key));

      try {
        const response = await fetch(operationCancelUrl(card.key), {
          cache: "no-store",
          method: "POST",
        });
        if (!response.ok) {
          throw new Error(await responseErrorMessage(response));
        }
        const operation = (await response.json()) as OperationStatus;
        setItems((current) => upsertOperationStatus(current, operation));
      } catch (error) {
        setManualErrors((current) => ({
          ...current,
          [card.key]: error instanceof Error ? error.message : "Cancel failed.",
        }));
      } finally {
        setCancelPendingKeys((current) => removeSetKey(current, card.key));
        await refreshStatuses();
      }
    },
    [refreshStatuses],
  );

  const operationMap = useMemo(
    () => new Map(items.map((operation) => [operation.key, operation])),
    [items],
  );

  return (
    <section className="grid gap-3 xl:grid-cols-3">
      {OPERATION_CARDS.map((card) => (
        <OperationIndicator
          key={card.key}
          icon={card.icon}
          isCancelPending={cancelPendingKeys.has(card.key)}
          isPending={pendingKeys.has(card.key)}
          manualError={manualErrors[card.key]}
          manualLabel={card.manualLabel}
          metrics={card.metrics}
          onCancel={() => void cancelOperation(card)}
          onRun={() => void runOperation(card)}
          operation={operationMap.get(card.key)}
          title={card.title}
        />
      ))}
    </section>
  );
}

function manualOperationUrl(card: (typeof OPERATION_CARDS)[number]) {
  const apiBase = frontendConfig.browserApiBaseUrl || "/api/backend";
  const apiPath = `${apiBase.replace(/\/$/, "")}${card.manualEndpoint}`;
  const url = new URL(apiPath, window.location.origin);
  if (card.key === "pool_fill_import") {
    url.searchParams.set(
      "limit",
      String(clampInteger(frontendConfig.poolReimportBatchLimit, 1, 100)),
    );
    url.searchParams.set(
      "max_batches",
      String(clampInteger(frontendConfig.poolReimportMaxBatches, 1, 1000)),
    );
    url.searchParams.set("include_items", "false");
    url.searchParams.set("force", "true");
  }
  if (card.key === "discovery_import") {
    return url.toString();
  }
  return url.toString();
}

function operationCancelUrl(key: string) {
  const apiBase = frontendConfig.browserApiBaseUrl || "/api/backend";
  return `${apiBase.replace(/\/$/, "")}/operations/${encodeURIComponent(key)}/cancel`;
}

function clampInteger(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function OperationIndicator({
  icon: Icon,
  isCancelPending,
  isPending,
  manualError,
  manualLabel,
  metrics,
  onCancel,
  onRun,
  operation,
  title,
}: {
  icon: LucideIcon;
  isCancelPending: boolean;
  isPending: boolean;
  manualError?: string;
  manualLabel: string;
  metrics: OperationMetric[];
  onCancel: () => void;
  onRun: () => void;
  operation?: OperationStatus;
  title: string;
}) {
  const status = operation?.status ?? "idle";
  const tone = operationTone(status);
  const StatusIcon = operationStatusIcon(status);
  const payload = operation?.payload ?? {};
  const duration = formatDuration(operation?.durationMs);
  const titleText = operation?.label ?? title;
  const isRunning = status === "running" || isPending;
  const isStopping = isCancelPending || payload.cancelRequested === true;
  const progress = operationProgressPercent(payload, isRunning);
  const stageLabel =
    stringPayloadValue(payload.stageLabel) ?? (isRunning ? "Running" : "Waiting");
  const stageDetail =
    stringPayloadValue(payload.stageDetail) ?? operationTimeText(operation);

  return (
    <article className="ui-metric">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase text-muted">{titleText}</p>
          <div className="mt-2 flex min-w-0 items-center gap-2">
            <span
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${operationToneClass(tone)}`}
            >
              <StatusIcon
                className={`h-4 w-4 ${status === "running" ? "animate-spin" : ""}`}
                aria-hidden="true"
              />
            </span>
            <p className="truncate text-lg font-semibold text-ink">
              {operationStatusLabel(status, isStopping)}
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onRun}
            disabled={isRunning}
            aria-label={manualLabel}
            title={manualLabel}
            className="ui-button-secondary h-8 px-2 text-xs disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{isRunning ? "Running" : "Run"}</span>
          </button>
          {isRunning ? (
            <button
              type="button"
              onClick={onCancel}
              disabled={isStopping}
              aria-label={`Cancel ${titleText}`}
              title={`Cancel ${titleText}`}
              className="ui-button-danger h-8 px-2 text-xs disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isCancelPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <CircleStop className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              <span>{isStopping ? "Stopping" : "Cancel"}</span>
            </button>
          ) : null}
          <Icon className="h-5 w-5 text-muted" aria-hidden="true" />
        </div>
      </div>

      <p className="mt-2 truncate text-xs text-muted">{operationTimeText(operation)}</p>
      {isRunning ? (
        <div className="mt-2">
          <div className="flex min-w-0 items-center justify-between gap-3 text-[11px]">
            <p className="min-w-0 truncate font-medium text-secondary" title={stageDetail}>
              <span>{stageLabel}</span>
              <span className="font-normal text-muted"> - {stageDetail}</span>
            </p>
            <span className="shrink-0 tabular-nums text-muted">{formatInteger(progress)}%</span>
          </div>
          <div
            className="mt-1 h-1 overflow-hidden rounded-full bg-line"
            role="progressbar"
            aria-label={`${titleText} progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                isStopping ? "bg-warning" : "bg-brand"
              }`}
              style={{ width: `${Math.max(3, progress)}%` }}
            />
          </div>
        </div>
      ) : null}
      <div className="mt-3 grid grid-cols-3 gap-2">
        {metrics.map((metric) => (
          <div key={metric.key} className="min-w-0">
            <p className="truncate text-[11px] font-medium uppercase text-muted">
              {metric.label}
            </p>
            <p className="mt-1 truncate text-sm font-semibold text-ink">
              {formatPayloadMetric(payload, metric)}
            </p>
          </div>
        ))}
      </div>
      <div className="mt-2 flex min-w-0 items-center justify-between gap-3 border-t border-line pt-2 text-xs text-muted">
        <span className="truncate">Duration {duration}</span>
        {operation?.lastError ? (
          <span className="truncate text-danger" title={operation.lastError}>
            {operation.lastError}
          </span>
        ) : null}
      </div>
      {manualError ? (
        <p className="mt-2 truncate text-xs font-medium text-danger" title={manualError}>
          {manualError}
        </p>
      ) : null}
    </article>
  );
}

type OperationTone = "positive" | "danger" | "neutral" | "warning";

function operationTone(status: string): OperationTone {
  if (status === "running") {
    return "warning";
  }
  if (status === "succeeded") {
    return "positive";
  }
  if (status === "failed") {
    return "danger";
  }
  return "neutral";
}

function operationToneClass(tone: OperationTone) {
  if (tone === "positive") {
    return "bg-positive-soft text-positive";
  }
  if (tone === "danger") {
    return "bg-danger-soft text-danger";
  }
  if (tone === "warning") {
    return "bg-warning-soft text-warning";
  }
  return "bg-subtle text-muted";
}

function operationStatusIcon(status: string) {
  if (status === "running") {
    return Loader2;
  }
  if (status === "succeeded") {
    return CheckCircle2;
  }
  if (status === "failed") {
    return Activity;
  }
  if (status === "canceled") {
    return CircleStop;
  }
  return Clock3;
}

function operationStatusLabel(status: string, isStopping = false) {
  if (isStopping) {
    return "Stopping";
  }
  if (status === "running") {
    return "Running";
  }
  if (status === "succeeded") {
    return "Complete";
  }
  if (status === "failed") {
    return "Failed";
  }
  if (status === "canceled") {
    return "Canceled";
  }
  return "No run";
}

function operationTimeText(operation?: OperationStatus) {
  if (!operation) {
    return "No run recorded";
  }
  if (operation.status === "running") {
    const progress = operationProgressText(operation.payload);
    return progress
      ? `Started ${formatDate(operation.startedAt)} (${progress})`
      : `Started ${formatDate(operation.startedAt)}`;
  }
  if (operation.status === "succeeded") {
    return `Last success ${formatDate(operation.lastSuccessAt ?? operation.completedAt)}`;
  }
  if (operation.status === "failed") {
    return `Failed ${formatDate(operation.completedAt ?? operation.updatedAt)}`;
  }
  if (operation.status === "canceled") {
    return `Canceled ${formatDate(operation.completedAt ?? operation.updatedAt)}`;
  }
  return "No run recorded";
}

function operationProgressPercent(
  payload: Record<string, unknown>,
  isRunning: boolean,
) {
  const savedProgress = numericPayloadValue(payload.progressPercent);
  if (savedProgress !== null) {
    return clampPercent(savedProgress);
  }
  const batchIndex = numericPayloadValue(payload.batchIndex);
  const batchSize = numericPayloadValue(payload.batchSize);
  if (batchIndex !== null && batchSize !== null && batchSize > 0) {
    return clampPercent((batchIndex / batchSize) * 100);
  }
  return isRunning ? 0 : 100;
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function stringPayloadValue(value: unknown) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function operationProgressText(payload: Record<string, unknown>) {
  const batchIndex = numericPayloadValue(payload.batchIndex);
  const batchSize = numericPayloadValue(payload.batchSize);
  const currentWallet = payload.currentWallet;
  if (batchIndex === null || batchSize === null) {
    return "";
  }
  const walletText =
    typeof currentWallet === "string" ? ` ${shortAddress(currentWallet)}` : "";
  return `${formatInteger(batchIndex)}/${formatInteger(batchSize)}${walletText}`;
}

function numericPayloadValue(value: unknown) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function shortAddress(address: string) {
  if (address.length <= 14) {
    return address;
  }
  return `${address.slice(0, 6)}...${address.slice(-4)}`;
}

function formatPayloadMetric(payload: Record<string, unknown>, metric: OperationMetric) {
  const value = payload[metric.key];
  if (typeof value !== "number" && typeof value !== "string") {
    return "-";
  }
  const suffix = metric.suffix ? ` ${metric.suffix}` : "";
  return `${formatInteger(value)}${suffix}`;
}

function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  if (value < 60_000) {
    return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(
      value / 1000,
    )} s`;
  }
  return `${formatInteger(Math.round(value / 60_000))} min`;
}

function optimisticRunningStatus(
  items: OperationStatus[],
  card: (typeof OPERATION_CARDS)[number],
  startedAt: string,
) {
  const existing = items.find((item) => item.key === card.key);
  const updated: OperationStatus = {
    key: card.key,
    label: existing?.label ?? card.title,
    status: "running",
    startedAt,
    completedAt: null,
    updatedAt: startedAt,
    lastSuccessAt: existing?.lastSuccessAt ?? null,
    durationMs: null,
    lastError: null,
    payload: {
      ...(existing?.payload ?? {}),
      stage: "queued",
      stageLabel: "Queued",
      stageDetail: "Waiting for the backend task to start.",
      progressPercent: 0,
    },
  };

  if (existing) {
    return items.map((item) => (item.key === card.key ? updated : item));
  }
  return [...items, updated];
}

function optimisticStoppingStatus(items: OperationStatus[], key: string) {
  const existing = items.find((item) => item.key === key);
  if (!existing) {
    return items;
  }
  return items.map((item) =>
    item.key === key
      ? {
          ...item,
          status: "running",
          payload: {
            ...item.payload,
            cancelRequested: true,
            stage: "cancel_requested",
            stageLabel: "Stopping",
            stageDetail: "Finishing the current safe checkpoint before stopping.",
          },
        }
      : item,
  );
}

function upsertOperationStatus(items: OperationStatus[], operation: OperationStatus) {
  if (items.some((item) => item.key === operation.key)) {
    return items.map((item) => (item.key === operation.key ? operation : item));
  }
  return [...items, operation];
}

function addSetKey(current: Set<string>, key: string) {
  const next = new Set(current);
  next.add(key);
  return next;
}

function removeSetKey(current: Set<string>, key: string) {
  const next = new Set(current);
  next.delete(key);
  return next;
}

function omitKey(current: Record<string, string>, key: string) {
  const next = { ...current };
  delete next[key];
  return next;
}

async function responseErrorMessage(response: Response) {
  try {
    const data = (await response.json()) as { detail?: unknown };
    if (typeof data.detail === "string") {
      return data.detail;
    }
    if (Array.isArray(data.detail)) {
      return data.detail
        .map((item) => {
          if (typeof item === "string") {
            return item;
          }
          if (item && typeof item === "object" && "msg" in item) {
            return String(item.msg);
          }
          return null;
        })
        .filter(Boolean)
        .join(", ");
    }
  } catch {
    return `${response.status} ${response.statusText}`;
  }
  return `${response.status} ${response.statusText}`;
}
