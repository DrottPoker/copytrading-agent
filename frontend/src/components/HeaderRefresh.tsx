"use client";

import { RefreshCw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useTransition } from "react";

export function HeaderRefreshButton({
  intervalMs,
  isRefreshing,
  onRefresh,
  title,
}: {
  intervalMs?: number;
  isRefreshing?: boolean;
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
    <button
      type="button"
      onClick={refresh}
      title={controlTitle}
      className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line-strong bg-white text-secondary shadow-panel hover:border-faint hover:bg-subtle hover:text-ink"
    >
      <RefreshCw className={`h-3.5 w-3.5 ${active ? "animate-spin" : ""}`} aria-hidden="true" />
      <span className="sr-only">Refresh</span>
    </button>
  );
}

export function HeaderUpdatedLabel({ label }: { label: string }) {
  return (
    <span className="inline-flex min-h-8 items-center whitespace-nowrap text-xs font-medium text-muted">
      {label}
    </span>
  );
}
