/*
  Number and time formatting for the whole dashboard. The interface is English, so numbers use
  en-US grouping. Missing values render as a plain dash and negative values use a real minus sign.
*/

type NumericInput = string | number | null | undefined;
type DateInput = string | number | Date | null | undefined;

const LOCALE = "en-US";
const MINUS = "−";
export const EMPTY = "-";

export function numberValue(value: string | number) {
  return typeof value === "number" ? value : Number(value);
}

/** Parses an API decimal string. Returns null for missing or non-finite values. */
export function toNumber(value: NumericInput): number | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Parses an API decimal string and falls back to zero. */
export function decimal(value: NumericInput) {
  return toNumber(value) ?? 0;
}

function withSign(formatted: string, value: number, signed: boolean) {
  if (value < 0) {
    return `${MINUS}${formatted}`;
  }
  if (signed && value > 0) {
    return `+${formatted}`;
  }
  return formatted;
}

export function formatInteger(value: NumericInput) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return withSign(
    new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 }).format(Math.abs(Math.round(parsed))),
    Math.round(parsed),
    false,
  );
}

export function formatNumber(value: NumericInput, maximumFractionDigits = 2) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return withSign(
    new Intl.NumberFormat(LOCALE, { maximumFractionDigits }).format(Math.abs(parsed)),
    parsed,
    false,
  );
}

export function formatCompact(value: NumericInput) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return withSign(
    new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1, notation: "compact" }).format(
      Math.abs(parsed),
    ),
    parsed,
    false,
  );
}

export function formatUsd(
  value: NumericInput,
  options: { compact?: boolean; decimals?: number; signed?: boolean } = {},
) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  const absolute = Math.abs(parsed);
  const compact = options.compact && absolute >= 10_000;
  const decimals = options.decimals ?? 2;
  const formatted = new Intl.NumberFormat(LOCALE, compact
    ? { maximumFractionDigits: 1, notation: "compact" }
    : { maximumFractionDigits: decimals, minimumFractionDigits: decimals }).format(absolute);
  const rounded = Number(absolute.toFixed(compact ? 1 : decimals));
  return withSign(`$${formatted}`, rounded === 0 ? 0 : parsed, options.signed ?? false);
}

export function formatSignedUsd(value: NumericInput, options: { compact?: boolean; decimals?: number } = {}) {
  return formatUsd(value, { ...options, signed: true });
}

/** Formats a ratio (0.123) as a percent (12.3%). */
export function formatPercent(
  value: NumericInput,
  options: { decimals?: number; signed?: boolean } = {},
) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  const decimals = options.decimals ?? 1;
  const percent = parsed * 100;
  const rounded = Number(Math.abs(percent).toFixed(decimals));
  const formatted = new Intl.NumberFormat(LOCALE, {
    maximumFractionDigits: decimals,
    minimumFractionDigits: decimals,
  }).format(rounded);
  return withSign(`${formatted}%`, rounded === 0 ? 0 : percent, options.signed ?? false);
}

export function formatSignedPercent(value: NumericInput, decimals = 1) {
  return formatPercent(value, { decimals, signed: true });
}

/** Formats a value that already is a percent number (12.3 means 12.3%). */
export function formatPercentNumber(value: NumericInput, decimals = 1) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return formatPercent(parsed / 100, { decimals });
}

export function formatMultiple(value: NumericInput, decimals = 2) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  if (!Number.isFinite(parsed)) {
    return "∞";
  }
  return `${formatNumber(parsed, decimals)}×`;
}

export function formatLeverage(value: NumericInput, marginMode?: string | null) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return `${formatNumber(parsed, 2)}×${marginMode ? ` ${marginMode}` : ""}`;
}

export function formatBps(value: NumericInput) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return `${formatNumber(parsed, 2)} bps`;
}

export function formatScore(value: NumericInput) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  return withSign(
    new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(
      Math.abs(parsed),
    ),
    Number(Math.abs(parsed).toFixed(1)) === 0 ? 0 : parsed,
    false,
  );
}

