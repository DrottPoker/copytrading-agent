"use client";

import Link from "next/link";

import { TimeAgo } from "@/components/ui/client";
import {
  buttonClass,
  EmptyState,
  Meter,
  Panel,
  Pnl,
  scoreTone,
  Spinner,
  StatusDot,
} from "@/components/ui/primitives";
import { toneText, type Tone } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import { formatInteger, formatPercent, formatScore, formatSeconds, formatSignedUsd, formatUsd } from "@/lib/format";
import { sourceDisplayName } from "@/lib/trading/labels";
import {
  MONITOR_STATUS_LABELS,
  SOURCE_STATE_LABELS,
  summarizeCopySourceStatuses,
} from "@/lib/trading/sources";
import type { CopySourceRow, SourceMonitorStatus, SourceState } from "@/lib/trading/types";

const MONITOR_TONES: Record<SourceMonitorStatus, Tone> = {
  connecting: "warning",
  monitored: "positive",
  offline: "negative",
  waiting: "neutral",
};

const STATE_TONES: Record<SourceState, Tone> = {
  entries_paused: "warning",
  retained: "warning",
  trading: "positive",
  waiting_for_slot: "neutral",
  waiting_for_trades: "info",
};

export function SourcesPanel({
  accountType,
  closingSource,
  combineAccounts,
  onCloseSource,
  sources,
}: {
  accountType: "paper" | "live";
  closingSource: string | null;
  combineAccounts: boolean;
  onCloseSource: (source: CopySourceRow) => void;
  sources: CopySourceRow[];
}) {
  const counts = summarizeCopySourceStatuses(sources);
  const meta = [
    `${formatInteger(counts.trading)} trading`,
    `${formatInteger(counts.monitored)} on the live feed`,
    counts.connecting > 0 ? `${formatInteger(counts.connecting)} connecting` : null,
    counts.offline > 0 ? `${formatInteger(counts.offline)} offline` : null,
    counts.waiting > 0 ? `${formatInteger(counts.waiting)} waiting for a slot` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <Panel
      title="Copy sources"
      meta={sources.length > 0 ? meta : undefined}
      description={
        combineAccounts
          ? "Wallets this account copies. Realized PnL and fill counts combine all paper accounts."
          : "Wallets this account copies, most allocation in use first."
      }
    >
      {sources.length === 0 ? (
        <EmptyState title="No copy sources">
          {accountType === "paper"
            ? "Sources are assigned from the top ranked wallets when the account is created and started."
            : "Live sources appear when live trading is enabled and the account is started."}
        </EmptyState>
      ) : (
        <div className="max-h-[35rem] overflow-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th className="num" title="Copy rank among the sources">
                  #
                </th>
                <th>Wallet</th>
                <th title="Whether the wallet's trades arrive over a realtime subscription">Feed</th>
                <th>State</th>
                <th title="Margin in use out of the share of the account this source may use">Allocation used</th>
                <th className="num">Open</th>
                <th className="num" title="Realized copy PnL, with the open PnL of current positions below">
                  PnL
                </th>
                <th className="num" title="Realized PnL per hour the source has been followed">
                  Per hour
                </th>
                <th className="num" title="Total time the source has been followed in realtime">
                  Followed
                </th>
                <th className="num" title="Copied fills and skipped fills, with the latest activity below">
                  Fills
                </th>
                {accountType === "paper" ? <th aria-label="Actions" /> : null}
              </tr>
            </thead>
            <tbody>
              {sources.map((source) => (
                <tr key={source.sourceWallet}>
                  <td className="num text-fg-3">{source.rank ?? "-"}</td>
                  <td>
                    <Link
                      href={`/wallets/${source.sourceWallet}`}
                      className="block max-w-[11.875rem] truncate font-medium hover:underline"
                      title={source.sourceWallet}
                    >
                      {sourceDisplayName(source.sourceLabel, source.sourceWallet)}
                    </Link>
                    <span className="sub">
                      pool #{source.poolRank ?? "-"}, score{" "}
                      <span className={toneText[scoreTone(source.score)]}>{formatScore(source.score)}</span>
                    </span>
                  </td>
                  <td>
                    <StatusDot
                      tone={MONITOR_TONES[source.monitorStatus]}
                      label={MONITOR_STATUS_LABELS[source.monitorStatus]}
                    />
                  </td>
                  <td>
                    <StatusDot tone={STATE_TONES[source.sourceState]} label={SOURCE_STATE_LABELS[source.sourceState]} />
                    <span className="sub max-w-[14.375rem] truncate" title={source.sourceStateDetail}>
                      {source.sourceStateDetail}
                    </span>
                  </td>
                  <td className="min-w-[9.375rem]">
                    <div className="flex items-center gap-2">
                      <Meter
                        className="w-16"
                        tone={(source.usedPct ?? 0) >= 0.9 ? "negative" : (source.usedPct ?? 0) >= 0.7 ? "warning" : "positive"}
                        value={source.usedPct}
                      />
                      <span className="tabular-nums text-fg-2">{formatPercent(source.usedPct, { decimals: 0 })}</span>
                    </div>
                    <span className="sub tabular-nums">
                      {formatUsd(source.openMarginUsd)} of {formatUsd(source.allocationUsd)}
                    </span>
                  </td>
                  <td className="num">
                    {formatInteger(source.openPositionCount)}
                    {source.openNotionalUsd > 0 ? <span className="sub">{formatUsd(source.openNotionalUsd)}</span> : null}
                  </td>
                  <td className="num">
                    <Pnl value={source.realizedPnlUsd} />
                    {source.openPositionCount > 0 ? (
                      <span className="sub">
                        open <Pnl value={source.unrealizedPnlUsd} />
                      </span>
                    ) : null}
                  </td>
                  <td className="num">
                    {source.realizedPnlPerHourUsd === null ? (
                      <span className="text-fg-4">-</span>
                    ) : (
                      <span
                        className={cx(
                          source.realizedPnlPerHourUsd > 0
                            ? "text-positive"
                            : source.realizedPnlPerHourUsd < 0
                              ? "text-negative"
                              : "text-fg-3",
                        )}
                      >
                        {formatSignedUsd(source.realizedPnlPerHourUsd, {
                          decimals: Math.abs(source.realizedPnlPerHourUsd) < 1 ? 3 : 2,
                        })}
                      </span>
                    )}
                  </td>
                  <td className="num text-fg-2">
                    {source.monitoredSeconds > 0 ? formatSeconds(source.monitoredSeconds) : "-"}
                  </td>
                  <td className="num text-fg-2">
                    {formatInteger(source.copiedFillCount)}
                    <span className="text-fg-4"> / {formatInteger(source.skippedFillCount)}</span>
                    <span className="sub">
                      <TimeAgo value={source.lastActivityAt} />
                    </span>
                  </td>
                  {accountType === "paper" ? (
                    <td className="text-right">
                      {source.openPaperPositionCount > 0 ? (
                        <button
                          type="button"
                          onClick={() => onCloseSource(source)}
                          disabled={closingSource !== null}
                          title="Close every open paper position from this source"
                          className={buttonClass({ size: "xs", variant: "danger" })}
                        >
                          {closingSource === source.sourceWallet ? <Spinner className="h-3 w-3" /> : null}
                          Close all
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
