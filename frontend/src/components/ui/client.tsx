"use client";

import { Check, Copy, MoreHorizontal, RefreshCw, X } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  useTransition,
} from "react";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";
import { formatClock, formatRelativeTime, formatTimestamp, shortAddress } from "@/lib/format";

import { buttonClass } from "./primitives";

/** Relative time that keeps itself current. The absolute timestamp is in the tooltip. */
export function TimeAgo({
  className,
  fallback = "-",
  value,
}: {
  className?: string;
  fallback?: string;
  value: string | number | Date | null | undefined;
}) {
  const now = useNow(15_000);
  if (value === null || value === undefined || value === "") {
    return <span className={className}>{fallback}</span>;
  }
  return (
    <time
      className={className}
      dateTime={new Date(value).toISOString()}
      suppressHydrationWarning
      title={formatTimestamp(value)}
    >
      {formatRelativeTime(value, now)}
    </time>
  );
}

/** Time since a start, counting up every second like a stopwatch. */
export function Stopwatch({ className, since }: { className?: string; since: string | number | Date }) {
  // The browser clock is only read after hydration, so a slow page never shows a stale time.
  const now = useSecondClock();
  return (
    <span className={cx("tabular-nums", className)} title={`Started ${formatTimestamp(since)}`}>
      {now === null ? null : formatClock(now - new Date(since).getTime())}
    </span>
  );
}

export function useNow(intervalMs: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const intervalId = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(intervalId);
  }, [intervalMs]);
  return now;
}

// One clock for every stopwatch on the page, so they all show the same second.
const secondListeners = new Set<() => void>();
let secondIntervalId: number | null = null;
let currentSecondMs = 0;

function subscribeToSeconds(listener: () => void) {
  secondListeners.add(listener);
  if (secondIntervalId === null) {
    currentSecondMs = Date.now();
    secondIntervalId = window.setInterval(() => {
      currentSecondMs = Date.now();
      secondListeners.forEach((notify) => notify());
    }, 1000);
  }
  return () => {
    secondListeners.delete(listener);
    if (secondListeners.size === 0 && secondIntervalId !== null) {
      window.clearInterval(secondIntervalId);
      secondIntervalId = null;
    }
  };
}

function readSecondClock() {
  if (secondIntervalId === null) {
    // Without a running clock the stored time is old. Whole seconds keep repeated reads equal.
    currentSecondMs = Math.floor(Date.now() / 1000) * 1000;
  }
  return currentSecondMs;
}

/** The current time, updated every second; null during server rendering and hydration. */
function useSecondClock(): number | null {
  return useSyncExternalStore(subscribeToSeconds, readSecondClock, () => null);
}

/** Shortened address with a copy button. */
export function Address({
  address,
  className,
  head,
  tail,
}: {
  address: string;
  className?: string;
  head?: number;
  tail?: number;
}) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(address);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  }, [address]);

  return (
    <span className={cx("group/address inline-flex min-w-0 items-center gap-1 font-mono text-xs text-fg-3", className)}>
      <span className="truncate" title={address}>
        {shortAddress(address, head, tail)}
      </span>
      <button
        type="button"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          void copy();
        }}
        className="rounded p-0.5 text-fg-4 opacity-0 hover:text-fg focus-visible:opacity-100 group-hover/address:opacity-100"
        aria-label="Copy address"
        title="Copy address"
      >
        {copied ? <Check className="h-3 w-3" aria-hidden="true" /> : <Copy className="h-3 w-3" aria-hidden="true" />}
      </button>
    </span>
  );
}

export type TabItem<T extends string> = {
  count?: number;
  id: T;
  label: string;
};

