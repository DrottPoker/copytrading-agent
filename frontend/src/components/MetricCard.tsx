import type { LucideIcon } from "lucide-react";

type Tone = "positive" | "warning" | "danger" | "neutral";

const toneClasses: Record<Tone, string> = {
  positive: "border-[#a7d8c4] bg-[#eefaf5] text-positive",
  warning: "border-[#f0c36d] bg-[#fff8e8] text-warning",
  danger: "border-[#f2aaa5] bg-[#fff2f0] text-danger",
  neutral: "border-line bg-panel text-[#344054]",
};

export function MetricCard({
  icon: Icon,
  label,
  value,
  detail,
  tone = "neutral",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  detail: string;
  tone?: Tone;
}) {
  return (
    <article className={`rounded-lg border p-4 ${toneClasses[tone]}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-[#526070]">{label}</p>
          <p className="mt-2 text-xl font-semibold text-ink">{value}</p>
        </div>
        <Icon className="h-5 w-5 shrink-0" aria-hidden="true" />
      </div>
      <p className="mt-3 min-h-5 text-sm text-[#526070]">{detail}</p>
    </article>
  );
}
