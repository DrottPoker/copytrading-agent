"use client";

import { ChevronRight, Pause, Play, Search } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";

import { buttonClass, EmptyState, StatusDot } from "@/components/ui/primitives";
import { cx } from "@/lib/cx";
import { formatInteger, formatTime, formatTimestamp, shortAddress } from "@/lib/format";
import { frontendConfig } from "@/lib/config";
import {
  dedupeEvents,
  EVENT_CATEGORY_LABELS,
  eventCategory,
  eventKey,
  eventTone,
  eventTypeLabel,
  eventWallet,
  type EventCategory,
} from "@/lib/events";
import { apiUrl } from "@/lib/http";
import type { LiveEvent } from "@/types/event";

const MAX_EVENTS = 300;

export type ConnectionState = "connecting" | "live" | "polling" | "offline";

const CONNECTION_LABELS: Record<ConnectionState, string> = {
  connecting: "Connecting",
  live: "Live",
  offline: "Offline, retrying",
  polling: "Reconnecting, polling meanwhile",
};

/**
  Streams events over server-sent events. While the stream is down it polls the recent events
  endpoint, and the browser reconnects the stream on its own.
*/
export function useEventStream(initialEvents: LiveEvent[]) {
  const [events, setEvents] = useState(initialEvents);
  const [connection, setConnection] = useState<ConnectionState>("connecting");

  useEffect(() => {
    let mounted = true;
    let pollTimer: number | null = null;
    let polling = false;

    const pollOnce = async () => {
      try {
        const response = await fetch(apiUrl("/events/recent", { limit: 100 }), { cache: "no-store" });
        if (!mounted || !polling) {
          return;
        }
        if (!response.ok) {
          setConnection("offline");
          return;
        }
        const payload = (await response.json()) as { items?: LiveEvent[] };
        if (!mounted || !polling) {
          return;
        }
        setEvents((current) => dedupeEvents([...(payload.items ?? []), ...current]).slice(0, MAX_EVENTS));
        setConnection("polling");
      } catch {
        if (mounted && polling) {
          setConnection("offline");
        }
      }
    };

    const startPolling = () => {
      if (polling) {
        return;
      }
      polling = true;
      void pollOnce();
      pollTimer = window.setInterval(() => void pollOnce(), frontendConfig.liveFeedPollMs);
    };

    const stopPolling = () => {
      polling = false;
      if (pollTimer !== null) {
        window.clearInterval(pollTimer);
        pollTimer = null;
      }
    };

    if (typeof EventSource === "undefined") {
      startPolling();
      return () => {
        mounted = false;
        stopPolling();
      };
    }

    const source = new EventSource(apiUrl("/events"));
    source.onopen = () => {
      if (mounted) {
        stopPolling();
        setConnection("live");
      }
    };
    source.onerror = () => {
      if (mounted) {
        // Keep the stream object: the browser reconnects with the last event id.
        setConnection("offline");
        startPolling();
      }
    };
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as LiveEvent;
        setEvents((current) => dedupeEvents([event, ...current]).slice(0, MAX_EVENTS));
      } catch {
        // Ignore malformed messages; the next one may be fine.
      }
    };

    return () => {
      mounted = false;
      source.close();
      stopPolling();
    };
  }, []);

  return { connection, events };
}

