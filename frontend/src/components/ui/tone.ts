export type Tone = "neutral" | "positive" | "negative" | "warning" | "info";

export const toneText: Record<Tone, string> = {
  info: "text-info",
  negative: "text-negative",
  neutral: "text-fg-2",
  positive: "text-positive",
  warning: "text-warning",
};

export const toneDot: Record<Tone, string> = {
  info: "bg-info",
  negative: "bg-negative",
  neutral: "bg-fg-4",
  positive: "bg-positive",
  warning: "bg-warning",
};

export const toneSoft: Record<Tone, string> = {
  info: "bg-info/12 text-info",
  negative: "bg-negative/12 text-negative",
  neutral: "bg-surface-3 text-fg-2",
  positive: "bg-positive/12 text-positive",
  warning: "bg-warning/14 text-warning",
};

export const toneBar: Record<Tone, string> = {
  info: "bg-info",
  negative: "bg-negative",
  neutral: "bg-fg-3",
  positive: "bg-positive",
  warning: "bg-warning",
};

/** Green for gains, red for losses, neutral for zero or missing values. */
export function signTone(value: number | null | undefined): Tone {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) {
    return "neutral";
  }
  return value > 0 ? "positive" : "negative";
}

/** Maps a backend health or job status to a tone. */
export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "ok":
    case "succeeded":
    case "running_ok":
    case "connected":
    case "live":
      return "positive";
    case "running":
    case "connecting":
      return "info";
    case "warning":
    case "missing":
    case "not_configured":
    case "stale":
    case "partial":
      return "warning";
    case "degraded":
    case "error":
    case "failed":
    case "offline":
    case "disconnected":
      return "negative";
    default:
      return "neutral";
  }
}
