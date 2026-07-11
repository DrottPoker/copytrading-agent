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
  RotateCw,
  ShieldAlert,
  Square,
  Target,
  Trash2,
  TrendingDown,
  TrendingUp,
  WalletCards,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { DashboardMetric, DashboardPanel } from "@/components/DashboardSurface";
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
import type {
  TradingAccount,
  TradingAccountsResponse,
  TradingCapitalBalance,
  TradingClosedTrade,
  TradingFill,
  TradingOrder,
  TradingPosition,
} from "@/types/trading";

import { HeaderRefreshButton, HeaderUpdatedLabel } from "./HeaderRefresh";
import { PageTopPanel } from "./PageTopPanel";
import { StatusPill } from "./StatusPill";

const ACCOUNT_REFRESH_MS = 4000;
const ACCOUNT_SUMMARY_LIMIT = 250;
const SELECTED_ACCOUNT_STORAGE_KEY = "copyagent.accounts.selectedAccountKey";
const CREATE_ACCOUNT_DRAFT_STORAGE_KEY = "copyagent.accounts.createAccountDraft";

type Tone = "positive" | "warning" | "danger" | "neutral";
type TradingAction = "start" | "stop" | "disable" | "close-all-and-stop" | "delete";
type CreateAccountType = "paper" | "live";
type RefreshOptions = {
  skipIfCreateDialogOpen?: boolean;
};
type CreateAccountDraft = {
  accountType: CreateAccountType;
  liveLabel: string;
  liveVaultAddress: string;
  liveWalletAddress: string;
  open: boolean;
  startingBalance: string;
};

type LiveAccountNotice = {
  detail: string;
  title: string;
  tone: "danger" | "neutral" | "warning";
};

const DEFAULT_CREATE_ACCOUNT_DRAFT: CreateAccountDraft = {
  accountType: "paper",
  liveLabel: "Main wallet",
  liveVaultAddress: "",
  liveWalletAddress: "",
  open: false,
  startingBalance: "1000",
};

type AccountOption =
  | {
      accountType: "paper";
      key: string;
      label: string;
      paper: PaperTradingAccount;
      live?: never;
    }
  | {
      accountType: "live";
      key: string;
      label: string;
      live: TradingAccount;
      paper?: never;
    };

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

type SourceMetadata = {
  allocationPct: number | null;
  label: string | null;
  poolRank: number | null;
  rank: number | null;
  score: string | null;
};

type AccountPositionRow = {
  accountType: "paper" | "live";
  coin: string;
  detail: string;
  entryDetail: string;
  entryPrice: string | number | null;
  executionDetail: string;
  executionValue: string;
  id: string;
  leverage: string | number | null;
  marginMode: "cross" | "isolated" | null;
  notionalUsd: string | number | null;
  side: "long" | "short";
  sourceHref: string | null;
  sourceLabel: string;
  unrealizedPnlUsd: string | number | null;
};

type AccountClosedTradeRow = {
  badges: RowPill[];
  closedAt: string;
  coin: string;
  detail: string;
  exitDetail: string;
  exitPrice: string | number | null;
  id: string;
  netPnlUsd: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
};

type AccountExecutionRow = {
  badges: RowPill[];
  coin: string;
  detail: string;
  id: string;
  notionalDetail?: string;
  notionalUsd: string | number | null;
  price: string | number | null;
  priceDetail?: string;
  realizedPnlUsd: string | number | null;
  sourceHref: string | null;
  sourceLabel: string;
};

type AccountDetailSection = {
  icon: LucideIcon;
  rows: Array<{ label: string; value: string }>;
  title: string;
};

type MetricTileView = {
  action?: ReactNode;
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: Tone;
  value: string;
};

type MetricLineView = {
  label: string;
  tone?: Tone;
  value: number;
  valueLabel: string;
};

type RowPill = {
  label: string;
  tone: Tone;
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
  accountType: "paper" | "live";
  allocations: PaperCopyAllocation[];
  balanceLines: MetricLineView[];
  capitalBalances: TradingCapitalBalance[];
  closedTrades: AccountClosedTradeRow[];
  detailSections: AccountDetailSection[];
  marketRows: MarketRow[];
  metrics: AccountMetrics;
  metricTiles: MetricTileView[];
  positions: AccountPositionRow[];
  recentActivity: AccountExecutionRow[];
  sourceRows: SourceRow[];
  timeline: TimelinePoint[];
};