export function ActivityFeed({ initialEvents }: { initialEvents: LiveEvent[] }) {
  const { connection, events } = useEventStream(initialEvents);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<LiveEvent[] | null>(null);
  const [category, setCategory] = useState<EventCategory | "all">("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const shown = paused && frozen ? frozen : events;
  const pendingCount = paused && frozen ? Math.max(0, events.length - frozen.length) : 0;

  const counts = useMemo(() => {
    const result: Record<EventCategory | "all", number> = { all: shown.length, copy: 0, job: 0, risk: 0, system: 0, trade: 0 };
    for (const event of shown) {
      result[eventCategory(event)] += 1;
    }
    return result;
  }, [shown]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return shown.filter(
      (event) =>
        (category === "all" || eventCategory(event) === category) &&
        (!needle ||
          event.message.toLowerCase().includes(needle) ||
          event.type.toLowerCase().includes(needle) ||
          JSON.stringify(event.payload).toLowerCase().includes(needle)),
    );
  }, [category, query, shown]);

  const togglePause = () => {
    if (paused) {
      setPaused(false);
      setFrozen(null);
    } else {
      setFrozen(events);
      setPaused(true);
    }
  };

  const categories: Array<EventCategory | "all"> = ["all", "trade", "copy", "job", "risk", "system"];

  return (
    <section className="min-w-0 overflow-hidden rounded-lg border border-line bg-surface">
      <div className="flex flex-col gap-2 border-b border-line px-3.5 py-2.5 lg:flex-row lg:items-center">
        <StatusDot
          tone={connection === "live" ? "positive" : connection === "offline" ? "negative" : "warning"}
          pulse={connection === "live" && !paused}
          label={<span className="text-sm">{paused ? "Paused" : CONNECTION_LABELS[connection]}</span>}
        />
        <div className="flex flex-wrap gap-1 lg:ml-3" role="group" aria-label="Filter events">
          {categories.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={category === item}
              onClick={() => setCategory(item)}
              className={cx(
                "inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium",
                category === item ? "bg-fg text-canvas" : "text-fg-2 hover:bg-surface-3",
              )}
            >
              {item === "all" ? "All" : EVENT_CATEGORY_LABELS[item]}
              <span className={cx("tabular-nums", category === item ? "opacity-70" : "text-fg-4")}>{counts[item]}</span>
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 lg:ml-auto">
          <label className="relative block lg:w-56">
            <span className="sr-only">Search events</span>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-4" aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search messages and payloads"
              className="field h-7 w-full pl-8 text-xs"
            />
          </label>
          <button type="button" onClick={togglePause} className={buttonClass({ size: "sm" })}>
            {paused ? <Play className="h-3 w-3" aria-hidden="true" /> : <Pause className="h-3 w-3" aria-hidden="true" />}
            {paused ? `Resume${pendingCount > 0 ? ` (${formatInteger(pendingCount)} new)` : ""}` : "Pause"}
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title={shown.length === 0 ? "No events yet" : "No events match"}>
          {shown.length === 0 ? "Events appear here as the workers process trades, jobs, and risk checks." : "Try another filter."}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto">
          <table className="data-table data-table-compact">
            <thead>
              <tr>
                <th className="w-6" aria-label="Details" />
                <th>Time</th>
                <th>Event</th>
                <th>Message</th>
                <th>Wallet</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((event, index) => {
                const key = eventKey(event, index);
                const wallet = eventWallet(event);
                const open = expanded === key;
                return (
                  <Fragment key={key}>
                    <tr className="cursor-pointer" onClick={() => setExpanded(open ? null : key)}>
                      <td className="text-fg-4">
                        <button
                          type="button"
                          aria-expanded={open}
                          aria-label={open ? "Hide event details" : "Show event details"}
                          onClick={(clickEvent) => {
                            clickEvent.stopPropagation();
                            setExpanded(open ? null : key);
                          }}
                          className="inline-flex h-5 w-5 items-center justify-center rounded hover:bg-surface-3"
                        >
                          <ChevronRight className={cx("h-3.5 w-3.5 transition-transform", open && "rotate-90")} aria-hidden="true" />
                        </button>
                      </td>
                      <td className="tabular-nums text-fg-2" title={formatTimestamp(event.createdAt)}>
                        {formatTime(event.createdAt)}
                      </td>
                      <td>
                        <StatusDot tone={eventTone(event)} label={eventTypeLabel(event.type)} />
                      </td>
                      <td className="wrap min-w-[20rem]">{event.message}</td>
                      <td>
                        {wallet ? (
                          <Link
                            href={`/wallets/${wallet}`}
                            onClick={(clickEvent) => clickEvent.stopPropagation()}
                            className="font-mono text-xs text-fg-2 hover:underline"
                          >
                            {shortAddress(wallet)}
                          </Link>
                        ) : null}
                      </td>
                    </tr>
                    {open ? (
                      <tr>
                        <td />
                        <td colSpan={4} className="wrap bg-surface-2">
                          <p className="text-xs text-fg-3">
                            {event.channel} | {event.type} | {formatTimestamp(event.createdAt)}
                          </p>
                          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all font-mono text-xs text-fg-2">
                            {JSON.stringify(event.payload, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="border-t border-line px-3.5 py-2 text-xs text-fg-3">
        Showing {formatInteger(filtered.length)} of the latest {formatInteger(shown.length)} events kept in this tab.
      </p>
    </section>
  );
}
