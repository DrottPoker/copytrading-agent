"use client";

import {
  Activity,
  CheckCircle2,
  Clock3,
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
    manualEndpoint: "/scores/recalculate",
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
        setManualErrors((current) => ({
          ...current,
          [card.key]: error instanceof Error ? error.message : "Manual run failed.",
        }));
      } finally {
        setPendingKeys((current) => removeSetKey(current, card.key));
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
          isPending={pendingKeys.has(card.key)}
          manualError={manualErrors[card.key]}
          manualLabel={card.manualLabel}
          metrics={card.metrics}
          onRun={() => void runOperation(card)}
          operation={operationMap.get(card.key)}
          title={card.title}
        />
      ))}
    </section>
  );
}

function manualOperationUrl(card: (typeof OPERATION_CARDS)[number]) {
  const url = new URL(`${frontendConfig.browserApiBaseUrl}${card.manualEndpoint}`);
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

function clampInteger(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function OperationIndicator({
  icon: Icon,
  isPending,
  manualError,
  manualLabel,
  metrics,
  onRun,
  operation,
  title,
}: {
  icon: LucideIcon;
  isPending: boolean;
  manualError?: string;
  manualLabel: string;
  metrics: OperationMetric[];
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

  return (
    <article className={`rounded-lg border p-4 shadow-sm ${operationToneClass(tone)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase text-[#5b6770]">{titleText}</p>
          <div className="mt-2 flex min-w-0 items-center gap-2">
            <StatusIcon
              className={`h-4 w-4 shrink-0 ${status === "running" ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            <p className="truncate text-lg font-semibold text-ink">
              {operationStatusLabel(status)}
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
            className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-medium text-ink shadow-sm transition hover:border-[#a9b5bf] hover:bg-[#f8fafb] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {isRunning ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Play className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            <span>{isRunning ? "Running" : "Run"}</span>
          </button>
          <Icon className="h-5 w-5 text-[#5b6770]" aria-hidden="true" />
        </div>
      </div>

      <p className="mt-3 truncate text-sm text-[#5b6770]">{operationTimeText(operation)}</p>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {metrics.map((metric) => (
          <div key={metric.key} className="min-w-0">
            <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">
              {metric.label}
            </p>
            <p className="mt-1 truncate text-sm font-semibold text-ink">
              {formatPayloadMetric(payload, metric)}
            </p>
          </div>
        ))}
      </div>
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3 border-t border-line pt-3 text-xs text-[#5b6770]">
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
    return "border-[#9ccfc0] bg-[#f2fbf7]";
  }
  if (tone === "danger") {
    return "border-[#efb1aa] bg-[#fff5f3]";
  }
  if (tone === "warning") {
    return "border-[#efd28b] bg-[#fff9e8]";
  }
  return "border-line bg-panel";
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
  return Clock3;
}

function operationStatusLabel(status: string) {
  if (status === "running") {
    return "Running";
  }
  if (status === "succeeded") {
    return "Complete";
  }
  if (status === "failed") {
    return "Failed";
  }
  return "No run";
}

function operationTimeText(operation?: OperationStatus) {
  if (!operation) {
    return "No run recorded";
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
  return "No run recorded";
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
    payload: existing?.payload ?? {},
  };

  if (existing) {
    return items.map((item) => (item.key === card.key ? updated : item));
  }
  return [...items, updated];
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
