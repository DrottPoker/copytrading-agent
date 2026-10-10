import type { Tone } from "@/components/ui/tone";
import {
  dateMs,
  decimal,
  formatDateTime,
  formatDuration,
  formatInteger,
  formatLeverage,
  formatPrice,
  formatSize,
  formatUsd,
  shortAddress,
  shortId,
} from "@/lib/format";
import {
  actionLabel,
  isLiveExchangeSource,
  liveOrderStatusTone,
  reasonLabel,
  reasonTitle,
  sourceDisplayName,
  walletHref,
} from "./labels";
import type { AccountNotice, ExecutionRow, TradeFlag } from "./types";
import type {
  LiveCopyDecision,
  TradingAccount,
  TradingFill,
  TradingOrder,
  TradingPosition,
} from "@/types/trading";

export function liveAccountEquity(account: TradingAccount) {
  return decimal(
    account.equityUsd ?? account.perpEquityUsd ?? account.tradableEquityUsd ?? account.cashBalanceUsd ?? 0,
  );
}

/**
  The exchange reports one position per market. When exactly one source wallet owns that market
  and side, the exchange row takes that owner so the user sees who opened it. Ambiguous ownership
  is never guessed.
*/
export function displayLivePositions(positions: TradingPosition[]) {
  const sourcePositionsByMarket = new Map<string, TradingPosition[]>();
  for (const position of positions) {
    if (isLiveExchangeSource(position.sourceWallet)) {
      continue;
    }
    const key = attributionKey(position);
    sourcePositionsByMarket.set(key, [...(sourcePositionsByMarket.get(key) ?? []), position]);
  }
  const exchangePositionKeys = new Set(
    positions.filter((position) => isLiveExchangeSource(position.sourceWallet)).map(marketKey),
  );
  return positions
    .filter(
      (position) => isLiveExchangeSource(position.sourceWallet) || !exchangePositionKeys.has(marketKey(position)),
    )
    .map((position) => {
      if (!isLiveExchangeSource(position.sourceWallet)) {
        return position;
      }
      const attributed = sourcePositionsByMarket.get(attributionKey(position)) ?? [];
      if (attributed.length !== 1) {
        return position;
      }
      return { ...position, sourceWallet: attributed[0].sourceWallet };
    });
}

function marketKey(position: TradingPosition) {
  return `${position.accountKey.toLowerCase()}:${position.coin.toUpperCase()}`;
}

function attributionKey(position: TradingPosition) {
  return `${marketKey(position)}:${position.side}`;
}

export function liveOrderResultLabel(error: string | null | undefined, submittedAt: string | null) {
  const normalizedError = error?.trim();
  if (!normalizedError) {
    return null;
  }
  if (
    submittedAt &&
    normalizedError.replace(/\.$/, "") === "Live entry intent expired before exchange submission"
  ) {
    return "Live entry retry window expired after an earlier exchange submission attempt.";
  }
  return reasonLabel(normalizedError.replace(/^skip:/, ""));
}

export function liveReconciliationIsStale(
  account: TradingAccount,
  maxSnapshotAgeSeconds: number,
  observedAt: string,
) {
  if (account.reconciliationStatus !== "complete" || !account.lastReconciledAt) {
    return false;
  }
  return dateMs(observedAt) - dateMs(account.lastReconciledAt) > maxSnapshotAgeSeconds * 1000;
}

function liveReconciliationIssue(account: TradingAccount, stale: boolean) {
  if (stale) {
    return `The last complete exchange snapshot is older than the configured limit (last complete ${formatDateTime(account.lastReconciledAt)}).`;
  }
  if (account.reconciliationStatus === "complete") {
    return null;
  }
  const errors = Object.entries(account.reconciliationErrors)
    .map(([component, message]) => `${component}: ${message}`)
    .join(" ");
  if (errors) {
    return `Reconciliation ${account.reconciliationStatus}. ${errors}`;
  }
  if (account.incompleteReconciliationComponents.length > 0) {
    return `Reconciliation ${account.reconciliationStatus}. Incomplete: ${account.incompleteReconciliationComponents.join(", ")}.`;
  }
  return `Reconciliation ${account.reconciliationStatus}.`;
}

