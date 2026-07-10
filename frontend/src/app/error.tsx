"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <section className="ui-panel mx-auto w-full max-w-2xl p-6 sm:p-8">
      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-danger-soft text-danger">
        <AlertTriangle className="h-5 w-5" aria-hidden="true" />
      </span>
      <p className="mt-5 text-[11px] font-semibold uppercase tracking-[0.08em] text-danger">
        Dashboard error
      </p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">
        This view could not be loaded
      </h1>
      <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
        The backend may be unavailable or the requested data may be invalid. Retry the view after
        checking the system status.
      </p>
      {error.digest ? (
        <p className="mt-3 font-mono text-xs text-faint">Reference {error.digest}</p>
      ) : null}
      <button type="button" onClick={reset} className="ui-button-primary mt-5">
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
        Try again
      </button>
    </section>
  );
}
