"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { ReactNode } from "react";

import { useConfirm } from "@/components/ui/client";
import { buttonClass, Callout, KeyValues, Panel, Spinner } from "@/components/ui/primitives";
import { formatBytes, formatDateTime, formatInteger, formatPercent, formatScore, formatUsd, shortAddress } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import type {
  FillRawJsonCompactResponse,
  FillRetentionCleanupResponse,
  IgnoredFillCleanupResponse,
} from "@/types/database";
import type { WalletPruneAllResponse, WalletPruneCandidate } from "@/types/prune";

type TaskDefinition<T> = {
  confirmBody: string;
  confirmLabel: string;
  description: string;
  endpoint: (dryRun: boolean) => string;
  idleHint: string;
  render: (result: T) => ReactNode;
  runLabel: string;
  title: string;
};

/** A cleanup task with a dry run preview and a confirmed real run. */
function MaintenanceTask<T extends { dryRun: boolean }>({ task }: { task: TaskDefinition<T> }) {
  const router = useRouter();
  const confirm = useConfirm();
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"preview" | "run" | null>(null);

  const execute = async (dryRun: boolean) => {
    if (!dryRun) {
      const confirmed = await confirm({
        body: task.confirmBody,
        confirmLabel: task.confirmLabel,
        title: `${task.runLabel}?`,
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
    }
    setBusy(dryRun ? "preview" : "run");
    setError(null);
    try {
      const payload = await apiRequest<T>(task.endpoint(dryRun), { fallback: `${task.title} failed`, method: "POST" });
      setResult(payload);
      if (!dryRun) {
        router.refresh();
      }
    } catch (taskError) {
      setError(errorMessage(taskError, `${task.title} failed.`));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel
      title={task.title}
      description={task.description}
      actions={
        <>
          <button type="button" className={buttonClass({ size: "sm" })} disabled={busy !== null} onClick={() => void execute(true)}>
            {busy === "preview" ? <Spinner /> : null}
            Preview
          </button>
          <button
            type="button"
            className={buttonClass({ size: "sm", variant: "danger" })}
            disabled={busy !== null}
            onClick={() => void execute(false)}
          >
            {busy === "run" ? <Spinner /> : null}
            {task.runLabel}
          </button>
        </>
      }
    >
      <div className="px-3.5 py-3">
        {error ? <Callout tone="negative" title={error} className="mb-3" /> : null}
        {result ? (
          <>
            <p className="mb-2 text-xs font-medium text-fg-3">
              {result.dryRun ? "Preview: nothing was changed." : "Done. The changes below were applied."}
            </p>
            {task.render(result)}
          </>
        ) : (
          <p className="text-sm text-fg-3">{task.idleHint}</p>
        )}
      </div>
    </Panel>
  );
}

const PRUNE: TaskDefinition<WalletPruneAllResponse> = {
  confirmBody: "Wallets that match a prune rule are archived and orphan fills are deleted. Archived wallets keep their history.",
  confirmLabel: "Prune now",
  description:
    "Archives wallets that fail a rule: orphan fills, no fills, stale fills, too few closed trades, deep realized drawdown, then low score.",
  endpoint: (dryRun) => `/wallets/prune-all?dry_run=${dryRun}&limit=1000`,
  idleHint: "Rules come from backend/config/prune.json and load at backend startup. Preview first.",
  render: (result) => (
    <div className="grid gap-3">
      <KeyValues
        className="max-w-md"
        items={[
          { label: "Wallets scanned", value: formatInteger(result.scannedWallets) },
          { label: "Matching a rule", value: formatInteger(result.candidateWallets) },
          { label: "Archived", value: formatInteger(result.archivedWallets ?? 0) },
          { label: "Orphan fills deleted", value: formatInteger(result.deletedFills) },
          { label: "Errors", tone: result.erroredWallets > 0 ? "warning" : "neutral", value: formatInteger(result.erroredWallets) },
        ]}
      />
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="data-table">
          <thead>
            <tr>
              <th>Rule</th>
              <th className="num">Scanned</th>
              <th className="num">Matching</th>
              <th className="num">Archived</th>
              <th>Wallets</th>
            </tr>
          </thead>
          <tbody>
            {result.rules.map((rule) => (
              <tr key={rule.key}>
                <td className="wrap max-w-[280px]">
                  <span className="font-medium">{rule.label}</span>
                  <span className="sub">{rule.rule}</span>
                </td>
                <td className="num">{formatInteger(rule.scannedWallets)}</td>
                <td className="num">{formatInteger(rule.candidateWallets)}</td>
                <td className="num">{formatInteger(rule.archivedWallets ?? 0)}</td>
                <td className="wrap max-w-[420px] text-xs text-fg-2">
                  {rule.items.length === 0 ? (
                    <span className="text-fg-4">none</span>
                  ) : (
                    rule.items.slice(0, 12).map((item) => candidateText(item)).join(", ") +
                    (rule.items.length > 12 ? `, and ${rule.items.length - 12} more` : "")
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  ),
  runLabel: "Run prune",
  title: "Prune the wallet pool",
};

function candidateText(item: WalletPruneCandidate) {
  const name = item.label || shortAddress(item.address);
  if (item.error) {
    return `${name} (not checked)`;
  }
  if (item.maxDrawdownPct) {
    return `${name} (${formatPercent(item.maxDrawdownPct)} drawdown)`;
  }
  if (item.totalUnrealizedPnlUsd) {
    return `${name} (${formatUsd(item.totalUnrealizedPnlUsd)} open)`;
  }
  if (item.score) {
    return `${name} (score ${formatScore(item.score)})`;
  }
  return name;
}

const RETENTION: TaskDefinition<FillRetentionCleanupResponse> = {
  confirmBody: "Old fills of unprotected wallets and the trades rebuilt from them are deleted in batches. This cannot be undone.",
  confirmLabel: "Delete old history",
  description: "Deletes old fill history of unprotected wallets, with the trades rebuilt from it, in safe batches.",
  endpoint: (dryRun) => `/database/fills/retention-cleanup?dry_run=${dryRun}`,
  idleHint:
    "Keeps 90 days by default and always protects active, realtime, copy enabled, top scored wallets, and wallets with open positions.",
  render: (result) => (
    <div className="grid gap-x-8 sm:grid-cols-2">
      <KeyValues
        items={[
          { label: "Retention", value: `${formatInteger(result.retentionDays)} days` },
          { label: "Older than", value: formatDateTime(result.cutoffTimeMs) },
          { label: "Protected wallets", value: formatInteger(result.protectedWallets) },
          { label: "Wallets affected", value: formatInteger(result.candidateWallets) },
          { label: "Still to delete", value: formatInteger(result.remainingCandidateFills) },
        ]}
      />
      <KeyValues
        items={[
          { label: "Fills", value: `${formatInteger(result.deletedFills)} of ${formatInteger(result.candidateFills)}` },
          { label: "Rebuilt trades", value: `${formatInteger(result.deletedSourceTrades)} of ${formatInteger(result.candidateSourceTrades)}` },
          { label: "Ignored fill markers", value: `${formatInteger(result.deletedIgnoredFills)} of ${formatInteger(result.candidateIgnoredFills)}` },
          { label: "Batch size", value: formatInteger(result.batchSize) },
        ]}
      />
      <p className="mt-2 text-xs text-fg-3 sm:col-span-2">{result.note}</p>
    </div>
  ),
  runLabel: "Delete old history",
  title: "Fill retention",
};

const IGNORED: TaskDefinition<IgnoredFillCleanupResponse> = {
  confirmBody: "Raw close-only and pre-existing position fills that no rebuilt trade needs are deleted. This cannot be undone.",
  confirmLabel: "Delete ignored fills",
  description: "Deletes raw fills that never become part of a rebuilt trade, such as closes of positions opened before the history starts.",
  endpoint: (dryRun) => `/database/fills/ignored-cleanup?dry_run=${dryRun}`,
  idleHint: "Fills that could have closed a rebuilt trade are always kept.",
  render: (result) => (
    <div className="grid gap-x-8 sm:grid-cols-2">
      <KeyValues
        items={[
          { label: "Older than", value: `${formatInteger(result.minAgeDays)} days (${formatDateTime(result.cutoffTimeMs)})` },
          { label: "Pre-existing opens", value: formatInteger(result.candidatePreexistingOpenFills) },
          { label: "Close-only fills", value: formatInteger(result.candidateUnmatchedCloseFills) },
          { label: "Kept as possible closes", value: formatInteger(result.excludedPotentialTradeCloseFills) },
        ]}
      />
      <KeyValues
        items={[
          { label: "Fills", value: `${formatInteger(result.deletedFills)} of ${formatInteger(result.candidateFills)}` },
          { label: "Markers deleted", value: formatInteger(result.deletedIgnoredFillMarkers) },
          { label: "Wallets affected", value: formatInteger(result.affectedWallets) },
          { label: "Still to delete", value: formatInteger(result.remainingCandidateFills) },
        ]}
      />
      <p className="mt-2 text-xs text-fg-3 sm:col-span-2">{result.note}</p>
    </div>
  ),
  runLabel: "Delete ignored fills",
  title: "Ignored fill cleanup",
};

const COMPACT: TaskDefinition<FillRawJsonCompactResponse> = {
  confirmBody: "Old fill rows are rewritten in batches with only the fields new imports keep.",
  confirmLabel: "Compact payloads",
  description: "Shrinks the stored raw payload of old fills to the fields that current imports keep.",
  endpoint: (dryRun) => `/database/fills/compact-raw-json?dry_run=${dryRun}&batch_size=5000&max_rows=50000`,
  idleHint: "Each run processes up to 50,000 old fills.",
  render: (result) => (
    <div className="grid gap-x-8 sm:grid-cols-2">
      <KeyValues
        items={[
          { label: "Fills to compact", value: formatInteger(result.candidateFills) },
          { label: "Processed", value: formatInteger(result.processedFills) },
          { label: "Still to compact", value: formatInteger(result.remainingCandidates) },
        ]}
      />
      <KeyValues
        items={[
          { label: "Payload before", value: formatBytes(result.beforeRawJsonBytes) },
          { label: "Payload after", value: formatBytes(result.afterRawJsonBytes) },
          { label: "Saved", tone: "positive", value: formatBytes(result.savedRawJsonBytes) },
        ]}
      />
      <p className="mt-2 text-xs text-fg-3 sm:col-span-2">
        Kept fields: {result.keptFields.join(", ") || "-"}. {result.note}
      </p>
    </div>
  ),
  runLabel: "Compact payloads",
  title: "Compact fill payloads",
};

export function MaintenanceTasks() {
  return (
    <div className="grid gap-4">
      <MaintenanceTask task={PRUNE} />
      <div className="grid gap-4 2xl:grid-cols-3">
        <MaintenanceTask task={RETENTION} />
        <MaintenanceTask task={IGNORED} />
        <MaintenanceTask task={COMPACT} />
      </div>
    </div>
  );
}
