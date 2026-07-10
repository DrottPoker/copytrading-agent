"use client";

import { ScanSearch, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import { formatInteger, formatMs } from "@/lib/format";
import type { IgnoredFillCleanupResponse } from "@/types/database";

type BusyMode = "dry-run" | "run" | null;

export function DatabaseIgnoredFillCleanupPanel() {
  const router = useRouter();
  const [result, setResult] = useState<IgnoredFillCleanupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyMode, setBusyMode] = useState<BusyMode>(null);

  async function runCleanup(dryRun: boolean) {
    if (!dryRun) {
      const confirmed = window.confirm(
        "Delete raw ignored fills now? Run dry-run first and only continue if the counts look correct.",
      );
      if (!confirmed) {
        return;
      }
    }

    setError(null);
    setBusyMode(dryRun ? "dry-run" : "run");

    try {
      const response = await fetch(
        `${getPublicApiBaseUrl()}/database/fills/ignored-cleanup?dry_run=${dryRun}`,
        { method: "POST" },
      );
      const responseText = await response.text();
      const payload = parseJson(responseText) as
        | IgnoredFillCleanupResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        setError(
          errorDetail(payload) ?? responseText.trim() ?? "Could not run ignored fill cleanup.",
        );
        return;
      }

      setResult(payload as IgnoredFillCleanupResponse);
      if (!dryRun) {
        router.refresh();
      }
    } catch {
      setError("Could not reach backend API.");
    } finally {
      setBusyMode(null);
    }
  }

  return (
    <section className="ui-panel overflow-hidden">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          <ScanSearch className="mt-0.5 h-4 w-4 shrink-0 text-muted" aria-hidden="true" />
          <div>
            <h2 className="text-base font-semibold">Ignored Fill Cleanup</h2>
            <p className="mt-1 text-sm leading-6 text-muted">
              Deletes raw close-only and pre-existing-position fills that are not needed for
              reconstructed source trades.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCleanup(true)}
            className="ui-button-secondary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ScanSearch className="h-4 w-4" aria-hidden="true" />
            {busyMode === "dry-run" ? "Checking" : "Dry run"}
          </button>
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCleanup(false)}
            className="ui-button-danger disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {busyMode === "run" ? "Cleaning" : "Run cleanup"}
          </button>
        </div>
      </div>

      <div className="p-4">
        {error ? (
          <div className="mb-4 rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm font-medium text-danger">
            {error}
          </div>
        ) : null}

        {result ? (
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <CleanupMetric label="Mode" value={result.dryRun ? "Dry run" : "Executed"} />
              <CleanupMetric label="Min age" value={`${formatInteger(result.minAgeDays)} d`} />
              <CleanupMetric label="Cutoff" value={formatMs(result.cutoffTimeMs)} />
              <CleanupMetric label="Candidate fills" value={formatInteger(result.candidateFills)} />
              <CleanupMetric label="Candidate wallets" value={formatInteger(result.candidateWallets)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <CleanupMetric
                label="Pre-existing opens"
                value={formatInteger(result.candidatePreexistingOpenFills)}
              />
              <CleanupMetric
                label="Close-only fills"
                value={formatInteger(result.candidateUnmatchedCloseFills)}
              />
              <CleanupMetric
                label="Excluded trade closes"
                value={formatInteger(result.excludedPotentialTradeCloseFills)}
              />
              <CleanupMetric label="Remaining" value={formatInteger(result.remainingCandidateFills)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <CleanupMetric label="Deleted fills" value={formatInteger(result.deletedFills)} />
              <CleanupMetric
                label="Deleted markers"
                value={formatInteger(result.deletedIgnoredFillMarkers)}
              />
              <CleanupMetric label="Affected wallets" value={formatInteger(result.affectedWallets)} />
              <CleanupMetric label="Max rows" value={formatInteger(result.maxRows)} />
            </div>
            <p className="text-sm leading-6 text-muted">{result.note}</p>
          </div>
        ) : (
          <p className="text-sm leading-6 text-muted">
            Ignored fills are diagnostic only. This cleanup keeps fills that may have closed a
            reconstructed source trade and removes only raw ignored fills that are not needed for
            trade rebuilds.
          </p>
        )}
      </div>
    </section>
  );
}

function CleanupMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-subtle p-3">
      <p className="text-xs font-medium uppercase text-muted">{label}</p>
      <p className="mt-2 break-words text-lg font-semibold leading-snug">{value}</p>
    </div>
  );
}

function errorDetail(value: unknown) {
  if (!value || typeof value !== "object") {
    return null;
  }
  const detail = (value as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : null;
}

function parseJson(value: string) {
  try {
    return JSON.parse(value) as unknown;
  } catch {
    return null;
  }
}
