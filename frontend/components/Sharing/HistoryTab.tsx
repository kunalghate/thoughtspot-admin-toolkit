/**
 * SharingHistoryTab — past bulk-share jobs aggregated by job_id.
 */
import { useEffect, useState } from "react";

import { sharingApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { SharingHistoryItem } from "@/lib/types";

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  SUCCESS: { bg: theme.color.successSoft, fg: theme.color.success },
  PARTIAL: { bg: theme.color.warnSoft, fg: theme.color.warn },
  FAILED:  { bg: theme.color.dangerSoft, fg: theme.color.danger },
};

export function SharingHistoryTab({
  clusterId,
  orgId,
}: {
  clusterId: string;
  orgId?: number;
}) {
  const [items, setItems] = useState<SharingHistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!clusterId) return;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await sharingApi.history({
          cluster_id: clusterId,
          org_id: orgId,
          page_size: 200,
        });
        setItems(res.items);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setError(msg);
        setItems([]);
      } finally {
        setLoading(false);
      }
    })();
  }, [clusterId, orgId]);

  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1, padding: 24, gap: 12,
      overflow: "auto", minHeight: 0,
    }}>
      <div style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
        {loading ? "Loading…" : `${items.length} job${items.length === 1 ? "" : "s"}`}
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
          No bulk-share jobs yet.
        </div>
      )}

      {items.length > 0 && (
        <div style={{
          border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
          overflow: "hidden",
        }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "180px 100px 120px 100px 100px 100px",
            padding: "10px 14px", background: theme.color.surface,
            borderBottom: `1px solid ${theme.color.border}`, fontSize: 11,
            fontWeight: 600, color: theme.color.textMuted, textTransform: "uppercase",
            letterSpacing: "0.04em", fontFamily: theme.font.sans,
          }}>
            <div>When</div>
            <div style={{ textAlign: "right" }}>Objects</div>
            <div style={{ textAlign: "right" }}>Principals</div>
            <div style={{ textAlign: "right" }}>Succeeded</div>
            <div style={{ textAlign: "right" }}>Failed</div>
            <div style={{ textAlign: "center" }}>Status</div>
          </div>
          {items.map((r) => {
            const status = STATUS_COLORS[r.status] ?? STATUS_COLORS.SUCCESS;
            return (
              <div key={r.job_id} style={{
                display: "grid",
                gridTemplateColumns: "180px 100px 120px 100px 100px 100px",
                padding: "10px 14px", fontSize: 12, alignItems: "center",
                borderBottom: `1px solid ${theme.color.border}`, fontFamily: theme.font.sans,
              }}>
                <div style={{ color: theme.color.textMuted }}>{new Date(r.executed_at).toLocaleString()}</div>
                <div style={{ textAlign: "right", color: theme.color.textPrimary }}>{r.object_count}</div>
                <div style={{ textAlign: "right", color: theme.color.textPrimary }}>{r.principal_count}</div>
                <div style={{ textAlign: "right", color: theme.color.textPrimary }}>{r.succeeded}</div>
                <div style={{ textAlign: "right", color: r.failed > 0 ? theme.color.danger : theme.color.textMuted }}>
                  {r.failed}
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
