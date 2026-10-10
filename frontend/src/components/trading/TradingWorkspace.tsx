"use client";

import { Play, Plus, RotateCw, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Menu, RefreshControl, TimeAgo, useConfirm, type MenuItem } from "@/components/ui/client";
import {
  Badge,
  buttonClass,
  Callout,
  EmptyState,
  PageHeader,
  Pnl,
  PnlPercent,
  Spinner,
  Stat,
  StatStrip,
  StatusDot,
} from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import { formatInteger, formatMultiple, formatPercent, formatSignedUsd, formatUsd, pluralize } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import {
  accountSummaryFor,
  buildAccountOptions,
  buildAccountView,
  latestUpdatedAt,
} from "@/lib/trading/accounts";
import { buildLiveCopyDecisionActivities } from "@/lib/trading/live";
import { buildPerformanceTimeline, calculatePerformance } from "@/lib/trading/performance";
import {
  buildLiveSourceHistory,
  buildPaperSourceHistory,
  buildSourceLabels,
  buildSourceMetadata,
  sumNumbers,
} from "@/lib/trading/sources";
import type { AccountOption, AccountSummary, CopySourceRow, PositionRow } from "@/lib/trading/types";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type { TradingAccount, TradingAccountsResponse } from "@/types/trading";

import { AccountDetailsPanel, PerformancePanel, RiskPanel } from "./AccountPanels";
import {
  CreateAccountDialog,
  DEFAULT_CREATE_DRAFT,
  readCreateDraft,
  writeCreateDraft,
  type CreateAccountDraft,
} from "./CreateAccountDialog";
import { HistoryPanel } from "./HistoryPanel";
import { PositionsPanel } from "./PositionsPanel";
import { SourcesPanel } from "./SourcesPanel";

const REFRESH_MS = 4000;
const SUMMARY_LIMIT = 250;
const SELECTED_ACCOUNT_KEY = "copyagent.accounts.selectedAccountKey";

type AccountAction = "start" | "stop" | "disable" | "close-all-and-stop" | "delete" | "reset" | "reconcile";

