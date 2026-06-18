"use client";

import { ShieldCheck, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import { formatInteger, formatMs } from "@/lib/format";
import type { FillRetentionCleanupResponse } from "@/types/database";

type BusyMode = "dry-run" | "run" | null;

export function DatabaseFillRetentionPanel() {
  const router = useRouter();
  const [result, setResult] = useState<FillRetentionCleanupResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyMode, setBusyMode] = useState<BusyMode>(null);

  async function runCleanup(dryRun: boolean) {
    if (!dryRun) {
      const confirmed = window.confirm(
        "Delete old unprotected fill history now? Run dry-run first and only continue if the counts look correct.",
      );
      if (!confirmed) {
        return;
      }
    }

    setError(null);
    setBusyMode(dryRun ? "dry-run" : "run");

    try {
      const response = await fetch(
        `${getPublicApiBaseUrl()}/database/fills/retention-cleanup?dry_run=${dryRun}`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => null)) as
        | FillRetentionCleanupResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        setError(errorDetail(payload) ?? "Could not run fill retention cleanup.");
        return;
      }

      setResult(payload as FillRetentionCleanupResponse);
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
    <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-2">
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-[#5b6770]" aria-hidden="true" />
          <div>
            <h2 className="text-base font-semibold">Fill Retention Cleanup</h2>
            <p className="mt-1 text-sm leading-6 text-[#5b6770]">
              Deletes old unprotected fill history and derived source-trade rows in safe batches.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCleanup(true)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ShieldCheck className="h-4 w-4" aria-hidden="true" />
            {busyMode === "dry-run" ? "Checking" : "Dry run"}
          </button>
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCleanup(false)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[#efb1aa] bg-[#fff5f3] px-3 text-sm font-medium text-danger disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {busyMode === "run" ? "Cleaning" : "Run cleanup"}
          </button>
        </div>
      </div>

      <div className="p-4">
        {error ? (
          <div className="mb-4 rounded-md border border-[#efb1aa] bg-[#fff5f3] px-3 py-2 text-sm font-medium text-danger">
            {error}
          </div>
        ) : null}

        {result ? (
          <div className="grid gap-4">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <RetentionMetric label="Mode" value={result.dryRun ? "Dry run" : "Executed"} />
              <RetentionMetric label="Retention" value={`${formatInteger(result.retentionDays)} d`} />
              <RetentionMetric label="Cutoff" value={formatMs(result.cutoffTimeMs)} />
              <RetentionMetric label="Protected wallets" value={formatInteger(result.protectedWallets)} />
              <RetentionMetric label="Candidate wallets" value={formatInteger(result.candidateWallets)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <RetentionMetric label="Candidate fills" value={formatInteger(result.candidateFills)} />
              <RetentionMetric label="Deleted fills" value={formatInteger(result.deletedFills)} />
              <RetentionMetric label="Deleted trades" value={formatInteger(result.deletedSourceTrades)} />
              <RetentionMetric label="Deleted ignored" value={formatInteger(result.deletedIgnoredFills)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <RetentionMetric
                label="Candidate trades"
                value={formatInteger(result.candidateSourceTrades)}
              />
              <RetentionMetric
                label="Candidate ignored"
                value={formatInteger(result.candidateIgnoredFills)}
              />
              <RetentionMetric label="Affected wallets" value={formatInteger(result.affectedWallets)} />
              <RetentionMetric
                label="Remaining fills"
                value={formatInteger(result.remainingCandidateFills)}
              />
            </div>
            <p className="text-sm leading-6 text-[#5b6770]">{result.note}</p>
          </div>
        ) : (
          <p className="text-sm leading-6 text-[#5b6770]">
            Default retention keeps 90 days and protects active, realtime, copy-enabled, open
            paper-position, open-position, and top scored wallets.
          </p>
        )}
      </div>
    </section>
  );
}

function RetentionMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
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
