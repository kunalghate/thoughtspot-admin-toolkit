/**
 * UserPicker — typeahead dropdown used by the offboarding wizards
 * to select the *target* user for transfer / transfer-sharing.
 *
 * Searches the user grid via /api/v1/users?search= with a small debounce.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, Check } from "lucide-react";

import { usersApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { UserListItem } from "@/lib/types";

export function UserPicker({
  clusterId,
  orgId,
  picked,
  excludeGuid,
  onPick,
  placeholder = "Search users…",
}: {
  clusterId: string;
  orgId?: number;
  picked: UserListItem | null;
  excludeGuid?: string;
  onPick: (u: UserListItem | null) => void;
  placeholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<UserListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // `cancelled` drops a stale response so a slow earlier query can't overwrite
  // the results of a later, faster one. `excludeGuid` filtering is done at render
  // so it doesn't need to be a fetch dependency.
  useEffect(() => {
    if (!open || !clusterId) return;
    let cancelled = false;
    const handle = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await usersApi.list({
          cluster_id: clusterId,
          org_id: orgId,
          search: query || undefined,
          status: "ACTIVE",
          page_size: 20,
        });
        if (!cancelled) setResults(res.items);
      } catch {
        if (!cancelled) setResults([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [query, open, clusterId, orgId]);

  const visibleResults = useMemo(
    () => results.filter((u) => u.ts_guid !== excludeGuid),
    [results, excludeGuid],
  );

  return (
    <div style={{ position: "relative", width: "100%" }}>
      {picked ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, padding: "9px 12px",
          border: `1px solid ${theme.color.violetBorder}`, background: theme.color.accentSoft, borderRadius: 6,
          fontFamily: theme.font.sans, fontSize: 13,
        }}>
          <Check size={14} style={{ color: theme.color.accent }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, color: theme.color.textPrimary }}>
              {picked.display_name || picked.username}
            </div>
            <div style={{ fontSize: 11, color: theme.color.textMuted }}>
              {picked.username}{picked.email ? ` · ${picked.email}` : ""}
            </div>
          </div>
          <button
            onClick={() => { onPick(null); setQuery(""); }}
            style={{
              padding: "3px 10px", fontSize: 11, fontWeight: 500,
              border: `1px solid ${theme.color.border}`, background: theme.color.surface, borderRadius: 4,
              cursor: "pointer", color: theme.color.textMuted, fontFamily: theme.font.sans,
            }}
          >Change</button>
        </div>
      ) : (
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          border: `1px solid ${open ? theme.color.violetBorder : theme.color.border}`, background: theme.color.surface,
          borderRadius: 6, padding: "6px 10px",
        }}>
          <Search size={14} style={{ color: theme.color.textMuted }} />
          <input
            type="text"
            value={query}
            placeholder={placeholder}
            onFocus={() => { if (blurTimer.current) clearTimeout(blurTimer.current); setOpen(true); }}
            onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150); }}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1, fontSize: 13, fontFamily: theme.font.sans,
              border: "none", outline: "none", background: "transparent",
              color: theme.color.textPrimary,
            }}
          />
        </div>
      )}

      {open && !picked && (
        <div style={{
          position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30,
          marginTop: 4, maxHeight: 240, overflowY: "auto",
          background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
          boxShadow: theme.shadow.md,
        }}>
          {loading && <div style={{ padding: 10, fontSize: 12, color: theme.color.textMuted }}>Searching…</div>}
          {!loading && visibleResults.length === 0 && (
            <div style={{ padding: 10, fontSize: 12, color: theme.color.textMuted }}>No matches.</div>
          )}
          {visibleResults.map((u) => (
            <button
              key={u.ts_guid}
              onMouseDown={(e) => { e.preventDefault(); onPick(u); setOpen(false); setQuery(""); }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 12px", fontSize: 12, border: "none",
                background: "transparent", cursor: "pointer",
                fontFamily: theme.font.sans, borderBottom: `1px solid ${theme.color.bg}`,
              }}
            >
              <div style={{ fontWeight: 500, color: theme.color.textPrimary }}>{u.display_name || u.username}</div>
              <div style={{ fontSize: 11, color: theme.color.textMuted }}>
                {u.username}{u.email ? ` · ${u.email}` : ""}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
