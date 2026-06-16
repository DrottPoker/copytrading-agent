"use client";

import { RadioTower } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { frontendConfig } from "@/lib/config";
import { getPublicApiBaseUrl } from "@/lib/api";
import type { LiveEvent } from "@/types/event";

import { StatusPill } from "./StatusPill";

export function LiveFeed({ initialEvents }: { initialEvents: LiveEvent[] }) {
  const [events, setEvents] = useState<LiveEvent[]>(initialEvents);
  const [connectionState, setConnectionState] = useState<"connecting" | "live" | "offline">(
    "connecting",
  );

  useEffect(() => {
    let isMounted = true;
    const apiBaseUrl = getPublicApiBaseUrl();

    async function pollRecentEvents() {
      try {
        const response = await fetch(`${apiBaseUrl}/events/recent?limit=100`, {
          cache: "no-store",
        });
        if (!response.ok) {
          setConnectionState("offline");
          return;
        }
        const payload = (await response.json()) as { items?: LiveEvent[] };
        if (!isMounted) {
          return;
        }
        setEvents((currentEvents) =>
          dedupeEvents([...(payload.items ?? []), ...currentEvents]).slice(0, 100),
        );
        setConnectionState("live");
      } catch {
        if (isMounted) {
          setConnectionState("offline");
        }
      }
    }

    if (typeof EventSource === "undefined") {
      void pollRecentEvents();
      const interval = window.setInterval(() => {
        void pollRecentEvents();
      }, frontendConfig.liveFeedPollMs);
      return () => {
        isMounted = false;
        window.clearInterval(interval);
      };
    }

    const source = new EventSource(`${apiBaseUrl}/events`);
    source.onopen = () => setConnectionState("live");
    source.onerror = () => setConnectionState("offline");
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as LiveEvent;
        setEvents((currentEvents) => dedupeEvents([event, ...currentEvents]).slice(0, 100));
      } catch {
        setConnectionState("offline");
      }
    };

    return () => {
      isMounted = false;
      source.close();
    };
  }, []);

  const connectionTone = connectionState === "live" ? "positive" : "warning";
  const newestEventAt = useMemo(() => events[0]?.createdAt ?? null, [events]);

  return (
    <section className="overflow-hidden rounded-lg border border-line bg-panel">
      <div className="flex flex-col gap-3 border-b border-line px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <RadioTower className="h-4 w-4 text-[#526070]" aria-hidden="true" />
          <h2 className="text-base font-semibold">Live Feed</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill label={connectionState} tone={connectionTone} />
          <StatusPill label={`${events.length} events`} tone="neutral" />
          {newestEventAt ? <StatusPill label={formatDate(newestEventAt)} tone="neutral" /> : null}
        </div>
      </div>

      <div className="divide-y divide-line">
        {events.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-[#526070]">No events yet.</div>
        ) : (
          events.map((event, index) => (
            <article
              key={event.id ?? `${event.type}-${event.createdAt ?? index}-${index}`}
              className="grid gap-3 px-4 py-3 md:grid-cols-[160px_1fr_220px]"
            >
              <div className="flex flex-wrap items-start gap-2">
                <StatusPill label={event.type} tone={event.type === "fill" ? "positive" : "neutral"} />
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{event.message}</p>
                <p className="mt-1 truncate font-mono text-xs text-[#526070]">{event.channel}</p>
                <PayloadSummary event={event} />
              </div>
              <time className="text-left text-xs text-[#526070] md:text-right">
                {event.createdAt ? formatDate(event.createdAt) : "-"}
              </time>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

function PayloadSummary({ event }: { event: LiveEvent }) {
  const walletAddress = stringPayload(event.payload.walletAddress);
  const fill = recordPayload(event.payload.fill);
  if (fill) {
    return (
      <p className="mt-2 font-mono text-xs text-[#526070]">
        {walletAddress ? `${shortAddress(walletAddress)} ` : ""}
        {stringPayload(fill.coin)} {stringPayload(fill.side)} size {stringPayload(fill.size)}
      </p>
    );
  }

  if (walletAddress) {
    return <p className="mt-2 font-mono text-xs text-[#526070]">{shortAddress(walletAddress)}</p>;
  }

  return null;
}

function dedupeEvents(events: LiveEvent[]) {
  const seen = new Set<string>();
  const deduped: LiveEvent[] = [];
  for (const event of events) {
    const key = event.id ?? `${event.type}:${event.createdAt}:${event.message}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(event);
  }
  return deduped;
}

function recordPayload(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringPayload(value: unknown): string | null {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    return `${value}`;
  }
  return null;
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}...${address.slice(-6)}`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("sv-SE", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}
