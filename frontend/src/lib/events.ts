import type { Tone } from "@/components/ui/tone";
import type { LiveEvent } from "@/types/event";

export type EventCategory = "trade" | "copy" | "job" | "risk" | "system";

export const EVENT_CATEGORY_LABELS: Record<EventCategory, string> = {
  copy: "Copy",
  job: "Jobs",
  risk: "Risk",
  system: "System",
  trade: "Source trades",
};

/** Groups backend event types so the feed can be filtered by what they are about. */
export function eventCategory(event: Pick<LiveEvent, "type" | "channel">): EventCategory {
  const type = event.type;
  if (type === "fill" || type === "fill_snapshot" || (event.channel === "events:fills" && !type.includes("copy"))) {
    return "trade";
  }
  if (type.includes("copy") || type.startsWith("realtime_execution")) {
    return "copy";
  }
  if (
    type.startsWith("discovery_import") ||
    type.startsWith("pool_fill_import") ||
    type.startsWith("wallet_scoring") ||
    type.startsWith("wallet_prune")
  ) {
    return "job";
  }
  if (type.startsWith("live_") || type.includes("risk")) {
    return "risk";
  }
  return "system";
}

export function eventTone(event: Pick<LiveEvent, "type">): Tone {
  const type = event.type;
  if (type.endsWith("_error") || type.includes("failed") || type.includes("risk_trip") || type.includes("queue_full")) {
    return "negative";
  }
  if (type.includes("retry") || type.includes("partial") || type.includes("deferred") || type.includes("external_exposure")) {
    return "warning";
  }
  if (type === "paper_copy" || type === "live_copy") {
    return "positive";
  }
  if (type === "fill") {
    return "info";
  }
  return "neutral";
}

export function eventTypeLabel(type: string) {
  const labels: Record<string, string> = {
    fill: "Source trade",
    fill_snapshot: "Snapshot",
    paper_copy: "Paper copy",
    paper_copy_error: "Paper copy error",
    system: "System",
  };
  if (labels[type]) {
    return labels[type];
  }
  const text = type.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function eventWallet(event: LiveEvent): string | null {
  const wallet = event.payload.walletAddress ?? event.payload.sourceWallet;
  return typeof wallet === "string" && wallet.startsWith("0x") ? wallet : null;
}

export function eventKey(event: LiveEvent, index: number) {
  return event.id ?? `${event.type}:${event.createdAt ?? ""}:${event.message}:${index}`;
}

export function dedupeEvents(events: LiveEvent[]) {
  const seen = new Set<string>();
  const result: LiveEvent[] = [];
  for (const event of events) {
    const key = event.id ?? `${event.type}:${event.createdAt}:${event.message}`;
    if (!seen.has(key)) {
      seen.add(key);
      result.push(event);
    }
  }
  return result;
}
