export default function Loading() {
  return (
    <div className="grid gap-4" aria-busy="true" aria-live="polite">
      <div className="border-b border-line pb-4">
        <div className="h-2.5 w-36 animate-pulse rounded bg-line-strong" />
        <div className="mt-3 h-7 w-full max-w-sm animate-pulse rounded-md bg-line" />
        <div className="mt-5 flex flex-wrap gap-2">
          <div className="h-6 w-24 animate-pulse rounded-full bg-line" />
          <div className="h-6 w-28 animate-pulse rounded-full bg-line" />
          <div className="h-6 w-20 animate-pulse rounded-full bg-line" />
        </div>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="ui-metric">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="h-2.5 w-24 animate-pulse rounded bg-line" />
                <div className="mt-3 h-7 w-32 animate-pulse rounded bg-line" />
              </div>
              <div className="h-8 w-8 animate-pulse rounded-lg bg-line" />
            </div>
            <div className="mt-3 h-3 w-40 animate-pulse rounded bg-line" />
          </div>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        {Array.from({ length: 2 }).map((_, index) => (
          <div key={index} className="ui-panel">
            <div className="ui-panel-header">
              <div className="h-4 w-40 animate-pulse rounded bg-line" />
            </div>
            <div className="grid gap-3 p-4">
              <div className="h-11 animate-pulse rounded-lg bg-line" />
              <div className="h-11 animate-pulse rounded-lg bg-line" />
              <div className="h-11 animate-pulse rounded-lg bg-line" />
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
