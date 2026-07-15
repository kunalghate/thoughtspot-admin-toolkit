/**
 * Searchable left list for one explorer tab. Supports a subtype filter
 * (Model / Table / Dataset / View — logical-tables tab only). Objects you've
 * opened or jumped to surface in a labeled "Recently viewed" section on top.
 */
import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { theme } from "@/lib/theme";
import type { TopologyItem } from "@/lib/types";
import { styleFor } from "./nodeStyles";

export function ObjectList({
  items,
  selectedGuid,
  recentGuids,
  showSubtypeFilter,
  onSelect,
}: {
  items: TopologyItem[];
  selectedGuid: string | null;
  /** Most-recent-first guids of objects opened or jumped to. */
  recentGuids: string[];
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

  const { recentRows, restRows } = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = (i: TopologyItem) => {
      if (subtype && i.subtype !== subtype) return false;
      if (q && !i.name.toLowerCase().includes(q) && !i.owner_name.toLowerCase().includes(q)) return false;
      return true;
    };
    const byGuid = new Map(items.map((i) => [i.ts_guid, i]));
    const recentSet = new Set(recentGuids);
    const recentRows = recentGuids
      .map((g) => byGuid.get(g))
      .filter((i): i is TopologyItem => !!i && matches(i));
    const restRows = items
      .filter((i) => matches(i) && !recentSet.has(i.ts_guid))
      .sort((a, b) => a.name.localeCompare(b.name));
    return { recentRows, restRows };
  }, [items, search, subtype, recentGuids]);

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
            placeholder="Search by name or owner…"
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
            <option value="">All subtypes</option>
            {subtypes.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
        {recentRows.length + restRows.length === 0 ? (
          <div style={{ padding: 16, fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
            No objects match.
          </div>
        ) : (
          <>
            {recentRows.length > 0 && (
              <>
                <SectionLabel>Recently viewed</SectionLabel>
                {recentRows.map((item) => (
                  <Row key={item.ts_guid} item={item} active={item.ts_guid === selectedGuid} onSelect={onSelect} />
                ))}
                <SectionLabel>All objects · {restRows.length.toLocaleString()}</SectionLabel>
              </>
            )}
            {restRows.map((item) => (
              <Row key={item.ts_guid} item={item} active={item.ts_guid === selectedGuid} onSelect={onSelect} />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        padding: "8px 12px 4px", fontSize: 10.5, fontWeight: 600, letterSpacing: "0.05em",
        textTransform: "uppercase", color: theme.color.textMuted, fontFamily: theme.font.sans,
      }}
    >
      {children}
    </div>
  );
}

function Row({
  item,
  active,
  onSelect,
}: {
  item: TopologyItem;
  active: boolean;
  onSelect: (item: TopologyItem) => void;
}) {
  const s = styleFor(item.node_type);
  const Icon = s.Icon;
  return (
    <button
      onClick={() => onSelect(item)}
      title={`${item.name} · ${item.owner_name || "—"}`}
      style={{
        display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left",
        padding: "8px 12px", border: "none", cursor: "pointer",
        borderLeft: active ? `2px solid ${s.color}` : "2px solid transparent",
        background: active ? theme.color.accentSoft : "transparent",
        fontFamily: theme.font.sans,
      }}
    >
      <Icon size={14} style={{ color: s.color, flexShrink: 0 }} />
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
    </button>
  );
}
