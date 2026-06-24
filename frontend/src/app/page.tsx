import {
  Activity,
  ArrowUpRight,
  Database,
  RadioTower,
  ShieldCheck,
  WalletCards,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { OperationStatusStrip } from "@/components/OperationStatusStrip";
import { StatusPill } from "@/components/StatusPill";
import {
  getDatabaseStats,
  getHealth,
  getLiveEvents,
  getOperationStatuses,
  getWallets,
} from "@/lib/api";
import {
  formatBytes,
  formatCompact,
  formatCurrency,
  formatDate,
  formatInteger,
  formatMs,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type { Wallet } from "@/types/wallet";

export default async function DashboardPage() {
  const [health, databaseStats, wallets, events, operations] = await Promise.all([
    getHealth(),
    getDatabaseStats(),
    getWallets(),
    getLiveEvents(12),
    getOperationStatuses(),
  ]);

  const apiReady = health?.status === "ok";
  const enabledWallets = wallets.items.filter((wallet) => wallet.enabled).length;
  const scoredWallets = wallets.items.filter((wallet) => wallet.score).length;
  const topWallets = wallets.items
    .filter((wallet) => wallet.score)
    .sort((left, right) => numberValue(right.score?.score ?? 0) - numberValue(left.score?.score ?? 0))
    .slice(0, 5);
  const largestTable = databaseStats?.tables[0] ?? null;

  return (
    <>
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-sm font-medium text-[#5b6770]">Control dashboard</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-normal text-ink">
            Hyperliquid Copy Agent
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <StatusPill
            label={apiReady ? "api online" : "api offline"}
            tone={apiReady ? "positive" : "danger"}
          />
          <StatusPill label={health?.hyperliquidNetwork ?? "network unknown"} tone="neutral" />
          <StatusPill
            label={health?.liveTradingEnabled ? "live enabled" : "paper mode"}
            tone={health?.liveTradingEnabled ? "danger" : "positive"}
          />
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <HeroMetric
          icon={Activity}
          label="API"
          value={apiReady ? "Online" : "Offline"}
          detail={health?.version ? `v${health.version}` : "No health response"}
          tone={apiReady ? "positive" : "danger"}
        />
        <HeroMetric
          icon={Database}
          label="Database"
          value={databaseStats?.databaseSizePretty ?? "-"}
          detail={`${formatInteger(databaseStats?.tableCount)} tables`}
        />
        <HeroMetric
          icon={WalletCards}
          label="Wallet pool"
          value={formatInteger(wallets.total)}
          detail={`${formatInteger(enabledWallets)} enabled, ${formatInteger(scoredWallets)} scored`}
        />
        <HeroMetric
          icon={RadioTower}
          label="Realtime slots"
          value={`${formatInteger(health?.activeCopyWallets)}/${formatInteger(
            health?.maxRealtimeWallets,
          )}`}
          detail="Active copy capacity"
        />
      </section>

      <OperationStatusStrip initialItems={operations.items} />

      <section className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Panel
          actionHref="/database"
          actionLabel="Open database"
          icon={Database}
          title="Database Health"
        >
          <div className="grid gap-3 md:grid-cols-3">
            <DataPoint
              label="Stored fills"
              value={formatCompact(databaseStats?.fills.total)}
              detail={`${formatInteger(databaseStats?.fills.walletCount)} wallets`}
            />
            <DataPoint
              label="Notional"
              value={formatCurrency(databaseStats?.fills.totalNotionalUsd)}
              detail={`${formatCurrency(databaseStats?.fills.totalFeeUsd)} fees`}
            />
            <DataPoint
              label="Connections"
              value={`${formatInteger(databaseStats?.connections.total)}/${formatInteger(
                databaseStats?.connections.maxConnections,
              )}`}
              detail={formatPercent(databaseStats?.connections.usagePct)}
            />
          </div>
          <div className="mt-4 border-t border-line pt-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-xs font-medium uppercase text-[#5b6770]">Largest table</p>
                <p className="mt-1 text-sm font-semibold text-ink">{largestTable?.name ?? "-"}</p>
              </div>
              <p className="font-mono text-sm text-[#5b6770]">
                {largestTable ? formatBytes(largestTable.totalSizeBytes) : "-"}
              </p>
            </div>
          </div>
        </Panel>

        <Panel actionHref="/wallets" actionLabel="Open wallets" icon={WalletCards} title="Pool State">
          <div className="grid gap-3 sm:grid-cols-2">
            <DataPoint label="Eligible" value={formatInteger(databaseStats?.wallets.eligible)} />
            <DataPoint label="Unpolled" value={formatInteger(databaseStats?.wallets.unpolled)} />
            <DataPoint label="Score >= 70" value={formatInteger(databaseStats?.scores.above70)} />
            <DataPoint
              label="Score <= 0"
              value={formatInteger(databaseStats?.scores.zeroOrNegative)}
            />
          </div>
          <div className="mt-4 border-t border-line pt-4 text-sm text-[#5b6770]">
            Last fill {formatMs(databaseStats?.fills.lastFillTimeMs)}
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel actionHref="/wallets" actionLabel="Rank wallets" icon={ShieldCheck} title="Top Scores">
          <div className="divide-y divide-line">
            {topWallets.length === 0 ? (
              <EmptyState text="No scored wallets yet." />
            ) : (
              topWallets.map((wallet) => <TopWalletRow key={wallet.id} wallet={wallet} />)
            )}
          </div>
        </Panel>

        <Panel actionHref="/live-feed" actionLabel="Open feed" icon={RadioTower} title="Recent Events">
          <div className="divide-y divide-line">
            {events.items.length === 0 ? (
              <EmptyState text="No events recorded yet." />
            ) : (
              events.items.slice(0, 6).map((event, index) => (
                <div
                  key={event.id ?? `${event.type}-${event.createdAt ?? index}`}
                  className="grid gap-2 py-3 sm:grid-cols-[110px_1fr_150px]"
                >
                  <StatusPill
                    label={event.type}
                    tone={event.type === "fill" ? "positive" : "neutral"}
                  />
                  <p className="min-w-0 truncate text-sm font-medium">{event.message}</p>
                  <p className="text-left text-xs text-[#5b6770] sm:text-right">
                    {formatDate(event.createdAt)}
                  </p>
                </div>
              ))
            )}
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

function Panel({
  actionHref,
  actionLabel,
  children,
  icon: Icon,
  title,
}: {
  actionHref: string;
  actionLabel: string;
  children: ReactNode;
  icon: LucideIcon;
  title: string;
}) {
  return (
    <section className="rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <Icon className="h-4 w-4 shrink-0 text-[#5b6770]" aria-hidden="true" />
          <h2 className="truncate text-base font-semibold">{title}</h2>
        </div>
        <Link
          href={actionHref}
          className="inline-flex h-8 shrink-0 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-medium text-ink"
        >
          {actionLabel}
          <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
        </Link>
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

function TopWalletRow({ wallet }: { wallet: Wallet }) {
  const score = numberValue(wallet.score?.score ?? 0);
  const scoreTone =
    score >= 70 ? "text-positive" : score >= 45 ? "text-warning" : "text-danger";

  return (
    <Link
      href={`/wallets/${wallet.address}`}
      className="grid gap-3 py-3 hover:bg-[#f8fafb] sm:grid-cols-[1fr_90px_120px]"
    >
      <div className="min-w-0">
        <p className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold">{wallet.label || shortAddress(wallet.address)}</p>
        <p className="mt-1 font-mono text-xs text-[#5b6770]">{shortAddress(wallet.address)}</p>
      </div>
      <p className={`text-lg font-semibold ${scoreTone}`}>{formatScore(score)}</p>
      <p className="text-sm text-[#5b6770]">{formatInteger(wallet.score?.tradeCount)} trades</p>
    </Link>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-10 text-center text-sm text-[#5b6770]">{text}</div>;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