/** Underlined tabs for switching between related lists. */
export function Tabs<T extends string>({
  className,
  items,
  label,
  onChange,
  value,
}: {
  className?: string;
  items: Array<TabItem<T>>;
  label: string;
  onChange: (value: T) => void;
  value: T;
}) {
  return (
    <div aria-label={label} className={cx("flex min-w-0 gap-4 overflow-x-auto", className)} role="tablist">
      {items.map((item) => {
        const active = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(item.id)}
            className={cx(
              "-mb-px inline-flex h-10 shrink-0 items-center gap-1.5 border-b-2 text-sm transition-colors",
              active ? "border-fg font-medium text-fg" : "border-transparent text-fg-3 hover:text-fg",
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span className="rounded bg-surface-3 px-1 text-2xs tabular-nums text-fg-3">{item.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

/** Compact segmented control for two to four exclusive options. */
export function Segmented<T extends string | number>({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: T) => void;
  options: Array<{ label: string; value: T }>;
  value: T;
}) {
  return (
    <div aria-label={label} className="inline-flex rounded-md border border-line bg-surface-2 p-0.5" role="group">
      {options.map((option) => (
        <button
          key={String(option.value)}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            "h-6 rounded px-2 text-xs font-medium transition-colors",
            option.value === value ? "bg-surface text-fg shadow-sm" : "text-fg-3 hover:text-fg",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function Dialog({
  children,
  description,
  footer,
  onClose,
  open,
  size = "md",
  title,
}: {
  children?: ReactNode;
  description?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  open: boolean;
  size?: "sm" | "md" | "lg" | "xl";
  title: ReactNode;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  // Parents often pass a new onClose on every render; the effect below must only run on open.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) {
      return;
    }
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    const focusTarget =
      panelRef.current?.querySelector<HTMLElement>("[data-autofocus]") ??
      panelRef.current?.querySelector<HTMLElement>("input, select, textarea, button");
    focusTarget?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus?.();
    };
  }, [open]);

  if (!open) {
    return null;
  }

  const widths = { lg: "max-w-3xl", md: "max-w-lg", sm: "max-w-md", xl: "max-w-5xl" };

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[var(--overlay)] px-3 py-10 sm:py-16"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={panelRef}
        aria-labelledby={titleId}
        aria-modal="true"
        className={cx("w-full overflow-hidden rounded-lg border border-line bg-surface shadow-pop", widths[size])}
        role="dialog"
      >
        <div className="flex items-start gap-3 border-b border-line px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 id={titleId} className="text-base font-semibold text-fg">
              {title}
            </h2>
            {description ? <div className="mt-0.5 text-xs text-fg-3">{description}</div> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className={buttonClass({ size: "xs", variant: "ghost", className: "w-6 px-0" })}
            aria-label="Close dialog"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        {children ? <div className="max-h-[calc(100vh-14rem)] overflow-y-auto px-4 py-3">{children}</div> : null}
        {footer ? (
          <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-surface-2 px-4 py-2.5">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}

type ConfirmRequest = {
  body?: ReactNode;
  confirmLabel: string;
  title: string;
  tone?: "danger" | "default";
};

type ConfirmState = ConfirmRequest & { resolve: (value: boolean) => void };

const ConfirmContext = createContext<((request: ConfirmRequest) => Promise<boolean>) | null>(null);

/** Provides a promise based confirmation dialog for destructive or important actions. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConfirmState | null>(null);

  const confirm = useCallback(
    (request: ConfirmRequest) =>
      new Promise<boolean>((resolve) => {
        setState({ ...request, resolve });
      }),
    [],
  );

  const close = useCallback(
    (value: boolean) => {
      state?.resolve(value);
      setState(null);
    },
    [state],
  );

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog
        open={state !== null}
        onClose={() => close(false)}
        size="sm"
        title={state?.title ?? ""}
        footer={
          <>
            <button
              type="button"
              data-autofocus={state?.tone === "danger" ? true : undefined}
              className={buttonClass()}
              onClick={() => close(false)}
            >
              Cancel
            </button>
            <button
              type="button"
              data-autofocus={state?.tone === "danger" ? undefined : true}
              className={buttonClass({ variant: state?.tone === "danger" ? "danger" : "primary" })}
              onClick={() => close(true)}
            >
              {state?.confirmLabel}
            </button>
          </>
        }
      >
        {state?.body ? <div className="text-sm text-fg-2">{state.body}</div> : null}
      </Dialog>
    </ConfirmContext.Provider>
  );
}

export function useConfirm() {
  const confirm = useContext(ConfirmContext);
  return useCallback(
    async (request: ConfirmRequest) => {
      if (confirm) {
        return confirm(request);
      }
      return window.confirm(request.title);
    },
    [confirm],
  );
}

export type MenuItem = {
  danger?: boolean;
  disabled?: boolean;
  hint?: string;
  label: string;
  onSelect: () => void;
};

/** Overflow menu for secondary row and page actions. */
export function Menu({
  items,
  label = "More actions",
  trigger,
}: {
  items: MenuItem[];
  label?: string;
  trigger?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const handlePointer = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative inline-flex">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={label}
        title={label}
        onClick={() => setOpen((current) => !current)}
        className={trigger ? buttonClass({ size: "sm" }) : buttonClass({ size: "sm", variant: "ghost", className: "w-7 px-0" })}
      >
        {trigger ?? <MoreHorizontal className="h-4 w-4" aria-hidden="true" />}
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 min-w-48 overflow-hidden rounded-md border border-line bg-surface py-1 shadow-pop"
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              title={item.hint}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
              className={cx(
                "flex w-full items-center px-3 py-1.5 text-left text-sm disabled:cursor-not-allowed disabled:opacity-45",
                item.danger ? "text-negative hover:bg-negative/8" : "text-fg hover:bg-surface-3",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
  Shows how fresh a server rendered page is and refreshes it. With an interval the page refreshes
  itself while the tab is visible.
*/
export function RefreshControl({
  intervalMs,
  isRefreshing,
  onRefresh,
  updatedAt,
}: {
  intervalMs?: number;
  isRefreshing?: boolean;
  onRefresh?: () => void | Promise<void>;
  updatedAt?: string | number | Date | null;
}) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);

  const refresh = useCallback(() => {
    if (document.visibilityState === "hidden") {
      return;
    }
    setLastRefresh(Date.now());
    if (onRefresh) {
      void onRefresh();
      return;
    }
    startTransition(() => router.refresh());
  }, [onRefresh, router]);

  useEffect(() => {
    if (!intervalMs) {
      return;
    }
    const intervalId = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(intervalId);
  }, [intervalMs, refresh]);

  const busy = Boolean(isRefreshing || isPending);
  const shownAt = updatedAt ?? lastRefresh;

  return (
    <div className="inline-flex items-center gap-2 text-xs text-fg-3">
      {shownAt ? (
        <span className="hidden sm:inline">
          Updated <TimeAgo value={shownAt} />
        </span>
      ) : null}
      <button
        type="button"
        onClick={refresh}
        title={intervalMs ? `Refresh now (auto every ${Math.round(intervalMs / 1000)}s)` : "Refresh now"}
        aria-label="Refresh"
        className={buttonClass({ size: "sm", className: "w-7 px-0" })}
      >
        <RefreshCw className={cx("h-3.5 w-3.5", busy && "animate-spin")} aria-hidden="true" />
      </button>
    </div>
  );
}

/** Client side pagination over an in-memory list. */
export function usePagination<T>(items: T[], pageSize: number) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, pageCount - 1);
  useEffect(() => {
    if (page !== safePage) {
      setPage(safePage);
    }
  }, [page, safePage]);
  return {
    page: safePage,
    pageCount,
    pageItems: items.slice(safePage * pageSize, safePage * pageSize + pageSize),
    setPage,
  };
}

export function Pagination({
  onPageChange,
  page,
  pageCount,
  pageSize,
  total,
}: {
  onPageChange: (page: number) => void;
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
}) {
  if (pageCount <= 1) {
    return null;
  }
  const from = page * pageSize + 1;
  const to = Math.min(total, (page + 1) * pageSize);
  return (
    <div className="flex items-center justify-between gap-3 border-t border-line px-3.5 py-2 text-xs text-fg-3">
      <span className="tabular-nums">
        {from}-{to} of {total}
      </span>
      <div className="flex gap-1.5">
        <button
          type="button"
          className={buttonClass({ size: "xs" })}
          disabled={page === 0}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className={buttonClass({ size: "xs" })}
          disabled={page >= pageCount - 1}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
