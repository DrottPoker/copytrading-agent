import { RadioTower } from "lucide-react";

import { HeaderRefresh } from "@/components/HeaderRefresh";
import { LiveFeed } from "@/components/LiveFeed";
import { PageTopPanel } from "@/components/PageTopPanel";
import { StatusPill } from "@/components/StatusPill";
import { getLiveEvents } from "@/lib/api";
import { formatDate } from "@/lib/format";

export default async function LiveFeedPage() {
  const events = await getLiveEvents();

  return (
    <>
      <PageTopPanel
        eyebrow="Realtime monitor"
        icon={RadioTower}
        title="Live Feed"
        actions={
          <>
            {events.items[0]?.createdAt ? (
              <HeaderRefresh
                label={`Updated ${formatDate(events.items[0].createdAt)}`}
                title="Refresh live feed data"
              />
            ) : null}
            <StatusPill label={`${events.total} stored events`} tone="neutral" />
          </>
        }
      />

      <LiveFeed initialEvents={events.items} />
    </>
  );
}
