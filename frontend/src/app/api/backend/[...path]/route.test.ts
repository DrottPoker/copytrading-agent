import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

function dashboardMutation(origin: string) {
  return new Request("http://frontend:3000/api/backend/trading/accounts/live/start", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Origin: origin,
      "X-Forwarded-Host": "copy.example.com",
      "X-Forwarded-Proto": "https",
    },
    body: "{}",
  });
}

describe("backend proxy mutation origin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("forwards an allowed public dashboard mutation with an internal upstream origin", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      Response.json({ status: "ok" }),
    );

    const response = await POST(dashboardMutation("https://copy.example.com"), {
      params: Promise.resolve({ path: ["trading", "accounts", "live", "start"] }),
    });

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [upstreamInput, requestInit] = fetchMock.mock.calls[0];
    const upstreamUrl = new URL(String(upstreamInput));
    const upstreamHeaders = new Headers(requestInit?.headers);
    expect(upstreamHeaders.get("Origin")).toBe(upstreamUrl.origin);
    expect(upstreamHeaders.get("X-Forwarded-Host")).toBe(upstreamUrl.host);
    expect(upstreamHeaders.get("X-Forwarded-Proto")).toBe(
      upstreamUrl.protocol.replace(/:$/, ""),
    );
  });

  it("rejects a foreign origin before contacting the backend", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");

    const response = await POST(dashboardMutation("https://evil.example"), {
      params: Promise.resolve({ path: ["wallets", "prune-all"] }),
    });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toEqual({
      detail: "Cross-origin mutation request rejected.",
    });
  });
});
