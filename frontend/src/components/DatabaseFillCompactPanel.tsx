"use client";

import { Archive, Search, Zap } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/api";
import { formatBytes, formatInteger } from "@/lib/format";
import type { FillRawJsonCompactResponse } from "@/types/database";

type BusyMode = "dry-run" | "run" | null;

export function DatabaseFillCompactPanel() {
  const router = useRouter();
  const [result, setResult] = useState<FillRawJsonCompactResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyMode, setBusyMode] = useState<BusyMode>(null);

  async function runCompact(dryRun: boolean) {
    if (!dryRun) {
      const confirmed = window.confirm(
        "Compact old fill raw_json payloads now? This rewrites old wallet_fills rows in batches.",
      );
      if (!confirmed) {
        return;
      }
    }

    setError(null);
    setBusyMode(dryRun ? "dry-run" : "run");

    try {
      const response = await fetch(
        `${getPublicApiBaseUrl()}/database/fills/compact-raw-json?dry_run=${dryRun}&batch_size=5000&max_rows=50000`,
        { method: "POST" },
      );
      const payload = (await response.json().catch(() => null)) as
        | FillRawJsonCompactResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        setError(errorDetail(payload) ?? "Could not compact fills.");
        return;
      }

      setResult(payload as FillRawJsonCompactResponse);
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
          <Archive className="mt-0.5 h-4 w-4 shrink-0 text-[#5b6770]" aria-hidden="true" />
          <div>
            <h2 className="text-base font-semibold">Compact Fill Payloads</h2>
            <p className="mt-1 text-sm leading-6 text-[#5b6770]">
              Rewrites old fill raw_json payloads to the current compact field set used by new
              imports.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCompact(true)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
            {busyMode === "dry-run" ? "Checking" : "Dry run"}
          </button>
          <button
            type="button"
            disabled={busyMode !== null}
            onClick={() => void runCompact(false)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[#9ccfc0] bg-[#f2fbf7] px-3 text-sm font-medium text-positive disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Zap className="h-4 w-4" aria-hidden="true" />
            {busyMode === "run" ? "Compacting" : "Run compact"}
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
              <CompactMetric label="Mode" value={result.dryRun ? "Dry run" : "Executed"} />
              <CompactMetric label="Candidates" value={formatInteger(result.candidateFills)} />
              <CompactMetric label="Processed" value={formatInteger(result.processedFills)} />
              <CompactMetric
                label="Remaining"
                value={formatInteger(result.remainingCandidates)}
              />
              <CompactMetric label="Saved" value={formatBytes(result.savedRawJsonBytes)} />
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <CompactMetric label="Before raw_json" value={formatBytes(result.beforeRawJsonBytes)} />
              <CompactMetric label="After raw_json" value={formatBytes(result.afterRawJsonBytes)} />
              <CompactMetric label="Kept fields" value={result.keptFields.join(", ") || "-"} />
            </div>
            <p className="text-sm leading-6 text-[#5b6770]">{result.note}</p>
          </div>
        ) : (
          <p className="text-sm leading-6 text-[#5b6770]">
            Use dry run first. Run compact processes up to 50 000 old fills per click.
          </p>
        )}
      </div>
    </section>
  );
}

function CompactMetric({ label, value }: { label: string; value: string }) {
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
