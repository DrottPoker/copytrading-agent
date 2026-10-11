"use client";

import { Timer } from "lucide-react";

import { Stopwatch } from "@/components/ui/client";
import { cx } from "@/lib/cx";
import { formatDuration } from "@/lib/format";
import { operationRunDurationMs } from "@/lib/operations";
import type { OperationStatus } from "@/types/operation";

/** How long a job has been running, counting up, or how long its last run took. */
export function JobTimer({
  className,
  operation,
}: {
  className?: string;
  operation: OperationStatus | null | undefined;
}) {
  if (operation?.status === "running" && operation.startedAt) {
    return (
      <span className={cx("inline-flex items-center gap-1 text-xs text-fg-2", className)}>
        <Timer className="h-3 w-3 text-fg-3" aria-hidden="true" />
        <span className="sr-only">Running for</span>
        <Stopwatch since={operation.startedAt} />
      </span>
    );
  }
  const duration = operationRunDurationMs(operation);
  if (duration === null) {
    return null;
  }
  return (
    <span className={cx("inline-flex items-center gap-1 text-xs text-fg-3", className)}>
      <Timer className="h-3 w-3" aria-hidden="true" />
      took {formatDuration(duration)}
    </span>
  );
}
