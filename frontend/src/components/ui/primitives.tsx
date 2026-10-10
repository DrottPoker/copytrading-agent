import { Loader2 } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";
import { formatPercent, formatSignedUsd, formatUsd, toNumber } from "@/lib/format";

import { signTone, toneBar, toneDot, toneSoft, toneText, type Tone } from "./tone";

export function Panel({
  actions,
  bodyClassName,
  children,
  className,
  description,
  id,
  meta,
  title,
}: {
  actions?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
  className?: string;
  description?: ReactNode;
  id?: string;
  meta?: ReactNode;
  title?: ReactNode;
}) {
  return (
    <section
      id={id}
      className={cx("min-w-0 overflow-hidden rounded-lg border border-line bg-surface", className)}
    >
      {title ? (
        <header className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3.5 py-2">
          <div className="min-w-0 flex-1">
            <h2 className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm font-semibold text-fg">
              <span>{title}</span>
              {meta ? <span className="text-xs font-normal text-fg-3">{meta}</span> : null}
            </h2>
            {description ? <p className="text-xs text-fg-3">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 flex-wrap items-center gap-1.5">{actions}</div> : null}
        </header>
      ) : null}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

export function PageHeader({
  actions,
  children,
  description,
  title,
}: {
  actions?: ReactNode;
  children?: ReactNode;
  description?: ReactNode;
  title: ReactNode;
}) {
  return (
    <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-[-0.01em] text-fg">{title}</h1>
        {description ? <p className="mt-0.5 max-w-3xl text-sm text-fg-3">{description}</p> : null}
        {children}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/**
  A row of key figures separated by hairlines. Every cell draws its right and bottom border and the
  inner grid overhangs the frame by one pixel, so rows that are not full stay clean.
*/
export function StatStrip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface">
      <div className={cx("-mb-px -mr-px grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4", className)}>{children}</div>
    </div>
  );
}

export function Stat({
  action,
  hint,
  label,
  sub,
  tone = "neutral",
  value,
}: {
  action?: ReactNode;
  hint?: string;
  label: ReactNode;
  sub?: ReactNode;
  tone?: Tone;
  value: ReactNode;
}) {
  return (
    <div className="min-w-0 border-b border-r border-line px-3.5 py-2.5">
      <div className="flex min-w-0 items-center justify-between gap-2">
        <p className="truncate text-xs text-fg-3" title={hint}>
          {hint ? <span className="cursor-help border-b border-dotted border-fg-4">{label}</span> : label}
        </p>
        {action}
      </div>
      <p
        className={cx(
          "mt-0.5 truncate text-lg font-semibold tabular-nums",
          tone === "neutral" ? "text-fg" : toneText[tone],
        )}
      >
        {value}
      </p>
      {sub ? (
        <p className="truncate text-xs text-fg-3" title={typeof sub === "string" ? sub : undefined}>
          {sub}
        </p>
      ) : null}
    </div>
  );
}

export function Badge({
  children,
  className,
  title,
  tone = "neutral",
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  tone?: Tone;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex h-[18px] max-w-full shrink-0 items-center whitespace-nowrap rounded px-1.5 text-2xs font-medium",
        toneSoft[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StatusDot({
  className,
  label,
  pulse = false,
  title,
  tone = "neutral",
}: {
  className?: string;
  label?: ReactNode;
  pulse?: boolean;
  title?: string;
  tone?: Tone;
}) {
  return (
    <span title={title} className={cx("inline-flex min-w-0 items-center gap-1.5", className)}>
      <span
        aria-hidden="true"
        className={cx(
          "h-1.5 w-1.5 shrink-0 rounded-full",
          toneDot[tone],
          pulse && cx("animate-pulse-dot", toneText[tone]),
        )}
      />
      {label ? <span className="truncate">{label}</span> : null}
    </span>
  );
}

export function Meter({
  className,
  label,
  tone = "neutral",
  value,
}: {
  className?: string;
  label?: string;
  tone?: Tone;
  value: number | null | undefined;
}) {
  const ratio = clampRatio(value ?? 0);
  return (
    <div
      aria-label={label}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={Math.round(ratio * 100)}
      className={cx("h-1 overflow-hidden rounded-full bg-surface-3", className)}
      role="meter"
    >
      <div className={cx("h-full rounded-full", toneBar[tone])} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

/** Signed USD value colored by sign. */
export function Pnl({
  className,
  compact,
  value,
}: {
  className?: string;
  compact?: boolean;
  value: string | number | null | undefined;
}) {
  const parsed = toNumber(value);
  return (
    <span className={cx("tabular-nums", toneText[signTone(parsed)], parsed === 0 && "text-fg-3", className)}>
      {formatSignedUsd(parsed, { compact })}
    </span>
  );
}

export function PnlPercent({
  className,
  value,
}: {
  className?: string;
  value: string | number | null | undefined;
}) {
  const parsed = toNumber(value);
  return (
    <span className={cx("tabular-nums", toneText[signTone(parsed)], className)}>
      {formatPercent(parsed, { signed: true })}
    </span>
  );
}

export function Usd({
  className,
  compact,
  value,
}: {
  className?: string;
  compact?: boolean;
  value: string | number | null | undefined;
}) {
  return <span className={cx("tabular-nums", className)}>{formatUsd(value, { compact })}</span>;
}

/** Long or short side, shown as colored text instead of a pill. */
export function Side({ side, suffix }: { side: string | null | undefined; suffix?: ReactNode }) {
  if (!side) {
    return <span className="text-fg-4">-</span>;
  }
  const normalized = side.toLowerCase();
  const isLong = normalized === "long" || normalized === "buy" || normalized === "b";
  const label =
    normalized === "b" ? "Buy" : normalized === "a" ? "Sell" : side.charAt(0).toUpperCase() + side.slice(1);
  return (
    <span className={cx("font-medium", isLong ? "text-positive" : "text-negative")}>
      {label}
      {suffix ? <span className="ml-1 font-normal text-fg-3">{suffix}</span> : null}
    </span>
  );
}

export function EmptyState({
  action,
  className,
  children,
  title,
}: {
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  title: ReactNode;
}) {
  return (
    <div className={cx("px-4 py-8 text-center", className)}>
      <p className="text-sm font-medium text-fg-2">{title}</p>
      {children ? <p className="mx-auto mt-1 max-w-md text-xs text-fg-3">{children}</p> : null}
      {action ? <div className="mt-3 flex justify-center">{action}</div> : null}
    </div>
  );
}

export function Callout({
  action,
  children,
  className,
  title,
  tone = "neutral",
}: {
  action?: ReactNode;
  children?: ReactNode;
  className?: string;
  title: ReactNode;
  tone?: Tone;
}) {
  const borderTone: Record<Tone, string> = {
    info: "border-l-info",
    negative: "border-l-negative",
    neutral: "border-l-fg-4",
    positive: "border-l-positive",
    warning: "border-l-warning",
  };
  return (
    <div
      role={tone === "negative" || tone === "warning" ? "alert" : undefined}
      className={cx(
        "flex flex-col gap-2 rounded-md border border-line border-l-2 bg-surface px-3 py-2 sm:flex-row sm:items-center",
        borderTone[tone],
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        <p className={cx("text-sm font-medium", tone === "neutral" ? "text-fg" : toneText[tone])}>{title}</p>
        {children ? <div className="mt-0.5 text-xs text-fg-2">{children}</div> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden="true" className={cx("h-3.5 w-3.5 animate-spin", className)} />;
}

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "positive";
type ButtonSize = "xs" | "sm" | "md";

export function buttonClass({
  className,
  size = "md",
  variant = "secondary",
}: {
  className?: string;
  size?: ButtonSize;
  variant?: ButtonVariant;
} = {}) {
  const sizes: Record<ButtonSize, string> = {
    md: "h-8 gap-1.5 px-3 text-sm",
    sm: "h-7 gap-1.5 px-2.5 text-xs",
    xs: "h-6 gap-1 px-2 text-xs",
  };
  const variants: Record<ButtonVariant, string> = {
    danger:
      "border border-line-strong bg-surface text-negative hover:border-negative/50 hover:bg-negative/8",
    ghost: "border border-transparent text-fg-2 hover:bg-surface-3 hover:text-fg",
    positive: "border border-positive bg-positive text-white hover:brightness-110",
    primary: "border border-fg bg-fg text-canvas hover:opacity-90",
    secondary: "border border-line-strong bg-surface text-fg hover:bg-surface-2 hover:border-fg-4",
  };
  return cx(
    "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-md font-medium transition-colors",
    "disabled:cursor-not-allowed disabled:opacity-50",
    sizes[size],
    variants[variant],
    className,
  );
}

export function LinkButton({
  children,
  className,
  href,
  size,
  variant,
}: {
  children: ReactNode;
  className?: string;
  href: string;
  size?: ButtonSize;
  variant?: ButtonVariant;
}) {
  return (
    <Link href={href} className={buttonClass({ className, size, variant })}>
      {children}
    </Link>
  );
}

/** Small label with an explanation on hover. */
export function Hint({ children, text }: { children: ReactNode; text: string }) {
  return (
    <span title={text} className="cursor-help border-b border-dotted border-fg-4">
      {children}
    </span>
  );
}

export function KeyValues({
  className,
  items,
}: {
  className?: string;
  items: Array<{ hint?: string; label: ReactNode; tone?: Tone; value: ReactNode }>;
}) {
  return (
    <dl className={cx("kv text-sm", className)}>
      {items.map((item, index) => (
        <KeyValueRow key={index} {...item} />
      ))}
    </dl>
  );
}

function KeyValueRow({
  hint,
  label,
  tone = "neutral",
  value,
}: {
  hint?: string;
  label: ReactNode;
  tone?: Tone;
  value: ReactNode;
}) {
  return (
    <>
      <dt className="min-w-0 truncate">{hint ? <Hint text={hint}>{label}</Hint> : label}</dt>
      <dd className={tone === "neutral" ? undefined : toneText[tone]}>{value}</dd>
    </>
  );
}

export function SectionLabel({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("mb-1.5 text-xs font-medium text-fg-3", className)}>{children}</p>;
}

export function clampRatio(value: number) {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

/** Score color: 70 and above is strong, 45 to 70 is mixed, below is weak. */
export function scoreTone(value: string | number | null | undefined): Tone {
  const parsed = toNumber(value);
  if (parsed === null) {
    return "neutral";
  }
  if (parsed >= 70) {
    return "positive";
  }
  if (parsed >= 45) {
    return "warning";
  }
  return "negative";
}
