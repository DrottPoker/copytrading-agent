import { RadioTower } from "lucide-react";

import { HeaderRefreshButton, HeaderUpdatedLabel } from "@/components/HeaderRefresh";
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
              <HeaderUpdatedLabel label={`Updated ${formatDate(events.items[0].createdAt)}`} />
            ) : null}
            <StatusPill label={`${events.total} stored events`} tone="neutral" />
          </>
        }
        refresh={<HeaderRefreshButton title="Refresh live feed data" />}
      />

      <LiveFeed initialEvents={events.items} />
    </>
  );
}
