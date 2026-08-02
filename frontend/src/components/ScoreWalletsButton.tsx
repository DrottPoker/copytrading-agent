"use client";

import { Gauge } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { frontendConfig, getPublicApiBaseUrl } from "@/lib/config";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

const SCORING_OPERATION_KEY = "wallet_scoring";

export function ScoreWalletsButton() {
  const router = useRouter();
  const [isStarting, setIsStarting] = useState(false);
  const [operation, setOperation] = useState<OperationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isScoring = isStarting || operation?.status === "running";

  useEffect(() => {
    if (operation?.status !== "running") {
      return;
    }

    let requestActive = false;
    const pollInterval = window.setInterval(async () => {
      if (requestActive) {
        return;
      }
      requestActive = true;
      try {
        const response = await fetch(`${getPublicApiBaseUrl()}/operations/status`, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error("Could not refresh scoring status.");
        }
        const payload = (await response.json()) as OperationStatusListResponse;
        const nextOperation =
          payload.items.find((item) => item.key === SCORING_OPERATION_KEY) ?? null;
        if (!nextOperation) {
          return;
        }

        setOperation(nextOperation);
        if (nextOperation.status === "succeeded") {
          setError(null);
          router.refresh();
        } else if (nextOperation.status === "failed") {
          setError(nextOperation.lastError ?? "Scoring failed.");
        }
      } catch {
        setError("Could not refresh scoring status.");
      } finally {
        requestActive = false;
      }
    }, Math.min(frontendConfig.operationStatusPollMs, 2000));

    return () => window.clearInterval(pollInterval);
  }, [operation?.status, router]);

  async function scoreWallets() {
    setIsStarting(true);
    setOperation(null);
    setError(null);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/scores/recalculate/start`, {
        method: "POST",
      });
      const payload = (await response.json().catch(() => null)) as
        | OperationStatus
        | { detail?: string }
        | null;
      if (!response.ok) {
        setError(
          payload && "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Scoring failed.",
        );
        return;
      }

      const startedOperation = payload as OperationStatus;
      setOperation(startedOperation);
      if (startedOperation.status === "succeeded") {
        router.refresh();
      } else if (startedOperation.status === "failed") {
        setError(startedOperation.lastError ?? "Scoring failed.");
      }
    } catch {
      setError("Could not reach API.");
    } finally {
      setIsStarting(false);
    }
  }

  const scoredWallets = numericPayload(operation?.payload.scoredWallets);
  const windowDays = numericPayload(operation?.payload.windowDays);

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        disabled={isScoring}
        onClick={scoreWallets}
        className="ui-button-primary h-8 gap-1 px-3 text-xs disabled:border-faint disabled:bg-faint"
        title="Recalculate wallet scores"
      >
        <Gauge className="h-3.5 w-3.5" aria-hidden="true" />
        {isScoring ? "Scoring" : "Score wallets"}
      </button>
      {operation?.status === "succeeded" && scoredWallets !== null ? (
        <p className="text-xs text-muted">
          {scoredWallets} scored{windowDays !== null ? `, ${windowDays}d` : ""}
        </p>
      ) : null}
      {error ? <p className="text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}

function numericPayload(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
