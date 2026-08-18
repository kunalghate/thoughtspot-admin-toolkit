/**
 * Empty-state copy for the grids backed by GET /api/v1/metadata.
 *
 * Those grids (Metadata Explorer, Bulk Sharing) hide objects owned by
 * ThoughtSpot's built-in "System User" — an admin can't share, archive, or
 * delete them, so they are noise. A fresh org contains *nothing else*, so the
 * grid legitimately renders zero rows straight after a green "Synced just now".
 * With no explanation that reads as a broken sync, which is exactly how the bug
 * was reported. `hidden_system_count` on the list response is what lets us tell
 * the three empty-cases apart instead of guessing.
 */

import { theme } from "@/lib/theme";

export function metadataEmptyMessage({
  hiddenSystem,
  isFiltered,
}: {
  hiddenSystem: number;
  isFiltered: boolean;
}): string {
  if (isFiltered) {
    const base = "No objects match the current filters.";
    return hiddenSystem > 0
      ? `${base} ${hiddenSystem.toLocaleString()} system-owned object${hiddenSystem === 1 ? " is" : "s are"} also hidden.`
      : base;
  }
  if (hiddenSystem > 0) {
    return (
      `Sync succeeded — nothing to show. All ${hiddenSystem.toLocaleString()} object${hiddenSystem === 1 ? "" : "s"} ` +
      "cached for this org are ThoughtSpot's built-in content (owned by “System User”), " +
      "which is hidden here because it can't be shared, archived, or deleted."
    );
  }
  return "No content found. Sync metadata first.";
}

/** AG Grid takes the no-rows overlay as an HTML string, so style it inline. */
export function noRowsOverlay(message: string): string {
  return (
    `<div style="max-width: 420px; padding: 0 16px; text-align: center; font-family: ${theme.font.sans}; ` +
    `font-size: 13px; line-height: 1.5; color: ${theme.color.textMuted};">${message}</div>`
  );
}

/** Append the withheld-object count to a grid's row-count label. */
export function withHiddenSuffix(label: string, hiddenSystem: number): string {
  return hiddenSystem > 0 ? `${label} · ${hiddenSystem.toLocaleString()} system hidden` : label;
}
