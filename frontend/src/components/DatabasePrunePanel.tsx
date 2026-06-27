"use client";

import { Search, Scissors, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
  formatScore,
} from "@/lib/format";
import type { WalletPruneAllResponse, WalletPruneCandidate } from "@/types/prune";

import { StatusPill } from "./StatusPill";

type BusyMode = "dry-run" | "run" | null;

export function DatabasePrunePanel() {
  const router = useRouter();
  const [result, setResult] = useState<WalletPruneAllResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyMode, setBusyMode] = useState<BusyMode>(null);

  async function runPrune(dryRun: boolean) {
    if (!dryRun) {
      const confirmed = window.confirm(
        "Run all configured prune rules now? Matching wallets and related rows will be deleted.",
      );
      if (!confirmed) {
        return;
      }
    }

    setError(null);
    setBusyMode(dryRun ? "dry-run" : "run");

    try {
      const response = await fetch(
        `${getPublicApiBaseUrl()}/wallets/prune-all?dry_run=${dryRun}&limit=1000`,
        { method: "POST" },
      );
      const responseText = await response.text();
      const payload = parseJson(responseText) as
        | WalletPruneAllResponse
        | { detail?: unknown }
        | null;

      if (!response.ok) {
        const fallbackError = responseText.trim() || "Could not run prune.";
        setError(errorDetail(payload) ?? fallbackError);
        return;
      }

      setResult(payload as WalletPruneAllResponse);
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
          <Scissors className="mt-0.5 h-4 w-4 shrink-0 text-[#5b6770]" aria-hidden="true" />
          <div>
            <h2 className="text-base font-semibold">Manual Prune</h2>
            <p className="mt-1 text-sm leading-6 text-[#5b6770]">
              Runs all active cleanup rules in order: orphan fills, zero-fill, stale fills,
              minimum closed trades, realized drawdown, low-score, then current drawdown.
            </p>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            data-testid="manual-prune-all-dry-run"
            disabled={busyMode !== null}
            onClick={() => void runPrune(true)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm font-medium text-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
            {busyMode === "dry-run" ? "Checking" : "Dry run"}
          </button>
          <button
            type="button"
            data-testid="manual-prune-all-run"
            disabled={busyMode !== null}
            onClick={() => void runPrune(false)}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-[#efb1aa] bg-[#fff5f3] px-3 text-sm font-medium text-danger disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            {busyMode === "run" ? "Pruning" : "Run prune"}
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
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
              <PruneMetric label="Mode" value={result.dryRun ? "Dry run" : "Executed"} />
              <PruneMetric label="Scanned" value={formatInteger(result.scannedWallets)} />
              <PruneMetric label="Candidates" value={formatInteger(result.candidateWallets)} />
              <PruneMetric label="Errors" value={formatInteger(result.erroredWallets)} />
              <PruneMetric label="Deleted wallets" value={formatInteger(result.deletedWallets)} />
              <PruneMetric label="Deleted fills" value={formatInteger(result.deletedFills)} />
            </div>

            <div className="grid gap-4 xl:grid-cols-3">
              {result.rules.map((rule) => (
                <RuleResult key={rule.key} rule={rule} />
              ))}
            </div>
          </div>
        ) : (
          <p className="text-sm leading-6 text-[#5b6770]">
            Use dry run first. Prune settings come from backend/config/prune.json and are loaded at
            backend startup.
          </p>
        )}
      </div>
    </section>
  );
}

function RuleResult({ rule }: { rule: WalletPruneAllResponse["rules"][number] }) {
  const candidates = rule.items;

  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold">{rule.label}</h3>
          <p className="mt-1 text-sm text-[#5b6770]">{rule.rule}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <StatusPill label={`${rule.candidateWallets} candidates`} tone="neutral" />
          {rule.erroredWallets > 0 ? (
            <StatusPill label={`${rule.erroredWallets} errors`} tone="warning" />
          ) : null}
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <SmallMetric label="Scanned" value={formatInteger(rule.scannedWallets)} />
        <SmallMetric label="Deleted" value={formatInteger(rule.deletedWallets)} />
      </div>

      <div className="mt-3 max-h-96 overflow-y-auto divide-y divide-line rounded-md border border-line bg-white px-3">
        {candidates.length === 0 ? (
          <p className="py-4 text-sm text-[#5b6770]">No wallets matched.</p>
        ) : (
          candidates.map((item) => <CandidateRow key={item.address} item={item} />)
        )}
      </div>
    </div>
  );
}

function CandidateRow({ item }: { item: WalletPruneCandidate }) {
  return (
    <div className="py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold">{item.label || shortAddress(item.address)}</p>
          <p className="font-mono text-xs text-[#5b6770]">{shortAddress(item.address)}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-xs font-semibold text-ink">{candidateScore(item)}</p>
          <p className="mt-1 font-mono text-xs text-[#5b6770]">{candidateValue(item)}</p>
        </div>
      </div>
      <p className="mt-2 text-xs text-[#5b6770]">{candidateDetail(item)}</p>
    </div>
  );
}

function candidateValue(item: WalletPruneCandidate) {
  if (item.error) {
    return "error";
  }
  if (item.totalUnrealizedPnlUsd) {
    return formatCurrency(item.totalUnrealizedPnlUsd);
  }
  if (item.maxDrawdownPct) {
    return `${formatPercent(item.maxDrawdownPct)} realized DD`;
  }
  if (item.closedTradeCount !== null && item.closedTradeCount !== undefined) {
    return `${formatInteger(item.closedTradeCount)} trades`;
  }
  return `${formatInteger(item.fillCount ?? 0)} fills`;
}

function candidateScore(item: WalletPruneCandidate) {
  if (item.error) {
    return "not checked";
  }
  return `score ${formatScore(item.score)}`;
}

function candidateDetail(item: WalletPruneCandidate) {
  if (item.detail) {
    return item.detail;
  }
  if (item.lastSeenFillAt) {
    return `Last fill ${formatDate(item.lastSeenFillAt)}`;
  }
  if (item.lastPolledAt) {
    return `Last poll ${formatDate(item.lastPolledAt)}`;
  }
  return "No activity timestamp.";
}

function PruneMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 break-words text-lg font-semibold leading-snug">{value}</p>
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  );
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
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
        if (!item || typeof item !== "object") {
          return String(item);
        }
        const message = (item as { msg?: unknown }).msg;
        return typeof message === "string" ? message : JSON.stringify(item);
      })
      .join(" ");
  }
  return null;
}

function parseJson(value: string) {
  if (!value) {
    return null;
  }
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}
