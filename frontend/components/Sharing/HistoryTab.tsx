/**
 * SharingHistoryTab — past bulk-share jobs aggregated by job_id.
 */
import { useEffect, useState } from "react";

import { sharingApi } from "@/lib/api";
import type { SharingHistoryItem } from "@/lib/types";

const STATUS_COLORS: Record<string, { bg: string; fg: string }> = {
  SUCCESS: { bg: "#D1FAE5", fg: "#065F46" },
  PARTIAL: { bg: "#FEF3C7", fg: "#92400E" },
  FAILED:  { bg: "#FEE2E2", fg: "#991B1B" },
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
      <div style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
        {loading ? "Loading…" : `${items.length} job${items.length === 1 ? "" : "s"}`}
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", fontSize: 12, background: "#FEF2F2",
          border: "1px solid #FCA5A5", borderRadius: 6, color: "#991B1B",
          fontFamily: "Geist, sans-serif",
        }}><strong>Error:</strong> {error}</div>
      )}

      {!loading && items.length === 0 && !error && (
        <div style={{
          padding: 32, fontSize: 13, color: "#7A7068", textAlign: "center",
          fontFamily: "Geist, sans-serif",
        }}>
          No bulk-share jobs yet.
        </div>
      )}

      {items.length > 0 && (
        <div style={{
          border: "1px solid #E8E1D5", borderRadius: 6, background: "white",
          overflow: "hidden",
        }}>
          <div style={{
            display: "grid",
            gridTemplateColumns: "180px 100px 120px 100px 100px 100px",
            padding: "10px 14px", background: "#FAF8F4",
            borderBottom: "1px solid #E8E1D5", fontSize: 11,
            fontWeight: 600, color: "#7A7068", textTransform: "uppercase",
            letterSpacing: "0.04em", fontFamily: "Geist, sans-serif",
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
                borderBottom: "1px solid #F2EDE3", fontFamily: "Geist, sans-serif",
              }}>
                <div style={{ color: "#7A7068" }}>{new Date(r.executed_at).toLocaleString()}</div>
                <div style={{ textAlign: "right", color: "#1A1714" }}>{r.object_count}</div>
                <div style={{ textAlign: "right", color: "#1A1714" }}>{r.principal_count}</div>
                <div style={{ textAlign: "right", color: "#1A1714" }}>{r.succeeded}</div>
                <div style={{ textAlign: "right", color: r.failed > 0 ? "#991B1B" : "#7A7068" }}>
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
