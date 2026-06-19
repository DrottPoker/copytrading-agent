"use client";

import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";

export function AutoRefresh({
  intervalMs = 15000,
  label = "Auto refresh",
}: {
  intervalMs?: number;
  label?: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [lastRefreshAt, setLastRefreshAt] = useState<Date | null>(null);
  const intervalSeconds = useMemo(() => Math.round(intervalMs / 1000), [intervalMs]);

  const refresh = useCallback(() => {
    if (document.visibilityState === "hidden") {
      return;
    }

    startTransition(() => {
      router.refresh();
      setLastRefreshAt(new Date());
    });
  }, [router]);

  useEffect(() => {
    const intervalId = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(intervalId);
  }, [intervalMs, refresh]);

  return (
    <span
      className="inline-flex h-8 items-center gap-2 rounded-md border border-line bg-white px-2 text-xs font-medium text-[#344054]"
      title={lastRefreshAt ? `Last refresh ${lastRefreshAt.toLocaleTimeString("sv-SE")}` : label}
    >
      <RefreshCw className={`h-3.5 w-3.5 ${isPending ? "animate-spin" : ""}`} aria-hidden="true" />
      <span>{label}</span>
      <span className="font-mono text-[#5b6770]">{intervalSeconds}s</span>
    </span>
  );
}
