type Tone = "positive" | "warning" | "danger" | "neutral";

const toneClasses: Record<Tone, string> = {
  positive: "border-positive/20 bg-positive-soft text-positive",
  warning: "border-warning/20 bg-warning-soft text-warning",
  danger: "border-danger/20 bg-danger-soft text-danger",
  neutral: "border-line bg-subtle text-secondary",
};

const dotClasses: Record<Tone, string> = {
  positive: "bg-positive",
  warning: "bg-warning",
  danger: "bg-danger",
  neutral: "bg-faint",
};

export function StatusPill({ label, tone = "neutral" }: { label: string; tone?: Tone }) {
  return (
    <span
      className={`inline-flex h-6 max-w-full items-center gap-1.5 whitespace-nowrap rounded-full border px-2 text-[11px] font-semibold leading-none ${toneClasses[tone]}`}
    >
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClasses[tone]}`} aria-hidden="true" />
      {label}
    </span>
  );
}
