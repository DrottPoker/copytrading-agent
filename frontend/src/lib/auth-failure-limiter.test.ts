// @vitest-environment node
import { describe, expect, it } from "vitest";

import { AuthFailureLimiter, authClientKey } from "./auth-failure-limiter";

function limiter(maxTrackedClients?: number) {
  return new AuthFailureLimiter({
    maxFailures: 3,
    windowMs: 60_000,
    lockoutMs: 120_000,
    maxTrackedClients,
  });
}

describe("AuthFailureLimiter", () => {
  it("locks a client out after the configured number of failures", () => {
    const subject = limiter();

    expect(subject.recordFailure("1.1.1.1", 0)).toBeNull();
    expect(subject.recordFailure("1.1.1.1", 1_000)).toBeNull();
    expect(subject.recordFailure("1.1.1.1", 2_000)).toBe(120);
    expect(subject.retryAfterSeconds("1.1.1.1", 62_000)).toBe(60);
    expect(subject.retryAfterSeconds("2.2.2.2", 62_000)).toBeNull();
  });

  it("releases the lockout once it expires and starts a fresh budget", () => {
    const subject = limiter();
    for (const now of [0, 1, 2]) {
      subject.recordFailure("client", now);
    }

    expect(subject.retryAfterSeconds("client", 122_002)).toBeNull();
    expect(subject.recordFailure("client", 122_003)).toBeNull();
  });

  it("forgets failures that fall outside the window", () => {
    const subject = limiter();
    subject.recordFailure("client", 0);
    subject.recordFailure("client", 1_000);

    expect(subject.recordFailure("client", 61_000)).toBeNull();
    expect(subject.retryAfterSeconds("client", 61_000)).toBeNull();
  });

  it("clears failures after a successful login", () => {
    const subject = limiter();
    subject.recordFailure("client", 0);
    subject.recordFailure("client", 1);
    subject.recordSuccess("client");

    expect(subject.recordFailure("client", 2)).toBeNull();
  });

  it("bounds the number of tracked clients", () => {
    const subject = limiter(2);
    subject.recordFailure("a", 0);
    subject.recordFailure("b", 1);
    subject.recordFailure("c", 2);

    expect(subject.trackedClientCount).toBe(2);
  });
});

describe("authClientKey", () => {
  it("prefers the proxy supplied real IP", () => {
    const headers = new Headers({ "x-real-ip": "203.0.113.7", "x-forwarded-for": "10.0.0.1" });
    expect(authClientKey(headers)).toBe("203.0.113.7");
  });

  it("falls back to the first forwarded address", () => {
    const headers = new Headers({ "x-forwarded-for": "198.51.100.1, 10.0.0.1" });
    expect(authClientKey(headers)).toBe("198.51.100.1");
  });

  it("uses a shared key without proxy headers", () => {
    expect(authClientKey(new Headers())).toBe("unknown");
  });
});
