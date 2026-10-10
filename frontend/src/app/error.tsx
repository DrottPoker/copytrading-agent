"use client";

import { RefreshCw } from "lucide-react";
import { useEffect } from "react";

import { buttonClass, LinkButton } from "@/components/ui/primitives";

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
    <section className="mx-auto mt-10 w-full max-w-lg rounded-lg border border-line border-l-2 border-l-negative bg-surface px-6 py-8">
      <p className="text-xs font-medium text-negative">Something went wrong</p>
      <h1 className="mt-1 text-xl font-semibold">This page could not be loaded</h1>
      <p className="mt-2 text-sm text-fg-3">
        The backend may be down or returned data the dashboard did not expect. The status bar at the top shows whether
        the API, Postgres, and Redis respond.
      </p>
      {error.digest ? <p className="mt-3 font-mono text-xs text-fg-4">Reference {error.digest}</p> : null}
      <div className="mt-5 flex gap-2">
        <button type="button" onClick={reset} className={buttonClass({ variant: "primary" })}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          Try again
        </button>
        <LinkButton href="/system">Open System</LinkButton>
      </div>
    </section>
  );
}
