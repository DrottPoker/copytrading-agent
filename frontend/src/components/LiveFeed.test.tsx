import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { frontendConfig } from "@/lib/config";

import { LiveFeed } from "./LiveFeed";

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

describe("LiveFeed", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps EventSource open for cursor-based reconnect and stops fallback polling on reopen", () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => undefined));
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<LiveFeed initialEvents={[]} />);
    const source = FakeEventSource.instances[0];

    act(() => source.onerror?.(new Event("error")));

    expect(source.close).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("offline")).toBeInTheDocument();

    act(() => source.onopen?.(new Event("open")));
    act(() => vi.advanceTimersByTime(frontendConfig.liveFeedPollMs * 2));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText("live")).toBeInTheDocument();

    unmount();
    expect(source.close).toHaveBeenCalledTimes(1);
  });
});
