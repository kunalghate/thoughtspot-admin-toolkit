/**
 * "Export CSV" toolbar button, rendered as a plain download link.
 *
 * The grids use AG Grid's infinite row model, where `exportDataAsCsv()` only
 * serializes the blocks the grid happens to have cached — so an export of
 * "the users" would silently be the first few hundred of them. These buttons
 * point at a backend endpoint that streams every matching row instead.
 *
 * It is an <a download> rather than a fetch + blob so the browser owns the
 * download: no memory ceiling on a large file, and a working progress UI.
 */
import { Download } from "lucide-react";

import { theme } from "@/lib/theme";

export function ExportCsvButton({
  href,
  title,
  label = "Export CSV",
}: {
  /** Absolute API path including the current filters as query params. */
  href: string;
  title?: string;
  label?: string;
}) {
  return (
    <a
      href={href}
      title={title}
      style={{
        display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
        borderRadius: 6, border: `1px solid ${theme.color.border}`, background: theme.color.surface,
        fontSize: 12, fontWeight: 500, color: theme.color.textPrimary, cursor: "pointer",
        fontFamily: theme.font.sans, textDecoration: "none", whiteSpace: "nowrap",
      }}
    >
      <Download size={13} /> {label}
    </a>
  );
}

/** Build an export URL, dropping params the caller left undefined. */
export function exportCsvHref(path: string, params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") q.set(key, String(value));
  }
  const query = q.toString();
  return query ? `${path}?${query}` : path;
}
