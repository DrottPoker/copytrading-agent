import type { Tone } from "@/components/ui/tone";
import type { Wallet, WalletScore } from "@/types/wallet";

import { decimal, formatDateTime, humanize } from "./format";

export type ScoreComponentKey = "pnl" | "consistency" | "risk" | "copyability" | "recency";

export const SCORE_COMPONENTS: Array<{
  description: string;
  key: ScoreComponentKey;
  label: string;
  short: string;
  value: (score: WalletScore) => string;
}> = [
  {
    description: "Time-weighted return on the wallet's own equity, plus its average and median trade ROI.",
    key: "pnl",
    label: "Profitability",
    short: "Profit",
    value: (score) => score.pnlScore,
  },
  {
    description: "How evenly profits are spread, how stable returns and downside are, and how regularly it trades.",
    key: "consistency",
    label: "Consistency",
    short: "Consist.",
    value: (score) => score.consistencyScore,
  },
  {
    description:
      "Loss ratio, equity drawdown, adding to losing positions, current drawdown, margin stress, and losing trade rate.",
    key: "risk",
    label: "Risk control",
    short: "Risk",
    value: (score) => score.riskScore,
  },
  {
    description: "Share of the wallet's trades that still reach the exchange minimum order at our copy size.",
    key: "copyability",
    label: "Copyability",
    short: "Copy",
    value: (score) => score.copyabilityScore,
  },
  {
    description: "How recently the wallet made a real trade (liquidations do not count).",
    key: "recency",
    label: "Recency",
    short: "Recent",
    value: (score) => score.recencyScore,
  },
];

export type WalletStatus = { detail: string; label: string; tone: Tone };

/** One status per wallet, in the order that matters most to the user. */
export function walletStatus(wallet: Wallet, followedLive: Set<string>): WalletStatus {
  if (wallet.archivedAt) {
    return {
      detail: `Archived ${formatDateTime(wallet.archivedAt)}${wallet.archiveReason ? `: ${humanize(wallet.archiveReason)}` : ""}`,
      label: "Archived",
      tone: "neutral",
    };
  }
  if (!wallet.enabled) {
    return { detail: "Disabled by hand. Not polled, scored, or copied.", label: "Disabled", tone: "neutral" };
  }
  if (wallet.pollingTier === "cooldown" || wallet.cooldownUntil) {
    return {
      detail: wallet.cooldownUntil ? `In cooldown until ${formatDateTime(wallet.cooldownUntil)}` : "In cooldown",
      label: "Cooldown",
      tone: "warning",
    };
  }
  if (followedLive.has(wallet.address.toLowerCase())) {
    return { detail: "Followed in realtime and copied by trading accounts.", label: "Copied", tone: "positive" };
  }
  if (!wallet.score) {
    return { detail: "Waiting for its first score.", label: "Unscored", tone: "neutral" };
  }
  return { detail: "In the pool and ranked. Polled periodically.", label: "In pool", tone: "info" };
}

export function walletDisplayName(wallet: Pick<Wallet, "address" | "label">) {
  return wallet.label?.trim() || `${wallet.address.slice(0, 6)}…${wallet.address.slice(-4)}`;
}

export function scoreValue(wallet: Wallet) {
  return wallet.score ? decimal(wallet.score.score) : null;
}

export const ADDRESS_PATTERN = /^0x[a-fA-F0-9]{40}$/;
