export type AuthFailureLimiterOptions = {
  maxFailures: number;
  windowMs: number;
  lockoutMs: number;
  maxTrackedClients?: number;
};

type FailureEntry = {
  failures: number;
  windowStartedAt: number;
  lockedUntil: number;
};

const DEFAULT_MAX_TRACKED_CLIENTS = 10_000;

// In-memory per-client throttle for failed dashboard logins. State is per process,
// which matches the single frontend container in the Compose deployments.
export class AuthFailureLimiter {
  private readonly entries = new Map<string, FailureEntry>();

  constructor(private readonly options: AuthFailureLimiterOptions) {}

  retryAfterSeconds(clientKey: string, now = Date.now()): number | null {
    const entry = this.entries.get(clientKey);
    if (!entry || entry.lockedUntil <= now) {
      return null;
    }
    return Math.max(1, Math.ceil((entry.lockedUntil - now) / 1000));
  }

  recordFailure(clientKey: string, now = Date.now()): number | null {
    const current = this.entries.get(clientKey);
    const windowExpired = !current || now - current.windowStartedAt >= this.options.windowMs;
    const entry: FailureEntry = windowExpired
      ? { failures: 0, windowStartedAt: now, lockedUntil: 0 }
      : current;

    entry.failures += 1;
    if (entry.failures >= this.options.maxFailures) {
      entry.lockedUntil = now + this.options.lockoutMs;
      entry.failures = 0;
      entry.windowStartedAt = now;
    }
    this.entries.delete(clientKey);
    this.entries.set(clientKey, entry);
    this.prune(now);
    return this.retryAfterSeconds(clientKey, now);
  }

  recordSuccess(clientKey: string) {
    this.entries.delete(clientKey);
  }

  get trackedClientCount() {
    return this.entries.size;
  }

  private prune(now: number) {
    const maxTrackedClients = this.options.maxTrackedClients ?? DEFAULT_MAX_TRACKED_CLIENTS;
    if (this.entries.size <= maxTrackedClients) {
      return;
    }
    for (const [clientKey, entry] of this.entries) {
      const expired =
        entry.lockedUntil <= now && now - entry.windowStartedAt >= this.options.windowMs;
      if (expired) {
        this.entries.delete(clientKey);
      }
    }
    // Map iteration order is insertion order, so the oldest updated clients go first.
    for (const clientKey of this.entries.keys()) {
      if (this.entries.size <= maxTrackedClients) {
        break;
      }
      this.entries.delete(clientKey);
    }
  }
}

// Caddy overwrites X-Real-IP with the TCP peer address, so it cannot be spoofed
// while the frontend is only reachable through Caddy.
export function authClientKey(headers: Headers) {
  const realIp = headers.get("x-real-ip")?.trim();
  if (realIp) {
    return realIp;
  }
  const forwardedFor = headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return forwardedFor || "unknown";
}
