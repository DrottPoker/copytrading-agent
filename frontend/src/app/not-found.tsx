import { ArrowLeft, SearchX } from "lucide-react";
import Link from "next/link";

export default function NotFoundPage() {
  return (
    <section className="ui-panel mx-auto w-full max-w-2xl p-6 sm:p-8">
      <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-subtle text-muted">
        <SearchX className="h-5 w-5" aria-hidden="true" />
      </span>
      <p className="mt-5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
        Not found
      </p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight text-ink">
        The requested resource does not exist
      </h1>
      <p className="mt-2 max-w-xl text-sm leading-6 text-muted">
        It may have been removed, archived, or requested with an invalid identifier.
      </p>
      <Link href="/" className="ui-button-secondary mt-5">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Back to overview
      </Link>
    </section>
  );
}
