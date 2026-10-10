"use client";

import Link from "next/link";
import { useState } from "react";
import type { ReactNode } from "react";

import { Pagination, Tabs, TimeAgo, usePagination, type TabItem } from "@/components/ui/client";
import { Badge, EmptyState, Pnl, Side, StatusDot, Usd } from "@/components/ui/primitives";
import { toneText } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import {
  formatDateTime,
  formatDuration,
  formatInteger,
  formatPrice,
  formatSeconds,
  formatSignedUsd,
  formatSize,
  formatUsd,
} from "@/lib/format";
import type { DecisionItem } from "@/lib/trading/live";
import { sourceDisplayName } from "@/lib/trading/labels";
import type { SourceHistoryRow } from "@/lib/trading/sources";
import type { ClosedTradeRow, ExecutionRow } from "@/lib/trading/types";

import { AccountTransactions } from "./AccountTransactions";

type HistoryTab = "trades" | "executions" | "decisions" | "sources" | "transactions";

const PAGE_SIZE = 25;

export function HistoryPanel({
  accountKey,
  accountType,
  cashFlowsVersion,
  closedTrades,
  decisions,
  executions,
  sourceHistory,
}: {
  accountKey: string;
  accountType: "paper" | "live";
  cashFlowsVersion: string | null;
  closedTrades: ClosedTradeRow[];
  decisions: DecisionItem[];
  executions: ExecutionRow[];
  sourceHistory: SourceHistoryRow[];
}) {
  const [tab, setTab] = useState<HistoryTab>("trades");
  const tabs: Array<TabItem<HistoryTab>> = [
    { count: closedTrades.length, id: "trades", label: "Closed trades" },
    { count: executions.length, id: "executions", label: "Executions" },
    ...(accountType === "live" ? [{ count: decisions.length, id: "decisions" as const, label: "Copy decisions" }] : []),
    { count: sourceHistory.length, id: "sources", label: "Source history" },
    ...(accountType === "live" ? [{ id: "transactions" as const, label: "Deposits and withdrawals" }] : []),
  ];

  // Live only tabs fall back to closed trades when switching to a paper account.
  const activeTab = tabs.some((item) => item.id === tab) ? tab : "trades";

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <div className="border-b border-line px-3.5">
        <Tabs label="Account history" items={tabs} value={activeTab} onChange={setTab} />
      </div>
      {activeTab === "trades" ? <ClosedTradesTable trades={closedTrades} showFunding={accountType === "live"} /> : null}
      {activeTab === "executions" ? <ExecutionsTable rows={executions} /> : null}
      {activeTab === "decisions" ? <DecisionsTable decisions={decisions} /> : null}
      {activeTab === "sources" ? <SourceHistoryTable rows={sourceHistory} /> : null}
      {activeTab === "transactions" ? (
        <AccountTransactions accountKey={accountKey} cashFlowsVersion={cashFlowsVersion} />
      ) : null}
    </section>
  );
}

function Paged<T>({
  empty,
  head,
  renderRow,
  rows,
}: {
  empty: ReactNode;
  head: ReactNode;
  renderRow: (row: T) => ReactNode;
  rows: T[];
}) {
  const { page, pageCount, pageItems, setPage } = usePagination(rows, PAGE_SIZE);
  if (rows.length === 0) {
    return <>{empty}</>;
  }
  return (
    <>
      <div className="overflow-x-auto">
        <table className="data-table">
          <thead>{head}</thead>
          <tbody>{pageItems.map(renderRow)}</tbody>
        </table>
      </div>
      <Pagination page={page} pageCount={pageCount} pageSize={PAGE_SIZE} total={rows.length} onPageChange={setPage} />
    </>
  );
}

function SourceCell({ href, label, title }: { href: string | null; label: string; title?: string }) {
  return href ? (
    <Link href={href} className="block max-w-[180px] truncate hover:underline" title={title}>
      {label}
    </Link>
  ) : (
    <span className="block max-w-[180px] truncate text-fg-3">{label}</span>
  );
}