export function TradingWorkspace({
  initialAccountKey,
  initialCreate,
  initialSummary,
  initialTradingAccounts,
}: {
  initialAccountKey: string | null;
  initialCreate: boolean;
  initialSummary: PaperTradingSummaryResponse;
  initialTradingAccounts: TradingAccountsResponse;
}) {
  const confirm = useConfirm();
  const [summary, setSummary] = useState(initialSummary);
  const [tradingAccounts, setTradingAccounts] = useState(initialTradingAccounts);
  const [connection, setConnection] = useState<"live" | "refreshing" | "offline">("live");
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(initialAccountKey);
  const [action, setAction] = useState<AccountAction | null>(null);
  const [closingPositionId, setClosingPositionId] = useState<string | null>(null);
  const [closingSource, setClosingSource] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [draft, setDraft] = useState<CreateAccountDraft>(DEFAULT_CREATE_DRAFT);
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const draftLoaded = useRef(false);

  const options = useMemo(() => buildAccountOptions(summary, tradingAccounts), [summary, tradingAccounts]);
  const selected = useMemo(
    () => options.find((option) => option.key === selectedKey) ?? options[0] ?? null,
    [options, selectedKey],
  );

  // Restore the last selected account and an unfinished create form after the first render.
  useEffect(() => {
    setLastRefreshAt(new Date());
    if (!initialAccountKey) {
      try {
        const stored = window.localStorage.getItem(SELECTED_ACCOUNT_KEY);
        if (stored) {
          setSelectedKey(stored);
        }
      } catch {
        // Local storage is optional.
      }
    }
    const storedDraft = readCreateDraft();
    setDraft(initialCreate ? { ...storedDraft, accountType: "paper", open: true } : storedDraft);
    draftLoaded.current = true;
  }, [initialAccountKey, initialCreate]);

  useEffect(() => {
    if (draftLoaded.current) {
      writeCreateDraft(draft);
    }
  }, [draft]);

  const selectAccount = useCallback((key: string) => {
    setSelectedKey(key);
    setActionError(null);
    try {
      window.localStorage.setItem(SELECTED_ACCOUNT_KEY, key);
    } catch {
      // Local storage is optional.
    }
    const url = new URL(window.location.href);
    url.searchParams.set("account", key);
    url.searchParams.delete("create");
    window.history.replaceState(null, "", url.toString());
  }, []);

  const refresh = useCallback(async () => {
    setConnection("refreshing");
    try {
      const [paper, live] = await Promise.all([
        apiRequest<PaperTradingSummaryResponse>("/paper-trading", {
          params: { closed_trade_limit: SUMMARY_LIMIT, recent_fill_limit: SUMMARY_LIMIT },
        }),
        apiRequest<TradingAccountsResponse>("/trading/accounts"),
      ]);
      setSummary(paper);
      setTradingAccounts(live);
      setLastRefreshAt(new Date());
      setConnection("live");
    } catch {
      setConnection("offline");
    }
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    }, REFRESH_MS);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  /** Runs a mutation, applies a returned paper summary directly, and refreshes everything else. */
  const mutate = useCallback(
    async (path: string, method: "POST" | "DELETE", fallback: string, returnsPaperSummary: boolean) => {
      const payload = await apiRequest<PaperTradingSummaryResponse | undefined>(path, { fallback, method });
      if (returnsPaperSummary && payload) {
        setSummary(payload);
        setLastRefreshAt(new Date());
      }
      await refresh();
    },
    [refresh],
  );

  const runAccountAction = useCallback(
    async (option: AccountOption, kind: AccountAction) => {
      if (action) {
        return;
      }
      const isPaper = option.accountType === "paper";
      const base = isPaper ? "/paper-trading/accounts" : "/trading/accounts";
      const key = encodeURIComponent(option.key);
      const confirmations: Partial<Record<AccountAction, Parameters<typeof confirm>[0]>> = {
        "close-all-and-stop": {
          body: isPaper
            ? "All open paper positions are closed at the current market price and the account stops copying."
            : "Real market orders close every open position on Hyperliquid, then the account stops copying.",
          confirmLabel: "Close all and stop",
          title: `Close all positions in ${option.label}?`,
          tone: "danger",
        },
        delete: {
          body: isPaper
            ? "This deletes the paper account with its positions, fills, allocations, and history. It cannot be undone."
            : "The live account is archived. Its trading history is kept.",
          confirmLabel: isPaper ? "Delete account" : "Archive account",
          title: isPaper ? `Delete ${option.label}?` : `Archive ${option.label}?`,
          tone: "danger",
        },
        reset: {
          body: `The cash balance goes back to ${formatUsd(option.paper?.startingBalanceUsd)}. Open positions and the fill history stay.`,
          confirmLabel: "Reset balance",
          title: `Reset ${option.label}?`,
          tone: "danger",
        },
        start: isPaper
          ? undefined
          : {
              body: "The account starts sending real orders for new copy trades as soon as reconciliation is complete.",
              confirmLabel: "Start live trading",
              title: `Start live trading in ${option.label}?`,
              tone: "danger",
            },
      };
      const request = confirmations[kind];
      if (request && !(await confirm(request))) {
        return;
      }

      setAction(kind);
      setActionError(null);
      try {
        if (kind === "delete") {
          await mutate(`${base}/${key}`, "DELETE", `${isPaper ? "Delete" : "Archive"} account failed`, false);
          setSelectedKey(null);
        } else if (kind === "reconcile") {
          await mutate(`/trading/accounts/${key}/reconcile`, "POST", "Reconcile account failed", false);
        } else {
          await mutate(`${base}/${key}/${kind}`, "POST", `${actionTitle(kind)} failed`, isPaper);
        }
      } catch (error) {
        setActionError(errorMessage(error, `${actionTitle(kind)} failed.`));
        await refresh();
      } finally {
        setAction(null);
      }
    },
    [action, confirm, mutate, refresh],
  );

  const closePosition = useCallback(
    async (position: PositionRow) => {
      if (closingPositionId) {
        return;
      }
      const live = position.accountType === "live";
      const confirmed = await confirm({
        body: live
          ? "A real reduce-only market order closes this position on Hyperliquid."
          : "The paper position closes at the current market price.",
        confirmLabel: "Close position",
        title: `Close ${position.coin} ${position.side}?`,
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
      setClosingPositionId(position.id);
      setActionError(null);
      try {
        await mutate(
          live ? `/trading/positions/${position.id}/close` : `/paper-trading/positions/${position.id}/close`,
          "POST",
          "Manual close failed",
          !live,
        );
      } catch (error) {
        setActionError(errorMessage(error, "Manual close failed."));
      } finally {
        setClosingPositionId(null);
      }
    },
    [closingPositionId, confirm, mutate],
  );

  const closeSource = useCallback(
    async (source: CopySourceRow) => {
      if (closingSource) {
        return;
      }
      const confirmed = await confirm({
        body: `Closes ${pluralize(source.openPaperPositionCount, "open paper position")} copied from this wallet, in every paper account.`,
        confirmLabel: "Close positions",
        title: `Close all positions from ${source.sourceLabel ?? "this source"}?`,
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
      setClosingSource(source.sourceWallet);
      setActionError(null);
      try {
        await mutate(
          `/paper-trading/sources/${encodeURIComponent(source.sourceWallet)}/close`,
          "POST",
          "Source close failed",
          true,
        );
      } catch (error) {
        setActionError(errorMessage(error, "Source close failed."));
      } finally {
        setClosingSource(null);
      }
    },
    [closingSource, confirm, mutate],
  );

  const createAccount = useCallback(async () => {
    if (creating) {
      return;
    }
    const balance = Number(draft.startingBalance);
    if (draft.accountType === "paper" && (!Number.isFinite(balance) || balance <= 0)) {
      setCreateError("Enter a starting balance greater than 0.");
      return;
    }
    if (draft.accountType === "live" && !draft.liveLabel.trim()) {
      setCreateError("Enter a name for the account.");
      return;
    }
    setCreating(true);
    setCreateError(null);
    try {
      if (draft.accountType === "paper") {
        const previous = new Set(summary.accounts.map((account) => account.key));
        const payload = await apiRequest<PaperTradingSummaryResponse>("/paper-trading/accounts", {
          body: { accountType: "paper", startingBalanceUsd: draft.startingBalance },
          fallback: "Create account failed",
          method: "POST",
        });
        setSummary(payload);
        const created = payload.accounts.find((account) => !previous.has(account.key)) ?? payload.accounts.at(-1);
        if (created) {
          selectAccount(created.key);
        }
      } else {
        const created = await apiRequest<TradingAccount>("/trading/accounts/live", {
          body: {
            label: draft.liveLabel.trim(),
            status: "disabled",
            vaultAddress: draft.liveVaultAddress.trim() || null,
            walletAddress: draft.liveWalletAddress.trim() || null,
          },
          fallback: "Create account failed",
          method: "POST",
        });
        selectAccount(created.key);
      }
      setDraft(DEFAULT_CREATE_DRAFT);
      await refresh();
    } catch (error) {
      setCreateError(errorMessage(error, "Create account failed."));
    } finally {
      setCreating(false);
    }
  }, [creating, draft, refresh, selectAccount, summary.accounts]);

  const view = useMemo(
    () => (selected ? buildAccountView(summary, tradingAccounts, selected) : null),
    [selected, summary, tradingAccounts],
  );
  const timeline = useMemo(() => buildPerformanceTimeline(view?.closedTrades ?? []), [view]);
  const analytics = useMemo(() => calculatePerformance(timeline), [timeline]);
  const decisions = useMemo(
    () =>
      selected?.accountType === "live"
        ? buildLiveCopyDecisionActivities(
            tradingAccounts.recentLiveCopyDecisions.filter((decision) => decision.accountKey === selected.key),
            buildSourceLabels(summary, tradingAccounts),
          )
        : [],
    [selected, summary, tradingAccounts],
  );
  const sourceHistory = useMemo(() => {
    if (!selected) {
      return [];
    }
    if (selected.accountType === "paper") {
      return buildPaperSourceHistory(summary.walletPerformance);
    }
    return buildLiveSourceHistory({
      fills: tradingAccounts.recentFills.filter((fill) => fill.accountKey === selected.key),
      metadata: buildSourceMetadata(summary, tradingAccounts),
      orders: tradingAccounts.recentOrders.filter((order) => order.accountKey === selected.key),
      positions: tradingAccounts.positions.filter((position) => position.accountKey === selected.key),
      realtimeMonitoring: summary.realtimeMonitoring,
    });
  }, [selected, summary, tradingAccounts]);
  const summaries = useMemo(
    () => options.map((option) => accountSummaryFor(option, tradingAccounts)),
    [options, tradingAccounts],
  );
  const paperAllocationUsd =
    selected?.accountType === "paper"
      ? sumNumbers(
          summary.allocations
            .filter((allocation) => allocation.accountKey === selected.key)
            .map((allocation) => allocation.allocationUsd),
        )
      : 0;

  const updatedAt = latestUpdatedAt(summary, tradingAccounts);

  return (
    <>
      <PageHeader
        title="Trading"
        description="Paper and live accounts that copy the top ranked wallets. Pick an account to see its positions, sources, and history."
        actions={
          <>
            {connection === "offline" ? <StatusDot tone="negative" label="Connection lost, retrying" /> : null}
            <RefreshControl
              isRefreshing={connection === "refreshing"}
              onRefresh={refresh}
              updatedAt={lastRefreshAt ?? updatedAt}
            />
            <button
              type="button"
              className={buttonClass({ size: "sm" })}
              onClick={() => {
                setCreateError(null);
                setDraft({ ...DEFAULT_CREATE_DRAFT, open: true });
              }}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              New account
            </button>
          </>
        }
      />

      <CreateAccountDialog
        draft={draft}
        error={createError}
        isSubmitting={creating}
        liveTradingEnabled={tradingAccounts.liveTradingEnabled}
        onChange={(patch) => setDraft((current) => ({ ...current, ...patch }))}
        onClose={() => {
          if (!creating) {
            setDraft((current) => ({ ...current, open: false }));
            setCreateError(null);
            const url = new URL(window.location.href);
            if (url.searchParams.has("create")) {
              url.searchParams.delete("create");
              window.history.replaceState(null, "", url.toString());
            }
          }
        }}
        onSubmit={() => void createAccount()}
      />

      {options.length === 0 || !selected || !view ? (
        <section className="rounded-lg border border-line bg-surface">
          <EmptyState
            title="No trading accounts"
            action={
              <button
                type="button"
                className={buttonClass({ variant: "primary" })}
                onClick={() => setDraft({ ...DEFAULT_CREATE_DRAFT, open: true })}
              >
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Create a paper account
              </button>
            }
          >
            A paper account copies the top ranked wallets with simulated money against the real order book. Start with
            paper; live accounts need LIVE_TRADING_ENABLED on the server.
          </EmptyState>
        </section>
      ) : (
        <>
          <AccountSwitcher onSelect={selectAccount} selectedKey={selected.key} summaries={summaries} />

          {actionError ? <Callout tone="negative" title="The action failed">{actionError}</Callout> : null}

          <AccountHeader
            action={action}
            liveTradingEnabled={tradingAccounts.liveTradingEnabled}
            onAction={(kind) => void runAccountAction(selected, kind)}
            option={selected}
            positionCount={view.positions.length}
            summary={view.summary}
          />

          {view.notices.map((notice) => (
            <Callout key={notice.title} tone={notice.tone} title={notice.title}>
              {notice.detail}
            </Callout>
          ))}

          <StatStrip className="xl:grid-cols-8">
            <Stat
              label="Equity"
              hint="Account value including unrealized PnL"
              value={formatUsd(view.summary.equityUsd)}
              sub={
                selected.accountType === "paper"
                  ? `started at ${formatUsd(selected.paper.startingBalanceUsd)}`
                  : `tradable ${formatUsd(selected.live.tradableEquityUsd)}`
              }
            />
            <Stat
              label="Total PnL"
              hint="Realized PnL minus fees, plus funding and unrealized PnL"
              value={<Pnl value={view.summary.totalPnlUsd} />}
              sub={
                <span title={view.summary.returnLabel}>
                  <PnlPercent value={view.summary.returnPct} /> return
                </span>
              }
            />
            <Stat
              label="Realized, net"
              value={<Pnl value={view.summary.netRealizedPnlUsd} />}
              sub={`fees ${formatUsd(view.summary.feeUsd)}, funding ${formatSignedUsd(view.summary.fundingUsd)}`}
              hint="Closed PnL after fees and funding"
            />
            <Stat
              label="Unrealized"
              value={<Pnl value={view.summary.unrealizedPnlUsd} />}
              sub={pluralize(view.summary.openPositionCount, "open position")}
            />
            <Stat
              label="Exposure"
              hint="Notional value of open positions"
              value={formatUsd(view.summary.openNotionalUsd)}
              sub={
                view.summary.equityUsd > 0
                  ? `${formatPercent(view.summary.openNotionalUsd / view.summary.equityUsd)} of equity`
                  : "-"
              }
            />
            <Stat
              label="Margin in use"
              value={formatUsd(view.summary.openMarginUsd)}
              sub={
                selected.accountType === "paper"
                  ? paperAllocationUsd > 0
                    ? `${formatPercent(view.summary.openMarginUsd / paperAllocationUsd, { decimals: 0 })} of allocations`
                    : "no allocations"
                  : view.summary.openMarginUsd > 0
                    ? `${formatMultiple(view.summary.openNotionalUsd / view.summary.openMarginUsd)} average leverage`
                    : "no open margin"
              }
            />
            <Stat
              label="Win rate"
              value={formatPercent(analytics.winRate)}
              tone={analytics.winRate === null ? "neutral" : analytics.winRate >= 0.5 ? "positive" : "warning"}
              sub={`of ${formatInteger(analytics.tradeCount)} closed trades`}
            />
            {selected.accountType === "live" ? (
              <ReconcileStat
                account={selected.live}
                busy={action === "reconcile"}
                onReconcile={() => void runAccountAction(selected, "reconcile")}
              />
            ) : (
              <Stat
                label="Profit factor"
                hint="Gross profit divided by gross loss of the loaded closed trades"
                value={formatMultiple(analytics.profitFactor)}
                sub={`${formatSignedUsd(analytics.expectancyUsd)} per trade`}
              />
            )}
          </StatStrip>

          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,1fr)]">
            <PerformancePanel analytics={analytics} points={timeline} />
            <RiskPanel view={view} />
          </div>

          <PositionsPanel closingId={closingPositionId} onClose={(position) => void closePosition(position)} positions={view.positions} />

          <SourcesPanel
            accountType={view.accountType}
            closingSource={closingSource}
            combineAccounts={view.sourcesCombineAccounts}
            onCloseSource={(source) => void closeSource(source)}
            sources={view.sources}
          />

          <HistoryPanel
            accountKey={view.accountKey}
            accountType={view.accountType}
            cashFlowsVersion={view.cashFlowsVersion}
            closedTrades={view.closedTrades}
            decisions={decisions}
            executions={view.executions}
            sourceHistory={sourceHistory}
          />

          <AccountDetailsPanel
            allocationUsd={paperAllocationUsd}
            lastRefreshAt={lastRefreshAt}
            liveTradingEnabled={tradingAccounts.liveTradingEnabled}
            option={selected}
            policy={summary.policy}
            riskLimits={tradingAccounts.riskLimits}
            view={view}
          />
        </>
      )}
    </>
  );
}

function AccountSwitcher({
  onSelect,
  selectedKey,
  summaries,
}: {
  onSelect: (key: string) => void;
  selectedKey: string;
  summaries: AccountSummary[];
}) {
  return (
    <div role="tablist" aria-label="Accounts" className="flex gap-2 overflow-x-auto pb-0.5">
      {summaries.map((account) => {
        const active = account.key === selectedKey;
        return (
          <button
            key={account.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onSelect(account.key)}
            className={cx(
              "min-w-[200px] shrink-0 rounded-lg border px-3 py-2 text-left transition-colors",
              active ? "border-fg bg-surface" : "border-line bg-surface hover:border-line-strong",
            )}
          >
            <span className="flex items-center gap-2">
              <span className="truncate text-sm font-medium">{account.label}</span>
              <Badge tone={account.accountType === "live" ? "negative" : "neutral"}>
                {account.accountType === "live" ? "Live" : "Paper"}
              </Badge>
            </span>
            <span className="mt-1 flex items-center justify-between gap-3 text-xs">
              <StatusDot tone={account.stateTone} label={<span className="text-fg-3">{account.stateLabel}</span>} />
              <span className="tabular-nums">
                {formatUsd(account.equityUsd)} <Pnl value={account.totalPnlUsd} className="ml-1" />
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function AccountHeader({
  action,
  liveTradingEnabled,
  onAction,
  option,
  positionCount,
  summary,
}: {
  action: AccountAction | null;
  liveTradingEnabled: boolean;
  onAction: (kind: AccountAction) => void;
  option: AccountOption;
  positionCount: number;
  summary: AccountSummary;
}) {
  const isPaper = option.accountType === "paper";
  const running = isPaper ? option.paper.enabled : option.live.status === "enabled";
  const exitOnlyLive = !isPaper && option.live.status === "exit_only";
  const liveArchivable = isPaper || option.live.status === "disabled";
  const busy = action !== null;
  const spinner = (kind: AccountAction) => (action === kind ? <Spinner /> : null);

  const menuItems: MenuItem[] = [
    ...(!running && !exitOnlyLive && positionCount > 0
      ? [{ danger: true, label: "Close all positions", onSelect: () => onAction("close-all-and-stop") }]
      : []),
    ...(isPaper ? [{ label: "Reset balance", onSelect: () => onAction("reset") }] : []),
    ...(!isPaper ? [{ label: "Reconcile with exchange now", onSelect: () => onAction("reconcile") }] : []),
    {
      danger: true,
      disabled: !liveArchivable,
      hint: liveArchivable ? undefined : "Disable the live account after a fresh flat reconciliation before archiving it.",
      label: isPaper ? "Delete account" : "Archive account",
      onSelect: () => onAction("delete"),
    },
  ];

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-base font-semibold">{option.label}</h2>
          <StatusDot
            tone={summary.stateTone}
            pulse={summary.state === "trading"}
            label={<span className="text-sm text-fg-2">{summary.stateLabel}</span>}
          />
        </div>
        {summary.stateDetail ? <p className="text-xs text-fg-3">{summary.stateDetail}</p> : null}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {exitOnlyLive ? (
          <>
            <button
              type="button"
              className={buttonClass({ size: "sm" })}
              disabled={busy}
              onClick={() => onAction("disable")}
              title="Reconcile the exchange account and disable it only when it is flat"
            >
              {spinner("disable") ?? <Square className="h-3 w-3" aria-hidden="true" />}
              Verify flat and disable
            </button>
            <button
              type="button"
              className={buttonClass({ size: "sm", variant: "danger" })}
              disabled={busy}
              onClick={() => onAction("close-all-and-stop")}
            >
              {spinner("close-all-and-stop")}
              Close all
            </button>
          </>
        ) : running ? (
          <>
            <button
              type="button"
              className={buttonClass({ size: "sm" })}
              disabled={busy}
              onClick={() => onAction("stop")}
            >
              {spinner("stop") ?? <Square className="h-3 w-3" aria-hidden="true" />}
              Stop trading
            </button>
            <button
              type="button"
              className={buttonClass({ size: "sm", variant: "danger" })}
              disabled={busy}
              onClick={() => onAction("close-all-and-stop")}
            >
              {spinner("close-all-and-stop")}
              Close all and stop
            </button>
          </>
        ) : (
          <button
            type="button"
            className={buttonClass({ size: "sm", variant: "primary" })}
            disabled={busy || (!isPaper && !liveTradingEnabled)}
            onClick={() => onAction("start")}
            title={!isPaper && !liveTradingEnabled ? "Set LIVE_TRADING_ENABLED=true before starting a live account" : undefined}
          >
            {spinner("start") ?? <Play className="h-3 w-3" aria-hidden="true" />}
            Start trading
          </button>
        )}
        <Menu items={menuItems} label="More account actions" />
      </div>
    </div>
  );
}

function ReconcileStat({
  account,
  busy,
  onReconcile,
}: {
  account: TradingAccount;
  busy: boolean;
  onReconcile: () => void;
}) {
  const status = account.reconciliationStatus;
  const tone = status === "complete" ? "positive" : status === "failed" ? "negative" : "warning";
  return (
    <Stat
      label="Exchange sync"
      hint="Our records compared with the exchange account"
      tone={tone}
      value={status === "complete" ? "In sync" : status === "never" ? "Pending" : status === "failed" ? "Failed" : "Partial"}
      sub={
        account.incompleteReconciliationComponents.length > 0
          ? `${account.incompleteReconciliationComponents.join(", ")} incomplete`
          : (
              <>
                checked <TimeAgo value={account.reconciliationAttemptedAt ?? account.lastReconciledAt} fallback="never" />
              </>
            )
      }
      action={
        <button
          type="button"
          onClick={onReconcile}
          disabled={busy}
          aria-label="Reconcile live account"
          title="Reconcile with the exchange now"
          className="rounded p-0.5 text-fg-3 hover:bg-surface-3 hover:text-fg disabled:opacity-50"
        >
          <RotateCw className={cx("h-3.5 w-3.5", busy && "animate-spin")} aria-hidden="true" />
        </button>
      }
    />
  );
}

function actionTitle(kind: AccountAction) {
  const titles: Record<AccountAction, string> = {
    "close-all-and-stop": "Close all and stop",
    delete: "Delete account",
    disable: "Disable account",
    reconcile: "Reconcile account",
    reset: "Reset balance",
    start: "Start trading",
    stop: "Stop trading",
  };
  return titles[kind];
}
