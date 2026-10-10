// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthFailureLimiter } from "./auth-failure-limiter";
import { createDashboardAuthMiddleware } from "./dashboard-auth-middleware";

const productionEnv = {
  APP_ENV: "production",
  DASHBOARD_AUTH_USERNAME: "owner",
  DASHBOARD_AUTH_PASSWORD: "correct horse",
};

function request(authorization?: string, method = "GET") {
  const headers = new Headers({ "x-real-ip": "203.0.113.7" });
  if (authorization !== undefined) {
    headers.set("authorization", authorization);
  }
  return new NextRequest("https://copy.example.com/trading", { method, headers });
}

function basic(username: string, password: string) {
  return `Basic ${Buffer.from(`${username}:${password}`, "utf8").toString("base64")}`;
}

function middleware({
  authEnabled = true,
  env = productionEnv,
}: { authEnabled?: boolean; env?: Record<string, string | undefined> } = {}) {
  return createDashboardAuthMiddleware({
    authEnabled,
    env,
    limiter: new AuthFailureLimiter({ maxFailures: 2, windowMs: 60_000, lockoutMs: 60_000 }),
    now: () => 0,
  });
}

function passedThrough(response: Response) {
  return response.headers.get("x-middleware-next") === "1";
}

describe("dashboard auth middleware", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("challenges a request without credentials", () => {
    const response = middleware()(request());

    expect(response.status).toBe(401);
    expect(response.headers.get("WWW-Authenticate")).toContain("Basic");
  });

  it("passes valid credentials through", () => {
    expect(passedThrough(middleware()(request(basic("owner", "correct horse"))))).toBe(true);
  });

  it("locks the client out after repeated failures, even with valid credentials", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const subject = middleware();

    expect(subject(request(basic("owner", "wrong"))).status).toBe(401);
    const lockedOut = subject(request(basic("owner", "wrong")));
    expect(lockedOut.status).toBe(429);
    expect(lockedOut.headers.get("Retry-After")).toBe("60");
    expect(subject(request(basic("owner", "correct horse"))).status).toBe(429);
  });

  it("counts malformed credentials as failures but not missing credentials", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const subject = middleware();

    expect(subject(request()).status).toBe(401);
    expect(subject(request()).status).toBe(401);
    expect(subject(request("Basic !!!")).status).toBe(401);
    expect(subject(request("Basic !!!")).status).toBe(429);
  });

  it("fails closed when production credentials are unsafe", () => {
    const errorMock = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const subject = middleware({ env: { ...productionEnv, DASHBOARD_AUTH_PASSWORD: "short" } });

    expect(subject(request(basic("owner", "short"))).status).toBe(503);
    expect(subject(request()).status).toBe(503);
    expect(errorMock).toHaveBeenCalledOnce();
  });

  it("fails closed when auth is disabled in production", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(middleware({ authEnabled: false })(request()).status).toBe(503);
  });

  it("allows disabled auth and preflight requests in development", () => {
    expect(passedThrough(middleware({ authEnabled: false, env: {} })(request()))).toBe(true);
    expect(passedThrough(middleware({ env: {} })(request(undefined, "OPTIONS")))).toBe(true);
  });
});
