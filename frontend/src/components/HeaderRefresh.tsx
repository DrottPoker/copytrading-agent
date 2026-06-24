"use client";

import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useTransition } from "react";

export function HeaderRefresh({
  intervalMs,
  isRefreshing,
  label,
  onRefresh,
  title,
}: {
  intervalMs?: number;
  isRefreshing?: boolean;
  label: string;
  onRefresh?: () => void | Promise<void>;
  title: string;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const intervalSeconds = useMemo(
    () => (intervalMs ? Math.round(intervalMs / 1000) : null),
    [intervalMs],
  );

  const refresh = useCallback(() => {
    if (document.visibilityState === "hidden") {
      return;
    }

    if (onRefresh) {
      void onRefresh();
      return;
    }

    startTransition(() => {
      router.refresh();
    });
  }, [onRefresh, router]);

  useEffect(() => {
    if (!intervalMs) {
      return;
    }

    const intervalId = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(intervalId);
  }, [intervalMs, refresh]);

  const active = Boolean(isRefreshing || isPending);
  const controlTitle = intervalSeconds ? `${title}, every ${intervalSeconds}s` : title;

  return (
    <span className="inline-flex min-h-8 items-center gap-2 whitespace-nowrap text-xs font-medium text-[#5b6770]">
      <span>{label}</span>
      <button
        type="button"
        onClick={refresh}
        title={controlTitle}
        className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-line bg-white text-[#344054] shadow-sm hover:bg-[#f7f9fb]"
      >
        <RefreshCw className={`h-3.5 w-3.5 ${active ? "animate-spin" : ""}`} aria-hidden="true" />
        <span className="sr-only">Refresh</span>
      </button>
    </span>
  );
}
