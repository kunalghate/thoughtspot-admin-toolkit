/**
 * DryRunModal — shared between Archiver and Bulk Deleter.
 *
 * State machine:
 *   polling → ready → running → complete
 *
 * - polling:  POST {api.dryrun} → poll job every 2s
 * - ready:    show impact cards + object list + DELETE confirmation input
 * - running:  POST {api.execute} → poll job every 2s, show progress bar
 * - complete: color-coded result summary + "View in History →" link
 *
 * The modal is API-agnostic — the consumer passes a small `api` adapter
 * object so the same UI works for /archiver/* and /deleter/* endpoints.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { IDatasource, IGetRowsParams } from "ag-grid-community";
import { X, Loader2, AlertTriangle, Share2, CheckCircle, XCircle, AlertCircle } from "lucide-react";

import { jobsApi } from "@/lib/api";
import type { DeleterItem, DryRunSummary, Job, PaginatedResponse } from "@/lib/types";
import { OBJECT_COLUMNS } from "./columns";

type ModalState = "polling" | "ready" | "running" | "complete";

export interface DeleteApiAdapter {
  /** Start a dryrun job; returns its id. */
  dryrun: (body: { cluster_id: string; org_id: number; object_ids: string[] }) => Promise<{ job_id: string }>;
  /** Paginated objects queued in the dryrun job (for the modal grid). */
  dryrunObjects: (
    job_id: string,
    params: { cluster_id: string; record_offset?: number; page_size?: number },
  ) => Promise<PaginatedResponse<DeleterItem>>;
  /** Start the actual delete job; returns its id. */
  execute: (body: { cluster_id: string; org_id: number; object_ids: string[] }) => Promise<{ job_id: string }>;
}

interface Props {
  api: DeleteApiAdapter;
  objectIds: string[];
  clusterId: string;
  orgId: number;
  onClose: (reloadNeeded: boolean) => void;
  onViewHistory: () => void;
}

const PAGE_SIZE = 100;

const TYPE_LABEL: Record<string, string> = {
  LIVEBOARD: "Liveboard", ANSWER: "Answer",
  WORKSHEET: "Worksheet", TABLE: "Table", MODEL: "Model", VIEW: "View",
};

