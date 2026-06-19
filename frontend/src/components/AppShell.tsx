"use client";

import {
  Activity,
  BarChart3,
  Compass,
  Database,
  LayoutDashboard,
  RadioTower,
  WalletCards,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const navItems = [
  {
    href: "/",
    icon: LayoutDashboard,
    label: "Dashboard",
    match: (pathname: string) => pathname === "/",
  },
  {
    href: "/wallets",
    icon: WalletCards,
    label: "Wallet Pool",
    match: (pathname: string) => pathname.startsWith("/wallets"),
  },
  {
    href: "/discovery",
    icon: Compass,
    label: "Discovery",
    match: (pathname: string) => pathname.startsWith("/discovery"),
  },
  {
    href: "/live-feed",
    icon: RadioTower,
    label: "Live Feed",
    match: (pathname: string) => pathname.startsWith("/live-feed"),
  },
  {
    href: "/paper-trading",
    icon: BarChart3,
    label: "Paper Trading",
    match: (pathname: string) => pathname.startsWith("/paper-trading"),
  },
  {
    href: "/database",
    icon: Database,
    label: "Database",
    match: (pathname: string) => pathname.startsWith("/database"),
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="min-h-screen bg-[#eef2f5] text-ink lg:grid lg:grid-cols-[264px_1fr]">
      <aside className="border-b border-[#d7dde5] bg-[#121619] text-white lg:min-h-screen lg:border-b-0 lg:border-r lg:border-[#252b2f]">
        <div className="flex h-full flex-col">
          <div className="border-b border-[#252b2f] px-4 py-4">
            <Link href="/" className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-[#f4f7f5] text-[#121619]">
                <Activity className="h-5 w-5" aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold">Hyperliquid</span>
                <span className="block truncate text-xs text-[#aeb7bd]">Copy Agent</span>
              </span>
            </Link>
          </div>

          <nav className="flex gap-2 overflow-x-auto px-3 py-3 lg:flex-col lg:overflow-visible">
            {navItems.map((item) => {
              const Icon = item.icon;
              const active = item.match(pathname);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={[
                    "inline-flex h-10 shrink-0 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors",
                    active
                      ? "bg-white text-[#121619]"
                      : "text-[#c7d0d6] hover:bg-[#1f262a] hover:text-white",
                  ].join(" ")}
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {item.label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-auto hidden border-t border-[#252b2f] p-4 text-xs text-[#aeb7bd] lg:block">
            <div className="flex items-center justify-between gap-3">
              <span>Mode</span>
              <span className="rounded-md border border-[#3c454a] px-1.5 py-0.5 text-xs text-white">Paper</span>
            </div>
          </div>
        </div>
      </aside>

      <div className="min-w-0">
        <main className="mx-auto flex w-full max-w-[1560px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
