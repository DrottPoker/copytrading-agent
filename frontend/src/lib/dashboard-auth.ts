import { createHash, timingSafeEqual } from "node:crypto";

export const DASHBOARD_AUTH_MIN_PRODUCTION_PASSWORD_LENGTH = 8;

const DEVELOPMENT_USERNAME = "admin";
const DEVELOPMENT_PASSWORD = "change-me";
const BASE64_PATTERN = /^[A-Za-z0-9+/]+={0,2}$/;

export type DashboardCredentials = {
  username: string;
  password: string;
};

type Environment = Record<string, string | undefined>;

export function isProductionAppEnv(env: Environment = process.env) {
  return env.APP_ENV === "production";
}

// Development falls back to the documented local defaults; production never does.
export function configuredDashboardCredentials(
  env: Environment = process.env,
): DashboardCredentials {
  if (isProductionAppEnv(env)) {
    return {
      username: env.DASHBOARD_AUTH_USERNAME ?? "",
      password: env.DASHBOARD_AUTH_PASSWORD ?? "",
    };
  }
  return {
    username: env.DASHBOARD_AUTH_USERNAME ?? DEVELOPMENT_USERNAME,
    password: env.DASHBOARD_AUTH_PASSWORD ?? DEVELOPMENT_PASSWORD,
  };
}

// Mirrors the backend production startup rules in backend/app/core/config.py.
export function dashboardAuthConfigurationError({
  authEnabled,
  env = process.env,
}: {
  authEnabled: boolean;
  env?: Environment;
}): string | null {
  if (!isProductionAppEnv(env)) {
    return null;
  }
  if (!authEnabled) {
    return "Dashboard auth must be enabled in production.";
  }
  const { username, password } = configuredDashboardCredentials(env);
  if (!username.trim()) {
    return "DASHBOARD_AUTH_USERNAME must not be empty in production.";
  }
  if (!password || password === DEVELOPMENT_PASSWORD) {
    return "DASHBOARD_AUTH_PASSWORD must be changed before production startup.";
  }
  if (codePointLength(password) < DASHBOARD_AUTH_MIN_PRODUCTION_PASSWORD_LENGTH) {
    return (
      "DASHBOARD_AUTH_PASSWORD must contain at least " +
      `${DASHBOARD_AUTH_MIN_PRODUCTION_PASSWORD_LENGTH} characters in production.`
    );
  }
  return null;
}

export function parseBasicAuthorization(header: string | null): DashboardCredentials | null {
  if (!header) {
    return null;
  }
  const separatorIndex = header.indexOf(" ");
  if (separatorIndex < 0) {
    return null;
  }
  const scheme = header.slice(0, separatorIndex);
  const encoded = header.slice(separatorIndex + 1).trim();
  if (scheme.toLowerCase() !== "basic" || !BASE64_PATTERN.test(encoded)) {
    return null;
  }

  let decoded: string;
  try {
    decoded = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.from(encoded, "base64"));
  } catch {
    return null;
  }
  const credentialSeparatorIndex = decoded.indexOf(":");
  if (credentialSeparatorIndex < 0) {
    return null;
  }
  return {
    username: decoded.slice(0, credentialSeparatorIndex),
    password: decoded.slice(credentialSeparatorIndex + 1),
  };
}

export function dashboardCredentialsMatch(
  candidate: DashboardCredentials,
  expected: DashboardCredentials,
) {
  // Compare fixed-length digests so neither content nor length leaks through timing.
  const usernameMatches = digestsEqual(candidate.username, expected.username);
  const passwordMatches = digestsEqual(candidate.password, expected.password);
  return usernameMatches && passwordMatches;
}

export function basicAuthorizationHeader(credentials: DashboardCredentials): string | null {
  if (!credentials.username || !credentials.password) {
    return null;
  }
  const token = Buffer.from(`${credentials.username}:${credentials.password}`, "utf8");
  return `Basic ${token.toString("base64")}`;
}

function digestsEqual(left: string, right: string) {
  return timingSafeEqual(sha256(left), sha256(right));
}

function sha256(value: string) {
  return createHash("sha256").update(value, "utf8").digest();
}

function codePointLength(value: string) {
  return [...value].length;
}