export function AccountsDashboard({
  initialSummary,
  initialTradingAccounts,
}: {
  initialSummary: PaperTradingSummaryResponse;
  initialTradingAccounts: TradingAccountsResponse;
}) {
  const [summary, setSummary] = useState(initialSummary);
  const [tradingAccounts, setTradingAccounts] = useState(initialTradingAccounts);
  const [selectedAccountKey, setSelectedAccountKey] = useState(
    initialAccountKey(initialSummary, initialTradingAccounts),
  );
  const [connectionState, setConnectionState] = useState<"live" | "refreshing" | "offline">(
    "live",
  );
  const [accountAction, setAccountAction] = useState<TradingAction | null>(null);
  const [createDraft, setCreateDraft] = useState<CreateAccountDraft>(readCreateAccountDraft);
  const [createSubmitting, setCreateSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reconcileAccountKey, setReconcileAccountKey] = useState<string | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(new Date());
  const [storedSelectionLoaded, setStoredSelectionLoaded] = useState(false);
  const createAccountOpenRef = useRef(createDraft.open);
  const createAccountOpen = createDraft.open;
  const createAccountType = createDraft.accountType;
  const createStartingBalance = createDraft.startingBalance;
  const createLiveLabel = createDraft.liveLabel;
  const createLiveWalletAddress = createDraft.liveWalletAddress;
  const createLiveVaultAddress = createDraft.liveVaultAddress;

  const accountOptions = useMemo(
    () => buildAccountOptions(summary, tradingAccounts),
    [summary, tradingAccounts],
  );
  const selectedAccount = useMemo(
    () =>
      accountOptions.find((account) => account.key === selectedAccountKey) ??
      accountOptions[0] ??
      null,
    [accountOptions, selectedAccountKey],
  );

  useEffect(() => {
    if (storedSelectionLoaded) {
      return;
    }
    const storedAccountKey = window.localStorage.getItem(SELECTED_ACCOUNT_STORAGE_KEY);
    if (storedAccountKey && accountOptions.some((account) => account.key === storedAccountKey)) {
      setSelectedAccountKey(storedAccountKey);
    }
    setStoredSelectionLoaded(true);
  }, [accountOptions, storedSelectionLoaded]);

  useEffect(() => {
    if (!selectedAccountKey) {
      return;
    }
    window.localStorage.setItem(SELECTED_ACCOUNT_STORAGE_KEY, selectedAccountKey);
  }, [selectedAccountKey]);

  useEffect(() => {
    if (
      selectedAccountKey &&
      accountOptions.some((account) => account.key === selectedAccountKey)
    ) {
      return;
    }
    setSelectedAccountKey(accountOptions[0]?.key ?? "");
  }, [accountOptions, selectedAccountKey]);

  useEffect(() => {
    createAccountOpenRef.current = createAccountOpen;
    if (createAccountOpen) {
      writeCreateAccountDraft(createDraft);
    } else {
      clearCreateAccountDraft();
    }
  }, [createAccountOpen, createDraft]);

  const refresh = useCallback(async (options: RefreshOptions = {}) => {
    if (options.skipIfCreateDialogOpen && createAccountOpenRef.current) {
      return;
    }

    setConnectionState("refreshing");
    try {
      const url = new URL(`${getPublicApiBaseUrl()}/paper-trading`, window.location.origin);
      url.searchParams.set("closed_trade_limit", String(ACCOUNT_SUMMARY_LIMIT));
      url.searchParams.set("recent_fill_limit", String(ACCOUNT_SUMMARY_LIMIT));
      const [paperResponse, tradingResponse] = await Promise.all([
        fetch(url.toString(), { cache: "no-store" }),
        fetch(`${getPublicApiBaseUrl()}/trading/accounts`, { cache: "no-store" }),
      ]);
      if (!paperResponse.ok || !tradingResponse.ok) {
        setConnectionState("offline");
        return;
      }
      const [paperPayload, tradingPayload] = await Promise.all([
        paperResponse.json() as Promise<PaperTradingSummaryResponse>,
        tradingResponse.json() as Promise<TradingAccountsResponse>,
      ]);
      if (options.skipIfCreateDialogOpen && createAccountOpenRef.current) {
        setConnectionState("live");
        return;
      }
      setSummary(paperPayload);
      setTradingAccounts(tradingPayload);
      setLastRefreshAt(new Date());
      setConnectionState("live");
    } catch {
      if (!options.skipIfCreateDialogOpen || !createAccountOpenRef.current) {
        setConnectionState("offline");
      }
    }
  }, []);

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      void refresh({ skipIfCreateDialogOpen: true });
    }, ACCOUNT_REFRESH_MS);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  const accountView = useMemo(
    () => buildSelectedAccountView(summary, tradingAccounts, selectedAccount),
    [selectedAccount, summary, tradingAccounts],
  );
  const liveAccountNotice =
    selectedAccount?.accountType === "live"
      ? buildLiveAccountNotice(
          selectedAccount.live,
          tradingAccounts.riskLimits.reconciliationMaxSnapshotAgeSeconds,
          tradingAccounts.updatedAt,
        )
      : null;

  const handleTradingAction = useCallback(
    async (action: TradingAction) => {
      if (!selectedAccount || accountAction) {
        return;
      }
      if (
        selectedAccount.accountType === "live" &&
        action === "start" &&
        !tradingAccounts.liveTradingEnabled
      ) {
        setActionError("Set LIVE_TRADING_ENABLED=true before starting a live account.");
        return;
      }
      if (
        selectedAccount.accountType === "paper" &&
        accountView &&
        action === "close-all-and-stop" &&
        accountView.positions.length > 0
      ) {
        const confirmed = window.confirm(
          `Close ${formatInteger(
            accountView.positions.length,
          )} open paper positions for ${selectedAccount.label} and stop trading?`,
        );
        if (!confirmed) {
          return;
        }
      }
      if (selectedAccount.accountType === "live" && action === "close-all-and-stop") {
        const confirmed = window.confirm(
          `Close all open live positions for ${selectedAccount.label} and stop trading?`,
        );
        if (!confirmed) {
          return;
        }
      }

      setAccountAction(action);
      setActionError(null);
      try {
        const basePath =
          selectedAccount.accountType === "paper" ? "paper-trading/accounts" : "trading/accounts";
        const response = await fetch(
          `${getPublicApiBaseUrl()}/${basePath}/${encodeURIComponent(selectedAccount.key)}/${action}`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, `${tradingActionLabel(action)} failed`));
          await refresh();
          return;
        }

        if (selectedAccount.accountType === "paper") {
          const payload = (await response.json()) as PaperTradingSummaryResponse;
          setSummary(payload);
          setLastRefreshAt(new Date());
          setConnectionState("live");
        } else {
          await refresh();
        }
      } catch {
        setConnectionState("offline");
        setActionError(`${tradingActionLabel(action)} failed.`);
      } finally {
        setAccountAction(null);
      }
    },
    [accountAction, accountView, refresh, selectedAccount, tradingAccounts.liveTradingEnabled],
  );

  const handleDeleteAccount = useCallback(async () => {
    if (!selectedAccount || accountAction) {
      return;
    }
    if (selectedAccount.accountType === "live" && selectedAccount.live.status !== "disabled") {
      setActionError("Disable the live account after a fresh flat reconciliation before archiving it.");
      return;
    }

    const confirmed = window.confirm(
      selectedAccount.accountType === "live"
        ? `Archive ${selectedAccount.label}? Trading history is retained.`
        : `Delete ${selectedAccount.label}? This deletes local paper positions, fills, ` +
            "allocations, and account history. This cannot be undone.",
    );
    if (!confirmed) {
      return;
    }

    setAccountAction("delete");
    setActionError(null);
    try {
      const basePath =
        selectedAccount.accountType === "paper" ? "paper-trading/accounts" : "trading/accounts";
      const response = await fetch(
        `${getPublicApiBaseUrl()}/${basePath}/${encodeURIComponent(selectedAccount.key)}`,
        { cache: "no-store", method: "DELETE" },
      );
      if (!response.ok) {
        setActionError(await responseError(response, "Delete account failed"));
        await refresh();
        return;
      }
      setSelectedAccountKey("");
      await refresh();
    } catch {
      setConnectionState("offline");
      setActionError("Delete account failed.");
    } finally {
      setAccountAction(null);
    }
  }, [accountAction, refresh, selectedAccount]);

  const handleReconcileLiveAccount = useCallback(
    async (account: TradingAccount) => {
      if (reconcileAccountKey) {
        return;
      }

      setReconcileAccountKey(account.key);
      setActionError(null);
      try {
        const response = await fetch(
          `${getPublicApiBaseUrl()}/trading/accounts/${encodeURIComponent(account.key)}/reconcile`,
          { cache: "no-store", method: "POST" },
        );
        if (!response.ok) {
          setActionError(await responseError(response, "Reconcile account failed"));
          await refresh();
          return;
        }
        await refresh();
      } catch {
        setConnectionState("offline");
        setActionError("Reconcile account failed.");
      } finally {
        setReconcileAccountKey(null);
      }
    },
    [reconcileAccountKey, refresh],
  );

  const updateCreateDraft = useCallback((patch: Partial<CreateAccountDraft>) => {
    setCreateDraft((current) => ({ ...current, ...patch }));
  }, []);

  const setCreateAccountType = useCallback(
    (accountType: CreateAccountType) => updateCreateDraft({ accountType }),
    [updateCreateDraft],
  );
  const setCreateStartingBalance = useCallback(
    (startingBalance: string) => updateCreateDraft({ startingBalance }),
    [updateCreateDraft],
  );
  const setCreateLiveLabel = useCallback(
    (liveLabel: string) => updateCreateDraft({ liveLabel }),
    [updateCreateDraft],
  );
  const setCreateLiveWalletAddress = useCallback(
    (liveWalletAddress: string) => updateCreateDraft({ liveWalletAddress }),
    [updateCreateDraft],
  );
  const setCreateLiveVaultAddress = useCallback(
    (liveVaultAddress: string) => updateCreateDraft({ liveVaultAddress }),
    [updateCreateDraft],
  );

  const openCreateAccount = useCallback(() => {
    setCreateDraft({ ...DEFAULT_CREATE_ACCOUNT_DRAFT, open: true });
    setCreateError(null);
  }, []);

  const closeCreateAccount = useCallback(() => {
    if (createSubmitting) {
      return;
    }
    updateCreateDraft({ open: false });
    setCreateError(null);
  }, [createSubmitting, updateCreateDraft]);

  const handleCreateAccount = useCallback(async () => {
    if (createSubmitting) {
      return;
    }

    const startingBalance = Number(createStartingBalance);
    if (
      createAccountType === "paper" &&
      (!Number.isFinite(startingBalance) || startingBalance <= 0)
    ) {
      setCreateError("Enter a starting balance greater than 0.");
      return;
    }
    if (createAccountType === "live" && !createLiveLabel.trim()) {
      setCreateError("Enter a wallet name.");
      return;
    }

    setCreateSubmitting(true);
    setCreateError(null);
    try {
      const previousKeys = new Set(accountOptions.map((account) => account.key));
      const endpoint =
        createAccountType === "paper" ? "/paper-trading/accounts" : "/trading/accounts/live";
      const body =
        createAccountType === "paper"
          ? {
              accountType: "paper",
              startingBalanceUsd: createStartingBalance,
            }
          : {
              label: createLiveLabel.trim(),
              walletAddress: createLiveWalletAddress.trim() || null,
              vaultAddress: createLiveVaultAddress.trim() || null,
              status: "disabled",
            };
      const response = await fetch(`${getPublicApiBaseUrl()}${endpoint}`, {
        body: JSON.stringify(body),
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

      if (createAccountType === "paper") {
        const payload = (await response.json()) as PaperTradingSummaryResponse;
        const createdAccount =
          payload.accounts.find((account) => !previousKeys.has(account.key)) ??
          payload.accounts[payload.accounts.length - 1];
        setSummary(payload);
        if (createdAccount) {
          setSelectedAccountKey(createdAccount.key);
        }
      } else {
        const createdAccount = (await response.json()) as TradingAccount;
        setSelectedAccountKey(createdAccount.key);
        await refresh();
      }
      setLastRefreshAt(new Date());
      setConnectionState("live");
      setCreateDraft(DEFAULT_CREATE_ACCOUNT_DRAFT);
    } catch {
      setConnectionState("offline");
      setCreateError("Create account failed.");
    } finally {
      setCreateSubmitting(false);
    }
  }, [
    accountOptions,
    createAccountType,
    createLiveLabel,
    createLiveVaultAddress,
    createLiveWalletAddress,
    createStartingBalance,
    createSubmitting,
    refresh,
  ]);

  return (
    <>
      <PageTopPanel
        eyebrow="Account performance"
        icon={WalletCards}
        title="Accounts"
        actions={
          <>
            <HeaderUpdatedLabel label={`Updated ${formatDate(lastUpdatedAt(summary, tradingAccounts))}`} />
            <StatusPill
              label={tradingAccounts.liveTradingEnabled ? "live enabled" : "live disabled"}
              tone={tradingAccounts.liveTradingEnabled ? "positive" : "neutral"}
            />
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

      <section className="ui-panel flex flex-col gap-3 p-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-[0.05em] text-muted">
            Active account
          </span>
          <select
            aria-label="Select account"
            className="ui-control min-w-[220px]"
            value={selectedAccountKey}
            onChange={(event) => setSelectedAccountKey(event.target.value)}
          >
            {accountOptions.map((account) => (
              <option key={`${account.accountType}:${account.key}`} value={account.key}>
                {accountOptionLabel(account)}
              </option>
            ))}
          </select>
          {selectedAccount ? (
            <StatusPill
              label={
                selectedAccount.accountType === "live" &&
                selectedAccount.live.status === "enabled" &&
                liveAccountNotice
                  ? "entries paused"
                  : accountTradingStatusLabel(selectedAccount)
              }
              tone={
                selectedAccount.accountType === "live" &&
                selectedAccount.live.status === "enabled" &&
                liveAccountNotice
                  ? "warning"
                  : accountTradingStatusTone(selectedAccount)
              }
            />
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2 xl:justify-end">
          <button type="button" onClick={openCreateAccount} className="ui-button-secondary">
            <Plus className="h-4 w-4" aria-hidden="true" />
            Create account
          </button>
          {selectedAccount?.accountType === "live" &&
          selectedAccount.live.status === "exit_only" ? (
            <>
              <button
                type="button"
                onClick={() => void handleTradingAction("disable")}
                disabled={accountAction !== null}
                title="Reconcile the exchange account and disable only when it is flat"
                className="ui-button-secondary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {accountAction === "disable" ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Square className="h-4 w-4" aria-hidden="true" />
                )}
                Verify flat and disable
              </button>
              <button
                type="button"
                onClick={() => void handleTradingAction("close-all-and-stop")}
                disabled={accountAction !== null}
                className="ui-button-danger disabled:cursor-not-allowed disabled:opacity-60"
              >
                <XCircle className="h-4 w-4" aria-hidden="true" />
                Close all
              </button>
            </>
          ) : null}
          {selectedAccount ? (
            accountTradingEnabled(selectedAccount) ? (
              <>
                <button
                  type="button"
                  onClick={() => void handleTradingAction("stop")}
                  disabled={accountAction !== null}
                  className="ui-button-warning disabled:cursor-not-allowed disabled:opacity-60"
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
                  className="ui-button-danger disabled:cursor-not-allowed disabled:opacity-60"
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
                disabled={
                  accountAction !== null ||
                  (selectedAccount.accountType === "live" &&
                    !tradingAccounts.liveTradingEnabled)
                }
                title={
                  selectedAccount.accountType === "live" &&
                  !tradingAccounts.liveTradingEnabled
                    ? "Set LIVE_TRADING_ENABLED=true before starting this account"
                    : "Start trading"
                }
                className="ui-button-positive disabled:cursor-not-allowed disabled:opacity-60"
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
          {selectedAccount ? (
            <button
              type="button"
              onClick={() => void handleDeleteAccount()}
              disabled={
                accountAction !== null ||
                (selectedAccount.accountType === "live" &&
                  selectedAccount.live.status !== "disabled")
              }
              title={
                selectedAccount.accountType === "live" &&
                selectedAccount.live.status !== "disabled"
                  ? "Disable the live account after a fresh flat reconciliation before archiving it"
                  : "Delete selected account"
              }
              className="ui-button-danger bg-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {accountAction === "delete" ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              )}
              {selectedAccount.accountType === "live" ? "Archive account" : "Delete account"}
            </button>
          ) : null}
        </div>
      </section>

      {liveAccountNotice ? <LiveAccountHealthNotice notice={liveAccountNotice} /> : null}

      <LiveRiskPanel
        enabled={tradingAccounts.liveTradingEnabled}
        limits={tradingAccounts.riskLimits}
      />

      <CreateAccountDialog
        accountType={createAccountType}
        balance={createStartingBalance}
        error={createError}
        isSubmitting={createSubmitting}
        liveLabel={createLiveLabel}
        liveVaultAddress={createLiveVaultAddress}
        liveWalletAddress={createLiveWalletAddress}
        onAccountTypeChange={setCreateAccountType}
        onBalanceChange={setCreateStartingBalance}
        onClose={closeCreateAccount}
        onLiveLabelChange={setCreateLiveLabel}
        onLiveVaultAddressChange={setCreateLiveVaultAddress}
        onLiveWalletAddressChange={setCreateLiveWalletAddress}
        onSubmit={handleCreateAccount}
        open={createAccountOpen}
      />

      {actionError ? (
        <div className="rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm font-medium text-danger">
          {actionError}
        </div>
      ) : null}

      {accountView ? (
        <AccountContent
          accountView={accountView}
          lastRefreshAt={lastRefreshAt}
          marketDataStatus={summary.marketDataStatus}
          isReconciling={
            selectedAccount?.accountType === "live" &&
            reconcileAccountKey === selectedAccount.live.key
          }
          onReconcile={
            selectedAccount?.accountType === "live"
              ? () => handleReconcileLiveAccount(selectedAccount.live)
              : null
          }
        />
      ) : (
        <section className="ui-panel p-8 text-center text-sm text-muted">
          No accounts are synced yet.
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
  liveLabel,
  liveVaultAddress,
  liveWalletAddress,
  onAccountTypeChange,
  onBalanceChange,
  onClose,
  onLiveLabelChange,
  onLiveVaultAddressChange,
  onLiveWalletAddressChange,
  onSubmit,
  open,
}: {
  accountType: CreateAccountType;
  balance: string;
  error: string | null;
  isSubmitting: boolean;
  liveLabel: string;
  liveVaultAddress: string;
  liveWalletAddress: string;
  onAccountTypeChange: (accountType: CreateAccountType) => void;
  onBalanceChange: (balance: string) => void;
  onClose: () => void;
  onLiveLabelChange: (value: string) => void;
  onLiveVaultAddressChange: (value: string) => void;
  onLiveWalletAddressChange: (value: string) => void;
  onSubmit: () => void;
  open: boolean;
}) {
  const titleId = useId();
  const parsedBalance = Number(balance);
  const canCreate =
    !isSubmitting &&
    (accountType === "paper"
      ? Number.isFinite(parsedBalance) && parsedBalance > 0
      : liveLabel.trim().length > 0);

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
      className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/60 px-3 py-6 backdrop-blur-sm sm:px-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mx-auto flex min-h-full w-full max-w-xl items-center"
      >
        <form
          className="w-full overflow-hidden rounded-xl border border-line bg-panel shadow-raised"
          onClick={(event) => event.stopPropagation()}
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <div className="flex items-start justify-between gap-4 border-b border-line px-4 py-4 sm:px-5">
            <div>
              <p className="text-xs font-medium uppercase text-muted">Accounts</p>
              <h2 id={titleId} className="mt-1 text-xl font-semibold">
                Create account
              </h2>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="ui-icon-button rounded-full"
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
                detail="Real exchange"
                icon={Activity}
                label="Live account"
                onClick={() => onAccountTypeChange("live")}
              />
            </div>

            {accountType === "paper" ? (
              <label className="grid gap-2">
                <span className="text-sm font-semibold text-ink">Starting balance</span>
                <div className="flex items-center rounded-md border border-line bg-white shadow-sm focus-within:border-brand">
                  <span className="border-r border-line px-3 text-sm font-semibold text-muted">
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
                <span className="text-xs font-medium text-muted">
                  Starts disabled with {formatCurrency(parsedBalance || 0)}
                </span>
              </label>
            ) : (
              <div className="grid gap-3">
                <label className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">Wallet name</span>
                  <input
                    maxLength={120}
                    value={liveLabel}
                    onChange={(event) => onLiveLabelChange(event.target.value)}
                    className="ui-control"
                    placeholder="Main wallet"
                  />
                </label>
                <label className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">Wallet address</span>
                  <input
                    value={liveWalletAddress}
                    onChange={(event) => onLiveWalletAddressChange(event.target.value)}
                    className="ui-control font-mono"
                    placeholder="Uses and saves HYPERLIQUID_WALLET_ADDRESS when empty"
                  />
                </label>
                <label className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">Vault address</span>
                  <input
                    value={liveVaultAddress}
                    onChange={(event) => onLiveVaultAddressChange(event.target.value)}
                    className="ui-control font-mono"
                    placeholder="Optional"
                  />
                </label>
                <span className="text-xs font-medium text-muted">
                  Starts disabled. The internal key is generated from the wallet route.
                </span>
              </div>
            )}

            {error ? (
              <div className="rounded-md border border-danger/25 bg-danger-soft px-3 py-2 text-sm font-medium text-danger">
                {error}
              </div>
            ) : null}
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-subtle px-4 py-3 sm:px-5">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="ui-button-secondary disabled:cursor-not-allowed disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canCreate}
              className="ui-button-primary disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isSubmitting ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Plus className="h-4 w-4" aria-hidden="true" />
              )}
              Create {accountType} account
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
          ? "border-brand bg-brand-soft text-ink"
          : "border-line bg-white text-secondary hover:bg-subtle"
      }`}
    >
      <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-white">
        <Icon className="h-4 w-4" aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{label}</span>
        <span className="mt-0.5 block text-xs font-medium text-muted">{detail}</span>
      </span>
    </button>
  );
}

function AccountContent({
  accountView,
  isReconciling,
  lastRefreshAt,
  marketDataStatus,
  onReconcile,
}: {
  accountView: AccountView;
  isReconciling: boolean;
  lastRefreshAt: Date | null;
  marketDataStatus: PaperTradingSummaryResponse["marketDataStatus"];
  onReconcile: (() => void) | null;
}) {
  const metricTiles = onReconcile
    ? accountView.metricTiles.map((tile) =>
        tile.label === "Reconciled"
          ? {
              ...tile,
              action: (
                <button
                  type="button"
                  onClick={onReconcile}
                  disabled={isReconciling}
                  className="ui-icon-button h-8 w-8 disabled:cursor-not-allowed disabled:opacity-60"
                  title="Reconcile live account"
                  aria-label="Reconcile live account"
                >
                  <RotateCw
                    className={`h-4 w-4 ${isReconciling ? "animate-spin" : ""}`}
                    aria-hidden="true"
                  />
                </button>
              ),
            }
          : tile,
      )
    : accountView.metricTiles;

  return (
    <>
      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        {metricTiles.map((tile) => (
          <MetricTile
            key={tile.label}
            action={tile.action}
            detail={tile.detail}
            icon={tile.icon}
            label={tile.label}
            tone={tile.tone}
            value={tile.value}
          />
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel icon={Activity} title="Account Balance">
          <BalanceBreakdown rows={accountView.balanceLines} />
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            {accountView.detailSections[0]?.rows.slice(0, 2).map((row) => (
              <SmallMetric key={row.label} label={row.label} value={row.value} />
            ))}
            <SmallMetric label="Last refresh" value={lastRefreshAt?.toLocaleTimeString("sv-SE") ?? "-"} />
          </div>
        </Panel>

        <Panel icon={LineChart} title="Closed Trade PnL">
          <CumulativePnlChart points={accountView.timeline} />
          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            <SmallMetric label="Closed trades" value={formatInteger(accountView.closedTrades.length)} />
            <SmallMetric label="Closed net" value={formatCurrency(accountView.metrics.closedNetPnlUsd)} />
            <SmallMetric label="Avg closed" value={formatCurrency(accountView.metrics.averageClosedPnlUsd)} />
            <SmallMetric label="Win rate" value={formatPercent(accountView.metrics.winRate)} />
          </div>
        </Panel>
      </section>

      {accountView.detailSections.length > 0 ? (
        <section className="grid gap-4 xl:grid-cols-2">
          {accountView.detailSections.map((section) => (
            <Panel key={section.title} icon={section.icon} title={section.title}>
              <div className="grid gap-2 sm:grid-cols-2">
                {section.rows.map((row) => (
                  <SmallMetric key={row.label} label={row.label} value={row.value} />
                ))}
              </div>
            </Panel>
          ))}
        </section>
      ) : null}

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

        <Panel icon={BarChart3} title="Recent Execution Activity">
          <FillRows fills={accountView.recentActivity} />
        </Panel>
      </section>

      {accountView.capitalBalances.length > 0 ? (
        <section>
        <Panel icon={Layers} title="Capital Balances">
            <CapitalBalanceRows balances={accountView.capitalBalances} />
        </Panel>
        </section>
      ) : null}
    </>
  );
}

function CapitalBalanceRows({ balances }: { balances: TradingCapitalBalance[] }) {
  if (balances.length === 0) {
    return <EmptyState text="No capital snapshot has been reconciled yet." />;
  }

  return (
    <div className="grid gap-2">
      {balances.map((balance) => (
        <div
          key={balance.key}
          className="grid gap-2 rounded-md border border-line bg-subtle px-3 py-2 sm:grid-cols-[minmax(180px,1fr)_150px_150px_110px] sm:items-center"
        >
          <div className="min-w-0">
            <p className="break-words text-sm font-semibold text-ink">{balance.label}</p>
            <p className="break-words font-mono text-xs text-muted">{balance.key}</p>
            {balance.stale ? (
              <p className="mt-1 text-xs font-medium text-warning">
                Stale snapshot{balance.error ? `: ${balance.error}` : ""}
              </p>
            ) : null}
          </div>
          <SmallMetric label="Equity" value={formatCurrency(balance.equityUsd)} />
          <SmallMetric label="Available" value={formatCurrency(balance.availableUsd)} />
          <StatusPill
            label={balance.stale ? "stale" : balance.tradable ? "tradable" : "not tradable"}
            tone={balance.stale ? "warning" : balance.tradable ? "positive" : "neutral"}
          />
        </div>
      ))}
    </div>
  );
}

function MetricTile({
  action,
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  action?: ReactNode;
  detail: string;
  icon: LucideIcon;
  label: string;
  tone?: Tone;
  value: string;
}) {
  return (
    <DashboardMetric
      action={action}
      detail={detail}
      icon={Icon}
      label={label}
      tone={tone}
      value={value}
    />
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
  return <DashboardPanel icon={Icon} title={title}>{children}</DashboardPanel>;
}

function LiveRiskPanel({
  enabled,
  limits,
}: {
  enabled: boolean;
  limits: TradingAccountsResponse["riskLimits"];
}) {
  return (
    <section className="ui-panel mb-4 overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand-soft text-brand">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-ink">Live Trading</h2>
              <StatusPill
                label={enabled ? "enabled by environment" : "disabled by environment"}
                tone={enabled ? "positive" : "neutral"}
              />
            </div>
            <p className="mt-1 text-sm text-muted">
              Controlled only by LIVE_TRADING_ENABLED in .env.
            </p>
          </div>
        </div>
      </div>

      <div className="px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-ink">Effective risk limits</p>
          <p className="text-xs font-medium text-muted">Configured risk policy</p>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5">
          <SmallMetric label="Weekly loss" value={formatPercent(limits.maxWeeklyLossPct)} />
          <SmallMetric
            label="Order rate"
            value={`${formatInteger(limits.maxOrdersPerMinute)} / min`}
          />
          <SmallMetric
            label="Reconciliation age"
            value={`${formatInteger(limits.reconciliationMaxSnapshotAgeSeconds)} s`}
          />
          <SmallMetric
            label="Entry intent TTL"
            value={`${formatInteger(limits.entryIntentTtlSeconds)} s`}
          />
        </div>
        <p className="mt-3 text-xs text-muted">
          Source leverage is copied without a local cap. The weekly loss limit uses account equity
          at the start of the current UTC week. {" "}
          Reduce-only exits
          {limits.reduceOnlyWhenStopped
            ? " remain available for exit-only accounts while live trading is enabled."
            : " require an enabled account."}
        </p>
      </div>
    </section>
  );
}

function LiveAccountHealthNotice({ notice }: { notice: LiveAccountNotice }) {
  const classes =
    notice.tone === "danger"
      ? "border-danger/25 bg-danger-soft text-danger"
      : notice.tone === "warning"
        ? "border-warning/25 bg-warning-soft text-warning"
        : "border-line bg-subtle text-secondary";

  return (
    <section className={`mb-4 flex items-start gap-3 rounded-md border px-3 py-2.5 ${classes}`}>
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-sm font-semibold">{notice.title}</p>
        <p className="mt-0.5 break-words text-xs leading-5 opacity-90">{notice.detail}</p>
      </div>
    </section>
  );
}

function BalanceBreakdown({ rows }: { rows: MetricLineView[] }) {
  return (
    <div className="grid gap-3">
      {rows.map((row) => (
        <MetricLine
          key={row.label}
          label={row.label}
          tone={row.tone}
          value={row.value}
          valueLabel={row.valueLabel}
        />
      ))}
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
          stroke="var(--chart-grid)"
          strokeWidth="1"
        />
        <polyline
          fill="none"
          points={plottedPoints}
          stroke={lastPoint.value >= 0 ? "var(--chart-positive)" : "var(--chart-danger)"}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="3"
        />
      </svg>
      <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted">
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
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <StatusPill
          label={marketStatusLabel(marketDataStatus)}
          tone={marketDataStatus === "live" || marketDataStatus === "no_open_positions" ? "positive" : "warning"}
        />
        <p className="text-xs text-muted">{formatInteger(rows.length)} markets</p>
      </div>
      {rows.map((row) => (
        <div key={row.coin} className="grid gap-2">
          <div className="grid grid-cols-[96px_1fr_110px] items-center gap-3">
            <div className="min-w-0">
              <p className="truncate font-mono text-sm font-semibold text-ink">{row.coin}</p>
              <p className="truncate text-[11px] text-muted">
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

function PositionRows({ positions }: { positions: AccountPositionRow[] }) {
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
                <StatusPill
                  label={position.accountType}
                  tone={position.accountType === "live" ? "positive" : "neutral"}
                />
              </div>
              {position.sourceHref ? (
                <Link
                  href={position.sourceHref}
                  className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
                >
                  {position.sourceLabel}
                </Link>
              ) : (
                <p className="mt-1 min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
                  {position.sourceLabel}
                </p>
              )}
              <p className="mt-1 truncate text-[11px] text-muted">
                {position.detail}
              </p>
            </div>
            <RowMetric label="Unrealized" tone={unrealized >= 0 ? "positive" : "danger"} value={formatCurrency(unrealized)} />
            <RowMetric
              label="Notional"
              detail={formatLeverage(position.leverage, position.marginMode)}
              value={formatCurrency(position.notionalUsd)}
            />
            <RowMetric label="Entry" detail={position.entryDetail} value={formatPrice(position.entryPrice)} />
            <RowMetric label="Execution" detail={position.executionDetail} value={position.executionValue} />
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
      {trades.slice(0, 12).map((trade) => {
        const netPnl = decimal(trade.netPnlUsd);
        return (
          <div key={trade.id} className="grid gap-3 py-3 xl:grid-cols-[1fr_120px_120px_120px]">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1">
                <p className="font-mono text-sm font-semibold text-ink">{trade.coin}</p>
                {trade.badges.map((badge) => (
                  <StatusPill key={`${badge.label}:${badge.tone}`} label={badge.label} tone={badge.tone} />
                ))}
              </div>
              {trade.sourceHref ? (
                <Link
                  href={trade.sourceHref}
                  className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
                >
                  {trade.sourceLabel}
                </Link>
              ) : (
                <p className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
                  {trade.sourceLabel}
                </p>
              )}
              <p className="mt-1 text-[11px] text-muted">
                {trade.detail}
              </p>
            </div>
            <RowMetric label="Net PnL" tone={netPnl >= 0 ? "positive" : "danger"} value={formatCurrency(netPnl)} />
            <RowMetric label="Closed" value={formatShortDateTime(trade.closedAt)} />
            <RowMetric label="Exit" detail={trade.exitDetail} value={formatPrice(trade.exitPrice)} />
          </div>
        );
      })}
    </div>
  );
}

function FillRows({ fills }: { fills: AccountExecutionRow[] }) {
  if (fills.length === 0) {
    return <EmptyState text="No recent execution activity for this account." />;
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
                {fill.badges.map((badge) => (
                  <StatusPill key={`${badge.label}:${badge.tone}`} label={badge.label} tone={badge.tone} />
                ))}
              </div>
              {fill.sourceHref ? (
                <Link
                  href={fill.sourceHref}
                  className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink hover:text-brand"
                >
                  {fill.sourceLabel}
                </Link>
              ) : (
                <p className="mt-1 block min-w-0 max-w-full whitespace-normal break-words text-xs font-semibold text-ink">
                  {fill.sourceLabel}
                </p>
              )}
              <p className="mt-1 truncate text-[11px] text-muted">
                {fill.detail}
              </p>
            </div>
            <RowMetric label="Realized" tone={realizedPnl >= 0 ? "positive" : "danger"} value={formatCurrency(realizedPnl)} />
            <RowMetric label="Notional" detail={fill.notionalDetail} value={formatCurrency(fill.notionalUsd)} />
            <RowMetric label="Price" detail={fill.priceDetail} value={formatPrice(fill.price)} />
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
          className="min-w-0 max-w-full whitespace-normal break-words text-sm font-semibold text-ink hover:text-brand"
        >
          {sourceDisplayName(row.sourceLabel, row.sourceWallet)}
        </Link>
        <StatusPill label={formatSourceStatus(row.sourceStatus)} tone={sourceStatusTone(row.sourceStatus)} />
      </div>
      <p className="mt-1 truncate font-mono text-xs text-muted">
        {shortAddress(row.sourceWallet)}
      </p>
      <p className="mt-1 truncate text-[11px] text-muted">
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
      <p className="truncate text-[11px] font-medium uppercase text-muted">{label}</p>
      <p className={`mt-0.5 truncate font-mono text-xs font-semibold ${valueClass}`}>{value}</p>
      {detail ? <p className="mt-0.5 truncate text-[11px] text-muted">{detail}</p> : null}
    </div>
  );
}

function SmallMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="ui-data-cell">
      <p className="truncate text-[11px] font-medium uppercase text-muted">{label}</p>
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
      <p className="truncate text-xs font-medium text-secondary">{label}</p>
      <Bar value={value} tone={tone} />
      <p className="truncate text-right font-mono text-xs font-semibold text-ink">{valueLabel}</p>
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
    <div className="h-2 overflow-hidden rounded-full bg-line">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${clamp(value, 0, 1) * 100}%` }} />
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="py-6 text-center text-sm text-muted">{text}</div>;
}

function readCreateAccountDraft(): CreateAccountDraft {
  if (typeof window === "undefined") {
    return DEFAULT_CREATE_ACCOUNT_DRAFT;
  }
  const rawValue = window.sessionStorage.getItem(CREATE_ACCOUNT_DRAFT_STORAGE_KEY);
  if (!rawValue) {
    return DEFAULT_CREATE_ACCOUNT_DRAFT;
  }
  try {
    const parsed = JSON.parse(rawValue) as Partial<CreateAccountDraft>;
    return {
      accountType: parsed.accountType === "live" ? "live" : "paper",
      liveLabel:
        typeof parsed.liveLabel === "string"
          ? parsed.liveLabel
          : DEFAULT_CREATE_ACCOUNT_DRAFT.liveLabel,
      liveVaultAddress:
        typeof parsed.liveVaultAddress === "string" ? parsed.liveVaultAddress : "",
      liveWalletAddress:
        typeof parsed.liveWalletAddress === "string" ? parsed.liveWalletAddress : "",
      open: parsed.open === true,
      startingBalance:
        typeof parsed.startingBalance === "string"
          ? parsed.startingBalance
          : DEFAULT_CREATE_ACCOUNT_DRAFT.startingBalance,
    };
  } catch {
    return DEFAULT_CREATE_ACCOUNT_DRAFT;
  }
}

function writeCreateAccountDraft(draft: CreateAccountDraft) {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.setItem(CREATE_ACCOUNT_DRAFT_STORAGE_KEY, JSON.stringify(draft));
}

function clearCreateAccountDraft() {
  if (typeof window === "undefined") {
    return;
  }
  window.sessionStorage.removeItem(CREATE_ACCOUNT_DRAFT_STORAGE_KEY);
}

function initialAccountKey(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
) {
  return buildAccountOptions(summary, tradingAccounts)[0]?.key ?? "";
}

function buildAccountOptions(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
): AccountOption[] {
  const mirroredPaperKeys = new Set(
    tradingAccounts.accounts
      .filter((account) => account.accountType === "paper")
      .map((account) => account.key),
  );
  const paperOptions = summary.accounts.map<AccountOption>((account) => ({
    accountType: "paper",
    key: account.key,
    label: account.label,
    paper: account,
  }));
  const liveOptions = tradingAccounts.accounts
    .filter((account) => account.accountType === "live" && !mirroredPaperKeys.has(account.key))
    .map<AccountOption>((account) => ({
      accountType: "live",
      key: account.key,
      label: account.label,
      live: account,
    }));
  return [...paperOptions, ...liveOptions];
}

function accountOptionLabel(account: AccountOption) {
  return `${account.label} (${account.accountType})`;
}

function accountTradingEnabled(account: AccountOption) {
  if (account.accountType === "paper") {
    return account.paper.enabled;
  }
  return account.live.status === "enabled";
}

function accountTradingStatusLabel(account: AccountOption) {
  if (account.accountType === "paper") {
    return account.paper.enabled ? "trading enabled" : "trading stopped";
  }
  if (account.live.status === "enabled" && account.live.reconciliationStatus !== "complete") {
    return "entries paused";
  }
  return `trading ${formatLiveAccountStatus(account.live.status)}`;
}

function accountTradingStatusTone(account: AccountOption): Tone {
  if (
    account.accountType === "live" &&
    account.live.status === "enabled" &&
    account.live.reconciliationStatus !== "complete"
  ) {
    return "warning";
  }
  if (accountTradingEnabled(account)) {
    return "positive";
  }
  return account.accountType === "live" && account.live.status === "disabled" ? "neutral" : "warning";
}

function buildLiveAccountNotice(
  account: TradingAccount,
  maxSnapshotAgeSeconds: number,
  observedAt: string,
): LiveAccountNotice | null {
  const reconciliationStale = liveReconciliationIsStale(
    account,
    maxSnapshotAgeSeconds,
    observedAt,
  );
  const reconciliationIssue = liveReconciliationIssue(account, reconciliationStale);
  if (
    account.status === "enabled" &&
    (account.reconciliationStatus !== "complete" || reconciliationStale)
  ) {
    return {
      detail:
        reconciliationIssue ??
        "A complete exchange snapshot is required before new entries can resume.",
      title:
        reconciliationStale
          ? "Entries paused: reconciliation snapshot is stale"
          : account.reconciliationStatus === "failed"
          ? "Entries paused: reconciliation failed"
          : "Entries paused: reconciliation incomplete",
      tone: account.reconciliationStatus === "failed" ? "danger" : "warning",
    };
  }
  if (account.status === "exit_only") {
    const reason = account.statusReason ?? "no lifecycle reason was recorded";
    return {
      detail: `Lifecycle reason: ${reason}.${reconciliationIssue ? ` ${reconciliationIssue}` : ""}`,
      title: "New entries stopped, reduce-only exits remain available",
      tone: "warning",
    };
  }
  if (account.status === "disabled" && account.statusReason) {
    return {
      detail: `Lifecycle reason: ${account.statusReason}.`,
      title: "Live account disabled",
      tone: "neutral",
    };
  }
  return null;
}

function liveReconciliationIssue(
  account: TradingAccount,
  reconciliationStale: boolean,
): string | null {
  if (reconciliationStale) {
    return `The last complete snapshot is older than the configured limit. Last complete: ${formatDate(account.lastReconciledAt)}.`;
  }
  if (account.reconciliationStatus === "complete") {
    return null;
  }
  const errors = Object.entries(account.reconciliationErrors)
    .map(([component, message]) => `${component}: ${message}`)
    .join(" ");
  if (errors) {
    return `Reconciliation ${account.reconciliationStatus}. ${errors}`;
  }
  if (account.incompleteReconciliationComponents.length > 0) {
    return `Reconciliation ${account.reconciliationStatus}. Incomplete: ${account.incompleteReconciliationComponents.join(", ")}.`;
  }
  return `Reconciliation ${account.reconciliationStatus}.`;
}

function liveReconciliationIsStale(
  account: TradingAccount,
  maxSnapshotAgeSeconds: number,
  observedAt: string,
): boolean {
  if (account.reconciliationStatus !== "complete" || !account.lastReconciledAt) {
    return false;
  }
  const ageMs = dateMs(observedAt) - dateMs(account.lastReconciledAt);
  return ageMs > maxSnapshotAgeSeconds * 1000;
}

function formatLiveAccountStatus(status: TradingAccount["status"]) {
  if (status === "exit_only") {
    return "exit only";
  }
  return status;
}

function formatCapitalMode(mode: TradingAccount["capitalMode"]) {
  if (mode === "standard_per_dex") {
    return "standard per DEX";
  }
  if (mode === "unified") {
    return "unified";
  }
  return "unknown";
}

function lastUpdatedAt(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
) {
  return dateMs(tradingAccounts.updatedAt) > dateMs(summary.updatedAt)
    ? tradingAccounts.updatedAt
    : summary.updatedAt;
}

function buildSelectedAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  selectedAccount: AccountOption | null,
): AccountView | null {
  if (!selectedAccount) {
    return null;
  }
  return selectedAccount.accountType === "paper"
    ? buildPaperAccountView(summary, selectedAccount.paper)
    : buildLiveAccountView(summary, tradingAccounts, selectedAccount.live);
}

