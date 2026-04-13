/**
 * Shared utility functions used across multiple pages.
 */

/**
 * Format an ISO 8601 timestamp as a human-readable relative date.
 * Returns "Never" for null/undefined values.
 */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}
