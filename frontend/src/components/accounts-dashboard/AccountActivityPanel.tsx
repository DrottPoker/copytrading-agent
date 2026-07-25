"use client";

import { Activity } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { DashboardPanel } from "@/components/DashboardSurface";
import { StatusPill } from "@/components/StatusPill";
import { formatCurrency, formatInteger, numberValue } from "@/lib/format";

import type {
  AccountClosedTradeRow,
  AccountDataTab,
  AccountExecutionRow,
  AccountPositionRow,
  Tone,
} from "./types";

export function AccountActivityPanel({
  closedTrades,
  positions,
  recentActivity,
}: {
  closedTrades: AccountClosedTradeRow[];
  positions: AccountPositionRow[];
  recentActivity: AccountExecutionRow[];
}) {
  const [activeTab, setActiveTab] = useState<AccountDataTab>("positions");
  const tabs: Array<{ count: number; id: AccountDataTab; label: string }> = [
    { count: positions.length, id: "positions", label: "Positions" },
    { count: closedTrades.length, id: "trades", label: "Closed trades" },
    { count: recentActivity.length, id: "activity", label: "Executions" },
  ];

  return (
    <DashboardPanel
      action={
        <div
          aria-label="Select account data"
          className="inline-flex rounded-lg border border-line bg-subtle p-0.5"
          role="tablist"
        >
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              aria-controls={`account-data-${tab.id}`}
              aria-selected={activeTab === tab.id}
              id={`account-tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold ${
                activeTab === tab.id
                  ? "bg-white text-ink shadow-panel"
                  : "text-muted hover:text-ink"
              }`}
              role="tab"
            >
              {tab.label}{" "}
              <span className="font-mono text-[10px]">{formatInteger(tab.count)}</span>
            </button>
          ))}
        </div>
      }
      bodyClassName="max-h-[560px] overflow-y-auto px-3"
      icon={Activity}
      title="Account activity"
    >
      <div
        aria-labelledby={`account-tab-${activeTab}`}
        id={`account-data-${activeTab}`}
        role="tabpanel"
      >
        {activeTab === "positions" ? (
          <PositionRows positions={positions} />
        ) : activeTab === "trades" ? (
          <ClosedTradeRows trades={closedTrades} />
        ) : (
          <ExecutionRows executions={recentActivity} />
        )}
      </div>
    </DashboardPanel>
  );
}

function PositionRows({ positions }: { positions: AccountPositionRow[] }) {
  if (positions.length === 0) {
    return <EmptyState text="No open positions for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {positions.map((position) => {
        const unrealized = numberValue(position.unrealizedPnlUsd ?? 0);
        return (
          <div
            key={position.id}
            className="grid gap-2 py-2.5 xl:grid-cols-[1fr_120px_120px_120px_120px]"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{position.coin}</p>
                <StatusPill
                  label={position.side}
                  tone={position.side === "long" ? "positive" : "warning"}
                />
                <StatusPill
                  label={position.accountType}
                  tone={position.accountType === "live" ? "positive" : "neutral"}
                />
              </div>
              <SourceLink href={position.sourceHref} label={position.sourceLabel} />
              <p className="mt-1 truncate text-[11px] text-muted">{position.detail}</p>
            </div>
            <RowMetric
              label="Unrealized"
              tone={unrealized >= 0 ? "positive" : "danger"}
              value={formatCurrency(unrealized)}
            />
            <RowMetric
              label="Notional"
              detail={formatLeverage(position.leverage, position.marginMode)}
              value={formatCurrency(position.notionalUsd)}
            />
            <RowMetric
              label="Entry"
              detail={position.entryDetail}
              value={formatPrice(position.entryPrice)}
            />
            <RowMetric
              label="Execution"
              detail={position.executionDetail}
              value={position.executionValue}
            />
          </div>
        );
      })}
    </div>
  );
}

