export default function Loading() {
  return (
    <div className="grid gap-5" aria-busy="true" aria-live="polite">
      <div className="rounded-lg border border-line bg-panel p-5 shadow-sm">
        <div className="h-3 w-36 animate-pulse rounded bg-[#dce4ea]" />
        <div className="mt-4 h-8 w-full max-w-md animate-pulse rounded bg-[#dce4ea]" />
        <div className="mt-5 flex flex-wrap gap-2">
          <div className="h-7 w-24 animate-pulse rounded-md bg-[#dce4ea]" />
          <div className="h-7 w-28 animate-pulse rounded-md bg-[#dce4ea]" />
          <div className="h-7 w-20 animate-pulse rounded-md bg-[#dce4ea]" />
        </div>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="rounded-lg border border-line bg-panel p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <div className="h-3 w-24 animate-pulse rounded bg-[#dce4ea]" />
                <div className="mt-3 h-7 w-32 animate-pulse rounded bg-[#dce4ea]" />
              </div>
              <div className="h-5 w-5 animate-pulse rounded bg-[#dce4ea]" />
            </div>
            <div className="mt-4 h-4 w-40 animate-pulse rounded bg-[#dce4ea]" />
          </div>
        ))}
      </section>

      <section className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        {Array.from({ length: 2 }).map((_, index) => (
          <div key={index} className="rounded-lg border border-line bg-panel shadow-sm">
            <div className="border-b border-line px-4 py-3">
              <div className="h-5 w-40 animate-pulse rounded bg-[#dce4ea]" />
            </div>
            <div className="grid gap-3 p-4">
              <div className="h-12 animate-pulse rounded-md bg-[#dce4ea]" />
              <div className="h-12 animate-pulse rounded-md bg-[#dce4ea]" />
              <div className="h-12 animate-pulse rounded-md bg-[#dce4ea]" />
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
