// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  basicAuthorizationHeader,
  configuredDashboardCredentials,
  dashboardAuthConfigurationError,
  dashboardCredentialsMatch,
  parseBasicAuthorization,
} from "./dashboard-auth";

function basicHeader(value: string) {
  return `Basic ${Buffer.from(value, "utf8").toString("base64")}`;
}

describe("configuredDashboardCredentials", () => {
  it("uses local defaults outside production", () => {
    expect(configuredDashboardCredentials({})).toEqual({
      username: "admin",
      password: "change-me",
    });
  });

  it("never falls back to defaults in production", () => {
    expect(configuredDashboardCredentials({ APP_ENV: "production" })).toEqual({
      username: "",
      password: "",
    });
  });
});

describe("dashboardAuthConfigurationError", () => {
  const production = {
    APP_ENV: "production",
    DASHBOARD_AUTH_USERNAME: "owner",
    DASHBOARD_AUTH_PASSWORD: "12345678",
  };

  it("accepts development defaults", () => {
    expect(dashboardAuthConfigurationError({ authEnabled: true, env: {} })).toBeNull();
    expect(dashboardAuthConfigurationError({ authEnabled: false, env: {} })).toBeNull();
  });

  it("accepts a configured production password of the minimum length", () => {
    expect(dashboardAuthConfigurationError({ authEnabled: true, env: production })).toBeNull();
  });

  it.each([
    [{ ...production, DASHBOARD_AUTH_PASSWORD: undefined }, "must be changed"],
    [{ ...production, DASHBOARD_AUTH_PASSWORD: "change-me" }, "must be changed"],
    [{ ...production, DASHBOARD_AUTH_PASSWORD: "1234567" }, "at least 8 characters"],
    [{ ...production, DASHBOARD_AUTH_USERNAME: " " }, "USERNAME must not be empty"],
  ])("rejects unsafe production credentials %#", (env, message) => {
    expect(dashboardAuthConfigurationError({ authEnabled: true, env })).toContain(message);
  });

  it("counts characters instead of UTF-16 code units", () => {
    const env = { ...production, DASHBOARD_AUTH_PASSWORD: "🔒🔒🔒🔒" };
    expect(dashboardAuthConfigurationError({ authEnabled: true, env })).toContain(
      "at least 8 characters",
    );
  });

  it("rejects disabled auth in production", () => {
    expect(dashboardAuthConfigurationError({ authEnabled: false, env: production })).toContain(
      "must be enabled",
    );
  });
});

describe("parseBasicAuthorization", () => {
  it("decodes UTF-8 credentials and keeps colons in the password", () => {
    expect(parseBasicAuthorization(basicHeader("åsa:lösen:ord"))).toEqual({
      username: "åsa",
      password: "lösen:ord",
    });
  });

  it("accepts a case-insensitive scheme", () => {
    expect(parseBasicAuthorization(basicHeader("a:b").replace("Basic", "bAsIc"))).toEqual({
      username: "a",
      password: "b",
    });
  });

  it.each([
    null,
    "",
    "Basic",
    "Bearer YTpi",
    "Basic not-base64!",
    basicHeader("missing-separator"),
    `Basic ${Buffer.from([0xff, 0x3a, 0x61]).toString("base64")}`,
  ])("rejects malformed header %#", (header) => {
    expect(parseBasicAuthorization(header)).toBeNull();
  });
});

describe("dashboardCredentialsMatch", () => {
  const expected = { username: "owner", password: "correct horse" };

  it("matches identical credentials", () => {
    expect(dashboardCredentialsMatch({ ...expected }, expected)).toBe(true);
  });

  it.each([
    { username: "owner", password: "correct hors" },
    { username: "Owner", password: "correct horse" },
    { username: "", password: "" },
  ])("rejects different credentials %#", (candidate) => {
    expect(dashboardCredentialsMatch(candidate, expected)).toBe(false);
  });
});

describe("basicAuthorizationHeader", () => {
  it("round-trips non-ASCII credentials", () => {
    const credentials = { username: "ägare", password: "lösenord" };
    expect(parseBasicAuthorization(basicAuthorizationHeader(credentials))).toEqual(credentials);
  });

  it("omits the header when credentials are incomplete", () => {
    expect(basicAuthorizationHeader({ username: "", password: "x" })).toBeNull();
    expect(basicAuthorizationHeader({ username: "x", password: "" })).toBeNull();
  });
});
