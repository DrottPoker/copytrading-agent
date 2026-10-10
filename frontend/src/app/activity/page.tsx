import type { Metadata } from "next";

import { ActivityFeed } from "@/components/ActivityFeed";
import { PageHeader } from "@/components/ui/primitives";
import { getLiveEvents } from "@/lib/api";
import { formatInteger } from "@/lib/format";

export const metadata: Metadata = { title: "Activity" };
export const dynamic = "force-dynamic";

export default async function ActivityPage() {
  const events = await getLiveEvents(100);
  return (
    <>
      <PageHeader
        title="Activity"
        description={`Everything the workers do as it happens: source trades, copies, jobs, and risk checks. ${formatInteger(events.total)} events are stored on the server.`}
      />
      <ActivityFeed initialEvents={events.items} />
    </>
  );
}