function ClosedTradesTable({ showFunding, trades }: { showFunding: boolean; trades: ClosedTradeRow[] }) {
  return (
    <Paged
      rows={trades}
      empty={<EmptyState title="No closed trades yet">Trades show up here once a copied position is fully closed.</EmptyState>}
      head={
        <tr>
          <th>Closed</th>
          <th>Market</th>
          <th>Source wallet</th>
          <th className="num">Size</th>
          {showFunding ? <th className="num">Entry</th> : null}
          <th className="num">Exit</th>
          <th className="num">Value</th>
          <th className="num">Fees</th>
          {showFunding ? <th className="num">Funding</th> : null}
          <th className="num" title="Realized PnL minus fees, plus funding">
            Net PnL
          </th>
          <th className="num">Held</th>
        </tr>
      }
      renderRow={(trade) => (
        <tr key={trade.id}>
          <td className="tabular-nums text-fg-2">{formatDateTime(trade.closedAt)}</td>
          <td>
            <span className="inline-flex items-center gap-1.5">
              <span className="font-medium">{trade.coin}</span>
              {trade.flags.map((flag) => (
                <Badge key={flag.label} tone={flag.tone}>
                  {flag.label}
                </Badge>
              ))}
            </span>
            <span className="sub">
              <Side side={trade.side} suffix={trade.closeTypeLabel === "Close" ? undefined : trade.closeTypeLabel} />
            </span>
          </td>
          <td>
            <SourceCell href={trade.sourceHref} label={trade.sourceLabel} title={trade.sourceWallet} />
          </td>
          <td className="num">{formatSize(trade.size)}</td>
          {showFunding ? <td className="num">{formatPrice(trade.entryPrice)}</td> : null}
          <td className="num">{formatPrice(trade.exitPrice)}</td>
          <td className="num">
            <Usd value={trade.notionalUsd} />
          </td>
          <td className="num text-fg-2">{formatUsd(trade.feeUsd)}</td>
          {showFunding ? (
            <td className="num">
              <Pnl value={trade.fundingUsd} />
            </td>
          ) : null}
          <td className="num font-medium">
            <Pnl value={trade.netPnlUsd} />
            <span className="sub">gross {formatSignedUsd(trade.realizedPnlUsd)}</span>
          </td>
          <td className="num text-fg-2">{formatDuration(trade.durationMs)}</td>
        </tr>
      )}
    />
  );
}

function ExecutionsTable({ rows }: { rows: ExecutionRow[] }) {
  return (
    <Paged
      rows={rows}
      empty={<EmptyState title="No executions yet">Every copied fill and every skipped source trade is listed here.</EmptyState>}
      head={
        <tr>
          <th>Time</th>
          <th>Market</th>
          <th>Source wallet</th>
          <th>Action</th>
          <th>Result</th>
          <th className="num">Price</th>
          <th className="num">Value</th>
          <th className="num">Fee</th>
          <th className="num">Realized</th>
          <th>Detail</th>
        </tr>
      }
      renderRow={(row) => (
        <tr key={row.id}>
          <td className="tabular-nums text-fg-2">{formatDateTime(row.at, { seconds: true })}</td>
          <td>
            <span className="font-medium">{row.coin}</span>
            <span className="sub">
              <Side side={row.side} />
            </span>
          </td>
          <td>
            <SourceCell href={row.sourceHref} label={row.sourceLabel} title={row.sourceWallet} />
          </td>
          <td>{row.action}</td>
          <td>
            <StatusDot tone={row.result.tone} label={row.result.label} />
          </td>
          <td className="num">
            {formatPrice(row.price)}
            {row.priceDetail ? <span className="sub">{row.priceDetail}</span> : null}
          </td>
          <td className="num">
            <Usd value={row.notionalUsd} />
            {row.notionalDetail ? <span className="sub">{row.notionalDetail}</span> : null}
          </td>
          <td className="num text-fg-2">{row.feeUsd === null ? "-" : formatUsd(row.feeUsd)}</td>
          <td className="num">{row.realizedPnlUsd === null ? <span className="text-fg-4">-</span> : <Pnl value={row.realizedPnlUsd} />}</td>
          <td className="wrap min-w-[220px] max-w-[380px] text-xs text-fg-3">{row.detail ?? ""}</td>
        </tr>
      )}
    />
  );
}