function buildPaperAccountView(
  summary: PaperTradingSummaryResponse,
  account: PaperTradingAccount,
): AccountView {
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
  const netEquityUsd = accountNetEquity(account);
  const startingBalance = decimal(account.startingBalanceUsd);
  const cashBalance = decimal(account.cashBalanceUsd);
  const openMargin = decimal(account.openMarginUsd);
  const unrealizedPnl = decimal(account.unrealizedPnlUsd);
  const totalPnl = decimal(account.totalPnlUsd);
  const realizedPnl = decimal(account.realizedPnlUsd);
  const balanceScale = Math.max(startingBalance, cashBalance, netEquityUsd, openMargin, 1);

  return {
    accountType: "paper",
    allocations,
    balanceLines: [
      {
        label: "Starting balance",
        tone: "neutral",
        value: startingBalance / balanceScale,
        valueLabel: formatCurrency(startingBalance),
      },
      {
        label: "Cash balance",
        tone: "neutral",
        value: cashBalance / balanceScale,
        valueLabel: formatCurrency(cashBalance),
      },
      {
        label: "Net equity",
        tone: netEquityUsd >= startingBalance ? "positive" : "danger",
        value: netEquityUsd / balanceScale,
        valueLabel: formatCurrency(netEquityUsd),
      },
      {
        label: "Open margin",
        tone: "warning",
        value: openMargin / balanceScale,
        valueLabel: formatCurrency(openMargin),
      },
      {
        label: "Unrealized PnL",
        tone: unrealizedPnl >= 0 ? "positive" : "danger",
        value: Math.abs(unrealizedPnl) / balanceScale,
        valueLabel: formatCurrency(unrealizedPnl),
      },
    ],
    capitalBalances: [],
    closedTrades: closedTrades.map(paperClosedTradeRow),
    detailSections: [
      {
        icon: WalletCards,
        title: "Account Details",
        rows: [
          { label: "Status", value: account.enabled ? "enabled" : "disabled" },
          { label: "Created", value: formatDate(account.createdAt) },
          { label: "Updated", value: formatDate(account.updatedAt) },
          { label: "Open positions", value: formatInteger(account.openPositionCount) },
        ],
      },
    ],
    marketRows: buildMarketRows(positions),
    metrics,
    metricTiles: [
      {
        detail: `${formatCurrency(account.cashBalanceUsd)} cash`,
        icon: WalletCards,
        label: "Net equity",
        value: formatCurrency(metrics.netEquityUsd),
      },
      {
        detail: formatPercent(metrics.returnPct),
        icon: totalPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Total PnL",
        tone: totalPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(totalPnl),
      },
      {
        detail: `${formatCurrency(account.feeUsd)} fees`,
        icon: realizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Realized",
        tone: realizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(realizedPnl),
      },
      {
        detail: `${formatInteger(account.openPositionCount)} open positions`,
        icon: unrealizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Unrealized",
        tone: unrealizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(unrealizedPnl),
      },
      {
        detail: `${formatPercent(metrics.exposureRatio)} of net equity`,
        icon: Target,
        label: "Open notional",
        value: formatCurrency(account.openNotionalUsd),
      },
      {
        detail: `${formatCurrency(metrics.remainingAllocationUsd)} available`,
        icon: Layers,
        label: "Allocation used",
        value: formatPercent(metrics.allocationUsedPct),
      },
    ],
    positions: positions.map(paperPositionRow),
    recentActivity: recentFills.map(paperExecutionRow),
    sourceRows,
    timeline: buildTimeline(closedTrades.map(paperClosedTradeRow)),
  };
}

