"use client";

import { Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { TimeAgo } from "@/components/ui/client";
import { buttonClass, Meter, Panel, Spinner, StatusDot } from "@/components/ui/primitives";
import { statusTone } from "@/components/ui/tone";
import { frontendConfig } from "@/lib/config";
import { formatDuration } from "@/lib/format";
import { apiRequest, errorMessage } from "@/lib/http";
import {
  MANUAL_JOBS,
  operationIsStopping,
  operationProgress,
  operationStatusLabel,
  optimisticRunning,
  optimisticStopping,
  upsertOperation,
  type JobDefinition,
} from "@/lib/operations";
import type { OperationStatus, OperationStatusListResponse } from "@/types/operation";

/** Background jobs with their progress and controls to start or cancel them. */
export function JobsPanel({
  initialItems,
  title = "Background jobs",
}: {
  initialItems: OperationStatus[];
  title?: string;
}) {
  const [items, setItems] = useState(initialItems);
  const [pendingKeys, setPendingKeys] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await apiRequest<OperationStatusListResponse>("/operations/status", { signal });
      setItems(data.items);
    } catch {
      // The next poll retries; the status bar reports a lasting outage.
    }
  }, []);

  const anyRunning = items.some((item) => item.status === "running");

  useEffect(() => {
    const controller = new AbortController();
    const intervalId = window.setInterval(
      () => void refresh(controller.signal),
      anyRunning ? 2000 : frontendConfig.operationStatusPollMs,
    );
    return () => {
      controller.abort();
      window.clearInterval(intervalId);
    };
  }, [anyRunning, refresh]);

  const setPending = (key: string, pending: boolean) =>
    setPendingKeys((current) => {
      const next = new Set(current);
      if (pending) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });

  const run = useCallback(
    async (job: JobDefinition) => {
      setErrors((current) => omit(current, job.key));
      setPending(job.key, true);
      setItems((current) => optimisticRunning(current, job, new Date().toISOString()));
      try {
        await apiRequest(job.endpoint, {
          fallback: `Could not start ${job.title.toLowerCase()}`,
          method: "POST",
          params: jobParams(job),
        });
      } catch (error) {
        setErrors((current) => ({ ...current, [job.key]: errorMessage(error, "Could not start the job.") }));
      } finally {
        setPending(job.key, false);
        await refresh();
      }
    },
    [refresh],
  );

  const cancel = useCallback(
    async (job: JobDefinition) => {
      setErrors((current) => omit(current, job.key));
      setItems((current) => optimisticStopping(current, job.key));
      try {
        const operation = await apiRequest<OperationStatus>(
          `/operations/${encodeURIComponent(job.key)}/cancel`,
          { fallback: "Cancel failed", method: "POST" },
        );
        setItems((current) => upsertOperation(current, operation));
      } catch (error) {
        setErrors((current) => ({ ...current, [job.key]: errorMessage(error, "Cancel failed.") }));
      } finally {
        await refresh();
      }
    },
    [refresh],
  );

  const byKey = useMemo(() => new Map(items.map((item) => [item.key, item])), [items]);

  return (
    <Panel id="jobs" title={title} meta={anyRunning ? "running" : undefined}>
      <ul className="divide-y divide-line">
        {MANUAL_JOBS.map((job) => (
          <JobRow
            key={job.key}
            error={errors[job.key]}
            isStarting={pendingKeys.has(job.key)}
            job={job}
            onCancel={() => void cancel(job)}
            onRun={() => void run(job)}
            operation={byKey.get(job.key)}
          />
        ))}
      </ul>
    </Panel>
  );
}

function JobRow({
  error,
  isStarting,
  job,
  onCancel,
  onRun,
  operation,
}: {
  error?: string;
  isStarting: boolean;
  job: JobDefinition;
  onCancel: () => void;
  onRun: () => void;
  operation?: OperationStatus;
}) {
  const running = operation?.status === "running" || isStarting;
  const stopping = operationIsStopping(operation);
  const progress = operationProgress(operation);
  const summary = operation ? job.summary(operation.payload) : null;
  const statusLabel = operationStatusLabel(operation);
  const tone = stopping ? "warning" : statusTone(operation?.status);
  const title = operation?.label ?? job.title;

  return (
    <li className="px-3.5 py-2.5">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5">
            <p className="text-sm font-medium text-fg">{title}</p>
            <StatusDot
              tone={tone}
              pulse={running && !stopping}
              label={
                <span className="text-xs text-fg-2">
                  {statusLabel}
                  {running && progress.percent !== null ? ` ${progress.percent}%` : ""}
                </span>
              }
            />
          </div>
          <p className="mt-0.5 text-xs text-fg-3">
            {running
              ? [progress.stageLabel, progress.stageDetail].filter(Boolean).join(": ") || job.description
              : (summary ?? job.description)}
          </p>
          {!running && operation ? (
            <p className="mt-0.5 text-2xs text-fg-4">
              {operation.status === "failed" ? "Failed " : "Finished "}
              <TimeAgo value={operation.completedAt ?? operation.updatedAt} />
              {operation.durationMs !== null ? ` in ${formatDuration(operation.durationMs)}` : ""}
              {operation.lastSuccessAt && operation.status !== "succeeded" ? (
                <>
                  , last success <TimeAgo value={operation.lastSuccessAt} />
                </>
              ) : null}
            </p>
          ) : null}
          {operation?.lastError ? (
            <p className="mt-1 line-clamp-2 text-xs text-negative" title={operation.lastError}>
              {operation.lastError}
            </p>
          ) : null}
          {error ? <p className="mt-1 text-xs text-negative">{error}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {running ? (
            <button
              type="button"
              onClick={onCancel}
              disabled={stopping || isStarting}
              aria-label={`Cancel ${title}`}
              className={buttonClass({ size: "sm", variant: "danger" })}
            >
              {stopping ? <Spinner /> : <Square className="h-3 w-3" aria-hidden="true" />}
              {stopping ? "Stopping" : "Cancel"}
            </button>
          ) : (
            <button
              type="button"
              onClick={onRun}
              aria-label={`Run ${title}`}
              className={buttonClass({ size: "sm" })}
            >
              <Play className="h-3 w-3" aria-hidden="true" />
              Run
            </button>
          )}
        </div>
      </div>
      {running ? (
        <div className="mt-2">
          {progress.percent !== null ? (
            <div
              role="progressbar"
              aria-label={`${title} progress`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.percent}
            >
              <Meter value={Math.max(progress.percent, 2) / 100} tone={stopping ? "warning" : "info"} />
            </div>
          ) : (
            <div className="relative h-1 overflow-hidden rounded-full bg-surface-3">
              <div className="animate-indeterminate absolute inset-y-0 w-1/3 rounded-full bg-info" />
            </div>
          )}
        </div>
      ) : null}
    </li>
  );
}

function jobParams(job: JobDefinition): Record<string, string> {
  if (job.key !== "pool_fill_import") {
    return job.params ?? {};
  }
  return {
    ...job.params,
    limit: String(clampInteger(frontendConfig.poolReimportBatchLimit, 1, 100)),
    max_batches: String(clampInteger(frontendConfig.poolReimportMaxBatches, 1, 1000)),
  };
}

function clampInteger(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

function omit(current: Record<string, string>, key: string) {
  const next = { ...current };
  delete next[key];
  return next;
}
