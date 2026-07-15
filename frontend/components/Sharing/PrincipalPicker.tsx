/**
 * PrincipalPicker — multi-select for users + groups (the "share with" target).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X, User, Users } from "lucide-react";

import { sharingApi } from "@/lib/api";
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
          border: `1px solid ${open ? "#C4B5FD" : "#E8E1D5"}`, background: "white",
          borderRadius: 6, padding: "6px 10px",
        }}>
          <Search size={14} style={{ color: "#7A7068" }} />
          <input
            type="text"
            value={query}
            placeholder="Search users or groups…"
            onFocus={() => { if (blurTimer.current) clearTimeout(blurTimer.current); setOpen(true); }}
            onBlur={() => { blurTimer.current = setTimeout(() => setOpen(false), 150); }}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1, fontSize: 13, fontFamily: "Geist, sans-serif",
              border: "none", outline: "none", background: "transparent",
            }}
          />
        </div>

        {open && (
          <div style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 30,
            marginTop: 4, maxHeight: 280, overflowY: "auto",
            background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
            boxShadow: "0 8px 24px rgba(0,0,0,0.08)",
          }}>
            {loading && <div style={{ padding: 10, fontSize: 12, color: "#7A7068" }}>Searching…</div>}
            {!loading && visibleResults.length === 0 && (
              <div style={{ padding: 10, fontSize: 12, color: "#7A7068" }}>No matches.</div>
            )}
            {visibleResults.map((p) => (
              <button
                key={p.ts_guid + ":" + p.principal_type}
                onMouseDown={(e) => { e.preventDefault(); toggle(p); }}
                style={{
                  display: "flex", alignItems: "center", gap: 8, width: "100%",
                  textAlign: "left", padding: "8px 12px", fontSize: 12,
                  border: "none", background: "transparent", cursor: "pointer",
                  fontFamily: "Geist, sans-serif", borderBottom: "1px solid #F2EDE3",
                }}
              >
                {p.principal_type === "USER_GROUP"
                  ? <Users size={12} style={{ color: "#6D28D9" }} />
                  : <User size={12} style={{ color: "#7A7068" }} />}
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, color: "#1A1714" }}>{p.display_name}</div>
                  <div style={{ fontSize: 10, color: "#7A7068" }}>{p.name}</div>
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
              background: p.principal_type === "USER_GROUP" ? "#EDE9FE" : "#F2EDE3",
              color: p.principal_type === "USER_GROUP" ? "#6D28D9" : "#374151",
              borderRadius: 12, fontFamily: "Geist, sans-serif",
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
