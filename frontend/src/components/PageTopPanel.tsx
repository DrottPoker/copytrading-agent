import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function PageTopPanel({
  actions,
  eyebrow,
  icon: Icon,
  meta,
  title,
}: {
  actions?: ReactNode;
  eyebrow?: string;
  icon?: LucideIcon;
  meta?: ReactNode;
  title: string;
}) {
  return (
    <header className="rounded-lg border border-line bg-panel px-4 py-3 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          {eyebrow ? <p className="text-sm font-medium text-[#5b6770]">{eyebrow}</p> : null}
          <h1 className="mt-1 flex min-w-0 items-center gap-2 whitespace-normal break-words text-2xl font-semibold tracking-normal text-ink">
            {Icon ? <Icon className="h-6 w-6 shrink-0 text-[#5b6770]" aria-hidden="true" /> : null}
            {title}
          </h1>
          {meta ? <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div> : null}
        </div>
        {actions ? (
          <div className="flex flex-wrap items-center gap-2 lg:justify-end">{actions}</div>
        ) : null}
      </div>
    </header>
  );
}