/** Explains why a live account is not taking new entries, or null when it is healthy. */
export function buildLiveAccountNotice(
  account: TradingAccount,
  maxSnapshotAgeSeconds: number,
  observedAt: string,
): AccountNotice | null {
  const stale = liveReconciliationIsStale(account, maxSnapshotAgeSeconds, observedAt);
  const issue = liveReconciliationIssue(account, stale);
  if (account.status === "enabled" && (account.reconciliationStatus !== "complete" || stale)) {
    return {
      detail: issue ?? "A complete exchange snapshot is required before new entries can resume.",
      title: stale
        ? "New entries paused: the exchange snapshot is stale"
        : account.reconciliationStatus === "failed"
          ? "New entries paused: reconciliation failed"
          : "New entries paused: reconciliation is incomplete",
      tone: account.reconciliationStatus === "failed" ? "negative" : "warning",
    };
  }
  if (account.status === "exit_only") {
    return {
      detail: `Reason: ${account.statusReason ?? "no lifecycle reason was recorded"}.${issue ? ` ${issue}` : ""}`,
      title: "Exit only: new entries are stopped, existing positions can still close",
      tone: "warning",
    };
  }
  if (account.status === "disabled" && account.statusReason) {
    return {
      detail: `Reason: ${account.statusReason}.`,
      title: "Live account disabled",
      tone: "neutral",
    };
  }
  return null;
}

export function buildLiveExecutionRows(
  fills: TradingFill[],
  orders: TradingOrder[],
  sourceLabels: Map<string, string>,
): ExecutionRow[] {
  const fillOrderIds = new Set(fills.map((fill) => fill.orderId).filter((value): value is string => Boolean(value)));
  const fillSourceKeys = new Set(fills.map(activitySourceKey));
  const fillRows = fills.map((fill) => buildLiveFillExecution(fill, sourceLabels));
  const orderRows = orders
    .filter((order) => !fillOrderIds.has(order.id) && !fillSourceKeys.has(activitySourceKey(order)))
    .map((order) => buildLiveOrderExecution(order, sourceLabels));
  return [...fillRows, ...orderRows].sort((left, right) => dateMs(right.at) - dateMs(left.at)).slice(0, 250);
}

function activitySourceKey(item: TradingFill | TradingOrder) {
  return [item.accountKey, item.sourceWallet, item.sourceFillId ?? "", item.sequenceIndex ?? ""].join(":");
}

function liveSourceName(sourceWallet: string, sourceLabels: Map<string, string>, exchangeLabel: string) {
  return isLiveExchangeSource(sourceWallet)
    ? exchangeLabel
    : sourceDisplayName(sourceLabels.get(sourceWallet.toLowerCase()), sourceWallet);
}

export function buildLiveFillExecution(fill: TradingFill, sourceLabels: Map<string, string>): ExecutionRow {
  const isExchange = isLiveExchangeSource(fill.sourceWallet);
  return {
    action: actionLabel(fill.action),
    at: fill.filledAt,
    coin: fill.coin,
    detail: fill.isLiquidation
      ? "Forced exchange liquidation"
      : fill.sourceFillId
        ? `Source fill ${shortId(fill.sourceFillId)}`
        : "Reconciled from the exchange",
    feeUsd: decimal(fill.feeUsd),
    id: `live:${fill.id}`,
    notionalDetail: `size ${formatSize(fill.size)}`,
    notionalUsd: fill.notionalUsd,
    price: fill.price,
    priceDetail: null,
    realizedPnlUsd: decimal(fill.realizedPnlUsd),
    result: fill.isLiquidation ? { label: "Liquidated", tone: "negative" } : { label: "Filled", tone: "positive" },
    side: fill.side,
    sourceHref: walletHref(fill.sourceWallet),
    sourceLabel: liveSourceName(
      fill.sourceWallet,
      sourceLabels,
      isExchange && fill.isLiquidation ? "Unattributed liquidation" : "Exchange fill",
    ),
    sourceWallet: fill.sourceWallet,
  };
}

function buildLiveOrderExecution(order: TradingOrder, sourceLabels: Map<string, string>): ExecutionRow {
  const error = order.error?.trim();
  const isSkip = order.orderType === "skip" || Boolean(error?.startsWith("skip:"));
  const resultLabel = liveOrderResultLabel(error, order.submittedAt);
  return {
    action: actionLabel(order.action),
    at: order.filledAt ?? order.updatedAt ?? order.createdAt,
    coin: order.coin,
    detail: resultLabel
      ? resultLabel.charAt(0).toUpperCase() + resultLabel.slice(1)
      : order.exchangeOrderId
        ? `Exchange order ${shortId(order.exchangeOrderId)}`
        : `Client order ${shortId(order.clientOrderId)}`,
    feeUsd: decimal(order.feeUsd),
    id: `live-order:${order.id}`,
    notionalDetail: `requested ${formatUsd(order.requestedNotionalUsd)}`,
    notionalUsd: order.filledNotionalUsd,
    price: order.averageFillPrice ?? order.limitPrice,
    priceDetail: order.averageFillPrice ? `limit ${formatPrice(order.limitPrice)}` : formatLeverage(order.leverage, order.marginMode),
    realizedPnlUsd: null,
    result: isSkip
      ? { label: "Skipped", tone: "warning" }
      : { label: reasonTitle(order.status), tone: error ? "negative" : liveOrderStatusTone(order.status) },
    side: order.isBuy ? "buy" : "sell",
    sourceHref: walletHref(order.sourceWallet),
    sourceLabel: liveSourceName(order.sourceWallet, sourceLabels, "Exchange order"),
    sourceWallet: order.sourceWallet,
  };
}

