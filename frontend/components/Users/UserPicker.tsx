/**
 * UserPicker — typeahead dropdown used by the offboarding wizards
 * to select the *target* user for transfer / transfer-sharing.
 *
 * Searches the user grid via /api/v1/users?search= with a small debounce.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, Check } from "lucide-react";

import { usersApi } from "@/lib/api";
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
          border: "1px solid #C4B5FD", background: "#F5F0FF", borderRadius: 6,
          fontFamily: "Geist, sans-serif", fontSize: 13,
        }}>
          <Check size={14} style={{ color: "#6D28D9" }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, color: "#1A1714" }}>
              {picked.display_name || picked.username}
            </div>
            <div style={{ fontSize: 11, color: "#7A7068" }}>
              {picked.username}{picked.email ? ` · ${picked.email}` : ""}
            </div>
          </div>
          <button
            onClick={() => { onPick(null); setQuery(""); }}
            style={{
              padding: "3px 10px", fontSize: 11, fontWeight: 500,
              border: "1px solid #E8E1D5", background: "white", borderRadius: 4,
              cursor: "pointer", color: "#7A7068", fontFamily: "Geist, sans-serif",
            }}
          >Change</button>
        </div>
      ) : (
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          border: `1px solid ${open ? "#C4B5FD" : "#E8E1D5"}`, background: "white",
          borderRadius: 6, padding: "6px 10px",
        }}>
          <Search size={14} style={{ color: "#7A7068" }} />
          <input
            type="text"
            value={query}
            placeholder={placeholder}
            onFocus={() => { if (blurTimer.current) clearTimeout(blurTimer.current); setOpen(true); }}
            onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150); }}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1, fontSize: 13, fontFamily: "Geist, sans-serif",
              border: "none", outline: "none", background: "transparent",
            }}
          />
        </div>
      )}

      {open && !picked && (
        <div style={{
          position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30,
          marginTop: 4, maxHeight: 240, overflowY: "auto",
          background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
          boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
        }}>
          {loading && <div style={{ padding: 10, fontSize: 12, color: "#7A7068" }}>Searching…</div>}
          {!loading && visibleResults.length === 0 && (
            <div style={{ padding: 10, fontSize: 12, color: "#7A7068" }}>No matches.</div>
          )}
          {visibleResults.map((u) => (
            <button
              key={u.ts_guid}
              onMouseDown={(e) => { e.preventDefault(); onPick(u); setOpen(false); setQuery(""); }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "8px 12px", fontSize: 12, border: "none",
                background: "transparent", cursor: "pointer",
                fontFamily: "Geist, sans-serif", borderBottom: "1px solid #F2EDE3",
              }}
            >
              <div style={{ fontWeight: 500, color: "#1A1714" }}>{u.display_name || u.username}</div>
              <div style={{ fontSize: 11, color: "#7A7068" }}>
                {u.username}{u.email ? ` · ${u.email}` : ""}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
