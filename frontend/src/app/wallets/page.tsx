import {
  Activity,
  BarChart3,
  Clock3,
  Search,
  ShieldCheck,
  WalletCards,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { AddWalletForm } from "@/components/AddWalletForm";
import { DashboardMetric } from "@/components/DashboardSurface";
import { HeaderRefreshButton, HeaderUpdatedLabel } from "@/components/HeaderRefresh";
import { PageTopPanel } from "@/components/PageTopPanel";
import { ScoreWalletsButton } from "@/components/ScoreWalletsButton";
import { StatusPill } from "@/components/StatusPill";
import { WalletActions } from "@/components/WalletActions";
import { getDatabaseStats, getWallets } from "@/lib/api";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatMs,
  formatScore,
  numberValue,
} from "@/lib/format";
import type { Wallet } from "@/types/wallet";

type WalletsPageProps = {
  searchParams?: Promise<{
    q?: string | string[];
  }>;
};

export default async function WalletsPage({ searchParams }: WalletsPageProps) {
  const params = await searchParams;
  const query = searchParamValue(params?.q);
  const [wallets, databaseStats] = await Promise.all([getWallets(query), getDatabaseStats()]);
  const isSearching = query.length > 0;
  const enabledCount = wallets.items.filter((wallet) => wallet.enabled).length;
  const eligibleCount = wallets.items.filter((wallet) => wallet.eligible).length;
  const copyCount = wallets.items.filter((wallet) => wallet.copyEnabled).length;
  const scoredCount = wallets.items.filter((wallet) => wallet.score).length;
  const sortedWallets = [...wallets.items].sort(
    (left, right) => numberValue(right.score?.score ?? -1) - numberValue(left.score?.score ?? -1),
  );

  return (
    <>
      <PageTopPanel
        eyebrow="Research pool"
        icon={WalletCards}
        title="Wallet Pool"
        actions={
          <>
            {databaseStats ? (
              <HeaderUpdatedLabel label={`Updated ${formatDate(databaseStats.measuredAt)}`} />
            ) : null}
            <ScoreWalletsButton />
            <StatusPill
              label={
                isSearching
                  ? `${formatInteger(wallets.total)} matching`
                  : `${formatInteger(wallets.total)} monitored`
              }
              tone="neutral"
            />
            <StatusPill label={`${enabledCount} enabled`} tone="positive" />
            <StatusPill label={`${eligibleCount} eligible`} tone="neutral" />
          </>
        }
        refresh={<HeaderRefreshButton title="Refresh wallet pool data" />}
      />

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <PoolMetric
          icon={WalletCards}
          label="Pool"
          value={formatInteger(wallets.total)}
          detail={`${formatInteger(enabledCount)} enabled`}
        />
        <PoolMetric
          icon={ShieldCheck}
          label="Scored"
          value={`${formatInteger(scoredCount)}/${formatInteger(wallets.total)}`}
          detail={`${formatInteger(databaseStats?.scores.above70)} score >= 70`}
        />
        <PoolMetric
          icon={Activity}
          label="Copy ready"
          value={formatInteger(copyCount)}
          detail={`${formatInteger(eligibleCount)} eligible`}
        />
        <PoolMetric
          icon={BarChart3}
          label="Stored fills"
          value={formatInteger(databaseStats?.fills.total)}
          detail={`${formatInteger(databaseStats?.fills.walletCount)} wallets with fills`}
        />
        <PoolMetric
          icon={Clock3}
          label="Unpolled"
          value={formatInteger(databaseStats?.wallets.unpolled)}
          detail={`Last fill ${formatMs(databaseStats?.fills.lastFillTimeMs)}`}
          tone={databaseStats?.wallets.unpolled ? "warning" : "neutral"}
        />
      </section>

      <AddWalletForm />

      <section className="ui-panel overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-line px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-semibold">Wallet Rankings</h2>
            <p className="mt-1 text-sm text-muted">
              Sorted by final score; unscored wallets stay at the bottom.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <StatusPill label={`${formatInteger(databaseStats?.wallets.stale24h)} stale 24h`} />
            <StatusPill label={`${formatInteger(databaseStats?.scores.zeroOrNegative)} score <= 0`} />
          </div>
        </div>
        <div className="border-b border-line px-4 py-3">
          <form action="/wallets" className="flex flex-col gap-2 lg:flex-row lg:items-center">
            <label className="relative block min-w-0 flex-1">
              <span className="sr-only">Search wallet address or label</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted"
                aria-hidden="true"
              />
              <input
                type="search"
                name="q"
                defaultValue={query}
                placeholder="Search address or label"
                className="ui-control w-full pl-9 pr-3"
              />
            </label>
            <div className="flex shrink-0 gap-2">
              <button
                type="submit"
                className="ui-button-primary"
              >
                <Search className="h-4 w-4" aria-hidden="true" />
                Search
              </button>
              {isSearching ? (
                <Link
                  href="/wallets"
                  className="ui-button-secondary"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                  Clear
                </Link>
              ) : null}
            </div>
          </form>
          {isSearching ? (
            <p className="mt-2 text-sm text-muted">
              Showing {formatInteger(wallets.items.length)} of {formatInteger(wallets.total)} matches for
              <span className="font-mono"> {query}</span>.
            </p>
          ) : null}
        </div>
        <div className="overflow-x-auto">
          <table className="ui-table min-w-[1220px] text-sm">
            <thead className="ui-table-head">
              <tr>
                <th scope="col" className="px-3 py-2.5 font-semibold">Wallet</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Pool rank</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Final score</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">PnL / risk</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Copyability</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">State</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Activity</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedWallets.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-10 text-center text-muted">
                    {isSearching ? "No wallets match this search." : "No wallets added yet."}
                  </td>
                </tr>
              ) : (
                sortedWallets.map((wallet) => <WalletRow key={wallet.id} wallet={wallet} />)
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

function PoolMetric({
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: "warning" | "neutral";
  value: string;
}) {
  return <DashboardMetric detail={detail} icon={Icon} label={label} tone={tone} value={value} />;
}

function WalletRow({ wallet }: { wallet: Wallet }) {
  return (
    <tr className="ui-table-row">
      <td className="px-3 py-2.5 align-top">
        <div className="flex min-w-0 flex-col gap-1">
          <Link
            href={`/wallets/${wallet.address}`}
            className="min-w-0 max-w-full whitespace-normal break-words font-semibold hover:text-brand"
          >
            {wallet.label || "Unlabeled"}
          </Link>
          <Link
            href={`/wallets/${wallet.address}`}
            className="font-mono text-xs text-muted hover:text-ink"
          >
            {shortAddress(wallet.address)}
          </Link>
          {wallet.notes ? (
            <p className="mt-1 min-w-0 max-w-full whitespace-normal break-words text-xs text-muted">{wallet.notes}</p>
          ) : null}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <span className="font-mono text-sm font-semibold">
          {wallet.poolRank ? `#${formatInteger(wallet.poolRank)}` : "Unranked"}
        </span>
      </td>
      <td className="px-3 py-2.5 align-top">
        <ScoreCell wallet={wallet} />
      </td>
      <td className="px-3 py-2.5 align-top">
        <ScorePair
          primaryLabel="PnL"
          primaryValue={wallet.score?.pnlScore}
          secondaryLabel="Risk"
          secondaryValue={wallet.score?.riskScore}
        />
      </td>
      <td className="px-3 py-2.5 align-top">
        <div className="grid gap-1">
          <span className="font-semibold">
            {wallet.score ? formatScore(wallet.score.copyabilityScore) : "-"}
          </span>
          <span className="text-xs text-muted">
            {wallet.score ? `${formatInteger(wallet.score.tradeCount)} trades` : "No score"}
          </span>
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <div className="flex max-w-[220px] flex-wrap gap-1">
          <StatusPill
            label={wallet.enabled ? "enabled" : "disabled"}
            tone={wallet.enabled ? "positive" : "warning"}
          />
          <StatusPill label={wallet.pollingTier} tone="neutral" />
          {wallet.copyEnabled ? <StatusPill label="copy" tone="positive" /> : null}
          {wallet.cooldownUntil ? <StatusPill label="cooldown" tone="warning" /> : null}
        </div>
      </td>
      <td className="px-3 py-2.5 align-top text-muted">
        <div className="grid gap-1">
          <span>Last fill {wallet.lastSeenFillAt ? formatDate(wallet.lastSeenFillAt) : "-"}</span>
          <span>Poll {wallet.lastPolledAt ? formatDate(wallet.lastPolledAt) : "Never"}</span>
        </div>
      </td>
      <td className="px-3 py-2.5 align-top">
        <WalletActions wallet={wallet} />
      </td>
    </tr>
  );
}

function ScoreCell({ wallet }: { wallet: Wallet }) {
  if (!wallet.score) {
    return <span className="text-muted">Unscored</span>;
  }

  const score = numberValue(wallet.score.score);

  return (
    <div className="min-w-[130px]">
      <div className="flex items-center justify-between gap-3">
        <span className={`text-xl font-semibold ${scoreClass(score)}`}>{formatScore(score)}</span>
        <span className="text-xs text-muted">{formatCurrency(wallet.score.copyablePnlUsd)}</span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-line">
        <div
          className={score >= 70 ? "h-full bg-positive" : score >= 45 ? "h-full bg-warning" : "h-full bg-danger"}
          style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
        />
      </div>
    </div>
  );
}

function ScorePair({
  primaryLabel,
  primaryValue,
  secondaryLabel,
  secondaryValue,
}: {
  primaryLabel: string;
  primaryValue: string | undefined;
  secondaryLabel: string;
  secondaryValue: string | undefined;
}) {
  return (
    <div className="grid gap-1">
      <span className="font-semibold">
        {primaryLabel} {primaryValue ? formatScore(primaryValue) : "-"}
      </span>
      <span className="text-xs text-muted">
        {secondaryLabel} {secondaryValue ? formatScore(secondaryValue) : "-"}
      </span>
    </div>
  );
}

function scoreClass(score: number) {
  if (score >= 70) {
    return "text-positive";
  }
  if (score >= 45) {
    return "text-warning";
  }
  return "text-danger";
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function searchParamValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return (value[0] ?? "").trim();
  }
  return (value ?? "").trim();
}
