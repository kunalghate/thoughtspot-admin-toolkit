/**
 * SharingPreviewTable — flat list of (object × principal) rows with the diff.
 *
 * Rows where `will_change` is false are dimmed so the operator can see
 * what's already in the desired state.
 */
import { useMemo, useState } from "react";

import { theme } from "@/lib/theme";
import type { SharingPreviewRow } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard", ANSWER: "Answer", WORKSHEET: "Worksheet",
  LOGICAL_TABLE: "Table", ONE_TO_ONE_LOGICAL: "Table",
};

const MODE_COLORS: Record<string, { bg: string; fg: string }> = {
  READ_ONLY: { bg: theme.color.successSoft, fg: theme.color.success },
  MODIFY:    { bg: theme.color.warnSoft, fg: theme.color.warn },
  NO_ACCESS: { bg: theme.color.dangerSoft, fg: theme.color.danger },
  "":        { bg: theme.color.surface3, fg: theme.color.textMuted },
};

export function SharingPreviewTable({ rows }: { rows: SharingPreviewRow[] }) {
  const [hideUnchanged, setHideUnchanged] = useState(false);
  const visible = useMemo(
    () => hideUnchanged ? rows.filter((r) => r.will_change) : rows,
    [rows, hideUnchanged],
  );

  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      border: `1px solid ${theme.color.border}`, borderRadius: 6,
      background: theme.color.surface, overflow: "hidden", minHeight: 0,
    }}>
      <div style={{
        display: "flex", alignItems: "center", padding: "8px 12px",
        borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface,
        fontSize: 12, fontFamily: theme.font.sans,
      }}>
        <span style={{ flex: 1, color: theme.color.textMuted }}>
          {rows.length} pair{rows.length === 1 ? "" : "s"} ·{" "}
          {rows.filter((r) => r.will_change).length} changing
        </span>
        <label style={{ display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input
            type="checkbox" checked={hideUnchanged}
            onChange={(e) => setHideUnchanged(e.target.checked)}
          />
          <span style={{ color: theme.color.textPrimary }}>Hide unchanged</span>
        </label>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "2fr 80px 1.5fr 100px 30px 100px",
        padding: "8px 12px", background: theme.color.surface,
        borderBottom: `1px solid ${theme.color.border}`, fontSize: 10, fontWeight: 600,
        color: theme.color.textMuted, textTransform: "uppercase", letterSpacing: "0.04em",
        fontFamily: theme.font.sans,
      }}>
        <div>Object</div>
        <div>Type</div>
        <div>Principal</div>
        <div style={{ textAlign: "center" }}>Was</div>
        <div></div>
        <div style={{ textAlign: "center" }}>Will be</div>
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
        {visible.length === 0 && (
          <div style={{
            padding: 24, textAlign: "center", color: theme.color.textMuted, fontSize: 12,
            fontFamily: theme.font.sans,
          }}>
            {hideUnchanged ? "Nothing will change." : "No pairs to display."}
          </div>
        )}
        {visible.map((r, idx) => {
          const prev = MODE_COLORS[r.previous_mode] ?? MODE_COLORS[""];
          const next = MODE_COLORS[r.new_mode] ?? MODE_COLORS[""];
          return (
            <div key={`${r.object_guid}:${r.principal_guid}:${idx}`} style={{
              display: "grid",
              gridTemplateColumns: "2fr 80px 1.5fr 100px 30px 100px",
              alignItems: "center", padding: "6px 12px", fontSize: 12,
              borderBottom: `1px solid ${theme.color.border}`,
              opacity: r.will_change ? 1 : 0.55,
              fontFamily: theme.font.sans,
            }}>
              <div style={{ color: theme.color.textPrimary }}>{r.object_name}</div>
              <div>
                <span style={{
                  padding: "1px 6px", fontSize: 10, fontWeight: 600,
                  borderRadius: 8, background: theme.color.surface3, color: theme.color.textSecondary,
                }}>{TYPE_LABELS[r.object_type] ?? r.object_type}</span>
              </div>
              <div style={{ color: theme.color.textPrimary }}>
                {r.principal_name}{" "}
                <span style={{ fontSize: 10, color: theme.color.textMuted }}>
                  ({r.principal_type === "USER_GROUP" ? "group" : "user"})
                </span>
              </div>
              <div style={{ textAlign: "center" }}>
                <span style={{
                  padding: "1px 6px", fontSize: 10, fontWeight: 600,
                  borderRadius: 8, background: prev.bg, color: prev.fg,
                }}>{r.previous_mode || "—"}</span>
              </div>
              <div style={{ textAlign: "center", color: r.will_change ? theme.color.accent : theme.color.violetBorder }}>
                →
              </div>
              <div style={{ textAlign: "center" }}>
                <span style={{
                  padding: "1px 6px", fontSize: 10, fontWeight: 600,
                  borderRadius: 8, background: next.bg, color: next.fg,
                }}>{r.new_mode}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