export type DecisionStat = { detail?: string; label: string; tone?: Tone; value: string };

export type DecisionItem = {
  at: string;
  coin: string;
  id: string;
  meta: string;
  pills: TradeFlag[];
  plannedAction: string;
  side: "long" | "short";
  sourceHref: string | null;
  sourceLabel: string;
  stats: DecisionStat[];
};

export function buildLiveCopyDecisionActivities(
  decisions: LiveCopyDecision[],
  sourceLabels: Map<string, string>,
): DecisionItem[] {
  return decisions
    .map((decision) => buildLiveCopyDecisionActivity(decision, sourceLabels))
    .sort((left, right) => dateMs(right.at) - dateMs(left.at));
}

export function buildLiveCopyDecisionActivity(
  decision: LiveCopyDecision,
  sourceLabels: Map<string, string>,
): DecisionItem {
  const isExchange = isLiveExchangeSource(decision.sourceWallet);
  const pipeline = liveCopyDecisionPipeline(decision);
  return {
    at: decision.decisionAt ?? decision.updatedAt,
    coin: decision.coin,
    id: `copy-decision:${decision.accountKey}:${decision.sourceWallet}:${decision.sourceFillId}:${decision.sequenceIndex}`,
    meta: `${isExchange ? "exchange" : shortAddress(decision.sourceWallet)} | ${decision.accountKey} | source ${shortId(decision.sourceFillId)}`,
    pills: [
      { label: decision.origin.replaceAll("_", " "), tone: "neutral" },
      ...liveCopyDecisionStatusPills(decision),
    ],
    plannedAction: actionLabel(decision.plannedAction),
    side: decision.side,
    sourceHref: walletHref(decision.sourceWallet),
    sourceLabel: isExchange
      ? "Copy decision"
      : sourceDisplayName(sourceLabels.get(decision.sourceWallet.toLowerCase()), decision.sourceWallet),
    stats: [
      {
        label: "Reason",
        value: decision.latestExchangeErrorCode
          ? reasonLabel(decision.latestExchangeErrorCode)
          : decision.reason
            ? reasonLabel(decision.reason)
            : "-",
        detail: decision.latestExchangeErrorMessage ?? decision.logicalOrderError ?? undefined,
        tone: decision.latestExchangeErrorCode || decision.outcome === "terminal_skip" ? "negative" : "neutral",
      },
      {
        label: "Prerequisite retries",
        value: formatInteger(decision.attemptCount),
        detail: decision.nextAttemptAt
          ? `next ${formatDateTime(decision.nextAttemptAt)}`
          : decision.lastAttemptAt
            ? `last ${formatDateTime(decision.lastAttemptAt)}`
            : "not attempted",
      },
      {
        label: "Exchange attempts",
        value: formatInteger(decision.submitAttemptCount),
        detail: [
          decision.latestDispatchAttemptNumber
            ? `attempt ${formatInteger(decision.latestDispatchAttemptNumber)}`
            : "not submitted",
          decision.latestDispatchClientOrderId ? `CLOID ${shortId(decision.latestDispatchClientOrderId)}` : null,
          decision.statusLookupCount > 0
            ? `status lookups ${formatInteger(decision.statusLookupCount)}`
            : "no status lookup",
        ]
          .filter(Boolean)
          .join(" | "),
        tone: liveCopyDecisionExchangeTone(decision),
      },
      {
        label: "First observed",
        value: formatDateTime(decision.firstObservedAt),
        detail: [
          `source ${formatDateTime(sourceTimestampDate(decision.sourceTimestampMs))}`,
          `ingest ${formatIngestLag(decision)}`,
        ].join(" | "),
      },
      {
        label: "Pipeline",
        value: pipeline.label,
        detail: `${pipeline.detail} | age ${formatDuration(decisionTimestampMs(decision) - decision.sourceTimestampMs)} | queue ${formatQueueLag(decision)} | prep ${formatPreparationLag(decision)} | work ${formatProcessingLag(decision)}`,
        tone: pipeline.tone,
      },
    ],
  };
}

