"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { getPublicApiBaseUrl } from "@/lib/config";
import { frontendConfig } from "@/lib/config";
import type { WalletFillImportResponse } from "@/types/wallet";

export function ImportFillsButton({
  address,
  compact = false,
  refreshOnSuccess = true,
}: {
  address: string;
  compact?: boolean;
  refreshOnSuccess?: boolean;
}) {
  const router = useRouter();
  const [isImporting, setIsImporting] = useState(false);
  const [result, setResult] = useState<WalletFillImportResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function importFills() {
    setIsImporting(true);
    setError(null);
    setResult(null);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/wallets/${address}/fills/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          days: frontendConfig.manualFillImportDays,
          maxPages: frontendConfig.manualFillImportMaxPages,
          targetFills: frontendConfig.manualFillImportTargetFills,
        }),
      });

      const payload = (await response.json().catch(() => null)) as
        | WalletFillImportResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        const detail =
          payload && "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : null;
        setError(detail ?? "Import failed.");
        return;
      }

      setResult(payload as WalletFillImportResponse);
      if (refreshOnSuccess) {
        router.refresh();
      }
    } catch {
      setError("Could not reach API.");
    } finally {
      setIsImporting(false);
    }
  }

  return (
    <div className={compact ? "flex flex-col gap-1" : "flex flex-col gap-2"}>
      <button
        type="button"
        disabled={isImporting}
        onClick={importFills}
        className={
          compact
            ? "ui-button-secondary h-8 gap-1 px-2 text-xs disabled:opacity-50"
            : "ui-button-primary h-10 px-4 disabled:border-faint disabled:bg-faint"
        }
        title="Import historical fills"
      >
        <Download className={compact ? "h-3.5 w-3.5" : "h-4 w-4"} aria-hidden="true" />
        {isImporting ? "Importing" : "Import fills"}
      </button>
      {result ? (
        <p className="text-xs text-muted">
          {result.inserted} new, {result.duplicate} duplicate, {result.fetched} perp /{" "}
          {result.rawFetched} raw, {result.pagesFetched} pages
        </p>
      ) : null}
      {error ? <p className="text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}