function ClosedTradeRows({ trades }: { trades: AccountClosedTradeRow[] }) {
  if (trades.length === 0) {
    return <EmptyState text="No closed trades for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {trades.map((trade) => {
        const netPnl = numberValue(trade.netPnlUsd ?? 0);
        return (
          <div
            key={trade.id}
            className="grid gap-2 py-2.5 xl:grid-cols-[1fr_120px_120px_120px]"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{trade.coin}</p>
                {trade.badges.map((badge) => (
                  <StatusPill
                    key={`${badge.label}:${badge.tone}`}
                    label={badge.label}
                    tone={badge.tone}
                  />
                ))}
              </div>
              <SourceLink href={trade.sourceHref} label={trade.sourceLabel} />
              <p className="mt-1 text-[11px] text-muted">{trade.detail}</p>
            </div>
            <RowMetric
              label="Net PnL"
              tone={netPnl >= 0 ? "positive" : "danger"}
              value={formatCurrency(netPnl)}
            />
            <RowMetric label="Closed" value={formatShortDateTime(trade.closedAt)} />
            <RowMetric
              label="Exit"
              detail={trade.exitDetail}
              value={formatPrice(trade.exitPrice)}
            />
          </div>
        );
      })}
    </div>
  );
}

function ExecutionRows({ executions }: { executions: AccountExecutionRow[] }) {
  if (executions.length === 0) {
    return <EmptyState text="No recent execution activity for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {executions.map((execution) => {
        const realizedPnl = numberValue(execution.realizedPnlUsd ?? 0);
        return (
          <div
            key={execution.id}
            className="grid gap-2 py-2.5 xl:grid-cols-[1fr_110px_120px_120px]"
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">
                  {execution.coin}
                </p>
                {execution.badges.map((badge) => (
                  <StatusPill
                    key={`${badge.label}:${badge.tone}`}
                    label={badge.label}
                    tone={badge.tone}
                  />
                ))}
              </div>
              <SourceLink href={execution.sourceHref} label={execution.sourceLabel} />
              <p className="mt-1 truncate text-[11px] text-muted">
                {execution.detail}
              </p>
            </div>
            <RowMetric
              label="Realized"
              tone={realizedPnl >= 0 ? "positive" : "danger"}
              value={formatCurrency(realizedPnl)}
            />
            <RowMetric
              label="Notional"
              detail={execution.notionalDetail}
              value={formatCurrency(execution.notionalUsd)}
            />
            <RowMetric
              label="Price"
              detail={execution.priceDetail}
              value={formatPrice(execution.price)}
            />
          </div>
        );
      })}
    </div>
  );
}

function SourceLink({ href, label }: { href: string | null; label: string }) {
  return href ? (
    <Link
      href={href}
      className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
    >
      {label}
    </Link>
  ) : (
    <p className="mt-1 min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
      {label}
    </p>
  );
}

function RowMetric({
  detail,
  label,
  tone = "neutral",
  value,
}: {
  detail?: string;
  label: string;
  tone?: Tone;
  value: string;
}) {
  const valueClass =
    tone === "positive" ? "text-positive" : tone === "danger" ? "text-danger" : "text-ink";
  return (
    <div className="min-w-0">
      <p className="truncate text-[11px] font-medium uppercase text-muted">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-xs font-semibold ${valueClass}`}>
        {value}
      </p>
      {detail ? <p className="mt-0.5 truncate text-[11px] text-muted">{detail}</p> : null}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-muted">{text}</div>;
}

function formatPrice(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 6,
    minimumFractionDigits: 2,
  }).format(numberValue(value));
}

function formatLeverage(
  value: string | number | null | undefined,
  marginMode?: "cross" | "isolated" | null,
) {
  if (value === null || value === undefined) {
    return "-";
  }
  const leverage = `${new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 2,
  }).format(numberValue(value))}x`;
  return marginMode ? `${leverage} ${marginMode}` : leverage;
}

function formatShortDateTime(value: string | null | undefined) {
  if (!value) {
    return "-";
  }
  return new Intl.DateTimeFormat("sv-SE", {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "2-digit",
  }).format(new Date(value));
}
