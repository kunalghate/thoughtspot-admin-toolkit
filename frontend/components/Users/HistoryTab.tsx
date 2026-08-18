/**
 * UsersHistoryTab — paginated grid of past user-management actions.
 * Backed by GET /api/v1/users/history (UserActionRecord rows).
 */
import { useEffect, useState } from "react";

import { usersApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { UserHistoryItem } from "@/lib/types";

const ACTION_LABELS: Record<string, string> = {
  transfer: "Transfer ownership",
  transfer_sharing: "Transfer sharing",
  delete: "Delete user",
};

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  SUCCESS: { bg: theme.color.successSoft, fg: theme.color.success },
  PARTIAL: { bg: theme.color.warnSoft, fg: theme.color.warn },
  FAILED:  { bg: theme.color.dangerSoft, fg: theme.color.danger },
  PENDING: { bg: theme.color.surface3, fg: theme.color.textSecondary },
};

export function UsersHistoryTab({
  clusterId,
  orgId,
}: {
  clusterId: string;
  orgId?: number;
}) {
  const [items, setItems] = useState<UserHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");

  useEffect(() => {
    if (!clusterId) return;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await usersApi.history({
          cluster_id: clusterId,
          org_id: orgId,
          action_type: filter || undefined,
          page_size: 200,
        });
        setItems(res.items);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setItems([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [clusterId, orgId, filter]);

  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1, padding: 24,
      gap: 12, overflow: "auto", minHeight: 0,
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            padding: "7px 10px", fontSize: 13, fontFamily: theme.font.sans,
            border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
            color: theme.color.textPrimary, cursor: "pointer",
          }}
        >
          <option value="">All actions</option>
          <option value="transfer">Transfer ownership</option>
          <option value="transfer_sharing">Transfer sharing</option>
          <option value="delete">Delete user</option>
        </select>
        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
          {loading ? "Loading…" : `${items.length} entries`}
        </span>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", fontSize: 12, background: theme.color.dangerSoft,
          border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6, color: theme.color.danger,
          fontFamily: theme.font.sans,
        }}><strong>Error:</strong> {error}</div>
      )}

      {!loading && items.length === 0 && !error && (
        <div style={{
          padding: 32, fontSize: 13, color: theme.color.textMuted, textAlign: "center",
          fontFamily: theme.font.sans,
        }}>
          No user-management actions yet.
        </div>
      )}

      {items.length > 0 && (
        <div style={{
          border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
          overflow: "hidden",
        }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "180px 160px 1fr 1fr 100px 100px",
            padding: "10px 14px", background: theme.color.surface,
            borderBottom: `1px solid ${theme.color.border}`, fontSize: 11,
            fontWeight: 600, color: theme.color.textMuted, textTransform: "uppercase",
            letterSpacing: "0.04em", fontFamily: theme.font.sans,
          }}>
            <div>When</div>
            <div>Action</div>
            <div>From</div>
            <div>To</div>
            <div style={{ textAlign: "right" }}>Items</div>
            <div style={{ textAlign: "center" }}>Status</div>
          </div>
          {items.map((r) => {
            const status = STATUS_COLORS[r.status] ?? STATUS_COLORS.PENDING;
            return (
              <div key={r.id} style={{
                display: "grid",
                gridTemplateColumns: "180px 160px 1fr 1fr 100px 100px",
                padding: "10px 14px", fontSize: 12, alignItems: "center",
                borderBottom: `1px solid ${theme.color.bg}`, fontFamily: theme.font.sans,
              }}>
                <div style={{ color: theme.color.textMuted }}>{new Date(r.executed_at).toLocaleString()}</div>
                <div style={{ color: theme.color.textPrimary }}>{ACTION_LABELS[r.action_type] ?? r.action_type}</div>
                <div style={{ color: theme.color.textPrimary }}>
                  {r.from_display_name || r.from_username || "—"}
                </div>
                <div style={{ color: theme.color.textPrimary }}>
                  {r.to_display_name || r.to_username || "—"}
                </div>
                <div style={{ textAlign: "right", color: theme.color.textPrimary }}>
                  {r.items_succeeded} / {r.items_total}
                </div>
                <div style={{ textAlign: "center" }}>
                  <span style={{
                    padding: "2px 8px", borderRadius: 12, fontSize: 10, fontWeight: 600,
                    background: status.bg, color: status.fg,
                  }}>{r.status}</span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
