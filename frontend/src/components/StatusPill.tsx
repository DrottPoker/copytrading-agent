type Tone = "positive" | "warning" | "danger" | "neutral";

const toneClasses: Record<Tone, string> = {
  positive: "border-[#a7d8c4] bg-[#eefaf5] text-positive",
  warning: "border-[#f0c36d] bg-[#fff8e8] text-warning",
  danger: "border-[#f2aaa5] bg-[#fff2f0] text-danger",
  neutral: "border-line bg-[#f7f9fb] text-[#344054]",
};

export function StatusPill({ label, tone = "neutral" }: { label: string; tone?: Tone }) {
  return (
    <span
      className={`inline-flex min-h-8 items-center rounded-full border px-3 text-sm font-medium ${toneClasses[tone]}`}
    >
      {label}
    </span>
  );
}
