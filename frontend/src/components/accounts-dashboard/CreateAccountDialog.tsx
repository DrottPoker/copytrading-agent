"use client";

import {
  Activity,
  Loader2,
  Plus,
  WalletCards,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId } from "react";

import { formatCurrency } from "@/lib/format";

export type CreateAccountType = "paper" | "live";

export function CreateAccountDialog({
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
