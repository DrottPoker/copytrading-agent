import { ArrowRight, ChevronRight } from "lucide-react";
import Link from "next/link";

import { JobsPanel } from "@/components/JobsPanel";
import { RefreshControl, TimeAgo } from "@/components/ui/client";
import {
  Badge,
  EmptyState,
  LinkButton,
  Meter,
  PageHeader,
  Panel,
  Pnl,
  PnlPercent,
  scoreTone,
  StatusDot,
  Usd,
} from "@/components/ui/primitives";
import { toneText } from "@/components/ui/tone";
import {
  getAnalytics,
  getLiveEvents,
  getOperationStatuses,
  getOpsHealth,
  getPaperTradingSummary,
  getTradingAccounts,
  getWallets,
} from "@/lib/api";
import { buildAttentionItems } from "@/lib/attention";
import { cx } from "@/lib/cx";
import { eventKey, eventTone, eventTypeLabel } from "@/lib/events";
import { decimal, formatInteger, formatPercent, formatScore, formatTime, pluralize } from "@/lib/format";
import { accountSummaryFor, buildAccountOptions } from "@/lib/trading/accounts";
import type { AccountSummary } from "@/lib/trading/types";
import type { Wallet } from "@/types/wallet";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  const [ops, wallets, analytics, summary, tradingAccounts, events, operations] = await Promise.all([
    getOpsHealth(),
    getWallets(),
    getAnalytics(),
    getPaperTradingSummary({ closedTradeLimit: 1, recentFillLimit: 1 }),
    getTradingAccounts(),
    getLiveEvents(14),
    getOperationStatuses(),
  ]);

  const accounts = buildAccountOptions(summary, tradingAccounts).map((option) =>
    accountSummaryFor(option, tradingAccounts),
  );
  const attention = buildAttentionItems({ ops, summary, tradingAccounts });
  const scored = wallets.items.filter((wallet) => wallet.score);
  const topWallets = [...scored]
    .sort((left, right) => decimal(right.score?.score) - decimal(left.score?.score))
    .slice(0, 8);
  const discovery = (analytics?.discoverySources ?? []).reduce(
    (totals, row) => ({
      candidates: totals.candidates + row.total,
      passed: totals.passed + row.accepted + row.promoted,
      promoted: totals.promoted + row.promoted,
      sources: totals.sources + 1,
    }),
    { candidates: 0, passed: 0, promoted: 0, sources: 0 },
  );
  const strongWallets = scored.filter((wallet) => decimal(wallet.score?.score) >= 70).length;
  const realtime = summary.realtimeMonitoring;
  const copyingCount = realtime.monitoredWallets.length;
  const copySources = new Set(
    [...realtime.desiredWallets, ...realtime.monitoredWallets].map((wallet) => wallet.toLowerCase()),
  );

  return (
    <>
      <PageHeader
        title="Overview"
        description={overviewSentence(accounts, copyingCount, summary.policy.topWalletCount)}
        actions={<RefreshControl intervalMs={15_000} updatedAt={ops?.measuredAt ?? summary.updatedAt} />}
      />

      <Panel
        title="Accounts"
        meta={accounts.length > 0 ? pluralize(accounts.length, "account") : undefined}
        actions={
          <LinkButton href="/trading" size="sm" variant="ghost">
            Open trading <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </LinkButton>
        }
      >
        {accounts.length === 0 ? (
          <EmptyState
            title="No trading account yet"
            action={
              <LinkButton href="/trading?create=paper" size="sm" variant="primary">
                Create a paper account
              </LinkButton>
            }
          >
            Wallets are still discovered and ranked. A paper account copies the top ranked wallets with simulated
            money so you can see how copying would have done.
          </EmptyState>
        ) : (
          <AccountsTable accounts={accounts} />
        )}
      </Panel>

      <Pipeline
        candidates={discovery.candidates}
        copied={copySources.size}
        copying={copyingCount}
        passed={discovery.passed}
        pool={wallets.total}
        promoted={discovery.promoted}
        scored={scored.length}
        sourceCount={discovery.sources}
        strong={strongWallets}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel
          title="Needs attention"
          meta={attention.length > 0 ? pluralize(attention.length, "item") : undefined}
        >
          {attention.length === 0 ? (
            <div className="flex items-center gap-2 px-3.5 py-4 text-sm text-fg-2">
              <StatusDot tone="positive" />
              Everything looks healthy. Workers, data sources, and accounts report no problems.
            </div>
          ) : (
            <ul className="max-h-[21.25rem] divide-y divide-line overflow-y-auto">
              {attention.map((item, index) => (
                <li key={`${item.title}:${index}`}>
                  <Link href={item.href} className="group flex items-start gap-2.5 px-3.5 py-2 hover:bg-surface-2">
                    <StatusDot tone={item.tone} className="mt-1.5" />
                    <span className="min-w-0 flex-1">
                      <span className={cx("block text-sm font-medium", toneText[item.tone])}>{item.title}</span>
                      <span className="block truncate text-xs text-fg-3" title={item.detail}>
                        {item.detail}
                      </span>
                    </span>
                    <ChevronRight className="mt-1 h-3.5 w-3.5 shrink-0 text-fg-4 group-hover:text-fg-2" aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <JobsPanel initialItems={operations.items} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Panel
          title="Top ranked wallets"
          meta={`${formatInteger(scored.length)} scored`}
          actions={
            <LinkButton href="/wallets" size="sm" variant="ghost">
              All wallets <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </LinkButton>
          }
        >
          {topWallets.length === 0 ? (
            <EmptyState title="No scored wallets yet">Run wallet scoring once the pool has trade history.</EmptyState>
          ) : (
            <TopWalletsTable wallets={topWallets} copySources={copySources} />
          )}
        </Panel>

        <Panel
          title="Recent activity"
          actions={
            <LinkButton href="/activity" size="sm" variant="ghost">
              Activity feed <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </LinkButton>
          }
        >
          {events.items.length === 0 ? (
            <EmptyState title="No events recorded yet" />
          ) : (
            <ul className="divide-y divide-line">
              {events.items.slice(0, 12).map((event, index) => (
                <li key={eventKey(event, index)} className="flex items-center gap-3 px-3.5 py-1.5 text-sm">
                  <span className="w-16 shrink-0 tabular-nums text-xs text-fg-3">{formatTime(event.createdAt)}</span>
                  <span className="w-28 shrink-0">
                    <StatusDot tone={eventTone(event)} label={<span className="text-xs text-fg-2">{eventTypeLabel(event.type)}</span>} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-fg" title={event.message}>
                    {event.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </>
  );
}

function overviewSentence(accounts: AccountSummary[], copyingCount: number, topWalletCount: number) {
  if (accounts.length === 0) {
    return "No trading account yet. The system keeps discovering and ranking wallets in the background.";
  }
  const trading = accounts.filter((account) => account.state === "trading");
  const openPositions = accounts.reduce((total, account) => total + account.openPositionCount, 0);
  const tradingText =
    trading.length === 0
      ? "No account is trading right now."
      : `${trading.map((account) => account.label).join(" and ")} ${trading.length === 1 ? "is" : "are"} copying the top ${topWalletCount} wallets (${copyingCount} on a live feed).`;
  return `${tradingText} ${openPositions === 0 ? "No positions are open." : `${pluralize(openPositions, "position")} open.`}`;
}

function AccountsTable({ accounts }: { accounts: AccountSummary[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Account</th>
            <th>Status</th>
            <th className="num">Equity</th>
            <th className="num">Total PnL</th>
            <th className="num">Return</th>
            <th className="num">Unrealized</th>
            <th className="num">Open positions</th>
            <th className="num">Exposure</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((account) => (
            <tr key={account.key}>
              <td>
                <Link
                  href={`/trading?account=${encodeURIComponent(account.key)}`}
                  className="inline-flex items-center gap-2 font-medium text-fg hover:underline"
                >
                  {account.label}
                  <Badge tone={account.accountType === "live" ? "negative" : "neutral"}>
                    {account.accountType === "live" ? "Live" : "Paper"}
                  </Badge>
                </Link>
              </td>
              <td>
                <StatusDot
                  tone={account.stateTone}
                  pulse={account.state === "trading"}
                  label={account.stateLabel}
                  title={account.stateDetail ?? undefined}
                />
              </td>
              <td className="num font-medium">
                <Usd value={account.equityUsd} />
              </td>
              <td className="num">
                <Pnl value={account.totalPnlUsd} />
              </td>
              <td className="num">
                <PnlPercent value={account.returnPct} />
              </td>
              <td className="num">
                <Pnl value={account.unrealizedPnlUsd} />
              </td>
              <td className="num">{formatInteger(account.openPositionCount)}</td>
              <td className="num">
                <Usd value={account.openNotionalUsd} />
                <span className="sub">
                  {account.equityUsd > 0 ? `${formatPercent(account.openNotionalUsd / account.equityUsd)} of equity` : "-"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Pipeline({
  candidates,
  copied,
  copying,
  passed,
  pool,
  promoted,
  scored,
  sourceCount,
  strong,
}: {
  candidates: number;
  copied: number;
  copying: number;
  passed: number;
  pool: number;
  promoted: number;
  scored: number;
  sourceCount: number;
  strong: number;
}) {
  const steps = [
    {
      detail: `from ${pluralize(sourceCount, "leaderboard source")}`,
      href: "/discovery",
      label: "Discovered",
      value: candidates,
    },
    {
      detail: candidates > 0 ? `${formatPercent(passed / candidates)} pass the prefilter` : "prefilter not run yet",
      href: "/discovery?status=accepted",
      label: "Passed filters",
      value: passed,
    },
    {
      detail: `${formatInteger(promoted)} admitted by discovery`,
      href: "/wallets",
      label: "In the pool",
      value: pool,
    },
    {
      detail: `${formatInteger(strong)} with score 70 or more`,
      href: "/wallets",
      label: "Scored",
      value: scored,
    },
    {
      detail: `${formatInteger(copying)} confirmed on the realtime feed`,
      href: "/trading",
      label: "Followed live",
      value: copied,
    },
  ];
  return (
    <section aria-label="Wallet pipeline" className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex items-center justify-between border-b border-line px-3.5 py-2">
        <h2 className="text-sm font-semibold">Wallet pipeline</h2>
        <p className="hidden text-xs text-fg-3 sm:block">
          How wallets move from leaderboards to being copied
        </p>
      </div>
      <ol className="-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        {steps.map((step, index) => (
          <li key={step.label} className="relative border-b border-r border-line">
            <Link href={step.href} className="block h-full px-3.5 py-2.5 hover:bg-surface-2">
              <p className="flex items-center gap-1.5 text-xs text-fg-3">
                <span className="tabular-nums text-fg-4">{index + 1}</span>
                {step.label}
              </p>
              <p className="mt-0.5 text-xl font-semibold tabular-nums">{formatInteger(step.value)}</p>
              <p className="truncate text-xs text-fg-3" title={step.detail}>
                {step.detail}
              </p>
            </Link>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TopWalletsTable({ copySources, wallets }: { copySources: Set<string>; wallets: Wallet[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Wallet</th>
            <th>Score</th>
            <th className="num" title="Time-weighted return on the wallet's own equity over 30 days">
              30D return
            </th>
            <th className="num" title="Largest equity drawdown over 30 days">
              Max DD
            </th>
            <th className="num">Trades</th>
            <th className="num">Last trade</th>
          </tr>
        </thead>
        <tbody>
          {wallets.map((wallet, index) => {
            const score = wallet.score;
            return (
              <tr key={wallet.id}>
                <td className="num text-fg-3">{wallet.poolRank ?? index + 1}</td>
                <td className="max-w-[13.75rem]">
                  <Link href={`/wallets/${wallet.address}`} className="block truncate font-medium hover:underline">
                    {wallet.label || wallet.address}
                  </Link>
                  {copySources.has(wallet.address.toLowerCase()) ? <span className="sub">Followed live</span> : null}
                </td>
                <td className="w-36">
                  <div className="flex items-center gap-2">
                    <span className={cx("w-9 tabular-nums font-medium", toneText[scoreTone(score?.score)])}>
                      {formatScore(score?.score)}
                    </span>
                    <Meter className="flex-1" value={decimal(score?.score) / 100} tone={scoreTone(score?.score)} />
                  </div>
                </td>
                <td className="num">
                  <PnlPercent value={score?.equityReturnPct} />
                </td>
                <td className="num text-fg-2">{formatPercent(score?.maxDrawdownPct)}</td>
                <td className="num">{formatInteger(score?.tradeCount)}</td>
                <td className="num text-fg-3">
                  <TimeAgo value={wallet.lastSeenFillAt} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
