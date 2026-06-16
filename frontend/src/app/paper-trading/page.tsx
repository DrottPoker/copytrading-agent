import { Activity, BarChart3, Percent, WalletCards, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { StatusPill } from "@/components/StatusPill";
import { getPaperTradingSummary } from "@/lib/api";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
  numberValue,
} from "@/lib/format";
import type { PaperCopyAllocation, PaperCopyFill, PaperPosition } from "@/types/paper";

export default async function PaperTradingPage() {
  const summary = await getPaperTradingSummary();
  const totalEquity = summary.accounts.reduce(
    (total, account) => total + numberValue(account.equityUsd),
    0,
  );
  const totalRealizedPnl = summary.accounts.reduce(
    (total, account) => total + numberValue(account.realizedPnlUsd),
    0,
  );
  const totalFees = summary.accounts.reduce(
    (total, account) => total + numberValue(account.feeUsd),
    0,
  );
  const activeAllocations = summary.allocations.filter((allocation) => allocation.active).length;
  const copiedFills = summary.recentFills.filter((fill) => fill.action !== "skip").length;
  const skippedFills = summary.recentFills.length - copiedFills;

  return (
    <>
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-sm font-medium text-[#5b6770]">Simulated execution</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-normal">
            <BarChart3 className="h-6 w-6 text-[#5b6770]" aria-hidden="true" />
            Paper Trading
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill
            label={summary.policy.enabled ? "paper copy enabled" : "paper copy disabled"}
            tone={summary.policy.enabled ? "positive" : "warning"}
          />
          <StatusPill label={`${formatInteger(summary.accounts.length)} accounts`} />
          <StatusPill label={`${formatInteger(activeAllocations)} allocations`} />
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <HeroMetric
          icon={WalletCards}
          label="Total equity"
          value={formatCurrency(totalEquity)}
          detail={`${formatInteger(summary.accounts.length)} paper accounts`}
        />
        <HeroMetric
          icon={Activity}
          label="Open positions"
          value={formatInteger(summary.positions.length)}
          detail={`${formatCurrency(openNotional(summary.positions))} notional, ${formatCurrency(
            openMargin(summary.positions),
          )} margin`}
        />
        <HeroMetric
          icon={BarChart3}
          label="Realized PnL"
          value={formatCurrency(totalRealizedPnl)}
          detail={`${formatCurrency(totalFees)} fees`}
          tone={totalRealizedPnl >= 0 ? "positive" : "danger"}
        />
        <HeroMetric
          icon={Percent}
          label="Copied fills"
          value={formatInteger(copiedFills)}
          detail={`${formatInteger(skippedFills)} skipped in recent history`}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel title="Policy">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <DataPoint label="Top wallets" value={formatInteger(summary.policy.topWalletCount)} />
            <DataPoint
              label="Top tier"
              value={`${formatInteger(summary.policy.topTierWalletCount)} wallets`}
              detail={formatPercent(summary.policy.topTierAllocationPct)}
            />
            <DataPoint
              label="Standard tier"
              value={formatPercent(summary.policy.standardAllocationPct)}
              detail="Ranks below top tier"
            />
            <DataPoint
              label="Total cap"
              value={formatPercent(summary.policy.maxTotalAllocationPct)}
              detail="Max open paper margin"
            />
            <DataPoint
              label="Min notional"
              value={formatCurrency(summary.policy.minOrderNotionalUsd)}
            />
            <DataPoint label="Fee rate" value={formatPercent(summary.policy.feeRate)} />
            <DataPoint label="Slippage" value={formatBps(summary.policy.slippageBps)} />
            <DataPoint label="Latency" value={`${formatInteger(summary.policy.latencyMs)} ms`} />
            <DataPoint
              label="Max drift"
              value={formatBps(summary.policy.maxPriceDriftBps)}
              detail={summary.policy.useLiveMidPrice ? "Live mid enabled" : "Source fill price"}
            />
          </div>
        </Panel>

        <Panel title="Accounts">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-left text-sm">
              <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
                <tr>
                  <th className="px-4 py-3 font-semibold">Account</th>
                  <th className="px-4 py-3 font-semibold">Equity</th>
                  <th className="px-4 py-3 font-semibold">Cash</th>
                  <th className="px-4 py-3 font-semibold">Realized PnL</th>
                  <th className="px-4 py-3 font-semibold">Fees</th>
                  <th className="px-4 py-3 font-semibold">State</th>
                </tr>
              </thead>
              <tbody>
                {summary.accounts.length === 0 ? (
                  <EmptyRow colSpan={6} text="No paper accounts synced yet." />
                ) : (
                  summary.accounts.map((account) => (
                    <tr key={account.key} className="border-b border-line last:border-b-0">
                      <td className="px-4 py-3">
                        <p className="font-semibold">{account.label}</p>
                        <p className="mt-1 font-mono text-xs text-[#5b6770]">{account.key}</p>
                      </td>
                      <td className="px-4 py-3 font-mono">{formatCurrency(account.equityUsd)}</td>
                      <td className="px-4 py-3 font-mono">{formatCurrency(account.cashBalanceUsd)}</td>
                      <td className={pnlClass(account.realizedPnlUsd)}>
                        {formatCurrency(account.realizedPnlUsd)}
                      </td>
                      <td className="px-4 py-3 font-mono">{formatCurrency(account.feeUsd)}</td>
                      <td className="px-4 py-3">
                        <StatusPill
                          label={account.enabled ? "enabled" : "disabled"}
                          tone={account.enabled ? "positive" : "warning"}
                        />
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>

      <Panel title="Allocations">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[940px] border-collapse text-left text-sm">
            <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
              <tr>
                <th className="px-4 py-3 font-semibold">Account</th>
                <th className="px-4 py-3 font-semibold">Rank</th>
                <th className="px-4 py-3 font-semibold">Source wallet</th>
                <th className="px-4 py-3 font-semibold">Score</th>
                <th className="px-4 py-3 font-semibold">Allocation</th>
                <th className="px-4 py-3 font-semibold">Pocket</th>
                <th className="px-4 py-3 font-semibold">State</th>
              </tr>
            </thead>
            <tbody>
              {summary.allocations.length === 0 ? (
                <EmptyRow colSpan={7} text="No scored paper allocation sources yet." />
              ) : (
                summary.allocations.map((allocation) => (
                  <AllocationRow key={allocation.id} allocation={allocation} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <section className="grid gap-4 xl:grid-cols-[1fr_1.05fr]">
        <Panel title="Open Positions">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-sm">
              <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
                <tr>
                  <th className="px-4 py-3 font-semibold">Account</th>
                  <th className="px-4 py-3 font-semibold">Source</th>
                  <th className="px-4 py-3 font-semibold">Coin</th>
                  <th className="px-4 py-3 font-semibold">Side</th>
                  <th className="px-4 py-3 font-semibold">Size</th>
                  <th className="px-4 py-3 font-semibold">Entry</th>
                  <th className="px-4 py-3 font-semibold">Leverage</th>
                  <th className="px-4 py-3 font-semibold">Margin</th>
                  <th className="px-4 py-3 font-semibold">Notional</th>
                  <th className="px-4 py-3 font-semibold">Opened</th>
                </tr>
              </thead>
              <tbody>
                {summary.positions.length === 0 ? (
                  <EmptyRow colSpan={10} text="No open paper positions." />
                ) : (
                  summary.positions.map((position) => (
                    <PositionRow key={position.id} position={position} />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Recent Paper Fills">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-sm">
              <thead className="border-b border-line bg-[#f8fafb] text-xs uppercase text-[#5b6770]">
                <tr>
                  <th className="px-4 py-3 font-semibold">Time</th>
                  <th className="px-4 py-3 font-semibold">Account</th>
                  <th className="px-4 py-3 font-semibold">Action</th>
                  <th className="px-4 py-3 font-semibold">Market</th>
                  <th className="px-4 py-3 font-semibold">Notional</th>
                  <th className="px-4 py-3 font-semibold">PnL</th>
                  <th className="px-4 py-3 font-semibold">Skip reason</th>
                </tr>
              </thead>
              <tbody>
                {summary.recentFills.length === 0 ? (
                  <EmptyRow colSpan={7} text="No paper fills recorded yet." />
                ) : (
                  summary.recentFills.map((fill) => (
                    <FillRow
                      key={fill.id}
                      fill={fill}
                      minOrderNotionalUsd={summary.policy.minOrderNotionalUsd}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>
    </>
  );
}

function HeroMetric({
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: "positive" | "danger" | "neutral";
  value: string;
}) {
  const toneClass =
    tone === "positive"
      ? "border-[#9ccfc0] bg-[#f2fbf7]"
      : tone === "danger"
        ? "border-[#efb1aa] bg-[#fff5f3]"
        : "border-line bg-panel";

  return (
    <article className={`rounded-lg border p-4 shadow-sm ${toneClass}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
          <p className="mt-2 truncate text-2xl font-semibold text-ink">{value}</p>
        </div>
        <Icon className="h-5 w-5 shrink-0 text-[#5b6770]" aria-hidden="true" />
      </div>
      <p className="mt-3 truncate text-sm text-[#5b6770]">{detail}</p>
    </article>
  );
}

function Panel({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="border-b border-line px-4 py-3">
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function DataPoint({
  detail,
  label,
  value,
}: {
  detail?: string;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] p-3">
      <p className="text-xs font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-2 break-words text-lg font-semibold leading-snug">{value}</p>
      {detail ? <p className="mt-1 truncate text-sm text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function AllocationRow({ allocation }: { allocation: PaperCopyAllocation }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-mono text-xs">{allocation.accountKey}</td>
      <td className="px-4 py-3 font-semibold">#{allocation.rank}</td>
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${allocation.sourceWallet}`}
          className="font-mono text-xs hover:text-[#297c73]"
        >
          {shortAddress(allocation.sourceWallet)}
        </Link>
      </td>
      <td className="px-4 py-3 font-mono">{allocation.score ? formatScore(allocation.score) : "-"}</td>
      <td className="px-4 py-3">{formatPercent(allocation.allocationPct)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(allocation.allocationUsd)}</td>
      <td className="px-4 py-3">
        <StatusPill
          label={allocation.active ? "active" : "inactive"}
          tone={allocation.active ? "positive" : "warning"}
        />
      </td>
    </tr>
  );
}

function PositionRow({ position }: { position: PaperPosition }) {
  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 font-mono text-xs">{position.accountKey}</td>
      <td className="px-4 py-3">
        <Link
          href={`/wallets/${position.sourceWallet}`}
          className="font-mono text-xs hover:text-[#297c73]"
        >
          {shortAddress(position.sourceWallet)}
        </Link>
      </td>
      <td className="px-4 py-3 font-semibold">{position.coin}</td>
      <td className="px-4 py-3">
        <StatusPill
          label={position.side}
          tone={position.side === "long" ? "positive" : "warning"}
        />
      </td>
      <td className="px-4 py-3 font-mono">{formatSize(position.size)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(position.entryPrice)}</td>
      <td className="px-4 py-3 font-mono">{formatLeverage(position.leverage)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(position.marginUsd)}</td>
      <td className="px-4 py-3 font-mono">{formatCurrency(position.notionalUsd)}</td>
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(position.openedAt)}</td>
    </tr>
  );
}

function FillRow({
  fill,
  minOrderNotionalUsd,
}: {
  fill: PaperCopyFill;
  minOrderNotionalUsd: string;
}) {
  const targetNotional = targetPaperNotional(fill);
  const skipDetail = skipReasonDetail(fill.skippedReason, targetNotional, minOrderNotionalUsd);

  return (
    <tr className="border-b border-line last:border-b-0">
      <td className="px-4 py-3 text-[#5b6770]">{formatDate(fill.filledAt)}</td>
      <td className="px-4 py-3 font-mono text-xs">{fill.accountKey}</td>
      <td className="px-4 py-3">
        <StatusPill
          label={fill.action}
          tone={fill.action === "skip" ? "warning" : "positive"}
        />
      </td>
      <td className="px-4 py-3">
        <p className="font-semibold">{fill.coin}</p>
        <p className="mt-1 text-xs text-[#5b6770]">{fill.side ?? "-"}</p>
      </td>
      <td className="px-4 py-3">
        <p className="font-mono">{formatCurrency(fill.notionalUsd)}</p>
        {fill.leverage ? (
          <p className="mt-1 text-xs text-[#5b6770]">
            {formatCurrency(fill.marginUsd)} margin, {formatLeverage(fill.leverage)}
          </p>
        ) : null}
        {fill.action === "skip" && targetNotional !== null ? (
          <p className="mt-1 text-xs text-[#5b6770]">target {formatCurrency(targetNotional)}</p>
        ) : null}
      </td>
      <td className={pnlClass(fill.realizedPnlUsd)}>{formatCurrency(fill.realizedPnlUsd)}</td>
      <td className="px-4 py-3 text-[#5b6770]">
        <p>{formatSkipReason(fill.skippedReason)}</p>
        {skipDetail ? <p className="mt-1 text-xs">{skipDetail}</p> : null}
      </td>
    </tr>
  );
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-10 text-center text-[#5b6770]">
        {text}
      </td>
    </tr>
  );
}

function openNotional(positions: PaperPosition[]) {
  return positions.reduce((total, position) => total + numberValue(position.notionalUsd), 0);
}

function openMargin(positions: PaperPosition[]) {
  return positions.reduce((total, position) => total + numberValue(position.marginUsd), 0);
}

function pnlClass(value: string | number | null | undefined) {
  const numericValue = value === null || value === undefined ? 0 : numberValue(value);
  return `px-4 py-3 font-mono ${numericValue >= 0 ? "text-positive" : "text-danger"}`;
}

function formatSize(value: string) {
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 6 }).format(numberValue(value));
}

function formatScore(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 1 }).format(numberValue(value));
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )} bps`;
}

function formatLeverage(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    numberValue(value),
  )}x`;
}

function formatSkipReason(reason: string | null) {
  if (!reason) {
    return "-";
  }
  const labels: Record<string, string> = {
    below_min_order_notional: "Below min order notional",
    below_min_or_cap_blocked: "Below min or allocation cap",
    execution_price_unavailable: "Execution price unavailable",
    invalid_close_size: "Invalid close size",
    invalid_price: "Invalid price",
    missing_source_start_position: "Missing source start position",
    no_matching_paper_position: "No matching paper position",
    opposite_paper_position: "Opposite paper position",
    preexisting_source_position: "Preexisting source position",
    price_drift_too_high: "Price drift too high",
    source_account_value_unavailable: "Source account value unavailable",
    source_allocation_cap_reached: "Source allocation cap reached",
    source_and_total_allocation_caps_reached: "Source and total allocation caps reached",
    total_allocation_cap_reached: "Total allocation cap reached",
    unsupported_source_fill_direction: "Unsupported source fill direction",
  };
  return labels[reason] ?? reason;
}

function skipReasonDetail(
  reason: string | null,
  targetNotional: number | null,
  minOrderNotionalUsd: string,
) {
  if (!reason || targetNotional === null) {
    return null;
  }
  const minOrderNotional = numberValue(minOrderNotionalUsd);
  const isLegacyMinOrCap = reason === "below_min_or_cap_blocked";
  if (reason === "below_min_order_notional" || isLegacyMinOrCap) {
    if (targetNotional < minOrderNotional) {
      return `Target ${formatCurrency(targetNotional)}, min ${formatCurrency(minOrderNotional)}`;
    }
    if (isLegacyMinOrCap) {
      return `Target before caps ${formatCurrency(targetNotional)}`;
    }
  }
  if (
    reason === "source_allocation_cap_reached" ||
    reason === "total_allocation_cap_reached" ||
    reason === "source_and_total_allocation_caps_reached"
  ) {
    return `Target before caps ${formatCurrency(targetNotional)}`;
  }
  return null;
}

function targetPaperNotional(fill: PaperCopyFill) {
  if (fill.allocationUsd === null || fill.sourceExposurePct === null) {
    return null;
  }
  const value = numberValue(fill.allocationUsd) * numberValue(fill.sourceExposurePct);
  return Number.isFinite(value) ? value : null;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
