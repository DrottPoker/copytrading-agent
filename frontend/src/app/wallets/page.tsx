import type { Metadata } from "next";
import Link from "next/link";

import { RefreshControl } from "@/components/ui/client";
import { PageHeader, Stat, StatStrip } from "@/components/ui/primitives";
import { ScoreWalletsButton } from "@/components/wallets/walletActions";
import { WalletsTable } from "@/components/wallets/WalletsTable";
import { getDatabaseStats, getPaperTradingSummary, getWallets } from "@/lib/api";
import { cx } from "@/lib/cx";
import { decimal, formatCompact, formatInteger } from "@/lib/format";

export const metadata: Metadata = { title: "Wallets" };
export const dynamic = "force-dynamic";

export default async function WalletsPage({
  searchParams,
}: {
  searchParams?: Promise<{ view?: string | string[] }>;
}) {
  const params = await searchParams;
  const view = (Array.isArray(params?.view) ? params?.view[0] : params?.view) === "archived" ? "archived" : "pool";
  const [pool, archived, stats, summary] = await Promise.all([
    getWallets(),
    getWallets({ archived: true }),
    getDatabaseStats(),
    getPaperTradingSummary({ closedTradeLimit: 1, includeMarketPrices: false, recentFillLimit: 1 }),
  ]);
  const followedLive = [
    ...summary.realtimeMonitoring.desiredWallets,
    ...summary.realtimeMonitoring.monitoredWallets,
  ];
  const scored = pool.items.filter((wallet) => wallet.score);
  const strong = scored.filter((wallet) => decimal(wallet.score?.score) >= 70).length;
  const enabled = pool.items.filter((wallet) => wallet.enabled).length;

  return (
    <>
      <PageHeader
        title="Wallets"
        description={`The pool of traders the system watches and ranks by their own trading history. The top ${summary.policy.topWalletCount} by score are copied.`}
        actions={
          <>
            <RefreshControl updatedAt={stats?.measuredAt} />
            <ScoreWalletsButton />
          </>
        }
      />

      <StatStrip className="lg:grid-cols-6">
        <Stat label="In the pool" value={formatInteger(pool.total)} sub={`${formatInteger(enabled)} enabled`} />
        <Stat
          label="Scored"
          value={formatInteger(scored.length)}
          sub={`${formatInteger(strong)} with score 70 or more`}
        />
        <Stat
          label="Followed live"
          hint="Wallets with a realtime subscription: the copy sources and wallets we still hold positions from"
          value={formatInteger(new Set(followedLive.map((wallet) => wallet.toLowerCase())).size)}
          sub={`top ${summary.policy.topWalletCount} are copied`}
        />
        <Stat
          label="Stored fills"
          value={formatCompact(stats?.fills.total)}
          sub={`${formatInteger(stats?.fills.walletCount)} wallets with history`}
        />
        <Stat
          label="Not polled in 24h"
          tone={(stats?.wallets.stale24h ?? 0) > 0 ? "warning" : "neutral"}
          value={formatInteger(stats?.wallets.stale24h)}
          sub={`${formatInteger(stats?.wallets.unpolled)} never polled`}
        />
        <Stat
          label="Archived"
          hint="Removed by prune rules. History is kept and they can be readmitted after 30 days."
          value={formatInteger(archived.total)}
          sub={<Link href="/wallets?view=archived" className="hover:underline">show archived</Link>}
        />
      </StatStrip>

      <nav aria-label="Wallet lists" className="flex gap-4 border-b border-line">
        {[
          { count: pool.total, href: "/wallets", id: "pool", label: "Pool" },
          { count: archived.total, href: "/wallets?view=archived", id: "archived", label: "Archived" },
        ].map((tab) => (
          <Link
            key={tab.id}
            href={tab.href}
            aria-current={view === tab.id ? "page" : undefined}
            className={cx(
              "-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 text-sm",
              view === tab.id ? "border-fg font-medium text-fg" : "border-transparent text-fg-3 hover:text-fg",
            )}
          >
            {tab.label}
            <span className="rounded bg-surface-3 px-1 text-2xs tabular-nums text-fg-3">{tab.count}</span>
          </Link>
        ))}
      </nav>

      <WalletsTable
        key={view}
        archived={view === "archived"}
        followedLive={followedLive}
        wallets={view === "archived" ? archived.items : pool.items}
      />
    </>
  );
}
