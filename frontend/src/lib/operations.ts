import { formatInteger } from "./format";
import type { OperationStatus } from "@/types/operation";

export type OperationProgress = {
  current: number | null;
  percent: number | null;
  stageDetail: string | null;
  stageLabel: string | null;
  total: number | null;
};

export function numericPayload(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function stringPayload(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Reads progress from an operation payload. Percent is null when the backend reports none. */
export function operationProgress(operation: OperationStatus | null | undefined): OperationProgress {
  const payload = operation?.payload ?? {};
  const savedPercent = numericPayload(payload.progressPercent);
  const current = numericPayload(payload.progressCurrent) ?? numericPayload(payload.batchIndex);
  const total = numericPayload(payload.progressTotal) ?? numericPayload(payload.batchSize);
  let percent: number | null = null;
  if (savedPercent !== null) {
    percent = clampPercent(savedPercent);
  } else if (current !== null && total !== null && total > 0) {
    percent = clampPercent((current / total) * 100);
  }
  return {
    current,
    percent,
    stageDetail: stringPayload(payload.stageDetail),
    stageLabel: stringPayload(payload.stageLabel),
    total,
  };
}

/** A cancel request only means "stopping" while the operation is still running. */
export function operationIsStopping(operation: OperationStatus | null | undefined) {
  return operation?.status === "running" && operation.payload.cancelRequested === true;
}

export function operationStatusLabel(operation: OperationStatus | null | undefined) {
  if (!operation) {
    return "Never run";
  }
  if (operationIsStopping(operation)) {
    return "Stopping";
  }
  switch (operation.status) {
    case "running":
      return "Running";
    case "succeeded":
      return "Succeeded";
    case "failed":
      return "Failed";
    case "canceled":
      return "Canceled";
    default:
      return "Idle";
  }
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

export type JobDefinition = {
  description: string;
  endpoint: string;
  key: string;
  params?: Record<string, string>;
  summary: (payload: Record<string, unknown>) => string | null;
  title: string;
};

function count(payload: Record<string, unknown>, key: string) {
  const value = numericPayload(payload[key]);
  return value === null ? null : formatInteger(value);
}

/** Background jobs the dashboard can start by hand. */
export const MANUAL_JOBS: JobDefinition[] = [
  {
    key: "discovery_import",
    title: "Discovery import",
    description:
      "Pulls new candidate wallets from the leaderboards, filters them, imports their history, and admits good ones to the pool.",
    endpoint: "/discovery/import/start",
    summary: (payload) => {
      const inserted = count(payload, "inserted");
      const pool = count(payload, "poolInserted");
      if (inserted === null && pool === null) {
        return null;
      }
      return `${inserted ?? "0"} new candidates, ${pool ?? "0"} added to the pool`;
    },
  },
  {
    key: "pool_fill_import",
    title: "Pool reimport",
    description: "Downloads recent trades for every wallet in the pool so scores stay current.",
    endpoint: "/wallets/fills/import-pool",
    params: { force: "true", include_items: "false" },
    summary: (payload) => {
      const wallets = count(payload, "importedWallets");
      const inserted = count(payload, "inserted");
      const failed = count(payload, "failed");
      if (wallets === null && inserted === null) {
        return null;
      }
      return `${wallets ?? "0"} wallets, ${inserted ?? "0"} new fills, ${failed ?? "0"} failed`;
    },
  },
  {
    key: "wallet_scoring",
    title: "Wallet scoring",
    description: "Recalculates every wallet's score from its stored trading history.",
    endpoint: "/scores/recalculate/start",
    summary: (payload) => {
      const scored = count(payload, "scoredWallets");
      const total = count(payload, "totalWallets");
      const window = count(payload, "windowDays");
      if (scored === null) {
        return null;
      }
      return `${scored}${total ? ` of ${total}` : ""} wallets scored${window ? `, ${window} day window` : ""}`;
    },
  },
];

export function optimisticRunning(
  items: OperationStatus[],
  job: JobDefinition,
  startedAt: string,
): OperationStatus[] {
  const existing = items.find((item) => item.key === job.key);
  const updated: OperationStatus = {
    key: job.key,
    label: existing?.label ?? job.title,
    status: "running",
    startedAt,
    completedAt: null,
    updatedAt: startedAt,
    lastSuccessAt: existing?.lastSuccessAt ?? null,
    durationMs: null,
    lastError: null,
    payload: {
      stage: "queued",
      stageLabel: "Queued",
      stageDetail: "Waiting for the backend task to start.",
      progressPercent: 0,
    },
  };
  return existing
    ? items.map((item) => (item.key === job.key ? updated : item))
    : [...items, updated];
}

export function optimisticStopping(items: OperationStatus[], key: string): OperationStatus[] {
  return items.map((item) =>
    item.key === key && item.status === "running"
      ? {
          ...item,
          payload: {
            ...item.payload,
            cancelRequested: true,
            stageLabel: "Stopping",
            stageDetail: "Finishing the current safe checkpoint before stopping.",
          },
        }
      : item,
  );
}

export function upsertOperation(items: OperationStatus[], operation: OperationStatus) {
  return items.some((item) => item.key === operation.key)
    ? items.map((item) => (item.key === operation.key ? operation : item))
    : [...items, operation];
}
