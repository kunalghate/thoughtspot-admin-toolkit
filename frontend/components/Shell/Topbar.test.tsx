import { describe, it, expect } from "vitest";
import { theme } from "@/lib/theme";
import { buildSyncLabel, buildSyncColor } from "./Topbar";
import type { SyncLog, SyncStatus } from "@/lib/types";

// The backend sends NAIVE UTC timestamps and `buildSyncLabel` re-attaches the
// zone itself (`new Date(log.synced_at + "Z")`). Fixtures are therefore built
// by subtracting from a fixed UTC instant and stripping the trailing "Z", so
// these assertions hold in every local timezone.
const NOW = Date.parse("2026-08-18T12:00:00Z");
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Naive-UTC timestamp (the backend's `synced_at` shape) for `ms` before NOW. */
function stampAgo(ms: number): string {
  return new Date(NOW - ms).toISOString().slice(0, 19);
}

function log(status: SyncStatus, syncedAt: string | null): SyncLog {
  return { entity_type: "metadata", synced_at: syncedAt, record_count: 1, status, error: null };
}

// Exhaustive by construction: `Record<SyncStatus, …>` means adding a value to
// the SyncStatus union without adding it here is a COMPILE error under
// `tsc --noEmit` (which typechecks *.test.tsx), not a silent coverage gap.
//
// Every fixture carries a *fresh* (30m old) synced_at on purpose. `_write_sync_log`
// preserves the last successful timestamp across FAILED/IN_PROGRESS rows, so this
// is the real production shape — and it means any status that falls through to
// the age branches renders as "Synced 30m ago" in the success token, i.e. the
// exact fail-open S23 fixed becomes observable below.
const STATUS_FIXTURES: Record<SyncStatus, SyncLog> = {
  SUCCESS: log("SUCCESS", stampAgo(30 * MINUTE)),
  FAILED: log("FAILED", stampAgo(30 * MINUTE)),
  IN_PROGRESS: log("IN_PROGRESS", stampAgo(30 * MINUTE)),
  NOT_SYNCED: log("NOT_SYNCED", stampAgo(30 * MINUTE)),
};

const ALL_STATUSES = Object.keys(STATUS_FIXTURES) as SyncStatus[];

describe("Topbar sync indicator — exhaustiveness", () => {
  it("covers every SyncStatus value", () => {
    // Guards the loops below against a vacuous fixture map.
    expect(ALL_STATUSES).toHaveLength(4);
    expect(ALL_STATUSES).toContain("IN_PROGRESS");
  });

  it.each(ALL_STATUSES.filter((s) => s !== "SUCCESS"))(
    "%s never renders as healthy",
    (status) => {
      const fixture = STATUS_FIXTURES[status];

      // A status with no branch of its own falls through to the age buckets and
      // reports a green "Synced 30m ago" over a cache that is missing, failed or
      // mid-rebuild. Stricter than /^Synced \d/ so a <2m fallthrough
      // ("Synced just now") is caught too.
      expect(buildSyncLabel(fixture, false, null, NOW)).not.toMatch(/^Synced\b/);
      expect(buildSyncColor(fixture, false, NOW)).not.toBe(theme.color.success);
    },
  );

  it("returns a non-empty label and a theme token for every status", () => {
    for (const status of ALL_STATUSES) {
      expect(buildSyncLabel(STATUS_FIXTURES[status], false, null, NOW)).not.toBe("");
      expect(buildSyncColor(STATUS_FIXTURES[status], false, NOW)).toMatch(/^var\(--/);
    }
  });
});

describe("buildSyncLabel", () => {
  it("reports IN_PROGRESS as syncing even when isSyncing is false", () => {
    // isSyncing is per-mount React state: false after a reload or in a second
    // tab while a perfectly healthy sync is still running.
    expect(buildSyncLabel(STATUS_FIXTURES.IN_PROGRESS, false, null, NOW)).toBe("Syncing…");
  });

  it("reports a missing log as never synced", () => {
    expect(buildSyncLabel(null, false, null, NOW)).toBe("Never synced");
  });

  it("reports NOT_SYNCED as never synced", () => {
    expect(buildSyncLabel(STATUS_FIXTURES.NOT_SYNCED, false, null, NOW)).toBe("Never synced");
  });

  it("reports FAILED as a failed sync", () => {
    expect(buildSyncLabel(STATUS_FIXTURES.FAILED, false, null, NOW)).toBe("Sync failed");
  });

  it("reports a SUCCESS row with no timestamp as unknown", () => {
    expect(buildSyncLabel(log("SUCCESS", null), false, null, NOW)).toBe("Unknown");
  });

  it.each([
    [30_000, "Synced just now"],
    [MINUTE, "Synced just now"],
    [2 * MINUTE, "Synced 2m ago"],
    [59 * MINUTE, "Synced 59m ago"],
    [HOUR, "Synced 1h ago"],
    [23 * HOUR, "Synced 23h ago"],
    [DAY, "Synced 1d ago"],
    [9 * DAY, "Synced 9d ago"],
  ])("renders a SUCCESS row %ims old as %s", (ago, expected) => {
    expect(buildSyncLabel(log("SUCCESS", stampAgo(ago)), false, null, NOW)).toBe(expected);
  });

  it("takes precedence over the log while a local sync is in flight", () => {
    // Determinate: the job reported a grand total.
    expect(buildSyncLabel(STATUS_FIXTURES.SUCCESS, true, { processed: 1500, total: 3000 }, NOW))
      .toBe("Syncing 1,500 / 3,000");
    // Indeterminate: no grand total, but objects are landing.
    expect(buildSyncLabel(STATUS_FIXTURES.SUCCESS, true, { processed: 1500, total: 0 }, NOW))
      .toBe("Syncing — 1,500 objects");
    // No progress reported at all.
    expect(buildSyncLabel(STATUS_FIXTURES.SUCCESS, true, null, NOW)).toBe("Syncing…");
    expect(buildSyncLabel(STATUS_FIXTURES.SUCCESS, true, { processed: 0, total: 0 }, NOW)).toBe("Syncing…");
  });
});

describe("buildSyncColor", () => {
  it("colours IN_PROGRESS as in-flight, not healthy and not alarming", () => {
    expect(buildSyncColor(STATUS_FIXTURES.IN_PROGRESS, false, NOW)).toBe(theme.color.accent);
  });

  it("colours a missing log and NOT_SYNCED as danger", () => {
    expect(buildSyncColor(null, false, NOW)).toBe(theme.color.danger);
    expect(buildSyncColor(STATUS_FIXTURES.NOT_SYNCED, false, NOW)).toBe(theme.color.danger);
  });

  it("colours FAILED as danger", () => {
    expect(buildSyncColor(STATUS_FIXTURES.FAILED, false, NOW)).toBe(theme.color.danger);
  });

  it("colours a SUCCESS row with no timestamp as muted", () => {
    expect(buildSyncColor(log("SUCCESS", null), false, NOW)).toBe(theme.color.textMuted);
  });

  it.each([
    [MINUTE, theme.color.success],
    [59 * MINUTE, theme.color.success],
    [HOUR, theme.color.textMuted],
    [5 * HOUR, theme.color.textMuted],
    [6 * HOUR, theme.color.warn],
    [3 * DAY, theme.color.warn],
  ])("colours a SUCCESS row %ims old as %s", (ago, expected) => {
    expect(buildSyncColor(log("SUCCESS", stampAgo(ago)), false, NOW)).toBe(expected);
  });

  it("takes precedence over the log while a local sync is in flight", () => {
    expect(buildSyncColor(STATUS_FIXTURES.NOT_SYNCED, true, NOW)).toBe(theme.color.accent);
    expect(buildSyncColor(null, true, NOW)).toBe(theme.color.accent);
  });
});
