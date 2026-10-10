import { describe, expect, it } from "vitest";

import { formatMultiple } from "./format";

describe("formatMultiple", () => {
  it("formats exposure multiples with an x suffix", () => {
    expect(formatMultiple("3")).toBe("3x");
    expect(formatMultiple(2.5)).toBe("2,5x");
  });

  it("shows a dash for missing values", () => {
    expect(formatMultiple(null)).toBe("-");
    expect(formatMultiple(undefined)).toBe("-");
  });
});
