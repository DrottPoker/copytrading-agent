import { AuthFailureLimiter } from "@/lib/auth-failure-limiter";
import { frontendConfig } from "@/lib/config";
import { createDashboardAuthMiddleware } from "@/lib/dashboard-auth-middleware";

export const middleware = createDashboardAuthMiddleware({
  authEnabled: frontendConfig.dashboardAuthEnabled,
  limiter: new AuthFailureLimiter({
    maxFailures: frontendConfig.dashboardAuthMaxFailedAttempts,
    windowMs: frontendConfig.dashboardAuthFailureWindowSeconds * 1000,
    lockoutMs: frontendConfig.dashboardAuthLockoutSeconds * 1000,
  }),
});

export const config = {
  // The Node.js runtime keeps one limiter instance per process and provides node:crypto.
  runtime: "nodejs",
  matcher: ["/((?!_next/static|favicon.ico|robots.txt).*)"],
};
