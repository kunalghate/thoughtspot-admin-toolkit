/**
 * Searchable left list for one explorer tab. Supports a subtype filter
 * (Model / Table / Dataset / View — logical-tables tab only) and pins the
 * currently-selected / jumped-to object to the top.
 */
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { theme } from "@/lib/theme";
import type { TopologyItem } from "@/lib/types";
import { styleFor } from "./nodeStyles";

export function ObjectList({
  items,
  selectedGuid,
  pinnedGuids,
  showSubtypeFilter,
  onSelect,
}: {
  items: TopologyItem[];
  selectedGuid: string | null;
  pinnedGuids: Set<string>;
  showSubtypeFilter: boolean;
  onSelect: (item: TopologyItem) => void;
}) {
  const [search, setSearch] = useState("");
  const [subtype, setSubtype] = useState("");

  const subtypes = useMemo(() => {
    const s = new Set<string>();
    items.forEach((i) => s.add(i.subtype));
    return Array.from(s).sort();
  }, [items]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const rows = items.filter((i) => {
      if (subtype && i.subtype !== subtype) return false;
      if (q && !i.name.toLowerCase().includes(q) && !i.owner_name.toLowerCase().includes(q)) return false;
      return true;
    });
    // Pinned objects float to the top; otherwise alphabetical (already sorted).
    return rows.sort((a, b) => {
      const ap = pinnedGuids.has(a.ts_guid) ? 0 : 1;
      const bp = pinnedGuids.has(b.ts_guid) ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return a.name.localeCompare(b.name);
    });
  }, [items, search, subtype, pinnedGuids]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ padding: 10, display: "flex", flexDirection: "column", gap: 8, flexShrink: 0 }}>
        <div style={{ position: "relative" }}>
          <Search
            size={14}
            style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: theme.color.textMuted }}
          />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search…"
            style={{
              width: "100%", padding: "7px 10px 7px 30px", fontSize: 13, boxSizing: "border-box",
              fontFamily: theme.font.sans, border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.control, background: theme.color.surface, outline: "none",
              color: theme.color.textPrimary,
            }}
          />
        </div>
        {showSubtypeFilter && subtypes.length > 1 && (
          <select
            value={subtype}
            onChange={(e) => setSubtype(e.target.value)}
            style={{
              padding: "6px 8px", fontSize: 12, fontFamily: theme.font.sans,
              border: `1px solid ${theme.color.border}`, borderRadius: theme.radius.control,
              background: theme.color.surface, cursor: "pointer", color: theme.color.textPrimary,
            }}
          >
            <option value="">All types</option>
            {subtypes.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 16, fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
            No objects match.
          </div>
        ) : (
          filtered.map((item) => {
            const s = styleFor(item.node_type);
            const active = item.ts_guid === selectedGuid;
            const pinned = pinnedGuids.has(item.ts_guid);
            return (
              <button
                key={item.ts_guid}
                onClick={() => onSelect(item)}
                title={`${item.name} · ${item.owner_name || "—"}`}
                style={{
                  display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
                  padding: "8px 12px", border: "none", cursor: "pointer",
                  borderLeft: active ? `2px solid ${s.color}` : "2px solid transparent",
                  background: active ? theme.color.accentSoft : "transparent",
                  fontFamily: theme.font.sans,
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: 2, background: s.color, flexShrink: 0 }} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span
                    style={{
                      display: "block", fontSize: 13, color: theme.color.textPrimary,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      fontWeight: active ? 600 : 400,
                    }}
                  >
                    {item.name}
                  </span>
                  <span style={{ display: "block", fontSize: 11, color: theme.color.textMuted }}>
                    {item.subtype}
                  </span>
                </span>
                {pinned && (
                  <span style={{ fontSize: 10, color: s.color, fontWeight: 600 }}>PIN</span>
                )}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
