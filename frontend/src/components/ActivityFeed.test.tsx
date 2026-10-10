import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { frontendConfig } from "@/lib/config";
import type { LiveEvent } from "@/types/event";

import { ActivityFeed } from "./ActivityFeed";

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  readonly close = vi.fn();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
}

const events: LiveEvent[] = [
  {
    channel: "events:fills",
    createdAt: "2026-01-01T00:00:02Z",
    id: "2",
    message: "Paper copied 2 fills from 0xabc.",
    payload: { walletAddress: "0x1234567890abcdef1234567890abcdef12345678" },
    type: "paper_copy",
  },
  {
    channel: "events:system",
    createdAt: "2026-01-01T00:00:01Z",
    id: "1",
    message: "Monitor worker started.",
    payload: {},
    type: "system",
  },
];

describe("ActivityFeed", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps the stream open for reconnects and stops fallback polling when it reopens", () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<ActivityFeed initialEvents={[]} />);
    const source = FakeEventSource.instances[0];

    act(() => source.onerror?.(new Event("error")));

    expect(source.close).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Offline, retrying")).toBeInTheDocument();

    act(() => source.onopen?.(new Event("open")));
    act(() => vi.advanceTimersByTime(frontendConfig.liveFeedPollMs * 2));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Live")).toBeInTheDocument();

    unmount();
    expect(source.close).toHaveBeenCalledTimes(1);
  });

  it("filters by category and freezes the list while paused", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    render(<ActivityFeed initialEvents={events} />);
    const source = FakeEventSource.instances[0];

    fireEvent.click(screen.getByRole("button", { name: /^Copy/ }));
    expect(screen.getByText("Paper copied 2 fills from 0xabc.")).toBeInTheDocument();
    expect(screen.queryByText("Monitor worker started.")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^All/ }));
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    act(() =>
      source.onmessage?.(
        new MessageEvent("message", {
          data: JSON.stringify({ ...events[1], id: "3", message: "New event while paused." }),
        }),
      ),
    );

    expect(screen.queryByText("New event while paused.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume (1 new)" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Resume (1 new)" }));
    expect(screen.getByText("New event while paused.")).toBeInTheDocument();
  });
});
