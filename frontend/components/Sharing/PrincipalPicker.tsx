/**
 * PrincipalPicker — multi-select for users + groups (the "share with" target).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X, User, Users } from "lucide-react";

import { sharingApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { PrincipalPickerItem } from "@/lib/types";

export function PrincipalPicker({
  clusterId,
  orgId,
  selected,
  onChange,
}: {
  clusterId: string;
  orgId: number;
  selected: PrincipalPickerItem[];
  onChange: (next: PrincipalPickerItem[]) => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<PrincipalPickerItem[]>([]);
  const [loading, setLoading] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch is keyed only on the search inputs — NOT on `selected`, which would
  // refire a request on every add/remove. Selection filtering happens at render.
  // `cancelled` drops a stale response so a slow earlier query can't overwrite
  // the results of a later, faster one.
  useEffect(() => {
    if (!open || !clusterId) return;
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await sharingApi.principals({
          cluster_id: clusterId,
          org_id: orgId,
          search: query || undefined,
          limit: 50,
        });
        if (!cancelled) setResults(res.items);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [open, query, clusterId, orgId]);

  // Hide already-selected principals without re-fetching.
  const visibleResults = useMemo(() => {
    const selectedSet = new Set(selected.map((s) => s.ts_guid));
    return results.filter((p) => !selectedSet.has(p.ts_guid));
  }, [results, selected]);

  function toggle(p: PrincipalPickerItem) {
    onChange([...selected, p]);
    setQuery("");
  }

  function remove(guid: string) {
    onChange(selected.filter((p) => p.ts_guid !== guid));
  }

  return (
    <div>
      <div style={{ position: "relative" }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          border: `1px solid ${open ? theme.color.violetBorder : theme.color.border}`, background: theme.color.surface,
          borderRadius: 6, padding: "6px 10px",
        }}>
          <Search size={14} style={{ color: theme.color.textMuted }} />
          <input
            type="text"
            value={query}
            placeholder="Search users or groups…"
            onFocus={() => { if (blurTimer.current) clearTimeout(blurTimer.current); setOpen(true); }}
            onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150); }}
            onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } }}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1, fontSize: 13, fontFamily: theme.font.sans,
              border: "none", outline: "none", background: "transparent",
              color: theme.color.textPrimary,
            }}
          />
        </div>

        {/* In-flow (not an absolute overlay): an overlay here covers the modal's
            footer, so the user's first click on "Preview changes" gets eaten by
            a result row. Rendering in-flow pushes content down instead. */}
        {open && (
          <div style={{
            marginTop: 4, maxHeight: 200, overflowY: "auto",
            background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
            boxShadow: theme.shadow.sm,
          }}>
            {loading && <div style={{ padding: 10, fontSize: 12, color: theme.color.textMuted }}>Searching…</div>}
            {!loading && visibleResults.length === 0 && (
              <div style={{ padding: 10, fontSize: 12, color: theme.color.textMuted }}>No matches.</div>
            )}
            {visibleResults.map((p) => (
              <button
                key={p.ts_guid + ":" + p.principal_type}
                onMouseDown={(e) => { e.preventDefault(); toggle(p); }}
                style={{
                  display: "flex", alignItems: "center", gap: 8, width: "100%",
                  textAlign: "left", padding: "8px 12px", fontSize: 12,
                  border: "none", background: "transparent", cursor: "pointer",
                  fontFamily: theme.font.sans, borderBottom: `1px solid ${theme.color.border}`,
                }}
              >
                {p.principal_type === "USER_GROUP"
                  ? <Users size={12} style={{ color: theme.color.accent2 }} />
                  : <User size={12} style={{ color: theme.color.textMuted }} />}
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, color: theme.color.textPrimary }}>{p.display_name}</div>
                  <div style={{ fontSize: 10, color: theme.color.textMuted }}>{p.name}</div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected.length > 0 && (
        <div style={{
          display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8,
        }}>
          {selected.map((p) => (
            <span key={p.ts_guid + ":" + p.principal_type} style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "3px 8px", fontSize: 11, fontWeight: 500,
              background: p.principal_type === "USER_GROUP" ? theme.color.violetSoft : theme.color.surface2,
              color: p.principal_type === "USER_GROUP" ? theme.color.accent2 : theme.color.textSecondary,
              borderRadius: 12, fontFamily: theme.font.sans,
            }}>
              {p.principal_type === "USER_GROUP" ? <Users size={11} /> : <User size={11} />}
              {p.display_name}
              <button
                onClick={() => remove(p.ts_guid)}
                style={{
                  border: "none", background: "transparent", cursor: "pointer",
                  padding: 0, display: "flex",
                }}
              ><X size={11} style={{ color: "inherit" }} /></button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
