/**
 * JobsList — paginated grid of every background job for the active cluster.
 *
 * Backed by GET /api/v1/jobs (paginated, filterable). Surfaces sync,
 * archive, archive_dryrun, bulk_delete, bulk_delete_dryrun, share,
 * etc. — anything that hit the Job table.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, IDatasource, IGetRowsParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { useShell } from "@/components/Shell";
import { jobsApi } from "@/lib/api";
import type { Job, JobStatus } from "@/lib/types";

const PAGE_SIZE = 200;

const JOB_TYPE_LABELS: Record<string, string> = {
  archive:              "Archive (tag/untag/delete)",
  archive_dryrun:       "Archive — dry-run",
  bulk_delete:          "Bulk delete",
  bulk_delete_dryrun:   "Bulk delete — dry-run",
  share:                "Share",
  sync:                 "Sync",
};

const STATUS_COLORS: Record<JobStatus, { bg: string; fg: string }> = {
  QUEUED:   { bg: "#F3F4F6", fg: "#374151" },
  PENDING:  { bg: "#F3F4F6", fg: "#374151" },
  RUNNING:  { bg: "#E0F2FE", fg: "#0369A1" },
  COMPLETE: { bg: "#D1FAE5", fg: "#065F46" },
  PARTIAL:  { bg: "#FEF3C7", fg: "#92400E" },
  FAILED:   { bg: "#FEF2F2", fg: "#991B1B" },
};

function relativeDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days === 0) {
    const mins = Math.floor((Date.now() - d.getTime()) / 60_000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    return `${Math.floor(mins / 60)}h ago`;
  }
  if (days === 1) return "Yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

const JOB_COLUMNS: ColDef<Job>[] = [
  {
    field: "job_type",
    headerName: "Type",
    flex: 2,
    minWidth: 200,
    sortable: false,
    cellRenderer: (p: { value: string }) => (
      <span style={{ fontFamily: "Geist, sans-serif", fontSize: 13 }}>
        {JOB_TYPE_LABELS[p.value] ?? p.value}
      </span>
    ),
  },
  {
    field: "status",
    headerName: "Status",
    width: 120,
    sortable: false,
    cellRenderer: (p: { value: JobStatus }) => {
      const c = STATUS_COLORS[p.value] ?? { bg: "#F3F4F6", fg: "#374151" };
      return (
        <span style={{
          padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 600,
          fontFamily: "Geist, sans-serif", letterSpacing: "0.02em",
          background: c.bg, color: c.fg,
        }}>{p.value}</span>
      );
    },
  },
  {
    headerName: "Progress",
    width: 140,
    sortable: false,
    valueGetter: (p) => {
      const job = p.data as Job | undefined;
      if (!job) return "";
      if (!job.total) return job.status;
      return `${job.progress.toLocaleString()} / ${job.total.toLocaleString()}`;
    },
    cellRenderer: (p: { value: string; data?: Job }) => {
      const job = p.data;
      if (!job || !job.total) {
        return <span style={{ color: "#9CA3AF", fontSize: 12 }}>—</span>;
      }
      const pct = Math.min(100, Math.round(job.progress_pct ?? 0));
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 60, height: 6, background: "#E8E1D5", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: "#8B5CF6" }} />
          </div>
          <span style={{ fontSize: 11, fontFamily: "Geist Mono, monospace", color: "#7A7068" }}>
            {p.value}
          </span>
        </div>
      );
    },
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 130,
    sortable: false,
    valueFormatter: (p) => relativeDate(p.value),
  },
  {
    field: "completed_at",
    headerName: "Completed",
    width: 130,
    sortable: false,
    valueFormatter: (p) => relativeDate(p.value),
  },
  {
    field: "error",
    headerName: "Error",
    flex: 2,
    minWidth: 200,
    sortable: false,
    cellRenderer: (p: { value: string | null }) => p.value
      ? <span style={{ color: "#991B1B", fontSize: 12, fontFamily: "Geist, sans-serif" }}>{p.value}</span>
      : <span style={{ color: "#C4B5FD", fontSize: 12 }}>—</span>,
  },
  {
    field: "id",
    headerName: "Job ID",
    width: 280,
    sortable: false,
    cellRenderer: (p: { value: string }) => (
      <span title={p.value} style={{ fontFamily: "Geist Mono, monospace", fontSize: 11, color: "#7A7068" }}>
        {p.value}
      </span>
    ),
  },
];

export function JobsList() {
  const { activeCluster } = useShell();
  const gridRef = useRef<AgGridReact<Job>>(null);
  const [total, setTotal] = useState<number | null>(null);

  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id) return;
    const cid = activeCluster.id;
    const ds: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const res = await jobsApi.list({
            cluster_id: cid,
            record_offset: params.startRow,
            page_size: PAGE_SIZE,
          });
          setTotal(res.total);
          params.successCallback(res.items, res.total);
        } catch {
          params.failCallback();
        }
      },
    };
    gridRef.current?.api?.setGridOption("datasource", ds);
  }, [activeCluster?.id]);

  useEffect(() => { reloadGrid(); }, [reloadGrid]);

  const handleGridReady = useCallback(() => {
    reloadGrid();
    setTimeout(() => gridRef.current?.api?.sizeColumnsToFit(), 0);
  }, [reloadGrid]);

  const displayCount = total == null ? "" : `${total.toLocaleString()} job${total === 1 ? "" : "s"}`;

  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1,
      padding: 24, gap: 12, overflow: "hidden",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace" }}>
          {displayCount}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={reloadGrid}
          style={{
            padding: "6px 12px", fontSize: 12, fontWeight: 500,
            background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 6,
            cursor: "pointer", color: "#1A1714", fontFamily: "Geist, sans-serif",
          }}
        >Refresh</button>
      </div>

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<Job>
          ref={gridRef}
          columnDefs={JOB_COLUMNS}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          defaultColDef={{
            resizable: true,
            wrapHeaderText: true,
            autoHeaderHeight: true,
          }}
          onGridReady={handleGridReady}
          onGridSizeChanged={() => gridRef.current?.api?.sizeColumnsToFit()}
          overlayNoRowsTemplate="No jobs yet."
        />
      </div>
    </div>
  );
}
