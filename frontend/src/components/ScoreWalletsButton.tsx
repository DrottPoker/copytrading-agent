"use client";

import { Gauge } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { getPublicApiBaseUrl } from "@/lib/api";
import type { WalletScoreRunResponse } from "@/types/wallet";

export function ScoreWalletsButton() {
  const router = useRouter();
  const [isScoring, setIsScoring] = useState(false);
  const [result, setResult] = useState<WalletScoreRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function scoreWallets() {
    setIsScoring(true);
    setResult(null);
    setError(null);
    try {
      const response = await fetch(`${getPublicApiBaseUrl()}/scores/recalculate`, {
        method: "POST",
      });
      const payload = (await response.json().catch(() => null)) as
        | WalletScoreRunResponse
        | { detail?: string }
        | null;
      if (!response.ok) {
        setError(
          payload && "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : "Scoring failed.",
        );
        return;
      }
      setResult(payload as WalletScoreRunResponse);
      router.refresh();
    } catch {
      setError("Could not reach API.");
    } finally {
      setIsScoring(false);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <button
        type="button"
        disabled={isScoring}
        onClick={scoreWallets}
        className="inline-flex h-8 items-center gap-1 rounded-md bg-ink px-3 text-xs font-medium text-white disabled:bg-[#98a2b3]"
        title="Recalculate wallet scores"
      >
        <Gauge className="h-3.5 w-3.5" aria-hidden="true" />
        {isScoring ? "Scoring" : "Score wallets"}
      </button>
      {result ? (
        <p className="text-xs text-[#526070]">
          {result.scoredWallets} scored, {result.windowDays}d
        </p>
      ) : null}
      {error ? <p className="text-xs font-medium text-danger">{error}</p> : null}
    </div>
  );
}
