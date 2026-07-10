import type { LucideIcon } from "lucide-react";

import { DashboardMetric, type DashboardTone } from "./DashboardSurface";

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
  tone?: DashboardTone;
}) {
  return <DashboardMetric detail={detail} icon={Icon} label={label} tone={tone} value={value} />;
}