function DecisionsTable({ decisions }: { decisions: DecisionItem[] }) {
  return (
    <Paged
      rows={decisions}
      empty={
        <EmptyState title="No live copy decisions yet">
          Each source trade seen by the live account gets a decision: copied, retried, or skipped with a reason.
        </EmptyState>
      }
      head={
        <tr>
          <th>Decided</th>
          <th>Source wallet</th>
          <th>Market</th>
          <th>Outcome</th>
          <th>Reason</th>
          <th className="num">Retries</th>
          <th>Exchange</th>
          <th>Observed</th>
          <th>Pipeline</th>
        </tr>
      }
      renderRow={(decision) => {
        const stat = (label: string) => decision.stats.find((item) => item.label === label);
        const reason = stat("Reason");
        const retries = stat("Prerequisite retries");
        const exchange = stat("Exchange attempts");
        const observed = stat("First observed");
        const pipeline = stat("Pipeline");
        return (
          <tr key={decision.id}>
            <td className="tabular-nums text-fg-2">{formatDateTime(decision.at, { seconds: true })}</td>
            <td>
              <SourceCell href={decision.sourceHref} label={decision.sourceLabel} />
              <span className="sub font-mono">{decision.meta}</span>
            </td>
            <td>
              <span className="font-medium">{decision.coin}</span>
              <span className="sub">
                <Side side={decision.side} suffix={decision.plannedAction} />
              </span>
            </td>
            <td>
              <span className="flex flex-wrap gap-1">
                {decision.pills.map((pill) => (
                  <Badge key={`${pill.label}:${pill.tone}`} tone={pill.tone}>
                    {pill.label}
                  </Badge>
                ))}
              </span>
            </td>
            <td className="wrap max-w-[260px]">
              <span className={cx(reason?.tone ? toneText[reason.tone] : undefined, reason?.tone === "neutral" && "text-fg")}>
                {reason?.value}
              </span>
              {reason?.detail ? <span className="sub">{reason.detail}</span> : null}
            </td>
            <td className="num">
              {retries?.value}
              <span className="sub">{retries?.detail}</span>
            </td>
            <td className="wrap max-w-[220px]">
              <span className={exchange?.tone ? toneText[exchange.tone] : undefined}>{exchange?.value} attempts</span>
              <span className="sub">{exchange?.detail}</span>
            </td>
            <td>
              {observed?.value}
              <span className="sub">{observed?.detail}</span>
            </td>
            <td className="wrap max-w-[300px]">
              <span className={pipeline?.tone ? toneText[pipeline.tone] : undefined}>{pipeline?.value}</span>
              <span className="sub">{pipeline?.detail}</span>
            </td>
          </tr>
        );
      }}
    />
  );
}

function SourceHistoryTable({ rows }: { rows: SourceHistoryRow[] }) {
  return (
    <Paged
      rows={rows}
      empty={<EmptyState title="No source history yet">Sources appear once they have produced copy activity.</EmptyState>}
      head={
        <tr>
          <th>Wallet</th>
          <th>Status</th>
          <th className="num">Realized</th>
          <th className="num">Unrealized</th>
          <th className="num">Total</th>
          <th className="num">Per hour</th>
          <th className="num">Followed</th>
          <th className="num">Open</th>
          <th className="num">Fills</th>
          <th className="num">Last fill</th>
        </tr>
      }
      renderRow={(row) => (
        <tr key={row.sourceWallet}>
          <td>
            <SourceCell
              href={`/wallets/${row.sourceWallet}`}
              label={sourceDisplayName(row.sourceLabel, row.sourceWallet)}
              title={row.sourceWallet}
            />
            <span className="sub">pool #{row.poolRank ?? "-"}</span>
          </td>
          <td>
            <StatusDot
              tone={row.monitorStatus === "monitored" ? "positive" : "neutral"}
              label={row.monitorStatus === "monitored" ? "Followed now" : "Past source"}
            />
          </td>
          <td className="num">
            <Pnl value={row.realizedPnlUsd} />
          </td>
          <td className="num">
            <Pnl value={row.unrealizedPnlUsd} />
          </td>
          <td className="num font-medium">
            <Pnl value={row.totalPnlUsd} />
          </td>
          <td className="num">
            {row.realizedPnlPerHourUsd === null ? (
              <span className="text-fg-4">-</span>
            ) : (
              <Pnl value={row.realizedPnlPerHourUsd} />
            )}
          </td>
          <td className="num text-fg-2">{row.monitoredSeconds > 0 ? formatSeconds(row.monitoredSeconds) : "-"}</td>
          <td className="num">{formatInteger(row.openPositionCount)}</td>
          <td className="num text-fg-2">
            {formatInteger(row.copiedFillCount)}
            <span className="text-fg-4"> / {formatInteger(row.skippedFillCount)}</span>
          </td>
          <td className="num text-fg-3">
            <TimeAgo value={row.lastFillAt} />
          </td>
        </tr>
      )}
    />
  );
}
