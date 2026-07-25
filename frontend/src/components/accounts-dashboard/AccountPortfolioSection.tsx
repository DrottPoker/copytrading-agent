import { BarChart3, WalletCards } from "lucide-react";
import Link from "next/link";

import { DashboardPanel } from "@/components/DashboardSurface";
import { StatusPill } from "@/components/StatusPill";
import {
  formatCurrency,
  formatInteger,
  formatPercent,
  formatScore,
} from "@/lib/format";
import type { PaperTradingSummaryResponse } from "@/types/paper";

import type { MarketRow, SourceRow, Tone } from "./types";

export function AccountPortfolioSection({
  marketDataStatus,
  marketRows,
  sourceRows,
}: {
  marketDataStatus: PaperTradingSummaryResponse["marketDataStatus"];
  marketRows: MarketRow[];
  sourceRows: SourceRow[];
}) {
  return (
    <section className="grid gap-3 xl:grid-cols-[1.05fr_0.95fr]">
      <DashboardPanel
        bodyClassName="p-3"
        icon={BarChart3}
        meta={`${formatInteger(marketRows.length)} active markets`}
        title="Portfolio composition"
      >
        <div className="grid gap-4 lg:grid-cols-2 lg:divide-x lg:divide-line">
          <div>
            <SectionLabel label="Allocation usage" />
            <AllocationRows rows={sourceRows} />
          </div>
          <div className="lg:pl-4">
            <SectionLabel label="Market exposure" />
            <MarketRows rows={marketRows} marketDataStatus={marketDataStatus} />
          </div>
        </div>
      </DashboardPanel>

      <DashboardPanel
        bodyClassName="max-h-[460px] overflow-y-auto p-3"
        icon={WalletCards}
        meta={`${formatInteger(sourceRows.length)} attributed sources`}
        title="Source leaderboard"
      >
        <SourceRows rows={sourceRows} />
      </DashboardPanel>
    </section>
  );
}

function AllocationRows({ rows }: { rows: SourceRow[] }) {
  const allocationRows = rows.filter(
    (row) => row.allocationUsd > 0 || row.openMarginUsd > 0,
  );
  if (allocationRows.length === 0) {
    return <EmptyState text="No allocation rows for this account." />;
  }

  return (
    <div className="grid max-h-[360px] gap-2 overflow-y-auto pr-1">
      {allocationRows.map((row) => {
        const usedPct =
          row.allocationUsd > 0 ? row.openMarginUsd / row.allocationUsd : 0;
        return (
          <div key={row.sourceWallet} className="grid gap-1.5">
            <div className="flex items-center justify-between gap-3">
              <SourceIdentity row={row} compact />
              <div className="text-right">
                <p className="font-mono text-xs font-semibold text-ink">
                  {formatCurrency(row.openMarginUsd)}
                </p>
                <p className="text-[11px] text-muted">
                  {formatPercent(usedPct)} used
                </p>
              </div>
            </div>
            <Bar value={usedPct} tone={usedPct >= 0.8 ? "warning" : "positive"} />
          </div>
        );
      })}
    </div>
  );
}

