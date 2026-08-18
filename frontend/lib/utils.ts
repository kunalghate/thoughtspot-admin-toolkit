/**
 * Shared utility functions used across multiple pages.
 */

/**
 * Parse a timestamp the backend sent.
 *
 * Every datetime on the wire is naive-UTC: the models store
 * `datetime.now(timezone.utc)` but SQLite drops the tzinfo on read, so FastAPI
 * serializes `"2026-08-18T22:37:55.275439"` with no `Z` and no offset. Per
 * ECMAScript, a date-time form without an offset is parsed as LOCAL time — so
 * `new Date(iso)` is wrong by the viewer's UTC offset, and west of Greenwich
 * that puts recent timestamps in the future ("-1d ago" for a job that finished
 * a minute ago). Always parse through here, never `new Date(iso)` directly.
 *
 * Tolerates a string that already carries an offset, so this stays correct if
 * the backend ever starts sending one.
 */
export function parseUtc(iso: string): Date {
  return new Date(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
}

/**
 * Format a backend timestamp as a human-readable relative date, to the day.
 * Returns "Never" for null/undefined values.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "Never";
  return relativeDays(Date.now() - parseUtc(iso).getTime());
}

/**
 * Same as `formatDate`, but resolves to minutes and hours within the last day —
 * for feeds where "Today" is too coarse to be useful (jobs, activity).
 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const elapsed = Date.now() - parseUtc(iso).getTime();
  if (elapsed < 86_400_000) {
    const mins = Math.floor(elapsed / 60_000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    return `${Math.floor(mins / 60)}h ago`;
  }
  return relativeDays(elapsed);
}

/** Exact local wall-clock date and time, for audit rows and tooltips. */
export function formatAbsolute(iso: string | null | undefined): string {
  return iso ? parseUtc(iso).toLocaleString() : "—";
}

/** Exact local calendar date, no time of day. */
export function formatDay(iso: string | null | undefined): string {
  return iso ? parseUtc(iso).toLocaleDateString() : "";
}

function relativeDays(elapsedMs: number): string {
  const days = Math.floor(elapsedMs / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}