export function DryRunModal({ api, objectIds, clusterId, orgId, onClose, onViewHistory }: Props) {
  const [state, setState] = useState<ModalState>("polling");
  const [dryRunJobId, setDryRunJobId] = useState<string | null>(null);
  const [executeJobId, setExecuteJobId] = useState<string | null>(null);
  const [summary, setSummary] = useState<DryRunSummary | null>(null);
  const [executeJob, setExecuteJob] = useState<Job | null>(null);
  const [confirmInput, setConfirmInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<AgGridReact<DeleterItem>>(null);

  // ── Kick off dry-run on mount ──────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    api.dryrun({ cluster_id: clusterId, org_id: orgId, object_ids: objectIds })
      .then(({ job_id }) => {
        if (!cancelled) setDryRunJobId(job_id);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? "Failed to start dry-run");
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);  // run once on mount

  // ── Poll dry-run job ───────────────────────────────────────────────────────

  useEffect(() => {
    if (!dryRunJobId) return;
    const iv = setInterval(async () => {
      try {
        const job = await jobsApi.get(dryRunJobId);
        if (job.status === "COMPLETE" || job.status === "FAILED" || job.status === "PARTIAL") {
          clearInterval(iv);
          if (job.status === "FAILED") {
            setError(job.error ?? "Dry-run failed");
            return;
          }
          setSummary((job.result ?? {}) as unknown as DryRunSummary);
          setError(null);
          setState("ready");
        }
      } catch {
        clearInterval(iv);
        setError("Lost connection while checking impact");
      }
    }, 2000);
    return () => clearInterval(iv);
  }, [dryRunJobId]);

  // ── Object list datasource (modal grid) ───────────────────────────────────

  const loadObjectDatasource = useCallback(() => {
    if (!dryRunJobId) return;
    const jid = dryRunJobId;
    const ds: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const res = await api.dryrunObjects(jid, {
            cluster_id: clusterId,
            record_offset: params.startRow,
            page_size: PAGE_SIZE,
          });
          params.successCallback(res.items, res.total);
        } catch {
          params.failCallback();
        }
      },
    };
    gridRef.current?.api?.setGridOption("datasource", ds);
  }, [dryRunJobId, clusterId, api]);

  useEffect(() => {
    if (state === "ready") loadObjectDatasource();
  }, [state, loadObjectDatasource]);

  // ── Execute delete ─────────────────────────────────────────────────────────

  const handleConfirm = async () => {
    if (confirmInput !== "DELETE") return;
    try {
      const { job_id } = await api.execute({
        cluster_id: clusterId,
        org_id: orgId,
        object_ids: objectIds,
      });
      setExecuteJobId(job_id);
      setState("running");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start delete job");
    }
  };

  // ── Poll execute job ───────────────────────────────────────────────────────

  useEffect(() => {
    if (!executeJobId) return;
    const iv = setInterval(async () => {
      try {
        const job = await jobsApi.get(executeJobId);
        setExecuteJob(job);
        if (job.status === "COMPLETE" || job.status === "FAILED" || job.status === "PARTIAL") {
          clearInterval(iv);
          setState("complete");
        }
      } catch {
        clearInterval(iv);
      }
    }, 2000);
    return () => clearInterval(iv);
  }, [executeJobId]);

  // ── Keyboard close ─────────────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && state !== "polling" && state !== "running") {
        onClose(state === "complete");
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [state, onClose]);

  // ── Result summary helpers ─────────────────────────────────────────────────

  const resultType = (): "success" | "partial" | "failed" => {
    if (!executeJob) return "failed";
    const r = (executeJob.result ?? {}) as Record<string, number>;
    if (executeJob.status === "FAILED" || r.succeeded === 0) return "failed";
    if (r.failed_tml_export > 0 || r.failed_delete > 0) return "partial";
    return "success";
  };

  const resultColors = {
    success: { bg: "#D1FAE5", border: "#6EE7B7", text: "#065F46", icon: CheckCircle },
    partial: { bg: "#FEF3C7", border: "#FCD34D", text: "#92400E", icon: AlertCircle },
    failed:  { bg: "#FEF2F2", border: "#FECACA", text: "#991B1B", icon: XCircle },
  };

  const resultText = () => {
    const r = ((executeJob?.result ?? {}) as Record<string, number>);
    const t = resultType();
    if (t === "success") return `${r.succeeded ?? objectIds.length} objects deleted successfully.`;
    if (t === "partial") return `${r.succeeded} deleted · ${(r.failed_tml_export ?? 0) + (r.failed_delete ?? 0)} skipped. View History for details.`;
    return `Delete completed but 0 objects deleted — ${r.failed_tml_export ?? 0} TML export${r.failed_tml_export !== 1 ? "s" : ""} failed.`;
  };

  // ── Adaptive impact cards ──────────────────────────────────────────────────

  const impactCards = useMemo(() => {
    if (!summary) return [];
    const cards: { label: string; value: number }[] = [{ label: "Total", value: summary.total }];
    const types = Object.entries(summary.by_type ?? {})
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3);
    for (const [t, n] of types) {
      cards.push({ label: TYPE_LABEL[t] ?? t, value: n });
    }
    return cards;
  }, [summary]);

  // Column defs for modal grid — no checkbox
  const modalColumns = OBJECT_COLUMNS.filter(
    (c) => c.colId !== "checkbox" && c.colId !== "actions"
  );

  return (
    <>
      {/* Backdrop */}
      <div
        style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 300 }}
        onClick={state === "polling" || state === "running" ? undefined : () => onClose(state === "complete")}
      />

      {/* Modal panel */}
      <div style={{
        position: "fixed", top: "50%", left: "50%",
        transform: "translate(-50%, -50%)",
        width: 680, maxHeight: "85vh",
        background: "#fff", borderRadius: 10, zIndex: 301,
        display: "flex", flexDirection: "column",
        boxShadow: "0 20px 60px rgba(0,0,0,0.18)",
        overflow: "hidden",
      }}>

        {/* ── Header ── */}
        <div style={{
          display: "flex", alignItems: "center", padding: "16px 20px",
          borderBottom: "1px solid #E8E1D5", flexShrink: 0,
        }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif", flex: 1 }}>
            {state === "polling"  && "Checking impact…"}
            {state === "ready"    && `Delete ${objectIds.length.toLocaleString()} objects`}
            {state === "running"  && "Deleting objects…"}
            {state === "complete" && "Delete complete"}
          </span>
          {(state === "ready" || state === "complete") && (
            <button onClick={() => onClose(state === "complete")} style={{ background: "none", border: "none", cursor: "pointer", color: "#7A7068", padding: 4 }}>
              <X size={16} />
            </button>
          )}
        </div>

        {/* ── Body ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>

          {error && state !== "complete" && (
            <div style={{ padding: 12, background: "#FEF2F2", border: "1px solid #FECACA", borderRadius: 6, color: "#991B1B", fontSize: 13, fontFamily: "Geist, sans-serif" }}>
              {error}
            </div>
          )}

          {/* Polling */}
          {state === "polling" && !error && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "32px 0", color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
              <Loader2 size={28} style={{ animation: "spin 1s linear infinite", color: "#8B5CF6" }} />
              <div style={{ fontSize: 14, textAlign: "center" }}>
                Checking {objectIds.length.toLocaleString()} objects for permissions and dependencies…
              </div>
              <div style={{ fontSize: 12, color: "#A89D94" }}>This runs as a background job — no timeout risk.</div>
            </div>
          )}

          {/* Ready */}
          {state === "ready" && summary && (
            <>
              {/* Impact cards (adaptive) */}
              <div style={{ display: "flex", gap: 10 }}>
                {impactCards.map(({ label, value }) => (
                  <div key={label} style={{
                    flex: 1, padding: "12px 14px", borderRadius: 8,
                    background: "#FAF8F4", border: "1px solid #E8E1D5", textAlign: "center",
                  }}>
                    <div style={{ fontSize: 22, fontWeight: 600, color: "#1A1714", fontFamily: "Geist Mono, monospace" }}>
                      {value.toLocaleString()}
                    </div>
                    <div style={{ fontSize: 11, color: "#7A7068", fontFamily: "Geist, sans-serif", marginTop: 2 }}>
                      {label}
                    </div>
                  </div>
                ))}
              </div>

              {summary.shared_count > 0 && (
                <WarningBox icon={<Share2 size={14} />} title={`${summary.shared_count} shared object${summary.shared_count !== 1 ? "s" : ""} — access will be revoked`}>
                  <div style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif", marginTop: 4 }}>
                    Affected: {summary.affected_principals.slice(0, 5).map((p) => p.name).join(", ")}
                    {summary.affected_principals.length > 5 && ` +${summary.affected_principals.length - 5} more`}
                  </div>
                </WarningBox>
              )}

              {summary.dependency_warnings.length > 0 && (
                <WarningBox icon={<AlertTriangle size={14} />} title={`${summary.dependency_warnings.length} object${summary.dependency_warnings.length !== 1 ? "s" : ""} have active dependents — HIGH RISK`} color="red">
                  <ul style={{ margin: "4px 0 0 0", padding: "0 0 0 16px", fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                    {summary.dependency_warnings.slice(0, 3).map((w) => (
                      <li key={w.ts_guid}>{w.name} ({w.dependents.length} dependent{w.dependents.length !== 1 ? "s" : ""})</li>
                    ))}
                    {summary.dependency_warnings.length > 3 && (
                      <li>…and {summary.dependency_warnings.length - 3} more</li>
                    )}
                  </ul>
                </WarningBox>
              )}

              {summary.errors.length > 0 && (
                <WarningBox icon={<AlertCircle size={14} />} title={`${summary.errors.length} object${summary.errors.length !== 1 ? "s" : ""} could not be checked`} color="gray">
                  <div style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif", marginTop: 4 }}>
                    These will still be included in the delete batch. Check the audit log for details.
                  </div>
                </WarningBox>
              )}

              {/* Object list grid */}
              <div>
                <div style={{ fontSize: 12, fontWeight: 500, color: "#7A7068", fontFamily: "Geist, sans-serif", marginBottom: 6 }}>
                  Objects to be deleted
                </div>
                <div className="ag-theme-alpine" style={{ height: 200 }}>
                  <AgGridReact<DeleterItem>
                    ref={gridRef}
                    columnDefs={modalColumns}
                    rowModelType="infinite"
                    cacheBlockSize={PAGE_SIZE}
                    maxBlocksInCache={5}
                    infiniteInitialRowCount={objectIds.length}
                    defaultColDef={{ resizable: true, sortable: false }}
                    onGridReady={loadObjectDatasource}
                  />
                </div>
              </div>

              {/* DELETE confirmation */}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <label style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                  Type <strong style={{ color: "#991B1B", fontFamily: "Geist Mono, monospace" }}>DELETE</strong> to confirm
                </label>
                <input
                  value={confirmInput}
                  onChange={(e) => setConfirmInput(e.target.value)}
                  placeholder="DELETE"
                  autoFocus
                  style={{
                    height: 36, padding: "0 12px", borderRadius: 6, fontSize: 14,
                    border: `1px solid ${confirmInput === "DELETE" ? "#6EE7B7" : "#E8E1D5"}`,
                    background: confirmInput === "DELETE" ? "#F0FDF4" : "#fff",
                    fontFamily: "Geist Mono, monospace", outline: "none", color: "#1A1714",
                  }}
                />
              </div>
            </>
          )}

          {/* Running */}
          {state === "running" && (
            <div style={{ display: "flex", flexDirection: "column", gap: 14, padding: "16px 0" }}>
              {executeJob ? (
                <>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: 14, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
                      {executeJob.progress.toLocaleString()} / {executeJob.total.toLocaleString()} objects processed
                    </span>
                    <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace" }}>
                      {executeJob.total > 0 ? Math.round((executeJob.progress / executeJob.total) * 100) : 0}%
                    </span>
                  </div>
                  <div style={{ height: 8, background: "#E8E1D5", borderRadius: 4, overflow: "hidden" }}>
                    <div style={{
                      height: "100%", borderRadius: 4, background: "#8B5CF6",
                      width: `${executeJob.total > 0 ? Math.round((executeJob.progress / executeJob.total) * 100) : 0}%`,
                      transition: "width 0.4s ease",
                    }} />
                  </div>
                </>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#7A7068", fontFamily: "Geist, sans-serif", fontSize: 14 }}>
                  <Loader2 size={16} style={{ animation: "spin 1s linear infinite", color: "#8B5CF6" }} />
                  Starting delete job…
                </div>
              )}
              <div style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                TML backups are written before each deletion — you can restore any object from History.
              </div>
            </div>
          )}

          {/* Complete */}
          {state === "complete" && executeJob && (() => {
            const t = resultType();
            const c = resultColors[t];
            const Icon = c.icon;
            return (
              <div style={{
                padding: 16, borderRadius: 8,
                background: c.bg, border: `1px solid ${c.border}`,
                display: "flex", alignItems: "flex-start", gap: 10,
              }}>
                <Icon size={18} style={{ color: c.text, flexShrink: 0, marginTop: 1 }} />
                <div>
                  <div style={{ fontSize: 14, fontWeight: 500, color: c.text, fontFamily: "Geist, sans-serif" }}>
                    {resultText()}
                  </div>
                  <div style={{ display: "flex", gap: 12, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
                    <button
                      onClick={() => { onClose(true); onViewHistory(); }}
                      style={{
                        fontSize: 12, color: "#6D28D9", fontFamily: "Geist, sans-serif",
                        background: "none", border: "none", cursor: "pointer", padding: 0,
                        textDecoration: "underline",
                      }}
                    >
                      View in History →
                    </button>
                    <span style={{ fontSize: 11, color: "#A89D94", fontFamily: "Geist, sans-serif" }}>
                      TML backups available to download from the History tab.
                    </span>
                  </div>
                </div>
              </div>
            );
          })()}
        </div>

        {/* Footer */}
        {state === "ready" && (
          <div style={{
            display: "flex", justifyContent: "flex-end", gap: 8,
            padding: "12px 20px", borderTop: "1px solid #E8E1D5", flexShrink: 0,
          }}>
            <button
              onClick={() => onClose(false)}
              style={{
                padding: "7px 16px", borderRadius: 6, fontSize: 13, cursor: "pointer",
                border: "1px solid #E8E1D5", background: "#FAF8F4",
                color: "#7A7068", fontFamily: "Geist, sans-serif",
              }}
            >
              Cancel
            </button>
            <button
              disabled={confirmInput !== "DELETE"}
              onClick={handleConfirm}
              style={{
                padding: "7px 16px", borderRadius: 6, fontSize: 13,
                fontWeight: 500, fontFamily: "Geist, sans-serif",
                cursor: confirmInput === "DELETE" ? "pointer" : "default",
                border: "none",
                background: confirmInput === "DELETE" ? "#DC2626" : "#FCA5A5",
                color: "#fff",
                transition: "background 0.15s",
              }}
            >
              Delete {objectIds.length.toLocaleString()} objects
            </button>
          </div>
        )}
      </div>
    </>
  );
}

// ── Warning box helper ─────────────────────────────────────────────────────────

function WarningBox({
  icon, title, children, color = "amber",
}: {
  icon: React.ReactNode;
  title: string;
  children?: React.ReactNode;
  color?: "amber" | "red" | "gray";
}) {
  const colors = {
    amber: { bg: "#FEF3C7", border: "#FCD34D", text: "#92400E" },
    red:   { bg: "#FEF2F2", border: "#FECACA", text: "#991B1B" },
    gray:  { bg: "#F9FAFB", border: "#E5E7EB", text: "#374151" },
  };
  const c = colors[color];
  return (
    <div style={{
      padding: "10px 12px", borderRadius: 6,
      background: c.bg, border: `1px solid ${c.border}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, color: c.text, fontSize: 13, fontFamily: "Geist, sans-serif", fontWeight: 500 }}>
        {icon} {title}
      </div>
      {children}
    </div>
  );
}