/** Formats an asset price with precision that follows its magnitude. */
export function formatPrice(value: NumericInput) {
  const parsed = toNumber(value);
  if (parsed === null) {
    return EMPTY;
  }
  const absolute = Math.abs(parsed);
  const formatted =
    absolute >= 1000
      ? new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 2 }).format(absolute)
      : absolute >= 1
        ? new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 4 }).format(absolute)
        : new Intl.NumberFormat(LOCALE, { maximumSignificantDigits: 5 }).format(absolute);
  return withSign(formatted, parsed, false);
}

export function formatSize(value: NumericInput) {
  return formatPrice(value);
}

export function formatBytes(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY;
  }
  if (value < 1024) {
    return `${value} B`;
  }
  const units = ["KB", "MB", "GB", "TB"];
  let size = value / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 }).format(size)} ${
    units[unitIndex]
  }`;
}

export function toDate(value: DateInput): Date | null {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const date = value instanceof Date ? value : new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function dateMs(value: DateInput) {
  return toDate(value)?.getTime() ?? 0;
}

/** Short date and time, for example "Oct 10, 23:51". Older years include the year. */
export function formatDateTime(value: DateInput, options: { seconds?: boolean } = {}) {
  const date = toDate(value);
  if (!date) {
    return EMPTY;
  }
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return new Intl.DateTimeFormat(LOCALE, {
    day: "numeric",
    hour: "2-digit",
    hourCycle: "h23",
    minute: "2-digit",
    month: "short",
    second: options.seconds ? "2-digit" : undefined,
    year: sameYear ? undefined : "numeric",
  }).format(date);
}

/** Full timestamp for tooltips, for example "2026-10-10 23:51:05". */
export function formatTimestamp(value: DateInput) {
  const date = toDate(value);
  if (!date) {
    return EMPTY;
  }
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function formatTime(value: DateInput) {
  const date = toDate(value);
  if (!date) {
    return EMPTY;
  }
  return new Intl.DateTimeFormat(LOCALE, {
    hour: "2-digit",
    hourCycle: "h23",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

/** Relative time such as "4m ago" or "in 2h". */
export function formatRelativeTime(value: DateInput, now: number = Date.now()) {
  const date = toDate(value);
  if (!date) {
    return EMPTY;
  }
  const diffMs = now - date.getTime();
  const future = diffMs < 0;
  const seconds = Math.round(Math.abs(diffMs) / 1000);
  if (seconds < 5) {
    return "just now";
  }
  const span = formatSpan(seconds);
  return future ? `in ${span}` : `${span} ago`;
}

function formatSpan(totalSeconds: number) {
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  if (days < 60) {
    return `${days}d`;
  }
  return `${Math.floor(days / 30)}mo`;
}

/** Duration in milliseconds, for example "450 ms", "2.3 s", "4m 12s", or "2h 14m". */
export function formatDuration(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY;
  }
  const ms = Math.max(0, value);
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${formatNumber(seconds, 1)} s`;
  }
  return formatSeconds(seconds);
}

/** Duration in seconds with two units, for example "4m 12s", "2h 14m", or "3d 4h". */
export function formatSeconds(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY;
  }
  const totalSeconds = Math.max(0, Math.round(value));
  if (totalSeconds < 60) {
    return `${totalSeconds}s`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) {
    const rest = totalSeconds % 60;
    return rest > 0 ? `${totalMinutes}m ${rest}s` : `${totalMinutes}m`;
  }
  const totalHours = Math.floor(totalMinutes / 60);
  if (totalHours < 48) {
    const rest = totalMinutes % 60;
    return rest > 0 ? `${totalHours}h ${rest}m` : `${totalHours}h`;
  }
  const days = Math.floor(totalHours / 24);
  const rest = totalHours % 24;
  return rest > 0 ? `${days}d ${rest}h` : `${days}d`;
}

export function shortAddress(address: string | null | undefined, head = 6, tail = 4) {
  if (!address) {
    return EMPTY;
  }
  if (address.length <= head + tail + 2) {
    return address;
  }
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

export function shortId(value: string | null | undefined) {
  if (!value) {
    return EMPTY;
  }
  return value.length <= 14 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`;
}

/** Turns snake_case API values into readable sentence case. */
export function humanize(value: string | null | undefined) {
  if (!value) {
    return EMPTY;
  }
  const text = value.replaceAll("_", " ").replaceAll("-", " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function pluralize(count: number, singular: string, plural = `${singular}s`) {
  return `${formatInteger(count)} ${count === 1 ? singular : plural}`;
}
