"use client";

import {
  Activity,
  BarChart3,
  CandlestickChart,
  LayoutDashboard,
  Menu as MenuIcon,
  Monitor,
  Moon,
  Radar,
  Server,
  Sun,
  Wallet,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { ConfirmProvider } from "@/components/ui/client";
import { cx } from "@/lib/cx";

import { SystemStatusBar } from "./SystemStatusBar";
import {
  applyTextSize,
  applyThemePreference,
  readTextSize,
  readThemePreference,
  type TextSize,
  type ThemePreference,
} from "./theme";

type NavItem = {
  href: string;
  icon: LucideIcon;
  label: string;
  match: (path: string) => boolean;
};

export const NAV_SECTIONS: Array<{ items: NavItem[]; label: string }> = [
  {
    label: "Monitor",
    items: [
      { href: "/", icon: LayoutDashboard, label: "Overview", match: (path) => path === "/" },
      {
        href: "/trading",
        icon: CandlestickChart,
        label: "Trading",
        match: (path) => path.startsWith("/trading"),
      },
      {
        href: "/activity",
        icon: Activity,
        label: "Activity",
        match: (path) => path.startsWith("/activity"),
      },
    ],
  },
  {
    label: "Research",
    items: [
      { href: "/wallets", icon: Wallet, label: "Wallets", match: (path) => path.startsWith("/wallets") },
      {
        href: "/discovery",
        icon: Radar,
        label: "Discovery",
        match: (path) => path.startsWith("/discovery"),
      },
      {
        href: "/analytics",
        icon: BarChart3,
        label: "Analytics",
        match: (path) => path.startsWith("/analytics"),
      },
    ],
  },
  {
    label: "Infrastructure",
    items: [
      { href: "/system", icon: Server, label: "System", match: (path) => path.startsWith("/system") },
    ],
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  return (
    <ConfirmProvider>
      <a
        href="#main-content"
        className="sr-only fixed left-3 top-3 z-[100] rounded-md bg-surface px-3 py-2 text-sm font-medium shadow-pop focus:not-sr-only"
      >
        Skip to content
      </a>
      <div className="min-h-screen lg:grid lg:grid-cols-[14.5rem_minmax(0,1fr)]">
        <aside className="hidden border-r border-line bg-surface lg:block">
          <div className="sticky top-0 flex h-screen flex-col">
            <Sidebar pathname={pathname} />
          </div>
        </aside>

        <div className="flex min-w-0 flex-col">
          <div className="sticky top-0 z-20 border-b border-line bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/85">
            <div className="flex h-11 items-center gap-2 px-3 lg:hidden">
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-2 hover:bg-surface-3"
                aria-label="Open navigation"
              >
                <MenuIcon className="h-4 w-4" aria-hidden="true" />
              </button>
              <Brand />
            </div>
            <SystemStatusBar />
          </div>
          <main
            id="main-content"
            className="mx-auto flex w-full max-w-[1760px] min-w-0 flex-1 flex-col gap-4 px-3 py-4 sm:px-5 lg:px-6 lg:py-5"
          >
            {children}
          </main>
        </div>
      </div>

      {drawerOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-[var(--overlay)]" onClick={() => setDrawerOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-line bg-surface shadow-pop">
            <button
              type="button"
              onClick={() => setDrawerOpen(false)}
              className="absolute right-2 top-2.5 inline-flex h-8 w-8 items-center justify-center rounded-md text-fg-3 hover:bg-surface-3"
              aria-label="Close navigation"
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
            <Sidebar pathname={pathname} />
          </div>
        </div>
      ) : null}
    </ConfirmProvider>
  );
}

function Sidebar({ pathname }: { pathname: string }) {
  return (
    <>
      <div className="flex h-12 items-center px-4">
        <Brand />
      </div>
      <nav aria-label="Primary navigation" className="flex-1 overflow-y-auto px-2 pb-4">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="mt-4 first:mt-1">
            <p className="px-2 pb-1 text-2xs font-medium text-fg-4">{section.label}</p>
            <ul className="grid gap-px">
              {section.items.map((item) => {
                const Icon = item.icon;
                const active = item.match(pathname);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      aria-current={active ? "page" : undefined}
                      className={cx(
                        "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm transition-colors",
                        active
                          ? "bg-surface-3 font-medium text-fg"
                          : "text-fg-2 hover:bg-surface-2 hover:text-fg",
                      )}
                    >
                      <Icon
                        className={cx("h-4 w-4 shrink-0", active ? "text-fg" : "text-fg-3")}
                        strokeWidth={1.75}
                        aria-hidden="true"
                      />
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="grid gap-2 border-t border-line px-3 py-3">
        <TextSizeToggle />
        <ThemeToggle />
      </div>
    </>
  );
}

export function Brand() {
  return (
    <Link href="/" className="inline-flex items-center gap-2 text-fg">
      <svg viewBox="0 0 20 20" className="h-5 w-5" aria-hidden="true">
        <rect x="2" y="2" width="11" height="11" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
        <rect x="7" y="7" width="11" height="11" rx="2.5" fill="currentColor" />
      </svg>
      <span className="text-sm font-semibold tracking-[-0.01em]">Copy Agent</span>
    </Link>
  );
}

const THEME_OPTIONS: Array<{ icon: LucideIcon; label: string; value: ThemePreference }> = [
  { icon: Monitor, label: "System theme", value: "system" },
  { icon: Sun, label: "Light theme", value: "light" },
  { icon: Moon, label: "Dark theme", value: "dark" },
];

function ThemeToggle() {
  const [preference, setPreference] = useState<ThemePreference>("system");

  useEffect(() => {
    setPreference(readThemePreference());
  }, []);

  useEffect(() => {
    if (preference !== "system" || typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = () => applyThemePreference("system");
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [preference]);

  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-fg-3">Theme</span>
      <div className="inline-flex rounded-md border border-line bg-surface-2 p-0.5" role="group" aria-label="Theme">
        {THEME_OPTIONS.map((option) => {
          const Icon = option.icon;
          const active = preference === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              aria-label={option.label}
              title={option.label}
              onClick={() => {
                setPreference(option.value);
                applyThemePreference(option.value);
              }}
              className={cx(
                "inline-flex h-7 w-8 items-center justify-center rounded",
                active ? "bg-surface text-fg shadow-sm" : "text-fg-3 hover:text-fg",
              )}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          );
        })}
      </div>
    </div>
  );
}

const TEXT_SIZE_OPTIONS: Array<{ className: string; label: string; value: TextSize }> = [
  { className: "text-xs", label: "Normal text size", value: "normal" },
  { className: "text-sm", label: "Large text size", value: "large" },
  { className: "text-base", label: "Larger text size", value: "larger" },
];

function TextSizeToggle() {
  const [size, setSize] = useState<TextSize>("normal");

  useEffect(() => {
    setSize(readTextSize());
  }, []);

  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs text-fg-3">Text size</span>
      <div className="inline-flex rounded-md border border-line bg-surface-2 p-0.5" role="group" aria-label="Text size">
        {TEXT_SIZE_OPTIONS.map((option) => {
          const active = size === option.value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={active}
              aria-label={option.label}
              title={option.label}
              onClick={() => {
                setSize(option.value);
                applyTextSize(option.value);
              }}
              className={cx(
                "inline-flex h-7 w-8 items-center justify-center rounded font-semibold leading-none",
                option.className,
                active ? "bg-surface text-fg shadow-sm" : "text-fg-3 hover:text-fg",
              )}
            >
              A
            </button>
          );
        })}
      </div>
    </div>
  );
}
