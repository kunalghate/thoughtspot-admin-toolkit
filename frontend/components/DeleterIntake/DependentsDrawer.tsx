/**
 * Dependents preview drawer for Downstream mode.
 *
 * Opens on row-click in the picker (non-destructive). Calls
 * deleterApi.resolveDownstream to show what would be deleted, with a
 * "Use as deletion root" button that commits the row and hands back the
 * already-fetched response so the parent grid can populate without a
 * duplicate API call.
 */
import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { deleterApi } from "@/lib/api";
import type { DeleterResolveResponse, RootSearchItem } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  WORKSHEET:          "Worksheet",
  LOGICAL_TABLE:      "Worksheet",
  ONE_TO_ONE_LOGICAL: "Table",
  TABLE:              "Table",
  MODEL:              "Model",
  VIEW:               "View",
  AGGR_WORKSHEET:     "Agg Worksheet",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "User Defined",
};

interface Props {
  object: RootSearchItem | null;
  clusterId: string;
  orgId: number;
  onClose: () => void;
  onCommit: (root: RootSearchItem, prefetched: DeleterResolveResponse) => void;
}

export default function DependentsDrawer({ object, clusterId, orgId, onClose, onCommit }: Props) {
  const [response, setResponse] = useState<DeleterResolveResponse | null>(null);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);

  useEffect(() => {
    const handle = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handle);
    return () => document.removeEventListener("keydown", handle);
  }, [onClose]);

  useEffect(() => {
    if (!object) return;
    setResponse(null);
    setError(null);
    setLoading(true);
    deleterApi
      .resolveDownstream({
        cluster_id: clusterId,
        org_id: orgId,
        root_guid: object.ts_guid,
        root_type: object.object_type,
      })
      .then(setResponse)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [object?.ts_guid, clusterId, orgId]);

  if (!object) return null;

  const total = response?.items.length ?? 0;
  const byTypeEntries = response ? Object.entries(response.by_type) : [];

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.15)", zIndex: 200 }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 480,
        background: "#FFFFFF", boxShadow: "-4px 0 24px rgba(0,0,0,0.12)",
        zIndex: 201, display: "flex", flexDirection: "column",
        fontFamily: "Geist, sans-serif",
      }}>

        {/* Header */}
        <div style={{
          padding: "20px 24px", borderBottom: "1px solid #E8E1D5",
          display: "flex", alignItems: "flex-start", gap: 12,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontSize: 11, color: "#7A7068", marginBottom: 4,
              textTransform: "uppercase", letterSpacing: "0.05em",
            }}>
              Deletion preview
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: "#1A1714", wordBreak: "break-word" }}>
              {object.name}
            </div>
            <div style={{ fontSize: 12, color: "#7A7068", marginTop: 4 }}>
              {TYPE_LABELS[object.object_type] ?? object.object_type}
              {object.owner_name ? ` · ${object.owner_name}` : ""}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", cursor: "pointer",
            color: "#7A7068", padding: 4, flexShrink: 0,
          }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
          {loading && (
            <div style={{ color: "#7A7068", fontSize: 13, textAlign: "center", marginTop: 40 }}>
              Resolving dependents…
            </div>
          )}

          {error && (
            <div style={{
              background: "#FEF2F2", border: "1px solid #FECACA", borderRadius: 8,
              padding: "12px 16px", fontSize: 13, color: "#991B1B",
            }}>
              {error}
            </div>
          )}

          {!loading && !error && response && (
            <>
              {/* Impact summary */}
              <div style={{
                background: total > 0 ? "#FFFBEB" : "#F0FDF4",
                border: `1px solid ${total > 0 ? "#FDE68A" : "#BBF7D0"}`,
                borderRadius: 8, padding: "12px 14px", marginBottom: 16,
                display: "flex", alignItems: "flex-start", gap: 10,
              }}>
                <AlertTriangle size={16} color={total > 0 ? "#92400E" : "#166534"} style={{ flexShrink: 0, marginTop: 2 }} />
                <div style={{ fontSize: 13, color: total > 0 ? "#78350F" : "#14532D" }}>
                  {total > 0 ? (
                    <>
                      Deleting this would also delete <strong>{total}</strong> downstream
                      object{total === 1 ? "" : "s"} that depend on it.
                    </>
                  ) : (
                    <>No downstream dependents — only the root would be deleted.</>
                  )}
                </div>
              </div>

              {/* By-type breakdown */}
              {byTypeEntries.length > 0 && (
                <div style={{
                  display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16,
                }}>
                  {byTypeEntries.map(([t, n]) => (
                    <span key={t} style={{
                      display: "inline-block", padding: "3px 10px", borderRadius: 20,
                      fontSize: 11, fontWeight: 500, background: "#F3F4F6", color: "#374151",
                    }}>
                      {n} {TYPE_LABELS[t] ?? t}{n > 1 ? "s" : ""}
                    </span>
                  ))}
                </div>
              )}

              {/* Items list */}
              {response.items.length > 0 && (
                <div style={{
                  fontSize: 11, fontWeight: 600, color: "#7A7068",
                  textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8,
                }}>
                  Affected objects
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {response.items.map((item) => (
                  <div key={item.ts_guid} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                    background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 6,
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13, color: "#1A1714",
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {item.name}
                      </div>
                      {item.owner_name && (
                        <div style={{ fontSize: 11, color: "#7A7068" }}>{item.owner_name}</div>
                      )}
                    </div>
                    <span style={{
                      padding: "1px 8px", borderRadius: 12, fontSize: 10, fontWeight: 500,
                      background: "#F3F4F6", color: "#374151", flexShrink: 0, lineHeight: "16px",
                    }}>
                      {TYPE_LABELS[item.object_type] ?? item.object_type}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        {!loading && !error && response && (
          <div style={{
            padding: "12px 24px", borderTop: "1px solid #E8E1D5",
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontSize: 12, color: "#7A7068" }}>
              {total + 1} total object{total === 0 ? "" : "s"} would be deleted
            </span>
            <div style={{ flex: 1 }} />
            <button onClick={onClose} style={{
              padding: "7px 14px", fontSize: 13, fontWeight: 500,
              background: "white", border: "1px solid #E8E1D5", borderRadius: 6,
              cursor: "pointer", color: "#7A7068", fontFamily: "Geist, sans-serif",
            }}>Cancel</button>
            <button onClick={() => onCommit(object, response)} style={{
              padding: "7px 14px", fontSize: 13, fontWeight: 600,
              background: "#6D28D9", color: "white", border: "none", borderRadius: 6,
              cursor: "pointer", fontFamily: "Geist, sans-serif",
            }}>
              Use as deletion root
            </button>
          </div>
        )}
      </div>
    </>
  );
}
