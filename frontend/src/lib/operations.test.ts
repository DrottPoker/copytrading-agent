import { describe, expect, it } from "vitest";

import type { OperationStatus } from "@/types/operation";

import {
  MANUAL_JOBS,
  operationIsStopping,
  operationProgress,
  operationStatusLabel,
  optimisticStopping,
} from "./operations";

function operation(overrides: Partial<OperationStatus>): OperationStatus {
  return {
    completedAt: null,
    durationMs: null,
    key: "discovery_import",
    label: "Discovery import",
    lastError: null,
    lastSuccessAt: null,
    payload: {},
    startedAt: "2026-01-01T00:00:00Z",
    status: "running",
    updatedAt: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("operation status", () => {
  it("only reports stopping while the operation still runs", () => {
    expect(operationIsStopping(operation({ payload: { cancelRequested: true } }))).toBe(true);
    expect(operationStatusLabel(operation({ payload: { cancelRequested: true } }))).toBe("Stopping");

    const canceled = operation({ payload: { cancelRequested: true }, status: "canceled" });
    expect(operationIsStopping(canceled)).toBe(false);
    expect(operationStatusLabel(canceled)).toBe("Canceled");
  });

  it("does not mark a finished operation as stopping optimistically", () => {
    const [finished] = optimisticStopping([operation({ status: "succeeded" })], "discovery_import");
    expect(finished.payload.cancelRequested).toBeUndefined();
  });

  it("reads saved progress before batch counters", () => {
    expect(operationProgress(operation({ payload: { progressPercent: 42.4 } })).percent).toBe(42);
    expect(operationProgress(operation({ payload: { batchIndex: 3, batchSize: 12 } })).percent).toBe(25);
    expect(operationProgress(operation({ payload: {} })).percent).toBeNull();
  });

  it("summarizes the result of each manual job", () => {
    const scoring = MANUAL_JOBS.find((job) => job.key === "wallet_scoring");
    expect(scoring?.summary({ scoredWallets: 83, totalWallets: 83, windowDays: 60 })).toBe(
      "83 of 83 wallets scored, 60 day window",
    );
    const pool = MANUAL_JOBS.find((job) => job.key === "pool_fill_import");
    expect(pool?.summary({})).toBeNull();
  });
});
