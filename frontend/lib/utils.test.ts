import { afterEach, describe, expect, it, vi } from "vitest";
import { formatAbsolute, formatDate, formatDay, formatRelative, parseUtc } from "./utils";

// vitest.config.mts pins TZ=America/New_York on purpose. Under TZ=UTC — what CI
// runs — a naive-UTC string parses to the same instant with or without the `Z`,
// so every assertion below would stay green with the bug reintroduced.
const OFFSET_HOURS = 4; // America/New_York in August (EDT, UTC-4)

/** A naive-UTC ISO string exactly `minutesAgo` in the past, as the backend sends it. */
function wire(minutesAgo: number): string {
  return new Date(Date.now() - minutesAgo * 60_000).toISOString().replace("Z", "");
}

afterEach(() => vi.useRealTimers());

describe("parseUtc", () => {
  it("reads a naive backend timestamp as UTC, not as local time", () => {
    const naive = "2026-08-18T22:37:55.275439";
    expect(parseUtc(naive).getTime()).toBe(Date.parse(naive + "Z"));
    // The bug: the platform default is local, which is off by the whole offset.
    expect(parseUtc(naive).getTime()).not.toBe(new Date(naive).getTime());
    expect(new Date(naive).getTime() - parseUtc(naive).getTime()).toBe(OFFSET_HOURS * 3_600_000);
  });

  it("leaves a timestamp that already carries a zone alone", () => {
    for (const iso of ["2026-08-18T22:37:55Z", "2026-08-18T22:37:55+02:00", "2026-08-18T22:37:55-0500"]) {
      expect(parseUtc(iso).getTime()).toBe(new Date(iso).getTime());
    }
  });
});

describe("relative formatting", () => {
  it("calls a job that just finished 'Today', not a negative day count", () => {
    expect(formatDate(wire(1))).toBe("Today");
    expect(formatRelative(wire(1))).toBe("1m ago");
  });

  it("resolves recent activity to minutes and hours", () => {
    expect(formatRelative(wire(0))).toBe("just now");
    expect(formatRelative(wire(45))).toBe("45m ago");
    expect(formatRelative(wire(5 * 60))).toBe("5h ago");
  });

  it("still reads whole days beyond the first", () => {
    expect(formatDate(wire(60 * 24 + 60))).toBe("Yesterday");
    expect(formatDate(wire(60 * 24 * 5))).toBe("5d ago");
    expect(formatDate(wire(60 * 24 * 90))).toBe("3mo ago");
    expect(formatDate(wire(60 * 24 * 800))).toBe("2y ago");
    expect(formatRelative(wire(60 * 24 * 5))).toBe("5d ago");
  });

  it("never renders the future when the server clock runs slightly ahead", () => {
    expect(formatDate(wire(-30))).toBe("Today");
  });

  it("has an explicit empty state", () => {
    expect(formatDate(null)).toBe("Never");
    expect(formatRelative(null)).toBe("—");
    expect(formatAbsolute(null)).toBe("—");
    expect(formatDay(null)).toBe("");
  });
});

describe("absolute formatting", () => {
  it("renders a naive backend timestamp in the viewer's local wall clock", () => {
    // 22:37 UTC is 18:37 the same evening in New York — not 22:37 local.
    expect(formatAbsolute("2026-08-18T22:37:55")).toBe(
      new Date(Date.parse("2026-08-18T22:37:55Z")).toLocaleString(),
    );
    expect(formatAbsolute("2026-08-18T22:37:55")).toContain("6:37");
  });

  it("does not roll the calendar date back a day for a late-UTC timestamp", () => {
    // 01:30 UTC on the 19th is still the evening of the 18th in New York.
    expect(formatDay("2026-08-19T01:30:00")).toBe("8/18/2026");
  });
});
