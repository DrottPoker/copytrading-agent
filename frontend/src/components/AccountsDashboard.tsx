"use client";

import {
  Activity,
  BarChart3,
  Clock,
  Layers,
  LineChart,
  Loader2,
  Play,
  Plus,
  Square,
  Target,
  TrendingDown,
  TrendingUp,
  WalletCards,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import {
  formatCurrency,
  formatDate,
  formatInteger,
  formatPercent,
  formatScore,
  numberValue,
} from "@/lib/format";
import type {
  PaperClosedTrade,
  PaperCopyAllocation,
  PaperCopyFill,
  PaperPosition,
  PaperTradingAccount,
  PaperTradingSummaryResponse,
} from "@/types/paper";

import { HeaderRefreshButton, HeaderUpdatedLabel } from "./HeaderRefresh";
import { PageTopPanel } from "./PageTopPanel";
import { StatusPill } from "./StatusPill";

const ACCOUNT_REFRESH_MS = 4000;
const ACCOUNT_SUMMARY_LIMIT = 250;
const SELECTED_ACCOUNT_STORAGE_KEY = "copyagent.accounts.selectedAccountKey";

type Tone = "positive" | "warning" | "danger" | "neutral";
type TradingAction = "start" | "stop" | "close-all-and-stop";
type CreateAccountType = "paper" | "live";

type AccountMetrics = {
  allocationUsd: number;
  allocationUsedPct: number | null;
  averageClosedPnlUsd: number;
  closedNetPnlUsd: number;
  copiedFillCount: number;
  exposureRatio: number | null;
  netEquityUsd: number;
  remainingAllocationUsd: number;
  returnPct: number | null;
  skippedFillCount: number;
  winRate: number | null;
};

type SourceRow = {
  allocationUsd: number;
  closedNetPnlUsd: number;
  closedTradeCount: number;
  copiedFillCount: number;
  lastActivityAt: string | null;
  openMarginUsd: number;
  openNotionalUsd: number;
  openPositionCount: number;
  poolRank: number | null;
  remainingAllocationUsd: number;
  score: string | null;
  skippedFillCount: number;
  sourceLabel: string | null;
  sourceStatus: PaperCopyAllocation["sourceStatus"] | "history";
  sourceWallet: string;
  totalPnlUsd: number;
  unrealizedPnlUsd: number;
  winRate: number | null;
};

type MarketRow = {
  coin: string;
  longCount: number;
  marginUsd: number;
  notionalUsd: number;
  positionCount: number;
  shortCount: number;
  unrealizedPnlUsd: number;
};

type TimelinePoint = {
  label: string;
  value: number;
};

type AccountView = {
  account: PaperTradingAccount;
  allocations: PaperCopyAllocation[];
  closedTrades: PaperClosedTrade[];
  marketRows: MarketRow[];
  metrics: AccountMetrics;
  positions: PaperPosition[];
  recentFills: PaperCopyFill[];
  sourceRows: SourceRow[];
  timeline: TimelinePoint[];
};

export function AccountsDashboard({
  initialSummary,
}: {
  initialSummary: PaperTradingSummaryResponse;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [selectedAccountKey, setSelectedAccountKey] = useState(
    initialSummary.accounts[0]?.key ?? "",
  );
  const [connectionState, setConnectionState] = useState<"live" | "refreshing" | "offline">(
    "live",
  );
  const [accountAction, setAccountAction] = useState<TradingAction | null>(null);
  const [createAccountOpen, setCreateAccountOpen] = useState(false);
  const [createAccountType, setCreateAccountType] = useState<CreateAccountType>("paper");
  const [createStartingBalance, setCreateStartingBalance] = useState("1000");
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(new Date());
  const [storedSelectionLoaded, setStoredSelectionLoaded] = useState(false);

  useEffect(() => {
    if (storedSelectionLoaded) {
      return;
    }
    const storedAccountKey = window.localStorage.getItem(SELECTED_ACCOUNT_STORAGE_KEY);
    if (storedAccountKey && summary.accounts.some((account) => account.key === storedAccountKey)) {
      setSelectedAccountKey(storedAccountKey);
    }
    setStoredSelectionLoaded(true);
  }, [storedSelectionLoaded, summary.accounts]);

  useEffect(() => {
    if (!selectedAccountKey) {
      return;
    }
    window.localStorage.setItem(SELECTED_ACCOUNT_STORAGE_KEY, selectedAccountKey);
  }, [selectedAccountKey]);

  useEffect(() => {
    if (
      selectedAccountKey &&
      summary.accounts.some((account) => account.key === selectedAccountKey)
    ) {
      return;
    }
    setSelectedAccountKey(summary.accounts[0]?.key ?? "");
  }, [selectedAccountKey, summary.accounts]);

  const refresh = useCallback(async () => {
    setConnectionState("refreshing");
    try {
      const url = new URL(`${getPublicApiBaseUrl()}/paper-trading`, window.location.origin);
      url.searchParams.set("closed_trade_limit", String(ACCOUNT_SUMMARY_LIMIT));
      url.searchParams.set("recent_fill_limit", String(ACCOUNT_SUMMARY_LIMIT));
      const response = await fetch(url.toString(), { cache: "no-store" });
      if (!response.ok) {
        setConnectionState("offline");
        return;
      }
      const payload = (await response.json()) as PaperTradingSummaryResponse;
      setSummary(payload);
      setLastRefreshAt(new Date());
      setConnectionState("live");
    } catch {
      setConnectionState("offline");
    }
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void refresh();
    }, ACCOUNT_REFRESH_MS);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  const accountView = useMemo(
    () => buildAccountView(summary, selectedAccountKey),
    [selectedAccountKey, summary],
  );

  const handleTradingAction = useCallback(
    async (action: TradingAction) => {
      if (!accountView || accountAction) {
        return;
      }
      if (action === "close-all-and-stop" && accountView.positions.length > 0) {
        const confirmed = window.confirm(
          `Close ${formatInteger(
            accountView.positions.length,
          )} open paper positions for ${accountView.account.label} and stop trading?`,
        );
        if (!confirmed) {
          return;
        }
      }

      setAccountAction(action);
      setActionError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/paper-trading/accounts/${encodeURIComponent(
            accountView.account.key,
          )}/${action}`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, `${tradingActionLabel(action)} failed`));
          await refresh();
          return;
        }

        const payload = (await response.json()) as PaperTradingSummaryResponse;
        setSummary(payload);
        setLastRefreshAt(new Date());
        setConnectionState("live");
      } catch {
        setConnectionState("offline");
        setActionError(`${tradingActionLabel(action)} failed.`);
      } finally {
        setAccountAction(null);
      }
    },
    [accountAction, accountView, refresh],
  );

  const openCreateAccount = useCallback(() => {
    setCreateAccountOpen(true);
    setCreateAccountType("paper");
    setCreateStartingBalance("1000");
    setCreateError(null);
  }, []);

  const closeCreateAccount = useCallback(() => {
    if (createSubmitting) {
      return;
    }
    setCreateAccountOpen(false);
    setCreateError(null);
  }, [createSubmitting]);

  const handleCreateAccount = useCallback(async () => {
    if (createSubmitting) {
      return;
    }

    const startingBalance = Number(createStartingBalance);
    if (createAccountType !== "paper") {
      setCreateError("Live accounts are not available yet.");
      return;
    }
    if (!Number.isFinite(startingBalance) || startingBalance <= 0) {
      setCreateError("Enter a starting balance greater than 0.");
      return;
    }

    setCreateSubmitting(true);
    setCreateError(null);
    try {
      const previousKeys = new Set(summary.accounts.map((account) => account.key));
      const response = await fetch(`${getPublicApiBaseUrl()}/paper-trading/accounts`, {
        body: JSON.stringify({
          accountType: createAccountType,
          startingBalanceUsd: createStartingBalance,
        }),
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
        },
        method: "POST",
      });
      if (!response.ok) {
        setCreateError(await responseError(response, "Create account failed"));
        await refresh();
        return;
      }

      const payload = (await response.json()) as PaperTradingSummaryResponse;
      const createdAccount =
        payload.accounts.find((account) => !previousKeys.has(account.key)) ??
        payload.accounts[payload.accounts.length - 1];
      setSummary(payload);
      if (createdAccount) {
        setSelectedAccountKey(createdAccount.key);
      }
      setLastRefreshAt(new Date());
      setConnectionState("live");
      setCreateAccountOpen(false);
    } catch {
      setConnectionState("offline");
      setCreateError("Create account failed.");
    } finally {
      setCreateSubmitting(false);
    }
  }, [
    createAccountType,
    createStartingBalance,
    createSubmitting,
    refresh,
    summary.accounts,
  ]);

  return (
    <>
      <PageTopPanel
        eyebrow="Paper account performance"
        icon={WalletCards}
        title="Accounts"
        actions={
          <>
            <HeaderUpdatedLabel label={`Updated ${formatDate(summary.updatedAt)}`} />
            <button
              type="button"
              onClick={openCreateAccount}
              className="inline-flex min-h-9 items-center gap-2 rounded-md border border-line bg-white px-3 py-1.5 text-sm font-semibold text-ink shadow-sm hover:bg-[#f7f9fb]"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Create account
            </button>
            <select
              aria-label="Select account"
              className="h-9 min-w-[190px] rounded-md border border-line bg-white px-3 text-sm font-medium text-ink shadow-sm"
              value={selectedAccountKey}
              onChange={(event) => setSelectedAccountKey(event.target.value)}
            >
              {summary.accounts.map((account) => (
                <option key={account.key} value={account.key}>
                  {account.label}
                </option>
              ))}
            </select>
            {accountView ? (
              <StatusPill
                label={accountView.account.enabled ? "trading enabled" : "trading stopped"}
                tone={accountView.account.enabled ? "positive" : "warning"}
              />
            ) : null}
            {accountView ? (
              accountView.account.enabled ? (
                <>
                  <button
                    type="button"
                    onClick={() => void handleTradingAction("stop")}
                    disabled={accountAction !== null}
                    className="inline-flex min-h-9 items-center gap-2 rounded-md border border-[#f0c36d] bg-[#fff8e8] px-3 py-1.5 text-sm font-semibold text-warning shadow-sm hover:bg-[#fff2d2] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {accountAction === "stop" ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Square className="h-4 w-4" aria-hidden="true" />
                    )}
                    Stop trading
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleTradingAction("close-all-and-stop")}
                    disabled={accountAction !== null}
                    className="inline-flex min-h-9 items-center gap-2 rounded-md border border-[#efb1aa] bg-[#fff5f3] px-3 py-1.5 text-sm font-semibold text-danger shadow-sm hover:bg-[#ffe9e6] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {accountAction === "close-all-and-stop" ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <XCircle className="h-4 w-4" aria-hidden="true" />
                    )}
                    Close all and stop trading
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleTradingAction("start")}
                  disabled={accountAction !== null}
                  className="inline-flex min-h-9 items-center gap-2 rounded-md border border-[#9ccfc0] bg-[#f2fbf7] px-3 py-1.5 text-sm font-semibold text-positive shadow-sm hover:bg-[#e5f6ee] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {accountAction === "start" ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Play className="h-4 w-4" aria-hidden="true" />
                  )}
                  Start trading
                </button>
              )
            ) : null}
            {connectionState === "offline" ? <StatusPill label="offline" tone="danger" /> : null}
          </>
        }
        refresh={
          <HeaderRefreshButton
            isRefreshing={connectionState === "refreshing"}
            onRefresh={refresh}
            title="Refresh account data"
          />
        }
      />

      <CreateAccountDialog
        accountType={createAccountType}
        balance={createStartingBalance}
        error={createError}
        isSubmitting={createSubmitting}
        onAccountTypeChange={setCreateAccountType}
        onBalanceChange={setCreateStartingBalance}
        onClose={closeCreateAccount}
        onSubmit={handleCreateAccount}
        open={createAccountOpen}
      />

      {actionError ? (
        <div className="rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-3 py-2 text-sm font-medium text-danger">
          {actionError}
        </div>
      ) : null}

      {accountView ? (
        <AccountContent
          accountView={accountView}
          lastRefreshAt={lastRefreshAt}
          marketDataStatus={summary.marketDataStatus}
        />
      ) : (
        <section className="rounded-lg border border-line bg-panel p-8 text-center text-sm text-[#5b6770]">
          No paper accounts are synced yet.
        </section>
      )}
    </>
  );
}

function CreateAccountDialog({
  accountType,
  balance,
  error,
  isSubmitting,
  onAccountTypeChange,
  onBalanceChange,
  onClose,
  onSubmit,
  open,
}: {
  accountType: CreateAccountType;
  balance: string;
  error: string | null;
  isSubmitting: boolean;
  onAccountTypeChange: (accountType: CreateAccountType) => void;
  onBalanceChange: (balance: string) => void;
  onClose: () => void;
  onSubmit: () => void;
  open: boolean;
}) {
  const titleId = useId();
  const parsedBalance = Number(balance);
  const canCreate =
    accountType === "paper" &&
    Number.isFinite(parsedBalance) &&
    parsedBalance > 0 &&
    !isSubmitting;

  useEffect(() => {
    if (!open) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose, open]);

  if (!open) {
    return null;
  }

  return (
    <div
      className="fixed inset-0 z-50 overflow-y-auto bg-[#071019]/55 px-3 py-6 backdrop-blur-sm sm:px-6"
      role="presentation"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mx-auto flex min-h-full w-full max-w-xl items-center"
      >
        <form
          className="w-full overflow-hidden rounded-lg border border-line bg-panel shadow-xl"
          onClick={(event) => event.stopPropagation()}
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-4 sm:px-5">
            <div>
              <p className="text-xs font-medium uppercase text-[#526070]">Accounts</p>
              <h2 id={titleId} className="mt-1 text-xl font-semibold">
                Create account
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-white text-[#526070] transition hover:border-[#9eb1c1] hover:text-ink"
              aria-label="Close create account"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <div className="grid gap-4 px-4 py-4 sm:px-5">
            <div className="grid gap-2 sm:grid-cols-2">
              <AccountTypeOption
                active={accountType === "paper"}
                detail="Paper balance"
                icon={WalletCards}
                label="Paper account"
                onClick={() => onAccountTypeChange("paper")}
              />
              <AccountTypeOption
                active={accountType === "live"}
                detail="Coming later"
                icon={Activity}
                label="Live account"
                onClick={() => onAccountTypeChange("live")}
              />
            </div>

            {accountType === "paper" ? (
              <label className="grid gap-2">
                <span className="text-sm font-semibold text-ink">Starting balance</span>
                <div className="flex items-center rounded-md border border-line bg-white shadow-sm focus-within:border-[#9eb1c1]">
                  <span className="border-r border-line px-3 text-sm font-semibold text-[#5b6770]">
                    USD
                  </span>
                  <input
                    min="0.01"
                    step="0.01"
                    type="number"
                    value={balance}
                    onChange={(event) => onBalanceChange(event.target.value)}
                    className="h-10 min-w-0 flex-1 rounded-r-md px-3 text-sm font-medium text-ink outline-none"
                  />
                </div>
                <span className="text-xs font-medium text-[#5b6770]">
                  Starts disabled with {formatCurrency(parsedBalance || 0)}
                </span>
              </label>
            ) : (
              <div className="rounded-md border border-line bg-[#f7f9fb] px-3 py-3 text-sm font-medium text-[#5b6770]">
                Live accounts are not available yet.
              </div>
            )}

            {error ? (
              <div className="rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-3 py-2 text-sm font-medium text-danger">
                {error}
              </div>
            ) : null}
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-[#f7f9fb] px-4 py-3 sm:px-5">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="inline-flex min-h-9 items-center rounded-md border border-line bg-white px-3 py-1.5 text-sm font-semibold text-ink hover:bg-[#f7f9fb] disabled:cursor-not-allowed disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canCreate}
              className="inline-flex min-h-9 items-center gap-2 rounded-md border border-[#9ccfc0] bg-[#f2fbf7] px-3 py-1.5 text-sm font-semibold text-positive shadow-sm hover:bg-[#e5f6ee] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="h-4 w-4" aria-hidden="true" />
              )}
              Create paper account
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AccountTypeOption({
  active,
  detail,
  icon: Icon,
  label,
  onClick,
}: {
  active: boolean;
  detail: string;
  icon: LucideIcon;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex min-h-20 items-center gap-3 rounded-md border px-3 py-3 text-left transition ${
        active
          ? "border-[#9ccfc0] bg-[#f2fbf7] text-ink"
          : "border-line bg-white text-[#344054] hover:bg-[#f7f9fb]"
      }`}
    >
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-white">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{label}</span>
        <span className="mt-0.5 block text-xs font-medium text-[#5b6770]">{detail}</span>
      </span>
    </button>
  );
}

function AccountContent({
  accountView,
  lastRefreshAt,
  marketDataStatus,
}: {
  accountView: AccountView;
  lastRefreshAt: Date | null;
  marketDataStatus: PaperTradingSummaryResponse["marketDataStatus"];
}) {
  const { account, metrics } = accountView;
  const totalPnl = decimal(account.totalPnlUsd);
  const realizedPnl = decimal(account.realizedPnlUsd);
  const unrealizedPnl = decimal(account.unrealizedPnlUsd);

  return (
    <>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <MetricTile
          detail={`${formatCurrency(account.cashBalanceUsd)} cash`}
          icon={WalletCards}
          label="Net equity"
          value={formatCurrency(metrics.netEquityUsd)}
        />
        <MetricTile
          detail={formatPercent(metrics.returnPct)}
          icon={totalPnl >= 0 ? TrendingUp : TrendingDown}
          label="Total PnL"
          tone={totalPnl >= 0 ? "positive" : "danger"}
          value={formatCurrency(totalPnl)}
        />
        <MetricTile
          detail={`${formatCurrency(account.feeUsd)} fees`}
          icon={realizedPnl >= 0 ? TrendingUp : TrendingDown}
          label="Realized"
          tone={realizedPnl >= 0 ? "positive" : "danger"}
          value={formatCurrency(realizedPnl)}
        />
        <MetricTile
          detail={`${formatInteger(account.openPositionCount)} open positions`}
          icon={unrealizedPnl >= 0 ? TrendingUp : TrendingDown}
          label="Unrealized"
          tone={unrealizedPnl >= 0 ? "positive" : "danger"}
          value={formatCurrency(unrealizedPnl)}
        />
        <MetricTile
          detail={`${formatPercent(metrics.exposureRatio)} of net equity`}
          icon={Target}
          label="Open notional"
          value={formatCurrency(account.openNotionalUsd)}
        />
        <MetricTile
          detail={`${formatCurrency(metrics.remainingAllocationUsd)} available`}
          icon={Layers}
          label="Allocation used"
          value={formatPercent(metrics.allocationUsedPct)}
        />
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel icon={Activity} title="Account Balance">
          <BalanceBreakdown account={account} metrics={metrics} />
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            <SmallMetric label="Status" value={account.enabled ? "enabled" : "disabled"} />
            <SmallMetric label="Created" value={formatDate(account.createdAt)} />
            <SmallMetric label="Last refresh" value={lastRefreshAt?.toLocaleTimeString("sv-SE") ?? "-"} />
          </div>
        </Panel>

        <Panel icon={LineChart} title="Closed Trade PnL">
          <CumulativePnlChart points={accountView.timeline} />
          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            <SmallMetric label="Closed trades" value={formatInteger(accountView.closedTrades.length)} />
            <SmallMetric label="Closed net" value={formatCurrency(metrics.closedNetPnlUsd)} />
            <SmallMetric label="Avg closed" value={formatCurrency(metrics.averageClosedPnlUsd)} />
            <SmallMetric label="Win rate" value={formatPercent(metrics.winRate)} />
          </div>
        </Panel>
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <Panel icon={BarChart3} title="Allocation Usage">
          <AllocationRows rows={accountView.sourceRows} />
        </Panel>

        <Panel icon={Target} title="Market Exposure">
          <MarketRows rows={accountView.marketRows} marketDataStatus={marketDataStatus} />
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
        <Panel icon={WalletCards} title="Source Performance">
          <SourceRows rows={accountView.sourceRows} />
        </Panel>

        <Panel icon={Activity} title="Open Positions">
          <PositionRows positions={accountView.positions} />
        </Panel>
      </section>

      <section className="grid gap-4 2xl:grid-cols-[1fr_1fr]">
        <Panel icon={Clock} title="Closed Trades">
          <ClosedTradeRows trades={accountView.closedTrades} />
        </Panel>

        <Panel icon={BarChart3} title="Recent Fills">
          <FillRows fills={accountView.recentFills} />
        </Panel>
      </section>
    </>
  );
}

function MetricTile({
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: Tone;
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
          <p className="truncate text-xs font-medium uppercase text-[#5b6770]">{label}</p>
          <p className="mt-2 truncate text-2xl font-semibold text-ink">{value}</p>
        </div>
        <Icon className="h-5 w-5 shrink-0 text-[#5b6770]" aria-hidden="true" />
      </div>
      <p className="mt-3 truncate text-sm text-[#5b6770]">{detail}</p>
    </article>
  );
}

function Panel({
  children,
  icon: Icon,
  title,
}: {
  children: ReactNode;
  icon: LucideIcon;
  title: string;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel shadow-sm">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Icon className="h-4 w-4 text-[#5b6770]" aria-hidden="true" />
        <h2 className="text-base font-semibold text-ink">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

function BalanceBreakdown({
  account,
  metrics,
}: {
  account: PaperTradingAccount;
  metrics: AccountMetrics;
}) {
  const startingBalance = decimal(account.startingBalanceUsd);
  const cashBalance = decimal(account.cashBalanceUsd);
  const openMargin = decimal(account.openMarginUsd);
  const unrealizedPnl = decimal(account.unrealizedPnlUsd);
  const balanceScale = Math.max(startingBalance, cashBalance, metrics.netEquityUsd, openMargin, 1);

  return (
    <div className="grid gap-3">
      <MetricLine
        label="Starting balance"
        tone="neutral"
        value={startingBalance / balanceScale}
        valueLabel={formatCurrency(startingBalance)}
      />
      <MetricLine
        label="Cash balance"
        tone="neutral"
        value={cashBalance / balanceScale}
        valueLabel={formatCurrency(cashBalance)}
      />
      <MetricLine
        label="Net equity"
        tone={metrics.netEquityUsd >= startingBalance ? "positive" : "danger"}
        value={metrics.netEquityUsd / balanceScale}
        valueLabel={formatCurrency(metrics.netEquityUsd)}
      />
      <MetricLine
        label="Open margin"
        tone="warning"
        value={openMargin / balanceScale}
        valueLabel={formatCurrency(openMargin)}
      />
      <MetricLine
        label="Unrealized PnL"
        tone={unrealizedPnl >= 0 ? "positive" : "danger"}
        value={Math.abs(unrealizedPnl) / balanceScale}
        valueLabel={formatCurrency(unrealizedPnl)}
      />
    </div>
  );
}

function CumulativePnlChart({ points }: { points: TimelinePoint[] }) {
  if (points.length === 0) {
    return <EmptyState text="No closed trades for this account yet." />;
  }

  const width = 640;
  const height = 180;
  const padding = 18;
  const values = points.map((point) => point.value);
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const range = maxValue - minValue || 1;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;
  const plottedPoints = points
    .map((point, index) => {
      const x =
        points.length === 1
          ? width / 2
          : padding + (index / (points.length - 1)) * chartWidth;
      const y = padding + ((maxValue - point.value) / range) * chartHeight;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  const zeroY = padding + ((maxValue - 0) / range) * chartHeight;
  const lastPoint = points[points.length - 1];

  return (
    <div>
      <svg
        className="h-[180px] w-full overflow-visible"
        preserveAspectRatio="none"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <line
          x1={padding}
          x2={width - padding}
          y1={zeroY}
          y2={zeroY}
          stroke="#d7dde5"
          strokeWidth="1"
        />
        <polyline
          fill="none"
          points={plottedPoints}
          stroke={lastPoint.value >= 0 ? "#097a5f" : "#c93a32"}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="3"
        />
      </svg>
      <div className="mt-2 flex items-center justify-between gap-3 text-xs text-[#5b6770]">
        <span>{points[0]?.label ?? "-"}</span>
        <span className={lastPoint.value >= 0 ? "text-positive" : "text-danger"}>
          {formatCurrency(lastPoint.value)}
        </span>
        <span>{lastPoint.label}</span>
      </div>
    </div>
  );
}

function AllocationRows({ rows }: { rows: SourceRow[] }) {
  const allocationRows = rows.filter((row) => row.allocationUsd > 0 || row.openMarginUsd > 0);
  if (allocationRows.length === 0) {
    return <EmptyState text="No allocation rows for this account." />;
  }

  return (
    <div className="grid gap-3">
      {allocationRows.slice(0, 12).map((row) => {
        const usedPct = row.allocationUsd > 0 ? row.openMarginUsd / row.allocationUsd : 0;
        return (
          <div key={row.sourceWallet} className="grid gap-2">
            <div className="flex items-center justify-between gap-3">
              <SourceIdentity row={row} />
              <div className="text-right">
                <p className="font-mono text-xs font-semibold text-ink">
                  {formatCurrency(row.openMarginUsd)}
                </p>
                <p className="text-[11px] text-[#5b6770]">
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
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <StatusPill
          label={marketStatusLabel(marketDataStatus)}
          tone={marketDataStatus === "live" || marketDataStatus === "no_open_positions" ? "positive" : "warning"}
        />
        <p className="text-xs text-[#5b6770]">{formatInteger(rows.length)} markets</p>
      </div>
      {rows.map((row) => (
        <div key={row.coin} className="grid gap-2">
          <div className="grid grid-cols-[96px_1fr_110px] items-center gap-3">
            <div className="min-w-0">
              <p className="truncate font-mono text-sm font-semibold text-ink">{row.coin}</p>
              <p className="truncate text-[11px] text-[#5b6770]">
                {formatInteger(row.longCount)} long, {formatInteger(row.shortCount)} short
              </p>
            </div>
            <Bar value={row.notionalUsd / maxNotional} />
            <div className="text-right">
              <p className="font-mono text-xs font-semibold text-ink">
                {formatCurrency(row.notionalUsd)}
              </p>
              <p className={row.unrealizedPnlUsd >= 0 ? "text-positive" : "text-danger"}>
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
      {rows.slice(0, 12).map((row) => (
        <div key={row.sourceWallet} className="grid gap-3 py-3 xl:grid-cols-[1fr_120px_120px_120px]">
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

function PositionRows({ positions }: { positions: PaperPosition[] }) {
  if (positions.length === 0) {
    return <EmptyState text="No open positions for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {positions.map((position) => {
        const unrealized = decimal(position.unrealizedPnlUsd);
        return (
          <div key={position.id} className="grid gap-3 py-3 xl:grid-cols-[1fr_120px_120px_120px_120px]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{position.coin}</p>
                <StatusPill
                  label={position.side}
                  tone={position.side === "long" ? "positive" : "warning"}
                />
              </div>
              <Link
                href={`/wallets/${position.sourceWallet}`}
                className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
              >
                {sourceDisplayName(position.sourceLabel, position.sourceWallet)}
              </Link>
              <p className="mt-1 truncate text-[11px] text-[#5b6770]">
                opened {formatDate(position.openedAt)}
              </p>
            </div>
            <RowMetric label="Unrealized" tone={unrealized >= 0 ? "positive" : "danger"} value={formatCurrency(unrealized)} />
            <RowMetric label="Notional" detail={`${formatLeverage(position.leverage)} leverage`} value={formatCurrency(position.currentNotionalUsd ?? position.notionalUsd)} />
            <RowMetric label="Entry" detail={`mark ${formatPrice(position.markPrice)}`} value={formatPrice(position.entryPrice)} />
            <RowMetric label="Execution" detail="source to open" value={formatExecutionMs(position.entryExecutionDelayMs)} />
          </div>
        );
      })}
    </div>
  );
}

function ClosedTradeRows({ trades }: { trades: PaperClosedTrade[] }) {
  if (trades.length === 0) {
    return <EmptyState text="No closed trades for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {trades.slice(0, 12).map((trade) => {
        const netPnl = decimal(trade.netPnlUsd);
        return (
          <div key={trade.id} className="grid gap-3 py-3 xl:grid-cols-[1fr_120px_120px_120px]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{trade.coin}</p>
                {trade.side ? (
                  <StatusPill
                    label={trade.side}
                    tone={trade.side === "long" ? "positive" : "warning"}
                  />
                ) : null}
                {trade.isSourceLiquidation ? <StatusPill label="liquidation" tone="danger" /> : null}
              </div>
              <Link
                href={`/wallets/${trade.sourceWallet}`}
                className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
              >
                {sourceDisplayName(trade.sourceLabel, trade.sourceWallet)}
              </Link>
              <p className="mt-1 text-[11px] text-[#5b6770]">
                {formatCloseType(trade.closeType)}, {formatDuration(trade.durationMs)}
              </p>
            </div>
            <RowMetric label="Net PnL" tone={netPnl >= 0 ? "positive" : "danger"} value={formatCurrency(netPnl)} />
            <RowMetric label="Closed" value={formatShortDateTime(trade.closedAt)} />
            <RowMetric label="Exit" detail={`size ${formatSize(trade.size)}`} value={formatPrice(trade.exitPrice)} />
          </div>
        );
      })}
    </div>
  );
}

function FillRows({ fills }: { fills: PaperCopyFill[] }) {
  if (fills.length === 0) {
    return <EmptyState text="No recent fills for this account." />;
  }

  return (
    <div className="divide-y divide-line">
      {fills.slice(0, 12).map((fill) => {
        const realizedPnl = decimal(fill.realizedPnlUsd);
        return (
          <div key={fill.id} className="grid gap-3 py-3 xl:grid-cols-[1fr_110px_120px_120px]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{fill.coin}</p>
                <StatusPill label={fill.action} tone={fill.action === "skip" ? "warning" : "neutral"} />
                {fill.side ? (
                  <StatusPill label={fill.side} tone={fill.side === "long" ? "positive" : "warning"} />
                ) : null}
              </div>
              <Link
                href={`/wallets/${fill.sourceWallet}`}
                className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-[#297c73]"
              >
                {sourceDisplayName(fill.sourceLabel, fill.sourceWallet)}
              </Link>
              <p className="mt-1 truncate text-[11px] text-[#5b6770]">
                {fill.skippedReason ? humanReason(fill.skippedReason) : formatShortDateTime(fill.filledAt)}
              </p>
            </div>
            <RowMetric label="Realized" tone={realizedPnl >= 0 ? "positive" : "danger"} value={formatCurrency(realizedPnl)} />
            <RowMetric label="Notional" value={formatCurrency(fill.notionalUsd)} />
            <RowMetric label="Price" detail={fillPriceDetail(fill)} value={formatPrice(fill.price)} />
          </div>
        );
      })}
    </div>
  );
}

function SourceIdentity({ row }: { row: SourceRow }) {
  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-1">
        <Link
          href={`/wallets/${row.sourceWallet}`}
          className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-[#297c73]"
        >
          {sourceDisplayName(row.sourceLabel, row.sourceWallet)}
        </Link>
        <StatusPill label={formatSourceStatus(row.sourceStatus)} tone={sourceStatusTone(row.sourceStatus)} />
      </div>
      <p className="mt-1 truncate font-mono text-xs text-[#5b6770]">
        {shortAddress(row.sourceWallet)}
      </p>
      <p className="mt-1 truncate text-[11px] text-[#5b6770]">
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
      <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-xs font-semibold ${valueClass}`}>{value}</p>
      {detail ? <p className="mt-0.5 truncate text-[11px] text-[#5b6770]">{detail}</p> : null}
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-[#f8fafb] px-3 py-2">
      <p className="truncate text-[11px] font-medium uppercase text-[#5b6770]">{label}</p>
      <p className="mt-1 truncate font-mono text-sm font-semibold text-ink">{value}</p>
    </div>
  );
}

function MetricLine({
  label,
  tone = "neutral",
  value,
  valueLabel,
}: {
  label: string;
  tone?: Tone;
  value: number;
  valueLabel: string;
}) {
  return (
    <div className="grid grid-cols-[130px_1fr_110px] items-center gap-3">
      <p className="truncate text-xs font-medium text-[#344054]">{label}</p>
      <Bar value={value} tone={tone} />
      <p className="truncate text-right font-mono text-xs font-semibold text-ink">{valueLabel}</p>
    </div>
  );
}

function Bar({ tone = "neutral", value }: { tone?: Tone; value: number }) {
  const color =
    tone === "positive"
      ? "bg-[#097a5f]"
      : tone === "danger"
        ? "bg-[#c93a32]"
        : tone === "warning"
          ? "bg-[#c47c14]"
          : "bg-[#53606b]";
  return (
    <div className="h-2 overflow-hidden rounded-full bg-[#e5ebf0]">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${clamp(value, 0, 1) * 100}%` }} />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-[#5b6770]">{text}</div>;
}

function buildAccountView(
  summary: PaperTradingSummaryResponse,
  selectedAccountKey: string,
): AccountView | null {
  const account =
    summary.accounts.find((item) => item.key === selectedAccountKey) ?? summary.accounts[0];
  if (!account) {
    return null;
  }

  const allocations = summary.allocations.filter((item) => item.accountKey === account.key);
  const positions = summary.positions.filter((item) => item.accountKey === account.key);
  const closedTrades = summary.closedTrades.filter((item) => item.accountKey === account.key);
  const recentFills = summary.recentFills.filter((item) => item.accountKey === account.key);
  const sourceRows = buildSourceRows({
    allocations,
    closedTrades,
    positions,
    recentFills,
  });
  const metrics = buildAccountMetrics({
    account,
    allocations,
    closedTrades,
    recentFills,
  });

  return {
    account,
    allocations,
    closedTrades,
    marketRows: buildMarketRows(positions),
    metrics,
    positions,
    recentFills,
    sourceRows,
    timeline: buildTimeline(closedTrades),
  };
}

function buildAccountMetrics({
  account,
  allocations,
  closedTrades,
  recentFills,
}: {
  account: PaperTradingAccount;
  allocations: PaperCopyAllocation[];
  closedTrades: PaperClosedTrade[];
  recentFills: PaperCopyFill[];
}): AccountMetrics {
  const netEquityUsd = accountNetEquity(account);
  const allocationUsd = sumNumbers(allocations.map((allocation) => allocation.allocationUsd));
  const openMarginUsd = decimal(account.openMarginUsd);
  const remainingAllocationUsd = sumNumbers(
    allocations.map((allocation) => allocation.remainingAllocationUsd),
  );
  const closedNetPnlUsd = sumNumbers(closedTrades.map((trade) => trade.netPnlUsd));
  const winningClosedTradeCount = closedTrades.filter((trade) => decimal(trade.netPnlUsd) > 0).length;
  const copiedFillCount = recentFills.filter((fill) => fill.action !== "skip").length;
  const skippedFillCount = recentFills.filter((fill) => fill.action === "skip").length;
  const startingBalance = decimal(account.startingBalanceUsd);

  return {
    allocationUsd,
    allocationUsedPct: allocationUsd > 0 ? openMarginUsd / allocationUsd : null,
    averageClosedPnlUsd: closedTrades.length > 0 ? closedNetPnlUsd / closedTrades.length : 0,
    closedNetPnlUsd,
    copiedFillCount,
    exposureRatio: netEquityUsd > 0 ? decimal(account.openNotionalUsd) / netEquityUsd : null,
    netEquityUsd,
    remainingAllocationUsd,
    returnPct:
      account.totalPnlPct !== null
        ? decimal(account.totalPnlPct)
        : startingBalance > 0
          ? decimal(account.totalPnlUsd) / startingBalance
          : null,
    skippedFillCount,
    winRate: closedTrades.length > 0 ? winningClosedTradeCount / closedTrades.length : null,
  };
}

function buildSourceRows({
  allocations,
  closedTrades,
  positions,
  recentFills,
}: {
  allocations: PaperCopyAllocation[];
  closedTrades: PaperClosedTrade[];
  positions: PaperPosition[];
  recentFills: PaperCopyFill[];
}) {
  const rows = new Map<string, SourceRow>();

  const ensureRow = (sourceWallet: string, sourceLabel: string | null): SourceRow => {
    const source = sourceWallet.toLowerCase();
    const existing = rows.get(source);
    if (existing) {
      if (!existing.sourceLabel && sourceLabel) {
        existing.sourceLabel = sourceLabel;
      }
      return existing;
    }
    const row: SourceRow = {
      allocationUsd: 0,
      closedNetPnlUsd: 0,
      closedTradeCount: 0,
      copiedFillCount: 0,
      lastActivityAt: null,
      openMarginUsd: 0,
      openNotionalUsd: 0,
      openPositionCount: 0,
      poolRank: null,
      remainingAllocationUsd: 0,
      score: null,
      skippedFillCount: 0,
      sourceLabel,
      sourceStatus: "history",
      sourceWallet: source,
      totalPnlUsd: 0,
      unrealizedPnlUsd: 0,
      winRate: null,
    };
    rows.set(source, row);
    return row;
  };

  for (const allocation of allocations) {
    const row = ensureRow(allocation.sourceWallet, allocation.sourceLabel);
    row.allocationUsd += decimal(allocation.allocationUsd);
    row.remainingAllocationUsd += decimal(allocation.remainingAllocationUsd);
    row.poolRank = minNullable(row.poolRank, allocation.poolRank);
    row.score = row.score ?? allocation.score;
    row.sourceStatus = allocation.sourceStatus;
    row.lastActivityAt = latestDate(row.lastActivityAt, allocation.updatedAt);
  }

  for (const position of positions) {
    const row = ensureRow(position.sourceWallet, position.sourceLabel);
    row.openPositionCount += 1;
    row.openMarginUsd += decimal(position.marginUsd);
    row.openNotionalUsd += decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, position.updatedAt);
  }

  const winsBySource = new Map<string, number>();
  for (const trade of closedTrades) {
    const row = ensureRow(trade.sourceWallet, trade.sourceLabel);
    row.closedTradeCount += 1;
    row.closedNetPnlUsd += decimal(trade.netPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, trade.closedAt);
    if (decimal(trade.netPnlUsd) > 0) {
      const source = trade.sourceWallet.toLowerCase();
      winsBySource.set(source, (winsBySource.get(source) ?? 0) + 1);
    }
  }

  for (const fill of recentFills) {
    const row = ensureRow(fill.sourceWallet, fill.sourceLabel);
    if (fill.action === "skip") {
      row.skippedFillCount += 1;
    } else {
      row.copiedFillCount += 1;
    }
    row.lastActivityAt = latestDate(row.lastActivityAt, fill.filledAt);
  }

  return Array.from(rows.values())
    .map((row) => ({
      ...row,
      totalPnlUsd: row.closedNetPnlUsd + row.unrealizedPnlUsd,
      winRate: row.closedTradeCount > 0 ? (winsBySource.get(row.sourceWallet) ?? 0) / row.closedTradeCount : null,
    }))
    .sort((left, right) => {
      if (left.openPositionCount !== right.openPositionCount) {
        return right.openPositionCount - left.openPositionCount;
      }
      const pnlDiff = right.totalPnlUsd - left.totalPnlUsd;
      if (pnlDiff !== 0) {
        return pnlDiff;
      }
      return (left.poolRank ?? 9999) - (right.poolRank ?? 9999);
    });
}

function buildMarketRows(positions: PaperPosition[]): MarketRow[] {
  const rows = new Map<string, MarketRow>();
  for (const position of positions) {
    const row = rows.get(position.coin) ?? {
      coin: position.coin,
      longCount: 0,
      marginUsd: 0,
      notionalUsd: 0,
      positionCount: 0,
      shortCount: 0,
      unrealizedPnlUsd: 0,
    };
    row.positionCount += 1;
    row.marginUsd += decimal(position.marginUsd);
    row.notionalUsd += decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    if (position.side === "long") {
      row.longCount += 1;
    } else {
      row.shortCount += 1;
    }
    rows.set(position.coin, row);
  }
  return Array.from(rows.values()).sort((left, right) => right.notionalUsd - left.notionalUsd);
}

function buildTimeline(closedTrades: PaperClosedTrade[]): TimelinePoint[] {
  let cumulativePnl = 0;
  return [...closedTrades]
    .sort((left, right) => dateMs(left.closedAt) - dateMs(right.closedAt))
    .map((trade) => {
      cumulativePnl += decimal(trade.netPnlUsd);
      return {
        label: formatShortDateTime(trade.closedAt),
        value: cumulativePnl,
      };
    });
}

function accountNetEquity(account: PaperTradingAccount) {
  return decimal(account.equityUsd) + decimal(account.unrealizedPnlUsd);
}

function decimal(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return 0;
  }
  const parsed = numberValue(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sumNumbers(values: Array<string | number | null | undefined>): number {
  return values.reduce<number>((total, value) => total + decimal(value), 0);
}

function minNullable(left: number | null, right: number | null) {
  if (left === null) {
    return right;
  }
  if (right === null) {
    return left;
  }
  return Math.min(left, right);
}

function latestDate(left: string | null, right: string | null | undefined) {
  if (!right) {
    return left;
  }
  if (!left || dateMs(right) > dateMs(left)) {
    return right;
  }
  return left;
}

function dateMs(value: string | null | undefined) {
  if (!value) {
    return 0;
  }
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function clamp(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(Math.max(value, min), max);
}

function sourceDisplayName(label: string | null | undefined, address: string) {
  const trimmed = label?.trim();
  return trimmed || shortAddress(address);
}

function shortAddress(address: string) {
  if (address.length <= 14) {
    return address;
  }
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function formatPoolRank(rank: number | null | undefined) {
  return rank ? `pool #${formatInteger(rank)}` : "pool unranked";
}

function formatSourceStatus(status: SourceRow["sourceStatus"]) {
  if (status === "waiting_for_slot") {
    return "waiting for slot";
  }
  if (status === "waiting_for_trades") {
    return "waiting for trades";
  }
  return status;
}

function sourceStatusTone(status: SourceRow["sourceStatus"]): Tone {
  if (status === "trading") {
    return "positive";
  }
  if (status === "waiting_for_slot" || status === "waiting_for_trades") {
    return "warning";
  }
  return "neutral";
}

function marketStatusLabel(status: PaperTradingSummaryResponse["marketDataStatus"]) {
  const labels = {
    live: "live marks",
    no_open_positions: "no open positions",
    partial: "partial marks",
    unavailable: "marks unavailable",
  };
  return labels[status] ?? status;
}

function formatCloseType(value: string) {
  if (value === "flip_close") {
    return "flip close";
  }
  return value;
}

function formatPrice(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", {
    maximumFractionDigits: 6,
    minimumFractionDigits: 2,
  }).format(decimal(value));
}

function formatSize(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 6 }).format(decimal(value));
}

function formatLeverage(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )}x`;
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )} bps`;
}

function fillPriceDetail(fill: PaperCopyFill) {
  if (fill.skippedReason && fill.priceDriftBps) {
    const maxDrift = fill.maxPriceDriftBps ? ` | max ${formatBps(fill.maxPriceDriftBps)}` : "";
    return `drift ${formatBps(fill.priceDriftBps)}${maxDrift} | live ${formatPrice(fill.observedPrice)}`;
  }
  const parts = [
    fill.sourcePrice ? `src ${formatPrice(fill.sourcePrice)}` : null,
    fill.observedPrice ? `live ${formatPrice(fill.observedPrice)}` : null,
    `fee ${formatCurrency(fill.feeUsd)}`,
  ].filter(Boolean);
  return parts.join(" | ");
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

function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "duration -";
  }
  const totalMinutes = Math.max(0, Math.round(value / 60_000));
  if (totalMinutes < 60) {
    return `duration ${totalMinutes}m`;
  }
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours < 48) {
    return minutes > 0 ? `duration ${hours}h ${minutes}m` : `duration ${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `duration ${days}d ${restHours}h` : `duration ${days}d`;
}

function formatExecutionMs(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  if (value < 1000) {
    return `${formatInteger(value)} ms`;
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    value / 1000,
  )} s`;
}

function humanReason(value: string) {
  return value.replaceAll("_", " ");
}

function tradingActionLabel(action: TradingAction) {
  if (action === "start") {
    return "Start trading";
  }
  if (action === "stop") {
    return "Stop trading";
  }
  return "Close all and stop trading";
}

async function responseError(response: Response, fallback: string) {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    return `${fallback} with HTTP ${response.status}.`;
  }
  return `${fallback} with HTTP ${response.status}.`;
}
