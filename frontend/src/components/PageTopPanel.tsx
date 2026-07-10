import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function PageTopPanel({
  actions,
  eyebrow,
  icon: Icon,
  meta,
  refresh,
  title,
}: {
  actions?: ReactNode;
  eyebrow?: string;
  icon?: LucideIcon;
  meta?: ReactNode;
  refresh?: ReactNode;
  title: string;
}) {
  return (
    <header className="border-b border-line pb-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
        <div className="min-w-0 lg:max-w-[38rem] lg:shrink-0">
          {eyebrow ? <p className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted">{eyebrow}</p> : null}
          <div className="mt-1.5 flex min-w-0 items-center gap-2.5">
            {Icon ? (
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-panel text-brand shadow-panel">
                <Icon className="h-4 w-4" strokeWidth={1.9} aria-hidden="true" />
              </span>
            ) : null}
            <h1 className="min-w-0 whitespace-normal break-words text-[22px] font-semibold tracking-[-0.025em] text-ink sm:text-2xl">
              {title}
            </h1>
          </div>
          {meta ? <div className="mt-2.5 flex flex-wrap items-center gap-2">{meta}</div> : null}
        </div>
        {actions ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2 lg:ml-auto lg:justify-end">
            {actions}
          </div>
        ) : null}
        {refresh ? <div className="flex shrink-0 self-end lg:self-auto">{refresh}</div> : null}
      </div>
    </header>
  );
}
