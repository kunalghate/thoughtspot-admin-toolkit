import { theme } from "@/lib/theme";
import type { EntityType, SyncLog } from "@/lib/types";
import { parseUtc } from "@/lib/utils";

/**
 * Pure helpers for the Topbar sync indicator.
 *
 * These live outside `Topbar.tsx` on purpose: react-refresh only treats a module
 * as a Fast Refresh boundary when every export looks like a component
 * (`isLikelyComponentType` requires a capitalised name), so exporting these two
 * functions from the component file downgraded every Topbar edit under
 * `make dev` to a full page reload.
 */

/**
 * Countable noun for the "N <noun>" running count, per entity.
 *
 * Deliberately NOT `ENTITY_LABELS` lowercased (`Topbar.tsx`): users and groups
 * are principals, not "objects", and `dependencies` is labelled "Lineage",
 * which is not a countable noun for edges.
 */
const ENTITY_NOUNS: Partial<Record<EntityType, string>> = {
  users: "users",
  groups: "groups",
  tags: "tags",
  metadata: "objects",
  dependencies: "objects",
};

/**
 * `undefined` means the sync status is NOT KNOWN — the read has not landed yet,
 * or it failed. `null` means it is known and there is no log row, i.e. genuinely
 * never synced. Collapsing the two made a failed status read render as a
 * confident "Never synced" (and, via `blockedReason`, permanently disabled the
 * Sync Lineage button on a cluster whose metadata was perfectly synced).
 */
export type SyncLogOrUnknown = SyncLog | null | undefined;

export function buildSyncLabel(
  log: SyncLogOrUnknown,
  isSyncing: boolean,
  progress: { processed: number; total: number } | null,
  now: number,
  entityType?: EntityType,
): string {
  if (isSyncing) {
    if (progress && progress.total > 0) return `Syncing ${progress.processed.toLocaleString()} / ${progress.total.toLocaleString()}`;
    // No grand total from the TS API — show the live running count instead.
    // Not `Syncing 6,400…`: a number followed by an ellipsis reads as a number
    // that got truncated, not as a sync still in flight.
    //
    // This is the NORMAL path, not an edge case: every sync calls
    // `mark_running(..., total=0)` and `job_service.update_progress` never sets
    // a total, so the determinate branch above is effectively unreachable today.
    if (progress && progress.processed > 0) {
      const noun = (entityType && ENTITY_NOUNS[entityType]) ?? "objects";
      return `Syncing — ${progress.processed.toLocaleString()} ${noun}`;
    }
    return "Syncing…";
  }
  if (log === undefined) return "Sync status unknown";
  if (!log || log.status === "NOT_SYNCED") return "Never synced";
  if (log.status === "FAILED") return "Sync failed";
  // An IN_PROGRESS marker is written before the sync deletes the cache and is
  // only cleared when it finishes. `isSyncing` above is per-mount local state,
  // so after a reload or in a second tab it is false while a perfectly healthy
  // sync is still running — this branch is the ONLY thing that reports it.
  // It must not claim "interrupted": a stranded marker and a running sync are
  // indistinguishable from here, and "Syncing…" is the honest reading of both.
  if (log.status === "IN_PROGRESS") return "Syncing…";
  if (!log.synced_at) return "Unknown";

  const minutes = Math.floor((now - parseUtc(log.synced_at).getTime()) / 60_000);
  if (minutes < 2)  return "Synced just now";
  if (minutes < 60) return `Synced ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24)   return `Synced ${hours}h ago`;
  return `Synced ${Math.floor(hours / 24)}d ago`;
}

export function buildSyncColor(log: SyncLogOrUnknown, isSyncing: boolean, now: number): string {
  if (isSyncing) return theme.color.accent;
  if (log === undefined) return theme.color.textMuted;
  if (!log || log.status === "NOT_SYNCED") return theme.color.danger;
  if (log.status === "FAILED") return theme.color.danger;
  // Same token as the local isSyncing branch above — in-flight, not alarming.
  if (log.status === "IN_PROGRESS") return theme.color.accent;
  if (!log.synced_at) return theme.color.textMuted;

  const hours = (now - parseUtc(log.synced_at).getTime()) / 3_600_000;
  if (hours < 1) return theme.color.success;
  if (hours < 6) return theme.color.textMuted;
  return theme.color.warn;
}
