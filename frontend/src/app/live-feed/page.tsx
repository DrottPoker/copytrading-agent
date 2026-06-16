import { RadioTower } from "lucide-react";

import { LiveFeed } from "@/components/LiveFeed";
import { StatusPill } from "@/components/StatusPill";
import { getLiveEvents } from "@/lib/api";

export default async function LiveFeedPage() {
  const events = await getLiveEvents();

  return (
    <>
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-sm font-medium text-[#5b6770]">Realtime monitor</p>
          <h1 className="mt-1 flex items-center gap-2 text-2xl font-semibold tracking-normal">
            <RadioTower className="h-6 w-6 text-[#5b6770]" aria-hidden="true" />
            Live Feed
          </h1>
        </div>
        <StatusPill label={`${events.total} stored events`} tone="neutral" />
      </header>

      <LiveFeed initialEvents={events.items} />
    </>
  );
}
