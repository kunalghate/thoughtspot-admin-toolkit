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
import { theme } from "@/lib/theme";
import type { DeleterResolveResponse, RootSearchItem } from "@/lib/types";
import { TYPE_LABELS } from "@/lib/objectTypes";


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
        style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 200 }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 480,
        background: theme.color.surface, boxShadow: theme.shadow.lg,
        zIndex: 201, display: "flex", flexDirection: "column",
        fontFamily: theme.font.sans,
      }}>

        {/* Header */}
        <div style={{
          padding: "20px 24px", borderBottom: `1px solid ${theme.color.border}`,
          display: "flex", alignItems: "flex-start", gap: 12,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              fontSize: 11, color: theme.color.textMuted, marginBottom: 4,
              textTransform: "uppercase", letterSpacing: "0.05em",
            }}>
              Deletion preview
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, wordBreak: "break-word" }}>
              {object.name}
            </div>
            <div style={{ fontSize: 12, color: theme.color.textMuted, marginTop: 4 }}>
              {TYPE_LABELS[object.object_type] ?? object.object_type}
              {object.owner_name ? ` · ${object.owner_name}` : ""}
            </div>
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", cursor: "pointer",
            color: theme.color.textMuted, padding: 4, flexShrink: 0,
          }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
          {loading && (
            <div style={{ color: theme.color.textMuted, fontSize: 13, textAlign: "center", marginTop: 40 }}>
              Resolving dependents…
            </div>
          )}

          {error && (
            <div style={{
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
              padding: "12px 16px", fontSize: 13, color: theme.color.danger,
            }}>
              {error}
            </div>
          )}

          {!loading && !error && response && (
            <>
              {/* Impact summary */}
              <div style={{
                background: total > 0 ? theme.color.warnSoft : theme.color.successSoft,
                border: `1px solid ${total > 0 ? theme.color.warnBorder : theme.color.successBorder}`,
                borderRadius: 8, padding: "12px 14px", marginBottom: 16,
                display: "flex", alignItems: "flex-start", gap: 10,
              }}>
                <AlertTriangle size={16} color={total > 0 ? theme.color.warn : theme.color.success} style={{ flexShrink: 0, marginTop: 2 }} />
                <div style={{ fontSize: 13, color: total > 0 ? theme.color.warn : theme.color.success }}>
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
                      fontSize: 11, fontWeight: 500, background: theme.color.surface3, color: theme.color.textSecondary,
                    }}>
                      {n} {TYPE_LABELS[t] ?? t}{n > 1 ? "s" : ""}
                    </span>
                  ))}
                </div>
              )}

              {/* Items list */}
              {response.items.length > 0 && (
                <div style={{
                  fontSize: 11, fontWeight: 600, color: theme.color.textMuted,
                  textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8,
                }}>
                  Affected objects
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {response.items.map((item) => (
                  <div key={item.ts_guid} style={{
                    display: "flex", alignItems: "center", gap: 10, padding: "8px 12px",
                    background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13, color: theme.color.textPrimary,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>
                        {item.name}
                      </div>
                      {item.owner_name && (
                        <div style={{ fontSize: 11, color: theme.color.textMuted }}>{item.owner_name}</div>
                      )}
                    </div>
                    <span style={{
                      padding: "1px 8px", borderRadius: 12, fontSize: 10, fontWeight: 500,
                      background: theme.color.surface3, color: theme.color.textSecondary, flexShrink: 0, lineHeight: "16px",
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
            padding: "12px 24px", borderTop: `1px solid ${theme.color.border}`,
            display: "flex", alignItems: "center", gap: 10,
          }}>
            <span style={{ fontSize: 12, color: theme.color.textMuted }}>
              {total + 1} total object{total === 0 ? "" : "s"} would be deleted
            </span>
            <div style={{ flex: 1 }} />
            <button onClick={onClose} style={{
              padding: "7px 14px", fontSize: 13, fontWeight: 500,
              background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
              cursor: "pointer", color: theme.color.textMuted, fontFamily: theme.font.sans,
            }}>Cancel</button>
            <button onClick={() => onCommit(object, response)} style={{
              padding: "7px 14px", fontSize: 13, fontWeight: 600,
              background: theme.gradient.accent, color: theme.color.onAccent, border: "none", borderRadius: 6,
              boxShadow: theme.shadow.glowAccent,
              cursor: "pointer", fontFamily: theme.font.sans,
            }}>
              Use as deletion root
            </button>
          </div>
        )}
      </div>
    </>
  );
}