export function liveCopyDecisionStatusPills(decision: LiveCopyDecision): TradeFlag[] {
  const orderRecordId = decision.orderRecordId ?? decision.tradingOrderId;
  const outcome: TradeFlag =
    decision.outcome === "pending"
      ? { label: "waiting/pending", tone: "warning" }
      : decision.outcome === "retryable"
        ? { label: decision.nextAttemptAt ? "retry scheduled" : "retry pending", tone: "warning" }
        : decision.outcome === "baseline_ignored"
          ? { label: "baseline ignored", tone: "neutral" }
          : decision.outcome === "terminal_skip" && decision.reason === "live_source_fill_too_old"
            ? { label: "stale no-order", tone: "negative" }
            : decision.outcome === "terminal_skip"
              ? { label: "no-order blocked", tone: "negative" }
              : orderRecordId
                ? { label: "order recorded", tone: "neutral" }
                : { label: "record missing", tone: "negative" };
  const exchangeStatus = decision.latestExchangeStatus ?? decision.logicalOrderStatus;
  if (!exchangeStatus) {
    return [outcome];
  }
  return [outcome, { label: reasonLabel(exchangeStatus), tone: liveOrderStatusTone(exchangeStatus) }];
}

function liveCopyDecisionExchangeTone(decision: LiveCopyDecision): Tone {
  return liveOrderStatusTone(
    decision.latestExchangeStatus ?? decision.logicalOrderStatus ?? decision.latestDispatchStatus ?? "unknown",
  );
}

function liveCopyDecisionPipeline(decision: LiveCopyDecision): { detail: string; label: string; tone: Tone } {
  const logicalStatus = decision.logicalOrderStatus;
  const exchangeStatus = decision.latestExchangeStatus;
  const orderRecordId = decision.orderRecordId ?? decision.tradingOrderId;
  if (!orderRecordId) {
    return {
      detail: "no logical order record",
      label: decision.outcome === "terminal_skip" ? "pre-submit skip" : "decision only",
      tone: decision.outcome === "terminal_skip" ? "negative" : "warning",
    };
  }
  if (exchangeStatus === "filled" || logicalStatus === "filled") {
    return { detail: `order ${shortId(orderRecordId)}`, label: "filled", tone: "positive" };
  }
  if (exchangeStatus === "accepted" || logicalStatus === "accepted") {
    return { detail: `order ${shortId(orderRecordId)}`, label: "accepted", tone: "positive" };
  }
  if (exchangeStatus === "rejected" || logicalStatus === "rejected") {
    return {
      detail: decision.latestExchangeErrorMessage ?? decision.logicalOrderError ?? "exchange rejected",
      label: decision.nextAttemptAt ? "exchange rejected, retry scheduled" : "exchange rejected",
      tone: "negative",
    };
  }
  if (logicalStatus === "failed" || logicalStatus === "canceled") {
    return {
      detail: decision.logicalOrderError ?? `order ${shortId(orderRecordId)}`,
      label: decision.submitAttemptCount === 0 ? "pre-submit skip" : logicalStatus,
      tone: "negative",
    };
  }
  if (logicalStatus === "uncertain" || decision.latestDispatchStatus === "uncertain") {
    return {
      detail: decision.lastStatusLookupError ?? "awaiting exchange status lookup",
      label: "status unknown",
      tone: "warning",
    };
  }
  return {
    detail: `order ${shortId(orderRecordId)}${
      decision.latestDispatchClientOrderId ? ` | CLOID ${shortId(decision.latestDispatchClientOrderId)}` : ""
    }`,
    label: logicalStatus ?? "order recorded",
    tone: liveCopyDecisionExchangeTone(decision),
  };
}

function sourceTimestampDate(sourceTimestampMs: number | null | undefined) {
  if (!sourceTimestampMs || !Number.isFinite(sourceTimestampMs) || sourceTimestampMs <= 0) {
    return null;
  }
  return new Date(sourceTimestampMs).toISOString();
}

function decisionTimestampMs(decision: LiveCopyDecision) {
  return dateMs(decision.decisionAt ?? decision.updatedAt);
}

function formatIngestLag(decision: LiveCopyDecision) {
  const observed = decision.firstObservedAt ?? decision.observedAt;
  return observed ? formatDuration(dateMs(observed) - decision.sourceTimestampMs) : "-";
}

function formatQueueLag(decision: LiveCopyDecision) {
  const observed = decision.firstObservedAt ?? decision.observedAt;
  const claimed = decision.executionClaimedAt ?? decision.processingStartedAt;
  return observed && claimed ? formatDuration(dateMs(claimed) - dateMs(observed)) : "-";
}

function formatProcessingLag(decision: LiveCopyDecision) {
  const started = decision.processingStartedAt ?? decision.executionClaimedAt;
  return started ? formatDuration(decisionTimestampMs(decision) - dateMs(started)) : "-";
}

function formatPreparationLag(decision: LiveCopyDecision) {
  if (!decision.executionClaimedAt || !decision.processingStartedAt) {
    return "-";
  }
  return formatDuration(dateMs(decision.processingStartedAt) - dateMs(decision.executionClaimedAt));
}
