"use client";

import { Plus } from "lucide-react";

import { Dialog } from "@/components/ui/client";
import { buttonClass, Spinner } from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import { formatUsd } from "@/lib/format";

export type CreateAccountDraft = {
  accountType: "paper" | "live";
  liveLabel: string;
  liveVaultAddress: string;
  liveWalletAddress: string;
  open: boolean;
  startingBalance: string;
};

export const DEFAULT_CREATE_DRAFT: CreateAccountDraft = {
  accountType: "paper",
  liveLabel: "Main wallet",
  liveVaultAddress: "",
  liveWalletAddress: "",
  open: false,
  startingBalance: "1000",
};

const DRAFT_STORAGE_KEY = "copyagent.accounts.createAccountDraft";

/** Keeps an unfinished account form across reloads in the same tab. */
export function readCreateDraft(): CreateAccountDraft {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_STORAGE_KEY);
    if (!raw) {
      return DEFAULT_CREATE_DRAFT;
    }
    const parsed = JSON.parse(raw) as Partial<CreateAccountDraft>;
    return {
      accountType: parsed.accountType === "live" ? "live" : "paper",
      liveLabel: typeof parsed.liveLabel === "string" ? parsed.liveLabel : DEFAULT_CREATE_DRAFT.liveLabel,
      liveVaultAddress: typeof parsed.liveVaultAddress === "string" ? parsed.liveVaultAddress : "",
      liveWalletAddress: typeof parsed.liveWalletAddress === "string" ? parsed.liveWalletAddress : "",
      open: parsed.open === true,
      startingBalance:
        typeof parsed.startingBalance === "string" ? parsed.startingBalance : DEFAULT_CREATE_DRAFT.startingBalance,
    };
  } catch {
    return DEFAULT_CREATE_DRAFT;
  }
}

export function writeCreateDraft(draft: CreateAccountDraft) {
  try {
    if (draft.open) {
      window.sessionStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(draft));
    } else {
      window.sessionStorage.removeItem(DRAFT_STORAGE_KEY);
    }
  } catch {
    // Session storage is optional.
  }
}

export function CreateAccountDialog({
  draft,
  error,
  isSubmitting,
  liveTradingEnabled,
  onChange,
  onClose,
  onSubmit,
}: {
  draft: CreateAccountDraft;
  error: string | null;
  isSubmitting: boolean;
  liveTradingEnabled: boolean;
  onChange: (patch: Partial<CreateAccountDraft>) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const balance = Number(draft.startingBalance);
  const canCreate =
    !isSubmitting &&
    (draft.accountType === "paper" ? Number.isFinite(balance) && balance > 0 : draft.liveLabel.trim().length > 0);

  return (
    <Dialog
      open={draft.open}
      onClose={onClose}
      title="New trading account"
      description="Accounts start stopped. Start one when you are ready to copy trades."
      footer={
        <>
          <button type="button" className={buttonClass()} onClick={onClose} disabled={isSubmitting}>
            Cancel
          </button>
          <button
            type="submit"
            form="create-account-form"
            className={buttonClass({ variant: "primary" })}
            disabled={!canCreate}
          >
            {isSubmitting ? <Spinner /> : <Plus className="h-3.5 w-3.5" aria-hidden="true" />}
            Create {draft.accountType} account
          </button>
        </>
      }
    >
      <form
        id="create-account-form"
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        <fieldset className="grid gap-2 sm:grid-cols-2">
          <legend className="sr-only">Account type</legend>
          <TypeOption
            active={draft.accountType === "paper"}
            description="Simulated money against the real order book, with fees, funding, and slippage."
            label="Paper"
            onSelect={() => onChange({ accountType: "paper" })}
          />
          <TypeOption
            active={draft.accountType === "live"}
            description={
              liveTradingEnabled
                ? "Real orders on Hyperliquid through the configured API wallet."
                : "Real orders on Hyperliquid. Live trading is turned off on the server right now."
            }
            label="Live"
            onSelect={() => onChange({ accountType: "live" })}
          />
        </fieldset>

        {draft.accountType === "paper" ? (
          <label className="grid gap-1">
            <span className="text-sm font-medium">Starting balance</span>
            <div className="relative">
              <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-fg-3">$</span>
              <input
                data-autofocus
                className="field w-full pl-6 tabular-nums"
                inputMode="decimal"
                min="0.01"
                onChange={(event) => onChange({ startingBalance: event.target.value })}
                step="0.01"
                type="number"
                value={draft.startingBalance}
              />
            </div>
            <span className="text-xs text-fg-3">
              The account copies with {formatUsd(Number.isFinite(balance) ? balance : 0)} of simulated capital.
            </span>
          </label>
        ) : (
          <div className="grid gap-3">
            <label className="grid gap-1">
              <span className="text-sm font-medium">Name</span>
              <input
                data-autofocus
                className="field"
                maxLength={120}
                onChange={(event) => onChange({ liveLabel: event.target.value })}
                placeholder="Main wallet"
                value={draft.liveLabel}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-sm font-medium">Wallet address</span>
              <input
                className="field font-mono text-xs"
                onChange={(event) => onChange({ liveWalletAddress: event.target.value })}
                placeholder="Empty uses HYPERLIQUID_WALLET_ADDRESS"
                spellCheck={false}
                value={draft.liveWalletAddress}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-sm font-medium">
                Vault address <span className="font-normal text-fg-3">(optional)</span>
              </span>
              <input
                className="field font-mono text-xs"
                onChange={(event) => onChange({ liveVaultAddress: event.target.value })}
                placeholder="0x..."
                spellCheck={false}
                value={draft.liveVaultAddress}
              />
            </label>
            <p className="text-xs text-fg-3">
              The signing key never leaves the server. The account needs a complete exchange reconciliation before it
              can trade.
            </p>
          </div>
        )}

        {error ? <p className="text-sm text-negative">{error}</p> : null}
      </form>
    </Dialog>
  );
}

function TypeOption({
  active,
  description,
  label,
  onSelect,
}: {
  active: boolean;
  description: string;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={cx(
        "rounded-md border px-3 py-2.5 text-left transition-colors",
        active ? "border-fg bg-surface-2" : "border-line hover:border-line-strong hover:bg-surface-2",
      )}
    >
      <span className="flex items-center gap-2 text-sm font-medium">
        <span
          aria-hidden="true"
          className={cx(
            "flex h-3.5 w-3.5 items-center justify-center rounded-full border",
            active ? "border-fg" : "border-line-strong",
          )}
        >
          {active ? <span className="h-1.5 w-1.5 rounded-full bg-fg" /> : null}
        </span>
        {label}
      </span>
      <span className="mt-1 block text-xs text-fg-3">{description}</span>
    </button>
  );
}
