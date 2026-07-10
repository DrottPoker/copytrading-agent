"use client";

import {
  Activity,
  BarChart3,
  Compass,
  Database,
  LayoutDashboard,
  LineChart,
  RadioTower,
  ServerCog,
  SquareStack,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { getPublicApiBaseUrl } from "@/lib/config";
import type { HealthResponse } from "@/types/health";

const navGroups = [
  {
    label: "Execution",
    items: [
      { href: "/", icon: LayoutDashboard, label: "Overview", match: (path: string) => path === "/" },
      { href: "/accounts", icon: SquareStack, label: "Accounts", match: (path: string) => path.startsWith("/accounts") },
      { href: "/trading", icon: BarChart3, label: "Trading", match: (path: string) => path.startsWith("/trading") || path.startsWith("/paper-trading") },
    ],
  },
  {
    label: "Intelligence",
    items: [
      { href: "/wallets", icon: WalletCards, label: "Wallets", match: (path: string) => path.startsWith("/wallets") },
      { href: "/discovery", icon: Compass, label: "Discovery", match: (path: string) => path.startsWith("/discovery") },
      { href: "/analytics", icon: LineChart, label: "Analytics", match: (path: string) => path.startsWith("/analytics") },
      { href: "/live-feed", icon: RadioTower, label: "Live feed", match: (path: string) => path.startsWith("/live-feed") },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/database", icon: Database, label: "Database", match: (path: string) => path.startsWith("/database") },
      { href: "/ops", icon: ServerCog, label: "Operations", match: (path: string) => path.startsWith("/ops") },
    ],
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-canvas text-ink lg:grid lg:grid-cols-[232px_minmax(0,1fr)]">
      <a
        href="#main-content"
        className="sr-only fixed left-4 top-4 z-[100] rounded-lg bg-white px-3 py-2 text-sm font-semibold text-ink shadow-raised focus:not-sr-only"
      >
        Skip to content
      </a>
      <aside className="border-b border-white/10 bg-sidebar text-white lg:sticky lg:top-0 lg:h-screen lg:border-b-0 lg:border-r lg:border-white/10">
        <div className="flex h-full flex-col">
          <div className="border-b border-white/10 px-4 py-3.5 lg:py-4">
            <Link href="/" className="flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand text-white shadow-[0_8px_20px_rgba(37,99,235,0.28)]">
                <Activity className="h-[18px] w-[18px]" aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold tracking-tight">Copy Agent</span>
                <span className="block truncate text-[11px] text-sidebar-muted">Hyperliquid execution</span>
              </span>
            </Link>
          </div>

          <nav
            aria-label="Primary navigation"
            className="flex gap-1.5 overflow-x-auto px-3 py-2.5 lg:block lg:overflow-y-auto lg:py-4"
          >
            {navGroups.map((group) => (
              <div key={group.label} className="contents lg:mb-5 lg:block">
                <p className="mb-1.5 hidden px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-sidebar-muted/70 lg:block">
                  {group.label}
                </p>
                <div className="contents lg:grid lg:gap-1">
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    const active = item.match(pathname);
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        aria-current={active ? "page" : undefined}
                        className={[
                          "inline-flex h-9 shrink-0 items-center gap-2.5 rounded-lg border-l-2 px-3 text-[13px] font-medium transition-colors",
                          active
                            ? "border-blue-400 bg-white/10 text-white"
                            : "border-transparent text-sidebar-muted hover:bg-white/[0.06] hover:text-white",
                        ].join(" ")}
                      >
                        <Icon className="h-4 w-4 shrink-0" strokeWidth={1.8} aria-hidden="true" />
                        {item.label}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          <div className="mt-auto hidden border-t border-white/10 p-4 text-xs text-sidebar-muted lg:block">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Execution mode</span>
              <SystemModeBadge />
            </div>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <main
          id="main-content"
          className="mx-auto flex w-full max-w-[1720px] flex-col gap-4 px-4 py-4 sm:px-5 lg:px-6 lg:py-5 2xl:px-8"
        >
          {children}
        </main>
      </div>
    </div>
  );
}

function SystemModeBadge() {
  const [mode, setMode] = useState<HealthResponse["mode"] | "checking" | "unknown">(
    "checking",
  );

  useEffect(() => {
    let ignored = false;

    async function loadMode() {
      try {
        const response = await fetch(`${getPublicApiBaseUrl()}/health`, {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`Health check failed with HTTP ${response.status}.`);
        }
        const payload = (await response.json()) as HealthResponse;
        if (!ignored) {
          setMode(payload.mode);
        }
      } catch {
        if (!ignored) {
          setMode("unknown");
        }
      }
    }

    void loadMode();
    const intervalId = window.setInterval(() => {
      void loadMode();
    }, 30_000);
    return () => {
      ignored = true;
      window.clearInterval(intervalId);
    };
  }, []);

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.06] px-2 py-1 text-[11px] font-medium text-white">
      <span className={`h-1.5 w-1.5 rounded-full ${systemModeDotClass(mode)}`} aria-hidden="true" />
      {formatSystemMode(mode)}
    </span>
  );
}

function systemModeDotClass(mode: HealthResponse["mode"] | "checking" | "unknown") {
  if (mode === "live_small") {
    return "bg-emerald-400";
  }
  if (mode === "paper") {
    return "bg-blue-400";
  }
  if (mode === "monitor" || mode === "checking") {
    return mode === "checking" ? "animate-pulse bg-amber-300" : "bg-amber-300";
  }
  return "bg-red-400";
}

function formatSystemMode(mode: HealthResponse["mode"] | "checking" | "unknown") {
  if (mode === "live_small") {
    return "Live";
  }
  if (mode === "paper") {
    return "Paper";
  }
  if (mode === "monitor") {
    return "Monitor";
  }
  if (mode === "checking") {
    return "Checking";
  }
  return "Unknown";
}
