/**
 * Fan-out drawer: the full, paginated consumer list behind a collapsed count
 * node. Searchable client-side within the loaded page; "Load more" pages through
 * the server-side /consumers endpoint so the graph payload never balloons.
 */
import { useCallback, useEffect, useState } from "react";
import { X, ExternalLink } from "lucide-react";
import { relationshipsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { ConsumerItem, RootKind } from "@/lib/types";
import { styleFor, tabForNodeType } from "./nodeStyles";

const PAGE = 100;

export function ConsumersDrawer({
  rootKind,
  guid,
  consumerType,
  clusterId,
  orgId,
  onJump,
  onClose,
}: {
  rootKind: RootKind;
  guid: string;
  consumerType: string;
  clusterId: string;
  orgId: number;
  onJump: (guid: string, nodeType: string) => void;
  onClose: () => void;
}) {
  const [items, setItems] = useState<ConsumerItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setError(null);
    try {
      const res = await relationshipsApi.consumers(rootKind, guid, {
        cluster_id: clusterId, org_id: orgId, type: consumerType, offset: nextOffset, limit: PAGE,
      });
      setItems((prev) => (nextOffset === 0 ? res.items : [...prev, ...res.items]));
      setTotal(res.total);
      setOffset(nextOffset + res.items.length);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [rootKind, guid, consumerType, clusterId, orgId]);

  useEffect(() => { load(0); }, [load]);

  const consumerStyle = styleFor(consumerType);
  const label = consumerStyle.label;
  const HeaderIcon = consumerStyle.Icon;
  const shown = items.filter((i) => !search.trim() || i.name.toLowerCase().includes(search.trim().toLowerCase()));

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", justifyContent: "flex-end" }}
    >
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: theme.color.overlay }} />
      <div
        style={{
          position: "relative", width: 400, maxWidth: "90vw", height: "100%",
          background: theme.color.surface, borderLeft: `1px solid ${theme.color.border}`,
          boxShadow: theme.shadow.lg, display: "flex", flexDirection: "column",
        }}
      >
        <div
          style={{
            display: "flex", alignItems: "center", gap: 8, padding: "14px 16px",
            borderBottom: `1px solid ${theme.color.border}`, flexShrink: 0,
          }}
        >
          <HeaderIcon size={18} style={{ color: consumerStyle.color, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
              {total ?? "…"} {label}{(total ?? 0) === 1 ? "" : "s"}
            </div>
            <div style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              Objects that depend on this one
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: "transparent", border: "none", cursor: "pointer", color: theme.color.textMuted, padding: 4 }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: 10, flexShrink: 0 }}>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filter loaded results…"
            style={{
              width: "100%", padding: "7px 10px", fontSize: 13, boxSizing: "border-box",
              fontFamily: theme.font.sans, border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.control, background: theme.color.bg, outline: "none",
              color: theme.color.textPrimary,
            }}
          />
        </div>

        <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }}>
          {error && (
            <div style={{ padding: 14, fontSize: 12, color: theme.color.danger, fontFamily: theme.font.sans }}>
              {error}
            </div>
          )}
          {shown.map((c) => (
            <button
              key={c.guid}
              onClick={() => { onJump(c.guid, c.node_type); onClose(); }}
              style={{
                display: "flex", alignItems: "center", gap: 8, width: "100%", textAlign: "left",
                padding: "9px 14px", border: "none", borderBottom: `1px solid ${theme.color.borderLight}`,
                cursor: "pointer", background: "transparent", fontFamily: theme.font.sans,
              }}
            >
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={{ display: "block", fontSize: 13, color: theme.color.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.name}
                </span>
                {c.owner_name && (
                  <span style={{ display: "block", fontSize: 11, color: theme.color.textMuted }}>{c.owner_name}</span>
                )}
              </span>
              <ExternalLink size={13} style={{ color: theme.color.textMuted, flexShrink: 0 }} />
            </button>
          ))}
          {total != null && offset < total && (
            <button
              onClick={() => load(offset)}
              disabled={loading}
              style={{
                display: "block", width: "100%", padding: "10px 14px", border: "none",
                background: "transparent", color: theme.color.accent2, cursor: loading ? "wait" : "pointer",
                fontSize: 12, fontWeight: 600, fontFamily: theme.font.sans,
              }}
            >
              {loading ? "Loading…" : `Load ${Math.min(PAGE, total - offset)} more`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** The consumer node's click target routes to its own tab (used by callers). */
export { tabForNodeType };