function MarketRows({
  marketDataStatus,
  rows,
}: {
  marketDataStatus: PaperTradingSummaryResponse["marketDataStatus"];
  rows: MarketRow[];
}) {
  if (rows.length === 0) {
    return <EmptyState text="No open market exposure for this account." />;
  }

  const maxNotional = Math.max(...rows.map((row) => row.notionalUsd), 1);
  return (
    <div className="grid max-h-[360px] gap-2 overflow-y-auto pr-1">
      <div className="flex items-center justify-between gap-3">
        <StatusPill
          label={marketStatusLabel(marketDataStatus)}
          tone={
            marketDataStatus === "live" ||
            marketDataStatus === "no_open_positions"
              ? "positive"
              : "warning"
          }
        />
        <p className="text-xs text-muted">{formatInteger(rows.length)} markets</p>
      </div>
      {rows.map((row) => (
        <div key={row.coin} className="grid gap-1.5">
          <div className="grid grid-cols-[76px_1fr_100px] items-center gap-2">
            <div className="min-w-0">
              <p className="truncate font-mono text-sm font-semibold text-ink">
                {row.coin}
              </p>
              <p className="truncate text-[10px] text-muted">
                {formatInteger(row.longCount)}L / {formatInteger(row.shortCount)}S
              </p>
            </div>
            <Bar value={row.notionalUsd / maxNotional} />
            <div className="text-right">
              <p className="font-mono text-xs font-semibold text-ink">
                {formatCurrency(row.notionalUsd)}
              </p>
              <p
                className={
                  row.unrealizedPnlUsd >= 0 ? "text-positive" : "text-danger"
                }
              >
                {formatCurrency(row.unrealizedPnlUsd)}
              </p>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function SourceRows({ rows }: { rows: SourceRow[] }) {
  if (rows.length === 0) {
    return <EmptyState text="No source performance for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {rows.map((row) => (
        <div
          key={row.sourceWallet}
          className="grid gap-2 py-2.5 xl:grid-cols-[1fr_100px_100px_86px]"
        >
          <SourceIdentity row={row} />
          <RowMetric
            label="Total PnL"
            tone={row.totalPnlUsd >= 0 ? "positive" : "danger"}
            value={formatCurrency(row.totalPnlUsd)}
          />
          <RowMetric
            label="Open margin"
            detail={`${formatInteger(row.openPositionCount)} positions`}
            value={formatCurrency(row.openMarginUsd)}
          />
          <RowMetric
            label="Fills"
            detail={`${formatInteger(row.skippedFillCount)} skipped`}
            value={formatInteger(row.copiedFillCount)}
          />
        </div>
      ))}
    </div>
  );
}

function SourceIdentity({
  compact = false,
  row,
}: {
  compact?: boolean;
  row: SourceRow;
}) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1">
        <Link
          href={`/wallets/${row.sourceWallet}`}
          className="min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
        >
          {sourceDisplayName(row.sourceLabel, row.sourceWallet)}
        </Link>
        {!compact ? (
          <StatusPill
            label={formatSourceStatus(row.sourceStatus)}
            tone={sourceStatusTone(row.sourceStatus)}
          />
        ) : null}
      </div>
      <p className="mt-0.5 truncate text-[10px] text-muted">
        {formatPoolRank(row.poolRank)}, {formatScore(row.score)} score
      </p>
    </div>
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
      <p className="truncate text-[10px] font-medium uppercase text-muted">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-xs font-semibold ${valueClass}`}>
        {value}
      </p>
      {detail ? <p className="mt-0.5 truncate text-[10px] text-muted">{detail}</p> : null}
    </div>
  );
}

function Bar({ tone = "neutral", value }: { tone?: Tone; value: number }) {
  const color =
    tone === "positive"
      ? "bg-positive"
      : tone === "danger"
        ? "bg-danger"
        : tone === "warning"
          ? "bg-warning"
          : "bg-muted";
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-line">
      <div
        className={`h-full rounded-full ${color}`}
        style={{ width: `${clamp(value, 0, 1) * 100}%` }}
      />
    </div>
  );
}

function SectionLabel({ label }: { label: string }) {
  return (
    <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.05em] text-muted">
      {label}
    </p>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-muted">{text}</div>;
}

function sourceDisplayName(label: string | null | undefined, address: string) {
  return label?.trim() || shortAddress(address);
}

function shortAddress(address: string) {
  return address.length <= 14 ? address : `${address.slice(0, 8)}...${address.slice(-5)}`;
}

function formatPoolRank(rank: number | null | undefined) {
  return rank === null || rank === undefined ? "pool -" : `pool #${formatInteger(rank)}`;
}

function formatSourceStatus(status: SourceRow["sourceStatus"]) {
  if (status === "waiting_for_trades") {
    return "waiting";
  }
  if (status === "waiting_for_slot") {
    return "queued";
  }
  return status;
}

function sourceStatusTone(status: SourceRow["sourceStatus"]): Tone {
  if (status === "trading") {
    return "positive";
  }
  if (status === "retained") {
    return "warning";
  }
  return "neutral";
}

function marketStatusLabel(
  status: PaperTradingSummaryResponse["marketDataStatus"],
) {
  if (status === "no_open_positions") {
    return "no open positions";
  }
  if (status === "unavailable") {
    return "market unavailable";
  }
  return status;
}

function clamp(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}
