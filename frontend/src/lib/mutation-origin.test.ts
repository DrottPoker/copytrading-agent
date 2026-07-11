import { describe, expect, it } from "vitest";

import { browserMutationOriginIsAllowed, requestOrigins } from "./mutation-origin";

function proxiedRequest({
  method = "POST",
  origin,
  publicHost,
  publicProtocol,
}: {
  method?: string;
  origin?: string;
  publicHost?: string;
  publicProtocol?: string;
}) {
  const headers = new Headers();
  if (origin) {
    headers.set("Origin", origin);
  }
  if (publicHost) {
    headers.set("X-Forwarded-Host", publicHost);
  }
  if (publicProtocol) {
    headers.set("X-Forwarded-Proto", publicProtocol);
  }
  return new Request("http://frontend:3000/api/backend/trading/accounts/live/start", {
    method,
    headers,
  });
}

describe("browserMutationOriginIsAllowed", () => {
  it("accepts an IP-based HTTP dashboard behind Caddy", () => {
    const request = proxiedRequest({
      origin: "http://203.0.113.10",
      publicHost: "203.0.113.10",
      publicProtocol: "http",
    });

    expect(browserMutationOriginIsAllowed(request)).toBe(true);
  });

  it("accepts an HTTPS dashboard domain behind Caddy", () => {
    const request = proxiedRequest({
      origin: "https://copy.example.com",
      publicHost: "copy.example.com",
      publicProtocol: "https",
    });

    expect(browserMutationOriginIsAllowed(request)).toBe(true);
  });

  it("rejects a foreign browser origin", () => {
    const request = proxiedRequest({
      origin: "https://evil.example",
      publicHost: "copy.example.com",
      publicProtocol: "https",
    });

    expect(browserMutationOriginIsAllowed(request)).toBe(false);
  });

  it("keeps non-browser and read requests supported", () => {
    expect(browserMutationOriginIsAllowed(proxiedRequest({}))).toBe(true);
    expect(
      browserMutationOriginIsAllowed(
        proxiedRequest({ method: "GET", origin: "https://evil.example" }),
      ),
    ).toBe(true);
  });
});

describe("requestOrigins", () => {
  it("keeps the internal origin separate from the trusted public origin", () => {
    const request = proxiedRequest({
      origin: "https://copy.example.com",
      publicHost: "copy.example.com",
      publicProtocol: "https",
    });

    expect(requestOrigins(request)).toEqual(
      new Set(["http://frontend:3000", "https://copy.example.com"]),
    );
  });
});