function buildLiveAccountView(
  summary: PaperTradingSummaryResponse,
  tradingAccounts: TradingAccountsResponse,
  account: TradingAccount,
): AccountView {
  const sourceMetadata = buildSourceMetadataMap(summary, tradingAccounts);
  const sourceLabels = buildSourceLabelMap(summary, tradingAccounts);
  const allPositions = tradingAccounts.positions.filter((item) => item.accountKey === account.key);
  const displayPositions = displayAccountLivePositions(allPositions);
  const sourcePositions = allPositions.filter((position) => !isLiveExchangeSource(position.sourceWallet));
  const sourcePerformancePositions = sourcePositions.length > 0 ? sourcePositions : displayPositions;
  const closedTrades = tradingAccounts.closedTrades.filter((item) => item.accountKey === account.key);
  const recentFills = tradingAccounts.recentFills.filter((item) => item.accountKey === account.key);
  const recentOrders = tradingAccounts.recentOrders.filter((item) => item.accountKey === account.key);
  const sourceRows = buildLiveSourceRows({
    closedTrades,
    positions: sourcePerformancePositions,
    recentFills,
    recentOrders,
    sourceMetadata,
    sourceLabels,
    allocationCapitalUsd: liveAccountEquity(account),
  });
  const metrics = buildLiveAccountMetrics({
    account,
    closedTrades,
    positions: displayPositions,
    recentFills,
    recentOrders,
  });
  const equity = liveAccountEquity(account);
  const cash = decimal(account.cashBalanceUsd);
  const tradable = decimal(account.tradableEquityUsd);
  const perpEquity = decimal(account.perpEquityUsd);
  const openMargin = sumNumbers(displayPositions.map((position) => position.marginUsd));
  const balanceScale = Math.max(equity, cash, tradable, perpEquity, openMargin, 1);
  const realizedPnl = decimal(account.realizedPnlUsd);
  const feeUsd = decimal(account.feeUsd);
  const capitalMode = formatCapitalMode(account.capitalMode);
  const reconciliationLabel =
    account.reconciliationStatus === "complete"
      ? "synced"
      : account.reconciliationStatus === "never"
        ? "pending"
        : account.reconciliationStatus;
  const reconciliationDetail =
    account.incompleteReconciliationComponents.length > 0
      ? `${account.incompleteReconciliationComponents.join(", ")} incomplete`
      : formatDate(account.reconciliationAttemptedAt ?? account.lastReconciledAt);
  const reconciliationTone: Tone =
    account.reconciliationStatus === "complete"
      ? "positive"
      : account.reconciliationStatus === "failed"
        ? "danger"
        : "warning";

  return {
    accountType: "live",
    allocations: [],
    balanceLines: [
      {
        label: "Equity",
        tone: "neutral",
        value: equity / balanceScale,
        valueLabel: formatCurrency(equity),
      },
      {
        label: "Tradable",
        tone: "positive",
        value: tradable / balanceScale,
        valueLabel: formatCurrency(tradable),
      },
      {
        label: "Cash balance",
        tone: "neutral",
        value: cash / balanceScale,
        valueLabel: formatCurrency(cash),
      },
      {
        label: "Perp equity",
        tone: "neutral",
        value: perpEquity / balanceScale,
        valueLabel: formatCurrency(perpEquity),
      },
      {
        label: "Open margin",
        tone: "warning",
        value: openMargin / balanceScale,
        valueLabel: formatCurrency(openMargin),
      },
    ],
    capitalBalances: account.capitalBalances,
    closedTrades: closedTrades.map((trade) => liveClosedTradeRow(trade, sourceLabels)),
    detailSections: [
      {
        icon: WalletCards,
        title: "Account Details",
        rows: [
          { label: "Status", value: formatLiveAccountStatus(account.status) },
          { label: "Status reason", value: account.statusReason ?? "none" },
          { label: "Status changed", value: formatDate(account.statusChangedAt) },
          { label: "Network", value: account.network },
          { label: "Capital mode", value: capitalMode },
          { label: "Abstraction", value: account.userAbstraction ?? "unknown" },
          { label: "Created", value: formatDate(account.createdAt) },
          { label: "Updated", value: formatDate(account.updatedAt) },
        ],
      },
      {
        icon: Activity,
        title: "Exchange Routing",
        rows: [
          { label: "Wallet address", value: account.walletAddress ?? "config wallet" },
          { label: "Vault address", value: account.vaultAddress ?? "none" },
          { label: "Internal key", value: account.key },
          { label: "Reconciliation", value: account.reconciliationStatus },
          { label: "Last complete", value: formatDate(account.lastReconciledAt) },
          { label: "Last attempt", value: formatDate(account.reconciliationAttemptedAt) },
          {
            label: "Incomplete",
            value:
              account.incompleteReconciliationComponents.length > 0
                ? account.incompleteReconciliationComponents.join(", ")
                : "none",
          },
        ],
      },
    ],
    marketRows: buildMarketRows(displayPositions),
    metrics,
    metricTiles: [
      {
        detail: `${formatLiveAccountStatus(account.status)} on ${account.network}, ${capitalMode}`,
        icon: WalletCards,
        label: "Equity",
        value: formatCurrency(equity),
      },
      {
        detail: "Sizing capital",
        icon: Target,
        label: "Tradable",
        value: formatCurrency(account.tradableEquityUsd),
      },
      {
        detail: "Available balance",
        icon: Layers,
        label: "Cash",
        value: formatCurrency(account.cashBalanceUsd),
      },
      {
        detail: "Exchange reconciled",
        icon: realizedPnl >= 0 ? TrendingUp : TrendingDown,
        label: "Realized",
        tone: realizedPnl >= 0 ? "positive" : "danger",
        value: formatCurrency(realizedPnl),
      },
      {
        detail: "Recorded fees",
        icon: Activity,
        label: "Fees",
        tone: feeUsd > 0 ? "warning" : "neutral",
        value: formatCurrency(feeUsd),
      },
      {
        detail: reconciliationDetail,
        icon: Clock,
        label: "Reconciled",
        tone: reconciliationTone,
        value: reconciliationLabel,
      },
    ],
    positions: displayPositions.map((position) => livePositionRow(position, sourceLabels)),
    recentActivity: buildLiveAccountExecutionRows(recentFills, recentOrders, sourceLabels),
    sourceRows,
    timeline: buildTimeline(closedTrades.map((trade) => liveClosedTradeRow(trade, sourceLabels))),
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

function buildLiveAccountMetrics({
  account,
  closedTrades,
  positions,
  recentFills,
  recentOrders,
}: {
  account: TradingAccount;
  closedTrades: TradingClosedTrade[];
  positions: TradingPosition[];
  recentFills: TradingFill[];
  recentOrders: TradingOrder[];
}): AccountMetrics {
  const netEquityUsd = liveAccountEquity(account);
  const openMarginUsd = sumNumbers(positions.map((position) => position.marginUsd));
  const openNotionalUsd = sumNumbers(
    positions.map((position) => position.currentNotionalUsd ?? position.notionalUsd),
  );
  const closedNetPnlUsd = sumNumbers(closedTrades.map((trade) => trade.netPnlUsd));
  const winningClosedTradeCount = closedTrades.filter((trade) => decimal(trade.netPnlUsd) > 0).length;
  const skippedFillCount = recentOrders.filter((order) => order.orderType === "skip").length;

  return {
    allocationUsd: netEquityUsd,
    allocationUsedPct: netEquityUsd > 0 ? openMarginUsd / netEquityUsd : null,
    averageClosedPnlUsd: closedTrades.length > 0 ? closedNetPnlUsd / closedTrades.length : 0,
    closedNetPnlUsd,
    copiedFillCount: recentFills.length,
    exposureRatio: netEquityUsd > 0 ? openNotionalUsd / netEquityUsd : null,
    netEquityUsd,
    remainingAllocationUsd: Math.max(netEquityUsd - openMarginUsd, 0),
    returnPct: netEquityUsd > 0 ? decimal(account.realizedPnlUsd) / netEquityUsd : null,
    skippedFillCount,
    winRate: closedTrades.length > 0 ? winningClosedTradeCount / closedTrades.length : null,
  };
}

function paperPositionRow(position: PaperPosition): AccountPositionRow {
  return {
    accountType: "paper",
    coin: position.coin,
    detail: `opened ${formatDate(position.openedAt)}`,
    entryDetail: `mark ${formatPrice(position.markPrice)}`,
    entryPrice: position.entryPrice,
    executionDetail: "source to open",
    executionValue: formatExecutionMs(position.entryExecutionDelayMs),
    id: position.id,
    leverage: position.leverage,
    marginMode: null,
    notionalUsd: position.currentNotionalUsd ?? position.notionalUsd,
    side: position.side,
    sourceHref: `/wallets/${position.sourceWallet}`,
    sourceLabel: sourceDisplayName(position.sourceLabel, position.sourceWallet),
    unrealizedPnlUsd: position.unrealizedPnlUsd,
  };
}

function livePositionRow(
  position: TradingPosition,
  sourceLabels: Map<string, string>,
): AccountPositionRow {
  const isExchange = isLiveExchangeSource(position.sourceWallet);
  return {
    accountType: "live",
    coin: position.coin,
    detail: `opened ${formatDate(position.openedAt)}`,
    entryDetail: `mark ${formatPrice(position.markPrice)}`,
    entryPrice: position.entryPrice,
    executionDetail:
      position.entryExecutionDelayMs !== null ? "source to open" : "live position",
    executionValue: formatExecutionMs(position.entryExecutionDelayMs),
    id: position.id,
    leverage: position.leverage,
    marginMode: position.marginMode,
    notionalUsd: position.currentNotionalUsd ?? position.notionalUsd,
    side: position.side,
    sourceHref: isExchange ? null : `/wallets/${position.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange position"
      : sourceDisplayName(sourceLabels.get(position.sourceWallet.toLowerCase()), position.sourceWallet),
    unrealizedPnlUsd: position.unrealizedPnlUsd,
  };
}

function paperClosedTradeRow(trade: PaperClosedTrade): AccountClosedTradeRow {
  return {
    badges: [
      ...(trade.side ? [{ label: trade.side, tone: trade.side === "long" ? "positive" as Tone : "warning" as Tone }] : []),
      ...(trade.isSourceLiquidation ? [{ label: "liquidation", tone: "danger" as Tone }] : []),
    ],
    closedAt: trade.closedAt,
    coin: trade.coin,
    detail: `${formatCloseType(trade.closeType)}, ${formatDuration(trade.durationMs)}`,
    exitDetail: `size ${formatSize(trade.size)}`,
    exitPrice: trade.exitPrice,
    id: trade.id,
    netPnlUsd: trade.netPnlUsd,
    sourceHref: `/wallets/${trade.sourceWallet}`,
    sourceLabel: sourceDisplayName(trade.sourceLabel, trade.sourceWallet),
  };
}

function liveClosedTradeRow(
  trade: TradingClosedTrade,
  sourceLabels: Map<string, string>,
): AccountClosedTradeRow {
  const isExchange = isLiveExchangeSource(trade.sourceWallet);
  return {
    badges: [
      { label: "live", tone: "positive" },
      { label: trade.side, tone: trade.side === "long" ? "positive" : "warning" },
    ],
    closedAt: trade.closedAt,
    coin: trade.coin,
    detail: `closed trade, ${formatDuration(trade.durationMs)}`,
    exitDetail: `size ${formatSize(trade.size)}`,
    exitPrice: trade.exitPrice,
    id: trade.id,
    netPnlUsd: trade.netPnlUsd,
    sourceHref: isExchange ? null : `/wallets/${trade.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange position"
      : sourceDisplayName(sourceLabels.get(trade.sourceWallet.toLowerCase()), trade.sourceWallet),
  };
}

function paperExecutionRow(fill: PaperCopyFill): AccountExecutionRow {
  return {
    badges: [
      { label: "paper", tone: "neutral" },
      { label: fill.action, tone: fill.action === "skip" ? "warning" : "neutral" },
      ...(fill.side ? [{ label: fill.side, tone: fill.side === "long" ? "positive" as Tone : "warning" as Tone }] : []),
      ...(fill.minOrderAdjusted ? [{ label: "min order adjusted", tone: "warning" as Tone }] : []),
    ],
    coin: fill.coin,
    detail: fill.skippedReason ? humanReason(fill.skippedReason) : formatShortDateTime(fill.filledAt),
    id: `paper:${fill.id}`,
    notionalDetail: paperFillNotionalDetail(fill),
    notionalUsd: fill.notionalUsd,
    price: fill.price,
    priceDetail: paperFillPriceDetail(fill),
    realizedPnlUsd: fill.realizedPnlUsd,
    sourceHref: `/wallets/${fill.sourceWallet}`,
    sourceLabel: sourceDisplayName(fill.sourceLabel, fill.sourceWallet),
  };
}

function buildLiveAccountExecutionRows(
  liveFills: TradingFill[],
  liveOrders: TradingOrder[],
  sourceLabels: Map<string, string>,
): AccountExecutionRow[] {
  const fillOrderIds = new Set(
    liveFills.map((fill) => fill.orderId).filter((value): value is string => Boolean(value)),
  );
  const fillRows = liveFills.map((fill) => liveFillExecutionRow(fill, sourceLabels));
  const orderRows = liveOrders
    .filter((order) => !fillOrderIds.has(order.id))
    .map((order) => liveOrderExecutionRow(order, sourceLabels));
  return [...fillRows, ...orderRows]
    .sort((left, right) => dateMs(right.detailDate ?? "") - dateMs(left.detailDate ?? ""))
    .map(accountExecutionRow)
    .slice(0, 100);
}

type DatedAccountExecutionRow = AccountExecutionRow & { detailDate?: string };

function accountExecutionRow(row: DatedAccountExecutionRow): AccountExecutionRow {
  return {
    badges: row.badges,
    coin: row.coin,
    detail: row.detail,
    id: row.id,
    notionalDetail: row.notionalDetail,
    notionalUsd: row.notionalUsd,
    price: row.price,
    priceDetail: row.priceDetail,
    realizedPnlUsd: row.realizedPnlUsd,
    sourceHref: row.sourceHref,
    sourceLabel: row.sourceLabel,
  };
}

function liveFillExecutionRow(
  fill: TradingFill,
  sourceLabels: Map<string, string>,
): DatedAccountExecutionRow {
  const isExchange = isLiveExchangeSource(fill.sourceWallet);
  return {
    badges: [
      { label: "live", tone: "positive" },
      { label: fill.action, tone: fill.action.includes("close") ? "neutral" : "positive" },
      { label: fill.side, tone: fill.side === "long" ? "positive" : "warning" },
    ],
    coin: fill.coin,
    detail: formatShortDateTime(fill.filledAt),
    detailDate: fill.filledAt,
    id: `live:${fill.id}`,
    notionalDetail: `size ${formatSize(fill.size)}`,
    notionalUsd: fill.notionalUsd,
    price: fill.price,
    priceDetail: `fee ${formatCurrency(fill.feeUsd)}`,
    realizedPnlUsd: fill.realizedPnlUsd,
    sourceHref: isExchange ? null : `/wallets/${fill.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange fill"
      : sourceDisplayName(sourceLabels.get(fill.sourceWallet.toLowerCase()), fill.sourceWallet),
  };
}

function liveOrderExecutionRow(
  order: TradingOrder,
  sourceLabels: Map<string, string>,
): DatedAccountExecutionRow {
  const isExchange = isLiveExchangeSource(order.sourceWallet);
  const error = order.error?.trim();
  const sortAt = order.orderType === "skip" ? order.createdAt : order.filledAt ?? order.updatedAt ?? order.createdAt;
  return {
    badges: [
      { label: order.orderType === "skip" ? "live skip" : "live order", tone: order.orderType === "skip" ? "warning" : "neutral" },
      { label: order.action, tone: order.action.includes("close") ? "neutral" : "positive" },
      { label: order.status, tone: liveOrderStatusTone(order.status) },
    ],
    coin: order.coin,
    detail: error ? humanReason(error.replace(/^skip:/, "")) : formatShortDateTime(sortAt),
    detailDate: sortAt,
    id: `live-order:${order.id}`,
    notionalDetail: `filled ${formatCurrency(order.filledNotionalUsd)}`,
    notionalUsd: order.requestedNotionalUsd,
    price: order.limitPrice,
    priceDetail: order.averageFillPrice
      ? `avg ${formatPrice(order.averageFillPrice)}`
      : formatLeverage(order.leverage, order.marginMode),
    realizedPnlUsd: "0",
    sourceHref: isExchange ? null : `/wallets/${order.sourceWallet}`,
    sourceLabel: isExchange
      ? "Exchange order"
      : sourceDisplayName(sourceLabels.get(order.sourceWallet.toLowerCase()), order.sourceWallet),
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

function buildLiveSourceRows({
  allocationCapitalUsd,
  closedTrades,
  positions,
  recentFills,
  recentOrders,
  sourceMetadata,
  sourceLabels,
}: {
  allocationCapitalUsd: number;
  closedTrades: TradingClosedTrade[];
  positions: TradingPosition[];
  recentFills: TradingFill[];
  recentOrders: TradingOrder[];
  sourceMetadata: Map<string, SourceMetadata>;
  sourceLabels: Map<string, string>;
}) {
  const rows = new Map<string, SourceRow>();
  const ensureRow = (sourceWallet: string): SourceRow => {
    const source = sourceWallet.toLowerCase();
    const existing = rows.get(source);
    if (existing) {
      return existing;
    }
    const metadata = sourceMetadata.get(source);
    const allocationUsd =
      allocationCapitalUsd > 0 && metadata?.allocationPct
        ? allocationCapitalUsd * metadata.allocationPct
        : 0;
    const row: SourceRow = {
      allocationUsd,
      closedNetPnlUsd: 0,
      closedTradeCount: 0,
      copiedFillCount: 0,
      lastActivityAt: null,
      openMarginUsd: 0,
      openNotionalUsd: 0,
      openPositionCount: 0,
      poolRank: metadata?.poolRank ?? null,
      remainingAllocationUsd: allocationUsd,
      score: metadata?.score ?? null,
      skippedFillCount: 0,
      sourceLabel: isLiveExchangeSource(sourceWallet)
        ? "Exchange"
        : metadata?.label ?? sourceLabels.get(source) ?? null,
      sourceStatus: "history",
      sourceWallet: sourceWallet,
      totalPnlUsd: 0,
      unrealizedPnlUsd: 0,
      winRate: null,
    };
    rows.set(source, row);
    return row;
  };

  for (const position of positions) {
    const row = ensureRow(position.sourceWallet);
    row.openPositionCount += 1;
    row.openMarginUsd += decimal(position.marginUsd);
    row.openNotionalUsd += decimal(position.currentNotionalUsd ?? position.notionalUsd);
    row.unrealizedPnlUsd += decimal(position.unrealizedPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, position.updatedAt);
    row.sourceStatus = "trading";
  }

  const winsBySource = new Map<string, number>();
  for (const trade of closedTrades) {
    const row = ensureRow(trade.sourceWallet);
    row.closedTradeCount += 1;
    row.closedNetPnlUsd += decimal(trade.netPnlUsd);
    row.lastActivityAt = latestDate(row.lastActivityAt, trade.closedAt);
    if (decimal(trade.netPnlUsd) > 0) {
      const source = trade.sourceWallet.toLowerCase();
      winsBySource.set(source, (winsBySource.get(source) ?? 0) + 1);
    }
  }

  for (const fill of recentFills) {
    const row = ensureRow(fill.sourceWallet);
    row.copiedFillCount += 1;
    row.lastActivityAt = latestDate(row.lastActivityAt, fill.filledAt);
  }

  for (const order of recentOrders) {
    const row = ensureRow(order.sourceWallet);
    if (order.orderType === "skip") {
      row.skippedFillCount += 1;
    }
    row.lastActivityAt = latestDate(row.lastActivityAt, order.createdAt);
  }

  return Array.from(rows.values())
    .map((row) => ({
      ...row,
      remainingAllocationUsd: Math.max(row.allocationUsd - row.openMarginUsd, 0),
      totalPnlUsd: row.closedNetPnlUsd + row.unrealizedPnlUsd,
      winRate: row.closedTradeCount > 0 ? (winsBySource.get(row.sourceWallet.toLowerCase()) ?? 0) / row.closedTradeCount : null,
    }))
    .sort((left, right) => {
      if (left.openPositionCount !== right.openPositionCount) {
        return right.openPositionCount - left.openPositionCount;
      }
      return right.totalPnlUsd - left.totalPnlUsd;
    });
}

function buildMarketRows(positions: Array<PaperPosition | TradingPosition>): MarketRow[] {
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

function buildTimeline(closedTrades: AccountClosedTradeRow[]): TimelinePoint[] {
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

function buildSourceLabelMap(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const labels = new Map<string, string>();
  for (const [source, metadata] of buildSourceMetadataMap(summary, tradingAccounts)) {
    if (metadata.label) {
      labels.set(source, metadata.label);
    }
  }
  return labels;
}

function buildSourceMetadataMap(
  summary: PaperTradingSummaryResponse,
  tradingAccounts?: TradingAccountsResponse,
) {
  const metadata = new Map<string, SourceMetadata>();
  const ensureMetadata = (wallet: string): SourceMetadata => {
    const source = wallet.toLowerCase();
    const existing = metadata.get(source);
    if (existing) {
      return existing;
    }
    const item: SourceMetadata = {
      allocationPct: null,
      label: null,
      poolRank: null,
      rank: null,
      score: null,
    };
    metadata.set(source, item);
    return item;
  };
  const addLabel = (wallet: string, label: string | null | undefined) => {
    const trimmed = label?.trim();
    if (trimmed) {
      ensureMetadata(wallet).label = trimmed;
    }
  };
  for (const allocation of summary.allocations) {
    const item = ensureMetadata(allocation.sourceWallet);
    addLabel(allocation.sourceWallet, allocation.sourceLabel);
    item.allocationPct ??= firstNumber([allocation.allocationPct]);
    item.poolRank = minNullable(item.poolRank, allocation.poolRank);
    item.rank = minNullable(item.rank, allocation.rank);
    item.score ??= allocation.score;
  }
  for (const wallet of summary.walletPerformance) {
    const item = ensureMetadata(wallet.sourceWallet);
    addLabel(wallet.sourceWallet, wallet.sourceLabel);
    item.allocationPct ??= firstNumber([wallet.allocationPct]);
    item.poolRank = minNullable(item.poolRank, wallet.poolRank);
    item.rank = minNullable(item.rank, wallet.rank);
    item.score ??= wallet.score;
  }
  for (const position of summary.positions) {
    addLabel(position.sourceWallet, position.sourceLabel);
  }
  for (const fill of summary.recentFills) {
    addLabel(fill.sourceWallet, fill.sourceLabel);
  }
  for (const trade of summary.closedTrades) {
    addLabel(trade.sourceWallet, trade.sourceLabel);
  }
  for (const source of tradingAccounts?.sourceMetadata ?? []) {
    const item = ensureMetadata(source.sourceWallet);
    addLabel(source.sourceWallet, source.sourceLabel);
    item.allocationPct ??= firstNumber([source.allocationPct]);
    item.poolRank = minNullable(item.poolRank, source.poolRank);
    item.rank = minNullable(item.rank, source.rank);
    item.score ??= source.score;
  }
  return metadata;
}

function displayAccountLivePositions(positions: TradingPosition[]) {
  const exchangeCoins = new Set(
    positions
      .filter((position) => isLiveExchangeSource(position.sourceWallet))
      .map((position) => position.coin),
  );
  return positions.filter(
    (position) =>
      isLiveExchangeSource(position.sourceWallet) ||
      !exchangeCoins.has(position.coin),
  );
}

function accountNetEquity(account: PaperTradingAccount) {
  return decimal(account.equityUsd) + decimal(account.unrealizedPnlUsd);
}

function liveAccountEquity(account: TradingAccount) {
  return decimal(account.equityUsd ?? account.perpEquityUsd ?? account.tradableEquityUsd);
}

function isLiveExchangeSource(sourceWallet: string) {
  return sourceWallet === "__exchange__";
}

function liveOrderStatusTone(status: string): Tone {
  if (status === "filled" || status === "accepted") {
    return "positive";
  }
  if (status === "rejected" || status === "failed" || status === "canceled") {
    return "danger";
  }
  if (
    status === "ready" ||
    status === "submitting" ||
    status === "uncertain" ||
    status === "submitted" ||
    status === "partially_filled"
  ) {
    return "warning";
  }
  return "neutral";
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

function firstNumber(values: Array<string | number | null | undefined>): number | null {
  for (const value of values) {
    if (value !== null && value !== undefined) {
      return decimal(value);
    }
  }
  return null;
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

function formatLeverage(
  value: string | number | null | undefined,
  marginMode?: "cross" | "isolated" | null,
) {
  if (value === null || value === undefined) {
    return "-";
  }
  const leverage = `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )}x`;
  return marginMode ? `${leverage} ${marginMode}` : leverage;
}

function formatBps(value: string | number | null | undefined) {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${new Intl.NumberFormat("sv-SE", { maximumFractionDigits: 2 }).format(
    decimal(value),
  )} bps`;
}

function paperFillPriceDetail(fill: PaperCopyFill) {
  if (fill.skippedReason && fill.priceDriftBps) {
    const maxDrift = fill.maxPriceDriftBps ? ` | max ${formatBps(fill.maxPriceDriftBps)}` : "";
    return `adverse drift ${formatBps(fill.priceDriftBps)}${maxDrift} | live ${formatPrice(fill.observedPrice)}`;
  }
  const parts = [
    fill.sourcePrice ? `src ${formatPrice(fill.sourcePrice)}` : null,
    fill.observedPrice ? `live ${formatPrice(fill.observedPrice)}` : null,
    `fee ${formatCurrency(fill.feeUsd)}`,
  ].filter(Boolean);
  return parts.join(" | ");
}

function paperFillNotionalDetail(fill: PaperCopyFill) {
  if (!fill.minOrderAdjusted || !fill.originalNotionalUsd) {
    return undefined;
  }
  return `adjusted from ${formatCurrency(fill.originalNotionalUsd)}`;
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
  if (action === "disable") {
    return "Disable account";
  }
  if (action === "delete") {
    return "Delete account";
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
