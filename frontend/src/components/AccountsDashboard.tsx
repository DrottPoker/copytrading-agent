"use client";

import {
  Loader2,
  Play,
  Plus,
  Square,
  Trash2,
  WalletCards,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AccountDashboardContent } from "@/components/accounts-dashboard/AccountDashboardContent";
import {
  CreateAccountDialog,
  type CreateAccountType,
} from "@/components/accounts-dashboard/CreateAccountDialog";
import {
  buildSelectedAccountView,
  formatLiveAccountStatus,
  lastUpdatedAt,
} from "@/components/accounts-dashboard/accountViewModel";
import type {
  AccountOption,
  LiveAccountNotice,
  Tone,
} from "@/components/accounts-dashboard/types";
import { getPublicApiBaseUrl } from "@/lib/config";
import { formatDate, formatInteger } from "@/lib/format";
import type { PaperTradingSummaryResponse } from "@/types/paper";
import type {
  TradingAccount,
  TradingAccountsResponse,
} from "@/types/trading";

import { HeaderRefreshButton, HeaderUpdatedLabel } from "./HeaderRefresh";
import { PageTopPanel } from "./PageTopPanel";
import { StatusPill } from "./StatusPill";

const ACCOUNT_REFRESH_MS = 4000;
const ACCOUNT_SUMMARY_LIMIT = 250;
const SELECTED_ACCOUNT_STORAGE_KEY = "copyagent.accounts.selectedAccountKey";
const CREATE_ACCOUNT_DRAFT_STORAGE_KEY = "copyagent.accounts.createAccountDraft";

type TradingAction = "start" | "stop" | "disable" | "close-all-and-stop" | "delete";
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

const DEFAULT_CREATE_ACCOUNT_DRAFT: CreateAccountDraft = {
  accountType: "paper",
  liveLabel: "Main wallet",
  liveVaultAddress: "",
  liveWalletAddress: "",
  open: false,
  startingBalance: "1000",
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
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(
    () => new Date(lastUpdatedAt(initialSummary, initialTradingAccounts)),
  );
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
        <AccountDashboardContent
          accountView={accountView}
          liveAccountNotice={liveAccountNotice}
          liveTradingEnabled={tradingAccounts.liveTradingEnabled}
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
          riskLimits={tradingAccounts.riskLimits}
        />
      ) : (
        <section className="ui-panel p-8 text-center text-sm text-muted">
          No accounts are synced yet.
        </section>
      )}
    </>
  );
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
  const ageMs =
    new Date(observedAt).getTime() - new Date(account.lastReconciledAt).getTime();
  return ageMs > maxSnapshotAgeSeconds * 1000;
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
