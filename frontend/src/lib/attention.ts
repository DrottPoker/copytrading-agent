import type { Tone } from "@/components/ui/tone";
import type { OpsHealthResponse } from "@/types/ops";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type { TradingAccountsResponse } from "@/types/trading";

import { formatBytes, formatPercent, formatSeconds } from "./format";
import { buildLiveAccountNotice } from "./trading/live";

export type AttentionItem = {
  detail: string;
  href: string;
  title: string;
  tone: Tone;
};

/**
  Collects everything that needs a human: failing infrastructure, stopped accounts, and stale data.
  Problems come first, warnings after.
*/
export function buildAttentionItems({
  ops,
  summary,
  tradingAccounts,
}: {
  ops: OpsHealthResponse | null;
  summary: PaperTradingSummaryResponse;
  tradingAccounts: TradingAccountsResponse;
}): AttentionItem[] {
  const items: AttentionItem[] = [];

  if (!ops) {
    items.push({
      detail: "The operations health endpoint did not respond, so worker and disk checks are unknown.",
      href: "/system",
      title: "Health checks unavailable",
      tone: "negative",
    });
  } else {
    for (const [name, dependency] of Object.entries(ops.dependencies)) {
      if (dependency.status !== "ok") {
        items.push({
          detail: dependency.detail ?? `Status ${dependency.status}.`,
          href: "/system",
          title:
            dependency.status === "error"
              ? `${capitalize(name)} is unreachable`
              : `${capitalize(name)} is ${dependency.status.replaceAll("_", " ")}`,
          tone: "negative",
        });
      }
    }
    for (const worker of ops.workers) {
      if (worker.status !== "ok") {
        items.push({
          detail:
            worker.ageSeconds !== null
              ? `Last heartbeat ${formatSeconds(worker.ageSeconds)} ago.`
              : "No heartbeat recorded.",
          href: "/system",
          title: `${capitalize(worker.role)} worker is ${worker.status.replaceAll("_", " ")}`,
          tone: worker.status === "error" || worker.status === "stale" ? "negative" : "warning",
        });
      }
      const failingLoops = worker.loops.filter((loop) => loop.health === "error" || loop.consecutiveFailures > 0);
      for (const loop of failingLoops) {
        items.push({
          detail: loop.lastError ?? `${loop.consecutiveFailures} failures in a row.`,
          href: "/system",
          title: `Worker loop ${loop.name} is failing`,
          tone: "negative",
        });
      }
      if (worker.realtimeQueue && worker.realtimeQueue.dropped > 0) {
        items.push({
          detail: `${worker.realtimeQueue.dropped} realtime events were dropped because the queue was full.`,
          href: "/system",
          title: "Realtime queue dropped events",
          tone: "negative",
        });
      }
    }
    if (ops.disk.status !== "ok") {
      items.push({
        detail: `${formatPercent(ops.disk.usagePct)} used, ${formatBytes(ops.disk.freeBytes)} free on ${ops.disk.path}.`,
        href: "/system",
        title: "Disk is filling up",
        tone: ops.disk.status === "error" ? "negative" : "warning",
      });
    }
    if (ops.memory.status === "warning" || ops.memory.status === "error") {
      items.push({
        detail: `${formatPercent(ops.memory.usagePct)} of memory in use.`,
        href: "/system",
        title: "Memory pressure",
        tone: ops.memory.status === "error" ? "negative" : "warning",
      });
    }
    if (ops.backup.status === "missing" || ops.backup.status === "stale" || ops.backup.status === "error") {
      items.push({
        detail:
          ops.backup.status === "missing"
            ? `No backup files found in ${ops.backup.directory}.`
            : `The latest backup is ${formatSeconds(ops.backup.latestAgeSeconds)} old.`,
        href: "/system",
        title: ops.backup.status === "missing" ? "No database backups" : "Backups are stale",
        tone: "warning",
      });
    }
    for (const operation of ops.operations) {
      if (operation.status === "failed") {
        items.push({
          detail: operation.lastError ?? "The last run failed.",
          href: "/#jobs",
          title: `${operation.label} failed`,
          tone: "negative",
        });
      }
    }
    for (const source of ops.discoverySources ?? []) {
      if (source.consecutiveFailures > 0) {
        items.push({
          detail: source.lastError ?? `${source.consecutiveFailures} failed runs in a row.`,
          href: "/system",
          title: `Discovery source ${source.source} is failing`,
          tone: "warning",
        });
      }
    }
    for (const signer of ops.liveSigners ?? []) {
      if (signer.required && signer.status !== "api_wallet") {
        items.push({
          detail: signer.description,
          href: "/system",
          title: `Signing key for ${signer.accountKey} is not an API wallet`,
          tone: "negative",
        });
      }
    }
    for (const note of ops.notes) {
      items.push({ detail: note, href: "/system", title: "Operations note", tone: "warning" });
    }
  }

  for (const account of summary.accounts) {
    if (account.exitOnlyAt) {
      items.push({
        detail: account.exitOnlyReason ?? "A loss limit was reached. Existing positions can still close.",
        href: `/trading?account=${encodeURIComponent(account.key)}`,
        title: `${account.label} is exit only`,
        tone: "warning",
      });
    }
  }

  for (const account of tradingAccounts.accounts.filter((item) => item.accountType === "live")) {
    const notice = buildLiveAccountNotice(
      account,
      tradingAccounts.riskLimits.reconciliationMaxSnapshotAgeSeconds,
      tradingAccounts.updatedAt,
    );
    if (notice && notice.tone !== "neutral") {
      items.push({
        detail: notice.detail,
        href: `/trading?account=${encodeURIComponent(account.key)}`,
        title: `${account.label}: ${notice.title}`,
        tone: notice.tone,
      });
    }
  }

  const tradingAccountsEnabled = summary.accounts.some((account) => account.enabled);
  if (tradingAccountsEnabled && summary.realtimeMonitoring.status === "disconnected") {
    items.push({
      detail: "The trading worker is not subscribed to the source wallets, so new trades are not copied.",
      href: "/system",
      title: "Realtime feed disconnected",
      tone: "negative",
    });
  }

  const order: Record<Tone, number> = { info: 2, negative: 0, neutral: 3, positive: 4, warning: 1 };
  return items.sort((left, right) => order[left.tone] - order[right.tone]);
}

function capitalize(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1);
}
