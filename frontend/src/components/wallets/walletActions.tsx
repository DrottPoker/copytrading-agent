"use client";

import { Download, Gauge } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Menu, useConfirm, type MenuItem } from "@/components/ui/client";
import { buttonClass, Spinner } from "@/components/ui/primitives";
import { frontendConfig } from "@/lib/config";
import { formatInteger, shortAddress } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import { numericPayload } from "@/lib/operations";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";
import type { Wallet, WalletFillImportResponse } from "@/types/wallet";

export type Notice = { text: string; tone: "positive" | "negative" };

const COOLDOWN_HOURS = 6;

/** Wallet mutations shared by the wallet table and the wallet detail page. */
export function useWalletActions(onNotice: (notice: Notice) => void) {
  const router = useRouter();
  const confirm = useConfirm();
  const [busyAddress, setBusyAddress] = useState<string | null>(null);

  const run = useCallback(
    async (wallet: Wallet, task: () => Promise<string | null>) => {
      setBusyAddress(wallet.address);
      try {
        const message = await task();
        if (message) {
          onNotice({ text: message, tone: "positive" });
        }
        router.refresh();
      } catch (error) {
        onNotice({ text: errorMessage(error, "The wallet update failed."), tone: "negative" });
      } finally {
        setBusyAddress(null);
      }
    },
    [onNotice, router],
  );

  const patch = useCallback(
    (wallet: Wallet, body: Record<string, unknown>, message: string) =>
      run(wallet, async () => {
        await apiRequest(`/wallets/${wallet.address}`, { body, fallback: "Wallet update failed", method: "PATCH" });
        return message;
      }),
    [run],
  );

  const importFills = useCallback(
    (wallet: Wallet) =>
      run(wallet, async () => {
        const result = await apiRequest<WalletFillImportResponse>(`/wallets/${wallet.address}/fills/import`, {
          body: {
            days: frontendConfig.manualFillImportDays,
            maxPages: frontendConfig.manualFillImportMaxPages,
            targetFills: frontendConfig.manualFillImportTargetFills,
          },
          fallback: "Fill import failed",
          method: "POST",
        });
        return `Imported ${formatInteger(result.inserted)} new fills for ${shortAddress(wallet.address)} (${formatInteger(result.duplicate)} already stored, ${formatInteger(result.pagesFetched)} pages).`;
      }),
    [run],
  );

  const remove = useCallback(
    async (wallet: Wallet) => {
      const confirmed = await confirm({
        body: "The wallet, its fills, trades, and scores are deleted. This cannot be undone. Archiving by disabling is usually enough.",
        confirmLabel: "Delete wallet",
        title: `Delete ${wallet.label ?? shortAddress(wallet.address)}?`,
        tone: "danger",
      });
      if (!confirmed) {
        return;
      }
      await run(wallet, async () => {
        await apiRequest(`/wallets/${wallet.address}`, { fallback: "Delete failed", method: "DELETE" });
        return `Deleted ${shortAddress(wallet.address)}.`;
      });
    },
    [confirm, run],
  );

  const menuItems = useCallback(
    (wallet: Wallet, options: { includeView?: boolean; onView?: () => void } = {}): MenuItem[] => {
      const inCooldown = wallet.pollingTier === "cooldown";
      return [
        ...(options.includeView && options.onView ? [{ label: "Open wallet", onSelect: options.onView }] : []),
        { label: "Import recent fills", onSelect: () => void importFills(wallet) },
        wallet.archivedAt
          ? {
              label: "Readmit to the pool",
              onSelect: () => void patch(wallet, { enabled: true }, `${shortAddress(wallet.address)} is back in the pool.`),
            }
          : {
              label: wallet.enabled ? "Disable" : "Enable",
              onSelect: () =>
                void patch(
                  wallet,
                  { enabled: !wallet.enabled },
                  `${shortAddress(wallet.address)} ${wallet.enabled ? "disabled" : "enabled"}.`,
                ),
            },
        {
          hint: inCooldown ? undefined : `Stops polling and copying for ${COOLDOWN_HOURS} hours`,
          label: inCooldown ? "End cooldown" : `Cooldown for ${COOLDOWN_HOURS} hours`,
          onSelect: () =>
            void patch(
              wallet,
              {
                cooldownUntil: inCooldown ? null : new Date(Date.now() + COOLDOWN_HOURS * 3_600_000).toISOString(),
                copyEnabled: false,
                pollingTier: inCooldown ? "pool" : "cooldown",
              },
              inCooldown ? "Cooldown ended." : `Cooldown started for ${COOLDOWN_HOURS} hours.`,
            ),
        },
        { danger: true, label: "Delete", onSelect: () => void remove(wallet) },
      ];
    },
    [importFills, patch, remove],
  );

  return { busyAddress, importFills, menuItems };
}

