/**
 * Downstream mode intake — type-ahead search to pick a root object,
 * then "Resolve dependents" calls the API. Reports the picked root up
 * to the parent which then displays the dependents grid.
 */
import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { deleterApi } from "@/lib/api";
import type { RootSearchItem } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  WORKSHEET: "Worksheet",
  TABLE: "Table",
  MODEL: "Model",
  VIEW: "View",
  ONE_TO_ONE_LOGICAL: "Table",
};

interface Props {
  clusterId: string;
  orgId: number;
  pickedRoot: RootSearchItem | null;
  onPick: (root: RootSearchItem | null) => void;
}

export function DownstreamPicker({ clusterId, orgId, pickedRoot, onPick }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RootSearchItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim() || pickedRoot) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(() => {
      setLoading(true);
      deleterApi
        .rootSearch({ cluster_id: clusterId, org_id: orgId, query, limit: 25 })
        .then((rows) => setResults(rows))
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 250);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [query, clusterId, orgId, pickedRoot]);

  if (pickedRoot) {
    return (
      <div style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
        background: "#F5F0FF", border: "1px solid #C4B5FD", borderRadius: 6,
      }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#5B21B6" }}>
          Root: {pickedRoot.name}
        </span>
        <span style={{ fontSize: 12, color: "#7A7068" }}>
          ({TYPE_LABELS[pickedRoot.object_type] ?? pickedRoot.object_type} · {pickedRoot.owner_name})
        </span>
        <button
          onClick={() => { onPick(null); setQuery(""); }}
          style={{
            marginLeft: "auto", padding: "4px 10px", fontSize: 12,
            background: "transparent", border: "1px solid #C4B5FD", borderRadius: 4,
            color: "#6D28D9", cursor: "pointer", fontFamily: "Geist, sans-serif",
          }}
        >Change root</button>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", maxWidth: 600 }}>
      <div style={{ position: "relative" }}>
        <Search size={14} style={{
          position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)",
          color: "#9CA3AF", pointerEvents: "none",
        }} />
        <input
          type="text"
          value={query}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Search Worksheets, Tables, Models, Views…"
          style={{
            width: "100%", padding: "9px 12px 9px 34px", fontSize: 13,
            border: "1px solid #E8E1D5", borderRadius: 6,
            fontFamily: "Geist, sans-serif", outline: "none", background: "white",
          }}
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            style={{
              position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
              background: "transparent", border: "none", color: "#9CA3AF", cursor: "pointer",
              padding: 4,
            }}
          ><X size={14} /></button>
        )}
      </div>

      {open && query.trim() && (
        <div style={{
          position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0,
          background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
          maxHeight: 320, overflowY: "auto", zIndex: 10,
          boxShadow: "0 6px 16px -4px rgba(0,0,0,0.08)",
        }}>
          {loading && (
            <div style={{ padding: 12, fontSize: 12, color: "#7A7068" }}>Searching…</div>
          )}
          {!loading && results.length === 0 && (
            <div style={{ padding: 12, fontSize: 12, color: "#7A7068" }}>
              No matching Worksheets/Tables/Models found. (Sync metadata first if the cache is empty.)
            </div>
          )}
          {!loading && results.map((r) => (
            <button
              key={r.ts_guid}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => { onPick(r); setQuery(""); setOpen(false); }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "9px 12px", fontSize: 13, background: "transparent",
                border: "none", borderBottom: "1px solid #F4EFE6", cursor: "pointer",
                fontFamily: "Geist, sans-serif",
              }}
            >
              <div style={{ fontWeight: 500, color: "#0F0E0D" }}>{r.name}</div>
              <div style={{ fontSize: 11, color: "#7A7068", marginTop: 2 }}>
                {TYPE_LABELS[r.object_type] ?? r.object_type} · {r.owner_name}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
