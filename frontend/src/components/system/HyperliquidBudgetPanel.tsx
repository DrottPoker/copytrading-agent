import { TimeAgo } from "@/components/ui/client";
import { EmptyState, Meter, Panel, StatusDot } from "@/components/ui/primitives";
import type { Tone } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import { formatInteger, formatNumber, formatSeconds, humanize } from "@/lib/format";
import type { OpsHyperliquidBudget, OpsHyperliquidProcessUsage } from "@/types/ops";

/** Share of the per-IP limit used, toned so a nearly full budget stands out. */
function usageTone(ratio: number): Tone {
  if (ratio >= 0.9) {
    return "negative";
  }
  return ratio >= 0.7 ? "warning" : "positive";
}

function processLabel(usage: OpsHyperliquidProcessUsage) {
  return usage.process === "api" ? "API" : `${humanize(usage.process)} worker`;
}

export function HyperliquidBudgetPanel({ budget }: { budget: OpsHyperliquidBudget | null | undefined }) {
  if (!budget) {
    return null;
  }
  const limit = budget.ipWeightLimitPerMinute;
  const averageRatio = limit > 0 ? budget.totalWeightPerMinute15m / limit : 0;
  return (
    <Panel
      title="Hyperliquid requests"
      meta={`request weight per minute, Hyperliquid allows ${formatInteger(limit)} per IP address`}
    >
      <div className="grid gap-2 border-b border-line px-3.5 py-2.5 text-xs text-fg-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div className="flex items-center gap-3">
          <Meter className="w-40" value={averageRatio} tone={usageTone(averageRatio)} label="Average share of the IP limit" />
          <span className="tabular-nums text-fg-2">
            {formatNumber(budget.totalWeightPerMinute15m, 0)} per minute over 15 minutes,{" "}
            {formatInteger(budget.totalWeightLastMinute)} in the last minute
          </span>
        </div>
        <span className={cx("tabular-nums", budget.rateLimited60m > 0 && "text-negative")}>
          {formatInteger(budget.rateLimited60m)} rate limited in the last hour
        </span>
      </div>
      {budget.processes.length === 0 ? (
        <EmptyState title="No usage reported yet">Workers report their usage with each heartbeat.</EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Process</th>
                <th className="num">Budget</th>
                <th className="num">Last minute</th>
                <th className="num">15 min average</th>
                <th className="num">Peak minute</th>
                <th className="num" title="Copy execution and background work, average per minute over 15 minutes">
                  Execution / background
                </th>
                <th className="num" title="Time requests waited for budget over 15 minutes">
                  Waited
                </th>
                <th className="num">Rate limited</th>
                <th>Heaviest requests</th>
              </tr>
            </thead>
            <tbody>
              {budget.processes.map((usage) => (
                <tr key={`${usage.process}:${usage.instanceId ?? "local"}`}>
                  <td>
                    <StatusDot tone={usage.rateLimited60m > 0 ? "negative" : "positive"} label={processLabel(usage)} />
                    <span className="sub">
                      reported <TimeAgo value={usage.updatedAt} />
                    </span>
                  </td>
                  <td className="num">
                    {formatInteger(usage.budgetWeightPerMinute)}
                    <span className="sub">{humanize(usage.budgetMode)}</span>
                  </td>
                  <td className="num">{formatInteger(usage.weightLastMinute)}</td>
                  <td className="num">{formatNumber(usage.weightPerMinute15m, 0)}</td>
                  <td className="num">{formatInteger(usage.peakWeightPerMinute60m)}</td>
                  <td className="num text-fg-2">
                    {formatNumber(usage.executionWeightPerMinute15m, 0)} / {formatNumber(usage.backgroundWeightPerMinute15m, 0)}
                  </td>
                  <td className="num text-fg-2">
                    {formatSeconds(usage.executionWaitSeconds15m + usage.backgroundWaitSeconds15m)}
                    {usage.executionWaitSeconds15m > 0 ? (
                      <span className="sub text-warning">execution {formatSeconds(usage.executionWaitSeconds15m)}</span>
                    ) : null}
                  </td>
                  <td className={cx("num", usage.rateLimited60m > 0 && "text-negative")}>
                    {formatInteger(usage.rateLimited60m)}
                  </td>
                  <td className="wrap text-xs text-fg-2">
                    {usage.topRequestTypes15m.length === 0
                      ? "-"
                      : usage.topRequestTypes15m
                          .slice(0, 3)
                          .map((item) => `${item.type} ${formatInteger(item.weight)}`)
                          .join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
