/**
 * JobsList — paginated grid of every background job for the active cluster.
 *
 * Backed by GET /api/v1/jobs (paginated, filterable). Surfaces sync,
 * archive, archive_dryrun, bulk_delete, bulk_delete_dryrun, share,
 * etc. — anything that hit the Job table.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, IDatasource, IGetRowsParams } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { useShell } from "@/components/Shell";
import { theme } from "@/lib/theme";
import { diagnosticsApi, jobsApi } from "@/lib/api";
import type { Job, JobStatus } from "@/lib/types";

const PAGE_SIZE = 50;

const JOB_TYPE_LABELS: Record<string, string> = {
  archive:              "Archive (tag/untag/delete)",
  archive_dryrun:       "Archive — dry-run",
  bulk_delete:          "Bulk delete",
  bulk_delete_dryrun:   "Bulk delete — dry-run",
  share:                "Share",
  sync:                 "Sync",
};

const STATUS_COLORS: Record<JobStatus, { bg: string; fg: string }> = {
  QUEUED:   { bg: theme.color.surface3, fg: theme.color.textSecondary },
  PENDING:  { bg: theme.color.surface3, fg: theme.color.textSecondary },
  RUNNING:  { bg: theme.color.accentSoft, fg: theme.color.accent2 },
  COMPLETE: { bg: theme.color.successSoft, fg: theme.color.success },
  PARTIAL:  { bg: theme.color.warnSoft, fg: theme.color.warn },
  FAILED:   { bg: theme.color.dangerSoft, fg: theme.color.danger },
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

function buildJobColumns(onShowDetails: (job: Job) => void): ColDef<Job>[] {
  return [
  {
    field: "job_type",
    headerName: "Type",
    flex: 2,
    minWidth: 200,
    sortable: true,
    cellRenderer: (p: { value: string }) => (
      <span style={{ fontFamily: theme.font.sans, fontSize: 13 }}>
        {JOB_TYPE_LABELS[p.value] ?? p.value}
      </span>
    ),
  },
  {
    field: "status",
    headerName: "Status",
    width: 120,
    sortable: true,
    cellRenderer: (p: { value: JobStatus }) => {
      const c = STATUS_COLORS[p.value] ?? { bg: theme.color.surface3, fg: theme.color.textSecondary };
      return (
        <span style={{
          padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 600,
          fontFamily: theme.font.sans, letterSpacing: "0.02em",
          background: c.bg, color: c.fg,
        }}>{p.value}</span>
      );
    },
  },
  {
    colId: "progress",
    headerName: "Progress",
    width: 140,
    sortable: true,
    valueGetter: (p) => {
      const job = p.data as Job | undefined;
      if (!job) return "";
      if (!job.total) return job.status;
      return `${job.progress.toLocaleString()} / ${job.total.toLocaleString()}`;
    },
    cellRenderer: (p: { value: string; data?: Job }) => {
      const job = p.data;
      if (!job || !job.total) {
        return <span style={{ color: theme.color.textMuted, fontSize: 12 }}>—</span>;
      }
      const pct = Math.min(100, Math.round(job.progress_pct ?? 0));
      return (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 60, height: 6, background: theme.color.border, borderRadius: 3, overflow: "hidden" }}>
            <div style={{ width: `${pct}%`, height: "100%", background: theme.gradient.accent }} />
          </div>
          <span style={{ fontSize: 11, fontFamily: theme.font.mono, color: theme.color.textMuted }}>
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
    sortable: true,
    sort: "desc",
    valueFormatter: (p) => relativeDate(p.value),
  },
  {
    field: "completed_at",
    headerName: "Completed",
    width: 130,
    sortable: true,
    valueFormatter: (p) => relativeDate(p.value),
  },
  {
    field: "error",
    headerName: "Error",
    flex: 2,
    minWidth: 240,
    sortable: false,
    cellRenderer: (p: { value: string | null; data?: Job }) => {
      const job = p.data;
      if (!p.value || !job) {
        return <span style={{ color: theme.color.violetBorder, fontSize: 12 }}>—</span>;
      }
      return (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <span style={{ color: theme.color.danger, fontSize: 12, fontFamily: theme.font.sans }}>
            {p.value}
          </span>
          <button
            onClick={(e) => { e.stopPropagation(); onShowDetails(job); }}
            style={{
              padding: "1px 8px", fontSize: 11, fontWeight: 500,
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 4,
              cursor: "pointer", color: theme.color.danger, fontFamily: theme.font.sans,
            }}
          >Details</button>
        </span>
      );
    },
  },
  {
    field: "id",
    headerName: "Job ID",
    width: 280,
    sortable: false,
    cellRenderer: (p: { value: string }) => (
      <span title={p.value} style={{ fontFamily: theme.font.mono, fontSize: 11, color: theme.color.textMuted }}>
        {p.value}
      </span>
    ),
  },
  ];
}

export function JobsList() {
  const { activeCluster } = useShell();
  const gridRef = useRef<AgGridReact<Job>>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [detailsJob, setDetailsJob] = useState<Job | null>(null);

  const columnDefs = useMemo(
    () => buildJobColumns((job) => setDetailsJob(job)),
    []
  );

  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id) return;
    const cid = activeCluster.id;
    const sf = sortField;
    const so = sortOrder;
    const ds: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const res = await jobsApi.list({
            cluster_id: cid,
            sort_field: sf,
            sort_order: so,
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
  }, [activeCluster?.id, sortField, sortOrder]);

  useEffect(() => { reloadGrid(); }, [reloadGrid]);

  const handleGridReady = useCallback(() => {
    reloadGrid();
    setTimeout(() => gridRef.current?.api?.sizeColumnsToFit(), 0);
  }, [reloadGrid]);

  const handleSortChanged = useCallback(() => {
    const cols = gridRef.current?.api.getColumnState() ?? [];
    const sorted = cols.find((c) => c.sort);
    if (sorted?.colId) {
      setSortField(sorted.colId);
      setSortOrder(sorted.sort as "asc" | "desc");
    } else {
      // User cleared the sort — fall back to default (newest first).
      setSortField("created_at");
      setSortOrder("desc");
    }
  }, []);

  const displayCount = total == null ? "" : `${total.toLocaleString()} job${total === 1 ? "" : "s"}`;

  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1,
      padding: 24, gap: 12, overflow: "hidden",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono }}>
          {displayCount}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={reloadGrid}
          style={{
            padding: "6px 12px", fontSize: 12, fontWeight: 500,
            background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
            cursor: "pointer", color: theme.color.textPrimary, fontFamily: theme.font.sans,
          }}
        >Refresh</button>
      </div>

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<Job>
          ref={gridRef}
          columnDefs={columnDefs}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          pagination
          paginationPageSize={PAGE_SIZE}
          paginationPageSizeSelector={[25, 50, 100, 200]}
          defaultColDef={{
            resizable: true,
            sortable: true,
            sortingOrder: ["asc", "desc"],
            wrapHeaderText: true,
            autoHeaderHeight: true,
          }}
          onGridReady={handleGridReady}
          onGridSizeChanged={() => gridRef.current?.api?.sizeColumnsToFit()}
          onSortChanged={handleSortChanged}
          overlayNoRowsTemplate="No jobs yet."
        />
      </div>

      {detailsJob && (
        <JobErrorDetailsModal job={detailsJob} onClose={() => setDetailsJob(null)} />
      )}
    </div>
  );
}


function JobErrorDetailsModal({ job, onClose }: { job: Job; onClose: () => void }) {
  const [showTraceback, setShowTraceback] = useState(false);

  // The friendly error is "Title — Hint"; split on the first em-dash for display.
  const [friendlyTitle, friendlyHint] = useMemo(() => {
    if (!job.error) return ["Job failed", ""];
    const idx = job.error.indexOf(" — ");
    if (idx === -1) return [job.error, ""];
    return [job.error.slice(0, idx), job.error.slice(idx + 3)];
  }, [job.error]);

  const bundleUrl = diagnosticsApi.bundleUrl(job.id);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: theme.color.overlay,
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000, padding: 24,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: theme.color.surface, borderRadius: 12,
          maxWidth: 720, width: "100%", maxHeight: "85vh",
          display: "flex", flexDirection: "column",
          boxShadow: theme.shadow.lg,
          border: `1px solid ${theme.color.border}`, fontFamily: theme.font.sans,
        }}
      >
        <div style={{
          padding: "18px 22px", borderBottom: `1px solid ${theme.color.border}`,
          display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12,
        }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: theme.color.textPrimary }}>
              {friendlyTitle}
            </div>
            {friendlyHint && (
              <div style={{ fontSize: 13, color: theme.color.textSecondary, marginTop: 4 }}>
                {friendlyHint}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "transparent", border: "none", cursor: "pointer",
              fontSize: 22, color: theme.color.textMuted, padding: "0 4px", lineHeight: 1,
            }}
          >×</button>
        </div>

        <div style={{ padding: "16px 22px", overflowY: "auto", flex: 1 }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 14, fontSize: 12 }}>
            {job.error_type && (
              <span style={{
                background: theme.color.dangerSoft, color: theme.color.danger,
                padding: "3px 10px", borderRadius: 12, fontFamily: theme.font.mono,
              }}>{job.error_type}</span>
            )}
            <span style={{
              background: theme.color.surface2, color: theme.color.textSecondary,
              padding: "3px 10px", borderRadius: 12, fontFamily: theme.font.mono,
            }}>{JOB_TYPE_LABELS[job.job_type] ?? job.job_type}</span>
            <span title={job.id} style={{
              background: theme.color.surface2, color: theme.color.textSecondary,
              padding: "3px 10px", borderRadius: 12, fontFamily: theme.font.mono,
            }}>{job.id.slice(0, 8)}…</span>
          </div>

          {job.error_traceback ? (
            <>
              <button
                onClick={() => setShowTraceback((v) => !v)}
                style={{
                  padding: "6px 12px", fontSize: 12, fontWeight: 500,
                  background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
                  cursor: "pointer", color: theme.color.textPrimary, fontFamily: theme.font.sans,
                  marginBottom: 10,
                }}
              >{showTraceback ? "Hide technical details" : "Show technical details"}</button>
              {showTraceback && (
                <pre style={{
                  background: theme.color.codeBg, color: theme.color.codeFg,
                  padding: 14, borderRadius: 6, fontSize: 11.5, lineHeight: 1.5,
                  fontFamily: theme.font.mono,
                  maxHeight: "40vh", overflow: "auto",
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}>{job.error_traceback}</pre>
              )}
            </>
          ) : (
            <div style={{ fontSize: 12, color: theme.color.textMuted, fontStyle: "italic" }}>
              No traceback was captured for this job.
            </div>
          )}
        </div>

        <div style={{
          padding: "14px 22px", borderTop: `1px solid ${theme.color.border}`,
          display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12,
        }}>
          <a
            href={bundleUrl}
            style={{
              fontSize: 12, color: theme.color.accent2, fontWeight: 500,
              textDecoration: "none",
            }}
          >Download diagnostics for this job</a>
          <button
            onClick={onClose}
            style={{
              padding: "6px 14px", fontSize: 12, fontWeight: 500,
              background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
              cursor: "pointer", color: theme.color.textPrimary, fontFamily: theme.font.sans,
            }}
          >Close</button>
        </div>
      </div>
    </div>
  );
}
