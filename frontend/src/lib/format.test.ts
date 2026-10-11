import { describe, expect, it } from "vitest";

import {
  formatCompact,
  formatDateTime,
  formatClock,
  formatDuration,
  formatMultiple,
  formatPercent,
  formatPercentNumber,
  formatPrice,
  formatRelativeTime,
  formatSeconds,
  formatUsd,
  humanize,
  shortAddress,
  toNumber,
} from "./format";

describe("formatUsd", () => {
  it("uses en-US grouping, two decimals, and a real minus sign", () => {
    expect(formatUsd("1234.5")).toBe("$1,234.50");
    expect(formatUsd(-12.345)).toBe("−$12.35");
    expect(formatUsd(0)).toBe("$0.00");
  });

  it("signs gains and keeps zero unsigned", () => {
    expect(formatUsd(3.2, { signed: true })).toBe("+$3.20");
    expect(formatUsd(-0.001, { signed: true })).toBe("$0.00");
  });

  it("compacts only large values", () => {
    expect(formatUsd(601243578.97, { compact: true })).toBe("$601.2M");
    expect(formatUsd(9999, { compact: true })).toBe("$9,999.00");
  });

  it("shows a dash for missing values", () => {
    expect(formatUsd(null)).toBe("-");
    expect(formatUsd("")).toBe("-");
    expect(formatUsd("not a number")).toBe("-");
  });
});

describe("percent formatting", () => {
  it("formats ratios as percent with fixed decimals", () => {
    expect(formatPercent(0.1234)).toBe("12.3%");
    expect(formatPercent(0.5, { decimals: 0 })).toBe("50%");
    expect(formatPercent(-0.04, { signed: true })).toBe("−4.0%");
    expect(formatPercent(0.04, { signed: true })).toBe("+4.0%");
  });

  it("formats values that already are percent numbers", () => {
    expect(formatPercentNumber("668.3")).toBe("668.3%");
  });
});

describe("number formatting", () => {
  it("formats exposure multiples with a multiplication sign", () => {
    expect(formatMultiple("3")).toBe("3×");
    expect(formatMultiple(2.5)).toBe("2.5×");
    expect(formatMultiple(null)).toBe("-");
  });

  it("scales price precision with magnitude", () => {
    expect(formatPrice("62345.5")).toBe("62,345.5");
    expect(formatPrice("13.04881")).toBe("13.0488");
    expect(formatPrice("0.0098712345")).toBe("0.0098712");
  });

  it("compacts large counts", () => {
    expect(formatCompact(596715)).toBe("596.7K");
  });

  it("parses API decimals defensively", () => {
    expect(toNumber("1.5")).toBe(1.5);
    expect(toNumber(undefined)).toBeNull();
    expect(toNumber("NaN")).toBeNull();
  });
});

describe("time formatting", () => {
  it("formats short dates in the pinned UTC test zone", () => {
    expect(formatDateTime("2020-01-01T00:00:00Z")).toBe("Jan 1, 2020, 00:00");
  });

  it("describes relative times", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(formatRelativeTime("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(formatRelativeTime("2026-01-01T11:55:00Z", now)).toBe("5m ago");
    expect(formatRelativeTime("2025-12-30T12:00:00Z", now)).toBe("2d ago");
    expect(formatRelativeTime("2026-01-01T12:10:00Z", now)).toBe("in 10m");
  });

  it("formats durations with two units", () => {
    expect(formatDuration(450)).toBe("450 ms");
    expect(formatDuration(2300)).toBe("2.3 s");
    expect(formatDuration(65_000)).toBe("1m 5s");
    expect(formatSeconds(3 * 86400 + 4 * 3600)).toBe("3d 4h");
  });

  it("formats elapsed time as a stopwatch", () => {
    expect(formatClock(42_900)).toBe("0:42");
    expect(formatClock(12 * 60_000 + 5_000)).toBe("12:05");
    expect(formatClock(3_723_000)).toBe("1:02:03");
    expect(formatClock(-5_000)).toBe("0:00");
    expect(formatClock(null)).toBe("-");
  });
});

describe("text helpers", () => {
  it("shortens addresses and humanizes codes", () => {
    expect(shortAddress("0x1234567890abcdef1234567890abcdef12345678")).toBe("0x1234…5678");
    expect(humanize("max_drawdown_too_high")).toBe("Max drawdown too high");
  });
});