export function WalletMenu({
  busy,
  items,
}: {
  busy: boolean;
  items: MenuItem[];
}) {
  if (busy) {
    return (
      <span className="inline-flex h-7 w-7 items-center justify-center">
        <Spinner />
      </span>
    );
  }
  return <Menu items={items} label="Wallet actions" />;
}

/** Starts a background scoring run and follows it until it finishes. */
export function ScoreWalletsButton() {
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const [operation, setOperation] = useState<OperationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const running = starting || operation?.status === "running";

  useEffect(() => {
    if (operation?.status !== "running") {
      return;
    }
    const intervalId = window.setInterval(async () => {
      try {
        const payload = await apiRequest<OperationStatusListResponse>("/operations/status");
        const next = payload.items.find((item) => item.key === "wallet_scoring");
        if (!next) {
          return;
        }
        setOperation(next);
        if (next.status === "succeeded") {
          router.refresh();
        } else if (next.status === "failed") {
          setError(next.lastError ?? "Scoring failed.");
        }
      } catch {
        setError("Could not refresh the scoring status.");
      }
    }, 2000);
    return () => window.clearInterval(intervalId);
  }, [operation?.status, router]);

  const start = async () => {
    setStarting(true);
    setError(null);
    try {
      const started = await apiRequest<OperationStatus>("/scores/recalculate/start", {
        fallback: "Scoring failed",
        method: "POST",
      });
      setOperation(started);
      if (started.status === "succeeded") {
        router.refresh();
      }
    } catch (startError) {
      setError(errorMessage(startError, "Scoring failed."));
    } finally {
      setStarting(false);
    }
  };

  const scored = numericPayload(operation?.payload.scoredWallets);
  return (
    <span className="inline-flex items-center gap-2">
      {error ? <span className="text-xs text-negative">{error}</span> : null}
      {!error && operation?.status === "succeeded" && scored !== null ? (
        <span className="text-xs text-fg-3">{formatInteger(scored)} wallets scored</span>
      ) : null}
      <button
        type="button"
        onClick={() => void start()}
        disabled={running}
        className={buttonClass({ size: "sm" })}
        title="Recalculate every wallet's score from its stored history"
      >
        {running ? <Spinner /> : <Gauge className="h-3.5 w-3.5" aria-hidden="true" />}
        {running ? "Scoring" : "Score wallets"}
      </button>
    </span>
  );
}

export function ImportFillsButton({ wallet, onNotice }: { onNotice: (notice: Notice) => void; wallet: Wallet }) {
  const { busyAddress, importFills } = useWalletActions(onNotice);
  const busy = busyAddress === wallet.address;
  return (
    <button type="button" onClick={() => void importFills(wallet)} disabled={busy} className={buttonClass({ size: "sm" })}>
      {busy ? <Spinner /> : <Download className="h-3.5 w-3.5" aria-hidden="true" />}
      {busy ? "Importing" : "Import fills"}
    </button>
  );
}
