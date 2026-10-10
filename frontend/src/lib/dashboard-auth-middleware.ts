import { NextResponse, type NextRequest } from "next/server";

import { AuthFailureLimiter, authClientKey } from "./auth-failure-limiter";
import {
  configuredDashboardCredentials,
  dashboardAuthConfigurationError,
  dashboardCredentialsMatch,
  parseBasicAuthorization,
} from "./dashboard-auth";

const AUTH_CHALLENGE = 'Basic realm="Hyperliquid Copy Agent"';

type DashboardAuthMiddlewareOptions = {
  authEnabled: boolean;
  limiter: AuthFailureLimiter;
  env?: Record<string, string | undefined>;
  now?: () => number;
};

export function createDashboardAuthMiddleware(options: DashboardAuthMiddlewareOptions) {
  const now = options.now ?? Date.now;
  let reportedConfigurationError: string | null = null;

  return function dashboardAuthMiddleware(request: NextRequest) {
    const env = options.env ?? process.env;
    const configurationError = dashboardAuthConfigurationError({
      authEnabled: options.authEnabled,
      env,
    });
    if (configurationError) {
      if (reportedConfigurationError !== configurationError) {
        reportedConfigurationError = configurationError;
        console.error("dashboard auth is misconfigured: %s", configurationError);
      }
      // Fail closed instead of serving the dashboard with unsafe credentials.
      return new NextResponse("Dashboard authentication is not configured.", { status: 503 });
    }
    if (!options.authEnabled || request.method === "OPTIONS") {
      return NextResponse.next();
    }

    const clientKey = authClientKey(request.headers);
    const lockedForSeconds = options.limiter.retryAfterSeconds(clientKey, now());
    if (lockedForSeconds !== null) {
      return tooManyFailedAttempts(lockedForSeconds);
    }

    const authorization = request.headers.get("authorization");
    if (!authorization) {
      return authenticationRequired();
    }

    const credentials = parseBasicAuthorization(authorization);
    if (
      credentials !== null &&
      dashboardCredentialsMatch(credentials, configuredDashboardCredentials(env))
    ) {
      options.limiter.recordSuccess(clientKey);
      return NextResponse.next();
    }

    const lockoutSeconds = options.limiter.recordFailure(clientKey, now());
    if (lockoutSeconds !== null) {
      console.warn(
        "dashboard auth locked out client=%s retry_after_seconds=%s",
        clientKey,
        lockoutSeconds,
      );
      return tooManyFailedAttempts(lockoutSeconds);
    }
    return authenticationRequired();
  };
}

function authenticationRequired() {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: { "WWW-Authenticate": AUTH_CHALLENGE },
  });
}

function tooManyFailedAttempts(retryAfterSeconds: number) {
  return new NextResponse("Too many failed login attempts. Try again later.", {
    status: 429,
    headers: { "Retry-After": String(retryAfterSeconds) },
  });
}
