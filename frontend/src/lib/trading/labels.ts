import type { Tone } from "@/components/ui/tone";
import { humanize } from "@/lib/format";

export const LIVE_EXCHANGE_SOURCE = "__exchange__";

export function isLiveExchangeSource(sourceWallet: string) {
  return sourceWallet === LIVE_EXCHANGE_SOURCE;
}

const REASON_LABELS: Record<string, string> = {
  live_exit_market_owned_by_other_source: "exit ignored: position owned by another source wallet",
  live_market_reserved_by_other_source: "entry blocked: market owned by another source wallet",
  live_source_attribution_ambiguous: "ownership could not be proven from live fill history",
};

/** Lower case explanation of a backend reason code, used inside sentences and cells. */
export function reasonLabel(value: string) {
  return REASON_LABELS[value] ?? value.replaceAll("_", " ");
}

/** Sentence case version of a skip or block reason, used as a standalone label. */
export function reasonTitle(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  const label = reasonLabel(value.replace(/^skip:/, ""));
  return label.charAt(0).toUpperCase() + label.slice(1);
}

export function liveOrderStatusTone(status: string): Tone {
  if (status === "filled" || status === "accepted") {
    return "positive";
  }
  if (
    status === "planned" ||
    status === "ready" ||
    status === "submitting" ||
    status === "uncertain" ||
    status === "submitted" ||
    status === "partially_filled"
  ) {
    return "warning";
  }
  if (status === "rejected" || status === "failed" || status === "canceled") {
    return "negative";
  }
  return "neutral";
}

export function formatCloseType(value: string) {
  if (value === "flip_close") {
    return "Flip close";
  }
  return humanize(value);
}

export function formatCapitalMode(value: string | null | undefined) {
  if (value === "standard_per_dex") {
    return "Standard per DEX";
  }
  if (value === "unified") {
    return "Unified";
  }
  return "Unknown";
}

const ACTION_LABELS: Record<string, string> = {
  add: "Add",
  close: "Close",
  flip_close: "Flip close",
  flip_open: "Flip open",
  open: "Open",
  reduce: "Reduce",
  skip: "Skipped",
};

export function actionLabel(action: string) {
  return ACTION_LABELS[action] ?? humanize(action);
}

export function sourceDisplayName(label: string | null | undefined, address: string) {
  if (isLiveExchangeSource(address)) {
    return label?.trim() || "Exchange position";
  }
  const trimmed = label?.trim();
  return trimmed || `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export function walletHref(address: string) {
  return isLiveExchangeSource(address) ? null : `/wallets/${address}`;
}

/** Plain-language source status reasons from the allocation engine. */
export function formatSourceStatusReason(reason: string | null) {
  const labels: Record<string, string> = {
    active_copy_source: "active copy source",
    allocation_inactive: "allocation inactive",
    allocation_missing: "allocation missing",
    copy_candidate: "active copy source",
    current_drawdown_blocked: "blocked by the wallet's current drawdown",
    existing_exposure_only: "managing existing exposure only",
    live_copy_disabled: "live copy disabled",
    live_copy_ready: "live copy ready",
    live_existing_exposure_only: "managing existing live exposure only",
    live_no_enabled_accounts: "no enabled live accounts",
    live_trading_disabled: "live trading is turned off",
    outside_copy_top_wallet_count: "no longer in the top ranked wallets",
    paper_account_disabled: "paper account stopped",
    score_not_positive: "score is not positive",
    score_unavailable: "score unavailable",
    waiting_for_realtime_slot: "waiting for a realtime slot",
    wallet_cooldown: "wallet in cooldown",
    wallet_disabled_or_missing: "wallet disabled or missing",
  };
  return (reason && labels[reason]) || "managing existing exposure only";
}
