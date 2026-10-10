"use client";

import { ArrowDown, ArrowUp, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { Address, Dialog, Pagination, TimeAgo, usePagination } from "@/components/ui/client";
import {
  buttonClass,
  Callout,
  EmptyState,
  Meter,
  Pnl,
  PnlPercent,
  scoreTone,
  Spinner,
  StatusDot,
} from "@/components/ui/primitives";
import { toneBar, toneText } from "@/components/ui/tone";
import { cx } from "@/lib/cx";
import {
  dateMs,
  decimal,
  formatInteger,
  formatMultiple,
  formatPercent,
  formatScore,
  humanize,
  toNumber,
} from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import { ADDRESS_PATTERN, SCORE_COMPONENTS, walletDisplayName, walletStatus } from "@/lib/wallets";
import type { Wallet } from "@/types/wallet";

import { useWalletActions, WalletMenu, type Notice } from "./walletActions";

type Filter = "all" | "copied" | "enabled" | "disabled" | "cooldown" | "unscored";
type SortKey = "rank" | "score" | "equityReturn" | "drawdown" | "winRate" | "profitFactor" | "trades" | "pnl" | "lastFill";

const PAGE_SIZE = 50;

export function WalletsTable({
  archived,
  followedLive,
  wallets,
}: {
  archived: boolean;
  followedLive: string[];
  wallets: Wallet[];
}) {
  const router = useRouter();
  const followed = useMemo(() => new Set(followedLive.map((wallet) => wallet.toLowerCase())), [followedLive]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ direction: "asc" | "desc"; key: SortKey }>({ direction: "desc", key: "score" });
  const [notice, setNotice] = useState<Notice | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const onNotice = useCallback((next: Notice) => setNotice(next), []);
  const { busyAddress, menuItems } = useWalletActions(onNotice);

  const counts = useMemo(() => {
    const result: Record<Filter, number> = { all: wallets.length, cooldown: 0, copied: 0, disabled: 0, enabled: 0, unscored: 0 };
    for (const wallet of wallets) {
      if (followed.has(wallet.address.toLowerCase())) {
        result.copied += 1;
      }
      if (wallet.enabled) {
        result.enabled += 1;
      } else {
        result.disabled += 1;
      }
      if (wallet.pollingTier === "cooldown" || wallet.cooldownUntil) {
        result.cooldown += 1;
      }
      if (!wallet.score) {
        result.unscored += 1;
      }
    }
    return result;
  }, [followed, wallets]);

  const rows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = wallets.filter((wallet) => {
      if (
        needle &&
        !wallet.address.toLowerCase().includes(needle) &&
        !(wallet.label ?? "").toLowerCase().includes(needle) &&
        !(wallet.notes ?? "").toLowerCase().includes(needle)
      ) {
        return false;
      }
      switch (filter) {
        case "copied":
          return followed.has(wallet.address.toLowerCase());
        case "enabled":
          return wallet.enabled;
        case "disabled":
          return !wallet.enabled;
        case "cooldown":
          return wallet.pollingTier === "cooldown" || Boolean(wallet.cooldownUntil);
        case "unscored":
          return !wallet.score;
        default:
          return true;
      }
    });
    if (archived) {
      return filtered.sort(
        (left, right) => dateMs(right.archivedAt) - dateMs(left.archivedAt) || left.address.localeCompare(right.address),
      );
    }
    const factor = sort.direction === "asc" ? 1 : -1;
    return filtered.sort((left, right) => {
      const a = sortValue(left, sort.key);
      const b = sortValue(right, sort.key);
      // Missing values always sort last.
      if (a === null && b === null) {
        return left.address.localeCompare(right.address);
      }
      if (a === null) {
        return 1;
      }
      if (b === null) {
        return -1;
      }
      return (a - b) * factor || left.address.localeCompare(right.address);
    });
  }, [archived, filter, followed, query, sort, wallets]);

  const { page, pageCount, pageItems, setPage } = usePagination(rows, PAGE_SIZE);

  const toggleSort = (key: SortKey) =>
    setSort((current) =>
      current.key === key
        ? { direction: current.direction === "desc" ? "asc" : "desc", key }
        : { direction: key === "rank" || key === "drawdown" ? "asc" : "desc", key },
    );

  const filters: Array<{ id: Filter; label: string }> = [
    { id: "all", label: "All" },
    { id: "copied", label: "Copied" },
    { id: "enabled", label: "Enabled" },
    { id: "disabled", label: "Disabled" },
    { id: "cooldown", label: "Cooldown" },
    { id: "unscored", label: "Unscored" },
  ];

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex flex-col gap-2 border-b border-line px-3.5 py-2.5 lg:flex-row lg:items-center">
        <label className="relative block min-w-0 lg:w-72">
          <span className="sr-only">Search wallets</span>
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-4" aria-hidden="true" />
          <input
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder="Search name, address, or notes"
            className="field w-full pl-8"
          />
        </label>
        {!archived ? (
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter wallets">
            {filters.map((item) => (
              <button
                key={item.id}
                type="button"
                aria-pressed={filter === item.id}
                onClick={() => {
                  setFilter(item.id);
                  setPage(0);
                }}
                className={cx(
                  "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium transition-colors",
                  filter === item.id ? "bg-fg text-canvas" : "text-fg-2 hover:bg-surface-3",
                )}
              >
                {item.label}
                <span className={cx("tabular-nums", filter === item.id ? "opacity-70" : "text-fg-4")}>
                  {counts[item.id]}
                </span>
              </button>
            ))}
          </div>
        ) : null}
        <div className="flex items-center gap-2 lg:ml-auto">
          <span className="text-xs text-fg-3">
            {formatInteger(rows.length)} of {formatInteger(wallets.length)}
          </span>
          {!archived ? (
            <button type="button" className={buttonClass({ size: "sm" })} onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              Add wallet
            </button>
          ) : null}
        </div>
      </div>

      {notice ? (
        <div className="border-b border-line px-3.5 py-2">
          <Callout
            tone={notice.tone}
            title={notice.text}
            action={
              <button
                type="button"
                onClick={() => setNotice(null)}
                className={buttonClass({ size: "xs", variant: "ghost" })}
                aria-label="Dismiss"
              >
                <X className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            }
          />
        </div>
      ) : null}

      {rows.length === 0 ? (
        <EmptyState title={wallets.length === 0 ? (archived ? "No archived wallets" : "The pool is empty") : "No wallets match"}>
          {wallets.length === 0
            ? archived
              ? "Wallets that prune rules remove from the pool are listed here and can be readmitted."
              : "Run a discovery import or add a wallet by address."
            : "Try another search or filter."}
        </EmptyState>
      ) : archived ? (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Wallet</th>
                <th>Reason</th>
                <th className="num">Archived</th>
                <th className="num">Last trade</th>
                <th>Notes</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {pageItems.map((wallet) => (
                <tr key={wallet.id}>
                  <td>
                    <Link href={`/wallets/${wallet.address}`} className="block max-w-[220px] truncate font-medium hover:underline">
                      {walletDisplayName(wallet)}
                    </Link>
                    <Address address={wallet.address} />
                  </td>
                  <td>{wallet.archiveReason ? humanize(wallet.archiveReason) : "-"}</td>
                  <td className="num text-fg-2">
                    <TimeAgo value={wallet.archivedAt} />
                  </td>
                  <td className="num text-fg-3">
                    <TimeAgo value={wallet.lastSeenFillAt} fallback="never" />
                  </td>
                  <td className="max-w-[360px]">
                    <span className="block truncate text-xs text-fg-3" title={wallet.notes ?? undefined}>
                      {wallet.notes ?? ""}
                    </span>
                  </td>
                  <td className="text-right">
                    <WalletMenu
                      busy={busyAddress === wallet.address}
                      items={menuItems(wallet, {
                        includeView: true,
                        onView: () => router.push(`/wallets/${wallet.address}`),
                      })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <SortHeader active={sort} align="num" label="#" onSort={toggleSort} sortKey="rank" title="Pool rank" />
                <th>Wallet</th>
                <SortHeader active={sort} label="Score" onSort={toggleSort} sortKey="score" />
                <th title="Profitability, consistency, risk control, copyability, and recency scores">Breakdown</th>
                <SortHeader
                  active={sort}
                  align="num"
                  label="30D return"
                  onSort={toggleSort}
                  sortKey="equityReturn"
                  title="Time-weighted return on the wallet's own equity over 30 days"
                />
                <SortHeader
                  active={sort}
                  align="num"
                  label="Max DD"
                  onSort={toggleSort}
                  sortKey="drawdown"
                  title="Largest equity drawdown over 30 days"
                />
                <SortHeader active={sort} align="num" label="Win rate" onSort={toggleSort} sortKey="winRate" />
                <SortHeader active={sort} align="num" label="PF" onSort={toggleSort} sortKey="profitFactor" title="Profit factor" />
                <SortHeader active={sort} align="num" label="Trades" onSort={toggleSort} sortKey="trades" title="Closed trades in the scoring window" />
                <SortHeader
                  active={sort}
                  align="num"
                  label="Realized PnL"
                  onSort={toggleSort}
                  sortKey="pnl"
                  title="The wallet's own realized PnL in the scoring window"
                />
                <th>Status</th>
                <SortHeader active={sort} align="num" label="Last trade" onSort={toggleSort} sortKey="lastFill" />
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {pageItems.map((wallet) => {
                const score = wallet.score;
                const status = walletStatus(wallet, followed);
                return (
                  <tr key={wallet.id}>
                    <td className="num text-fg-3">{wallet.poolRank ?? "-"}</td>
                    <td>
                      <Link
                        href={`/wallets/${wallet.address}`}
                        className="block max-w-[220px] truncate font-medium hover:underline"
                        title={wallet.notes ?? undefined}
                      >
                        {walletDisplayName(wallet)}
                      </Link>
                      <Address address={wallet.address} />
                    </td>
                    <td className="w-32">
                      {score ? (
                        <div className="flex items-center gap-2">
                          <span className={cx("w-8 font-medium tabular-nums", toneText[scoreTone(score.score)])}>
                            {formatScore(score.score)}
                          </span>
                          <Meter className="w-14" value={decimal(score.score) / 100} tone={scoreTone(score.score)} />
                        </div>
                      ) : (
                        <span className="text-fg-4">Unscored</span>
                      )}
                    </td>
                    <td>{score ? <ScoreBreakdown wallet={wallet} /> : null}</td>
                    <td className="num">
                      <PnlPercent value={score?.equityReturnPct} />
                    </td>
                    <td className="num text-fg-2">{formatPercent(score?.maxDrawdownPct)}</td>
                    <td className="num text-fg-2">{formatPercent(score?.winRate, { decimals: 0 })}</td>
                    <td className="num text-fg-2">{formatMultiple(score?.profitFactor)}</td>
                    <td className="num">{formatInteger(score?.tradeCount)}</td>
                    <td className="num">{score ? <Pnl value={score.copyablePnlUsd} compact /> : "-"}</td>
                    <td>
                      <StatusDot tone={status.tone} label={status.label} title={status.detail} />
                    </td>
                    <td className="num text-fg-3">
                      <TimeAgo value={wallet.lastSeenFillAt} />
                    </td>
                    <td className="text-right">
                      <WalletMenu
                        busy={busyAddress === wallet.address}
                        items={menuItems(wallet, {
                          includeView: true,
                          onView: () => router.push(`/wallets/${wallet.address}`),
                        })}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <Pagination page={page} pageCount={pageCount} pageSize={PAGE_SIZE} total={rows.length} onPageChange={setPage} />
      <AddWalletDialog open={addOpen} onClose={() => setAddOpen(false)} onAdded={onNotice} />
    </section>
  );
}

function sortValue(wallet: Wallet, key: SortKey): number | null {
  const score = wallet.score;
  switch (key) {
    case "rank":
      return wallet.poolRank;
    case "score":
      return score ? decimal(score.score) : null;
    case "equityReturn":
      return toNumber(score?.equityReturnPct);
    case "drawdown":
      return toNumber(score?.maxDrawdownPct);
    case "winRate":
      return toNumber(score?.winRate);
    case "profitFactor":
      return toNumber(score?.profitFactor);
    case "trades":
      return score ? score.tradeCount : null;
    case "pnl":
      return toNumber(score?.copyablePnlUsd);
    case "lastFill":
      return wallet.lastSeenFillAt ? new Date(wallet.lastSeenFillAt).getTime() : null;
  }
}

function SortHeader({
  active,
  align,
  label,
  onSort,
  sortKey,
  title,
}: {
  active: { direction: "asc" | "desc"; key: SortKey };
  align?: "num";
  label: ReactNode;
  onSort: (key: SortKey) => void;
  sortKey: SortKey;
  title?: string;
}) {
  const isActive = active.key === sortKey;
  return (
    <th className={align} title={title} aria-sort={isActive ? (active.direction === "asc" ? "ascending" : "descending") : undefined}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={cx("inline-flex items-center gap-0.5 hover:text-fg", isActive && "text-fg")}
      >
        {label}
        {isActive ? (
          active.direction === "asc" ? (
            <ArrowUp className="h-3 w-3" aria-hidden="true" />
          ) : (
            <ArrowDown className="h-3 w-3" aria-hidden="true" />
          )
        ) : null}
      </button>
    </th>
  );
}

/** Five small bars, one per score component, with the values in the tooltip. */
export function ScoreBreakdown({ wallet }: { wallet: Wallet }) {
  const score = wallet.score;
  if (!score) {
    return null;
  }
  const penalty = decimal(score.penaltyScore);
  const title = [
    ...SCORE_COMPONENTS.map((component) => `${component.label}: ${formatScore(component.value(score))}`),
    `Penalty: ${formatScore(penalty)}`,
  ].join("\n");
  return (
    <span className="inline-flex h-4 items-end gap-[3px]" title={title} aria-label={title.replaceAll("\n", ", ")} role="img">
      {SCORE_COMPONENTS.map((component) => {
        const value = decimal(component.value(score));
        return (
          <span key={component.key} className="relative h-4 w-1.5 overflow-hidden rounded-[1px] bg-surface-3">
            <span
              className={cx("absolute inset-x-0 bottom-0", toneBar[scoreTone(value)])}
              style={{ height: `${Math.max(6, Math.min(100, value))}%` }}
            />
          </span>
        );
      })}
      {penalty > 0 ? <span className="ml-1 text-2xs text-negative">-{formatScore(penalty)}</span> : null}
    </span>
  );
}

function AddWalletDialog({
  onAdded,
  onClose,
  open,
}: {
  onAdded: (notice: Notice) => void;
  onClose: () => void;
  open: boolean;
}) {
  const router = useRouter();
  const [address, setAddress] = useState("");
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    const normalized = address.trim().toLowerCase();
    if (!ADDRESS_PATTERN.test(normalized)) {
      setError("Enter a 0x address with 40 hex characters.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await apiRequest("/wallets", {
        body: { address: normalized, enabled: true, label: label.trim() || null, notes: notes.trim() || null },
        fallback: "Could not add the wallet",
        method: "POST",
      });
      setAddress("");
      setLabel("");
      setNotes("");
      onAdded({ text: `Added ${normalized}. Import its fills and score it to rank it.`, tone: "positive" });
      onClose();
      router.refresh();
    } catch (submitError) {
      setError(errorMessage(submitError, "Could not add the wallet."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add a wallet to the pool"
      description="The wallet is polled, scored, and can become a copy source if it ranks high enough."
      footer={
        <>
          <button type="button" className={buttonClass()} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="add-wallet-form" className={buttonClass({ variant: "primary" })} disabled={saving}>
            {saving ? <Spinner /> : <Plus className="h-3.5 w-3.5" aria-hidden="true" />}
            Add wallet
          </button>
        </>
      }
    >
      <form
        id="add-wallet-form"
        className="grid gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <label className="grid gap-1">
          <span className="text-sm font-medium">Address</span>
          <input
            data-autofocus
            className="field font-mono text-xs"
            onChange={(event) => setAddress(event.target.value)}
            placeholder="0x..."
            spellCheck={false}
            value={address}
          />
        </label>
        <label className="grid gap-1">
          <span className="text-sm font-medium">
            Name <span className="font-normal text-fg-3">(optional)</span>
          </span>
          <input className="field" onChange={(event) => setLabel(event.target.value)} value={label} />
        </label>
        <label className="grid gap-1">
          <span className="text-sm font-medium">
            Notes <span className="font-normal text-fg-3">(optional)</span>
          </span>
          <textarea
            className="field h-auto min-h-16 py-1.5"
            onChange={(event) => setNotes(event.target.value)}
            value={notes}
          />
        </label>
        {error ? <p className="text-sm text-negative">{error}</p> : null}
      </form>
    </Dialog>
  );
}
