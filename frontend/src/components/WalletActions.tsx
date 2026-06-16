"use client";

import { Ban, CheckCircle2, Clock3, Eye, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { getPublicApiBaseUrl } from "@/lib/config";
import type { Wallet } from "@/types/wallet";

import { ImportFillsButton } from "./ImportFillsButton";

export function WalletActions({ wallet }: { wallet: Wallet }) {
  const router = useRouter();
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function patchWallet(payload: Record<string, unknown>) {
    setIsBusy(true);
    setError(null);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/wallets/${wallet.address}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        setError("Update failed.");
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach API.");
    } finally {
      setIsBusy(false);
    }
  }

  async function deleteWallet() {
    const confirmed = window.confirm(
      `Delete ${shortAddress(wallet.address)} and all related rows? This cannot be undone.`,
    );
    if (!confirmed) {
      return;
    }

    setIsBusy(true);
    setError(null);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/wallets/${wallet.address}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        setError("Delete failed.");
        return;
      }
      router.refresh();
    } catch {
      setError("Could not reach API.");
    } finally {
      setIsBusy(false);
    }
  }

  const cooldownUntil = new Date(Date.now() + 6 * 60 * 60 * 1000).toISOString();

  return (
    <div className="flex min-w-[300px] flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Link
          href={`/wallets/${wallet.address}`}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-medium text-ink"
          title="View wallet details"
        >
          <Eye className="h-3.5 w-3.5" aria-hidden="true" />
          View
        </Link>
        <ImportFillsButton address={wallet.address} compact refreshOnSuccess={false} />
        <button
          type="button"
          disabled={isBusy}
          onClick={() => patchWallet({ enabled: !wallet.enabled })}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-medium text-ink disabled:opacity-50"
          title={wallet.enabled ? "Disable wallet" : "Enable wallet"}
        >
          {wallet.enabled ? (
            <Ban className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {wallet.enabled ? "Disable" : "Enable"}
        </button>
        <button
          type="button"
          disabled={isBusy}
          onClick={() =>
            patchWallet({
              pollingTier: wallet.pollingTier === "cooldown" ? "pool" : "cooldown",
              cooldownUntil: wallet.pollingTier === "cooldown" ? null : cooldownUntil,
              copyEnabled: false,
            })
          }
          className="inline-flex h-8 items-center gap-1 rounded-md border border-line bg-white px-2 text-xs font-medium text-ink disabled:opacity-50"
          title={wallet.pollingTier === "cooldown" ? "Clear cooldown" : "Force cooldown"}
        >
          <Clock3 className="h-3.5 w-3.5" aria-hidden="true" />
          {wallet.pollingTier === "cooldown" ? "Clear" : "Cooldown"}
        </button>
        <button
          type="button"
          disabled={isBusy}
          onClick={deleteWallet}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-[#f2aaa5] bg-[#fff2f0] px-2 text-xs font-medium text-danger disabled:opacity-50"
          title="Delete wallet"
        >
          <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          Delete
        </button>
      </div>
      {error ? <p className="text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}
