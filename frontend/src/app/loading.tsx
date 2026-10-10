export default function Loading() {
  return (
    <div className="grid gap-4" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading</span>
      <div>
        <div className="h-6 w-40 animate-pulse rounded bg-surface-3" />
        <div className="mt-2 h-4 w-full max-w-xl animate-pulse rounded bg-surface-3" />
      </div>
      <div className="h-[4.625rem] animate-pulse rounded-lg border border-line bg-surface" />
      <div className="grid gap-4 xl:grid-cols-2">
        {[0, 1].map((index) => (
          <div key={index} className="rounded-lg border border-line bg-surface">
            <div className="h-10 border-b border-line" />
            <div className="grid gap-2 p-3.5">
              {[0, 1, 2, 3].map((row) => (
                <div key={row} className="h-5 animate-pulse rounded bg-surface-3" />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
