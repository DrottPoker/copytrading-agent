import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type DashboardTone = "neutral" | "positive" | "warning" | "danger";

const metricToneClasses: Record<DashboardTone, string> = {
  neutral: "bg-brand-soft text-brand",
  positive: "bg-positive-soft text-positive",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
};

export function DashboardMetric({
  action,
  detail,
  icon: Icon,
  label,
  tone = "neutral",
  value,
}: {
  action?: ReactNode;
  detail: ReactNode;
  icon: LucideIcon;
  label: string;
  tone?: DashboardTone;
  value: ReactNode;
}) {
  return (
    <article className="ui-metric min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[11px] font-semibold uppercase tracking-[0.05em] text-muted">
            {label}
          </p>
          <div className="mt-1.5 truncate text-xl font-semibold leading-tight text-ink tabular-nums">
            {value}
          </div>
        </div>
        {action ?? (
          <span
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${metricToneClasses[tone]}`}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
        )}
      </div>
      <div className="mt-2 truncate text-xs leading-5 text-muted">{detail}</div>
    </article>
  );
}

export function DashboardPanel({
  action,
  bodyClassName = "p-4",
  children,
  className = "",
  icon: Icon,
  meta,
  title,
}: {
  action?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
  className?: string;
  icon?: LucideIcon;
  meta?: ReactNode;
  title: ReactNode;
}) {
  return (
    <section className={`ui-panel overflow-hidden ${className}`.trim()}>
      <div className="ui-panel-header">
        <div className="flex min-w-0 items-center gap-2.5">
          {Icon ? (
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-subtle text-secondary">
              <Icon className="h-4 w-4" aria-hidden="true" />
            </span>
          ) : null}
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-ink">{title}</h2>
            {meta ? <div className="mt-0.5 truncate text-xs text-muted">{meta}</div> : null}
          </div>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}
