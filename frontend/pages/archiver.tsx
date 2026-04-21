import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { IDatasource, IGetRowsParams } from "ag-grid-community";
import { Download, X, Search, ChevronDown, Clock } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { ARCHIVER_COLUMNS, serializeFilterModel } from "@/components/ArchiverGrid/columns";
import { DryRunModal } from "@/components/ArchiverGrid/DryRunModal";
import { archiverApi, jobsApi } from "@/lib/api";
import type { ArchiverItem, ArchiveRecordFlatItem, Job } from "@/lib/types";

const PAGE_SIZE = 200;

// ── Criteria state ─────────────────────────────────────────────────────────────

interface Criteria {
  staleActivityDays: number;
  staleModifiedDays: number;
  staleOperator: "AND" | "OR";
  types: string[];
  search: string;
  excludeTags: string[];
  filterTags: string[];      // show ONLY objects that have these tags
  ownerGuid: string;
  excludeOwnerGuids: string[];
}

const DEFAULT_CRITERIA: Criteria = {
  staleActivityDays: 90,
  staleModifiedDays: 90,
  staleOperator: "AND",
  types: ["LIVEBOARD", "ANSWER"],
  search: "",
  excludeTags: [],
  filterTags: [],
  ownerGuid: "",
  excludeOwnerGuids: [],
};

// ── Page wrapper ───────────────────────────────────────────────────────────────

export default function ArchiverPage() {
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell
      pageTitle="Archiver"
      entityType="metadata"
      onSyncComplete={() => setSyncVersion((v) => v + 1)}
    >
      <ArchiverContent syncVersion={syncVersion} />
    </AppShell>
  );
}

// ── Main content ───────────────────────────────────────────────────────────────

function ArchiverContent({ syncVersion }: { syncVersion: number }) {
  const [activeTab, setActiveTab] = useState<"archive" | "history">("archive");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Tab strip */}
      <div style={{
        display: "flex", gap: 0, borderBottom: "1px solid #E8E1D5",
        background: "#FAF8F4", paddingLeft: 24, flexShrink: 0,
      }}>
        {(["archive", "history"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              padding: "10px 18px", fontSize: 13, fontWeight: activeTab === tab ? 600 : 400,
              fontFamily: "Geist, sans-serif", cursor: "pointer", border: "none",
              background: "transparent", color: activeTab === tab ? "#6D28D9" : "#7A7068",
              borderBottom: activeTab === tab ? "2px solid #8B5CF6" : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            {tab === "archive" ? "Archive" : "History"}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
        {activeTab === "archive" ? (
          <ArchiveTab syncVersion={syncVersion} onViewHistory={() => setActiveTab("history")} />
        ) : (
          <HistoryTab />
        )}
      </div>
    </div>
  );
}

// ── Stale Criteria pill + popover ──────────────────────────────────────────────

type StaleCriteriaValues = {
  staleActivityDays: number;
  staleModifiedDays: number;
  staleOperator: "AND" | "OR";
};

function StaleCriteriaPill(props: StaleCriteriaValues & {
  onApply: (v: StaleCriteriaValues) => void;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<StaleCriteriaValues>({
    staleActivityDays: props.staleActivityDays,
    staleModifiedDays: props.staleModifiedDays,
    staleOperator: props.staleOperator,
  });
  const wrapRef = useRef<HTMLDivElement>(null);

  // Sync draft → props whenever popover opens (so it always shows the live state)
  useEffect(() => {
    if (open) {
      setDraft({
        staleActivityDays: props.staleActivityDays,
        staleModifiedDays: props.staleModifiedDays,
        staleOperator: props.staleOperator,
      });
    }
  }, [open, props.staleActivityDays, props.staleModifiedDays, props.staleOperator]);

  // Close on outside click + Esc
  useEffect(() => {
    if (!open) return;
    const onMouseDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const summary = `${props.staleActivityDays}d ${props.staleOperator} ${props.staleModifiedDays}d`;

  const apply = () => { props.onApply(draft); setOpen(false); };
  const reset = () => { props.onReset(); setOpen(false); };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <button
        onClick={() => setOpen((o) => !o)}
        title="Stale criteria"
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          padding: "6px 10px 6px 12px", borderRadius: 20,
          fontSize: 12, fontWeight: 500, fontFamily: "Geist, sans-serif",
          background: open ? "#F3EFFE" : "#FAF8F4",
          border: `1px solid ${open ? "#C4B5FD" : "#E8E1D5"}`,
          color: open ? "#6D28D9" : "#1A1714",
          cursor: "pointer", whiteSpace: "nowrap",
          transition: "background 120ms ease, border-color 120ms ease, color 120ms ease",
        }}
        onMouseEnter={(e) => { if (!open) e.currentTarget.style.borderColor = "#D8CFC8"; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.borderColor = "#E8E1D5"; }}
      >
        <Clock size={12} strokeWidth={2} />
        <span style={{ color: "#7A7068", fontWeight: 400 }}>Stale:</span>
        <span>{summary}</span>
        <ChevronDown size={12} color="#7A7068" />
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 50,
          width: 260, background: "#FFFFFF",
          border: "1px solid #E8E1D5", borderRadius: 10,
          boxShadow: "0 1px 2px rgba(26,23,20,0.04), 0 12px 28px -8px rgba(26,23,20,0.18)",
          fontFamily: "Geist, sans-serif",
          animation: "popoverIn 140ms ease-out",
          overflow: "hidden",
        }}>
          {/* Header */}
          <div style={{
            padding: "10px 14px", borderBottom: "1px solid #F0EAE0",
            display: "flex", alignItems: "center", gap: 6,
          }}>
            <Clock size={12} color="#8B5CF6" strokeWidth={2.25} />
            <span style={{ fontSize: 12, fontWeight: 600, color: "#1A1714", letterSpacing: "0.01em" }}>
              Stale criteria
            </span>
          </div>

          {/* Body */}
          <div style={{ padding: "14px 14px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Last Accessed row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <span style={{ fontSize: 12, color: "#3F3A34", fontWeight: 500 }}>Last Accessed ≥</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number" min={1} value={draft.staleActivityDays}
                  onChange={(e) => setDraft({ ...draft, staleActivityDays: Math.max(1, Number(e.target.value)) })}
                  onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
                  className="stale-num"
                  style={{
                    width: 64, height: 30, padding: "0 10px", borderRadius: 6,
                    border: "1px solid #EBE3D5", background: "#FAF8F4",
                    fontSize: 13, fontFamily: "Geist, sans-serif", color: "#1A1714",
                    outline: "none", textAlign: "right",
                  }}
                />
                <span style={{ fontSize: 11, color: "#7A7068", width: 28 }}>days</span>
              </div>
            </div>

            {/* AND/OR unified segmented control */}
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ flex: 1, height: 1, background: "#F0EAE0" }} />
              <div style={{
                display: "inline-flex", padding: 2, borderRadius: 999,
                background: "#F4EFE6", border: "1px solid #E8E1D5",
              }}>
                {(["AND", "OR"] as const).map((op) => {
                  const active = draft.staleOperator === op;
                  return (
                    <button
                      key={op}
                      onClick={() => setDraft({ ...draft, staleOperator: op })}
                      style={{
                        height: 22, padding: "0 12px", minWidth: 44,
                        fontSize: 10, fontWeight: 700, letterSpacing: "0.08em",
                        borderRadius: 999, fontFamily: "Geist, sans-serif",
                        background: active ? "#8B5CF6" : "transparent",
                        color: active ? "#FFFFFF" : "#7A7068",
                        border: "none", cursor: "pointer",
                        boxShadow: active ? "0 1px 2px rgba(139,92,246,0.25)" : "none",
                        transition: "background 140ms ease, color 140ms ease",
                      }}
                    >
                      {op}
                    </button>
                  );
                })}
              </div>
              <div style={{ flex: 1, height: 1, background: "#F0EAE0" }} />
            </div>

            {/* Last Modified row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <span style={{ fontSize: 12, color: "#3F3A34", fontWeight: 500 }}>Last Modified ≥</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number" min={1} value={draft.staleModifiedDays}
                  onChange={(e) => setDraft({ ...draft, staleModifiedDays: Math.max(1, Number(e.target.value)) })}
                  onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
                  className="stale-num"
                  style={{
                    width: 64, height: 30, padding: "0 10px", borderRadius: 6,
                    border: "1px solid #EBE3D5", background: "#FAF8F4",
                    fontSize: 13, fontFamily: "Geist, sans-serif", color: "#1A1714",
                    outline: "none", textAlign: "right",
                  }}
                />
                <span style={{ fontSize: 11, color: "#7A7068", width: 28 }}>days</span>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div style={{
            display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8,
            padding: "8px 12px", borderTop: "1px solid #F0EAE0", background: "#FFFFFF",
          }}>
            <button
              onClick={reset}
              style={{
                height: 30, padding: "0 12px", borderRadius: 6,
                border: "1px solid transparent", background: "transparent",
                fontSize: 12, fontWeight: 500, color: "#6B6056",
                fontFamily: "Geist, sans-serif", cursor: "pointer",
                transition: "background 120ms ease, color 120ms ease",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "#F4EFE6"; e.currentTarget.style.color = "#1A1714"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = "#6B6056"; }}
            >
              Reset
            </button>
            <button
              onClick={apply}
              style={{
                height: 30, padding: "0 14px", borderRadius: 6,
                border: "1px solid #8B5CF6", background: "#8B5CF6",
                fontSize: 12, fontWeight: 600, color: "#FFFFFF",
                fontFamily: "Geist, sans-serif", cursor: "pointer", minWidth: 64,
                boxShadow: "0 1px 2px rgba(139,92,246,0.25)",
                transition: "background 120ms ease, border-color 120ms ease",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "#7C3AED"; e.currentTarget.style.borderColor = "#7C3AED"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "#8B5CF6"; e.currentTarget.style.borderColor = "#8B5CF6"; }}
            >
              Apply
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Archive tab ────────────────────────────────────────────────────────────────

function ArchiveTab({ syncVersion, onViewHistory }: { syncVersion: number; onViewHistory: () => void }) {
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact<ArchiverItem>>(null);

  const [criteria, setCriteria] = useState<Criteria>(DEFAULT_CRITERIA);
  const [debouncedCriteria, setDebouncedCriteria] = useState<Criteria>(DEFAULT_CRITERIA);
  const [previewTotal, setPreviewTotal] = useState<number | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("days_unused");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selectedGuids, setSelectedGuids] = useState<Set<string>>(new Set());
  // Union of tags across currently-selected rows — drives the click-to-remove
  // chip cluster in the bulk-action toolbar.
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagName, setTagName] = useState("Stale");            // selected tag for bulk-tag action
  const [customTagName, setCustomTagName] = useState("");     // typed when "New tag…" chosen
  const [showCustomInput, setShowCustomInput] = useState(false);
  const [tagPickerOpen, setTagPickerOpen] = useState(false);  // filter-by-tag dropdown
  const [availableTags, setAvailableTags] = useState<{ ts_guid: string; name: string }[]>([]);
  const [toast, setToast] = useState<{ msg: string; ok: boolean } | null>(null);

  // Held in a ref so updating filters doesn't recreate the datasource (which
  // would close the open filter popup mid-typing). On filter change we purge
  // the infinite cache; the existing getRows closure reads the ref.
  const colFiltersRef = useRef<Record<string, any>>({});

  // DryRunModal state
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [pendingDeleteGuids, setPendingDeleteGuids] = useState<string[]>([]);

  // Reload available filter tags whenever criteria (thresholds + types) changes
  useEffect(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    archiverApi.tags({
      cluster_id: activeCluster.id,
      org_id: activeOrg.org_id,
      stale_activity_days: debouncedCriteria.staleActivityDays,
      stale_modified_days: debouncedCriteria.staleModifiedDays,
      types: debouncedCriteria.types,
    }).then(setAvailableTags).catch(() => {});
  }, [activeCluster?.id, activeOrg?.org_id, debouncedCriteria.staleActivityDays, debouncedCriteria.staleModifiedDays, debouncedCriteria.types]);

  // Debounce criteria → live count badge
  useEffect(() => {
    const t = setTimeout(() => setDebouncedCriteria(criteria), 300);
    return () => clearTimeout(t);
  }, [criteria]);

  // Live count preview
  useEffect(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    archiverApi.preview({
      cluster_id: activeCluster.id,
      org_id: activeOrg.org_id,
      stale_activity_days: debouncedCriteria.staleActivityDays,
      stale_modified_days: debouncedCriteria.staleModifiedDays,
      types: debouncedCriteria.types,
      exclude_tags: debouncedCriteria.excludeTags.length ? debouncedCriteria.excludeTags : undefined,
      stale_operator: debouncedCriteria.staleOperator,
    }).then((p) => setPreviewTotal(p.total)).catch(() => {});
  }, [debouncedCriteria, activeCluster?.id, activeOrg?.org_id]);

  // Reload grid when debounced criteria, sort, cluster, org, or sync changes
  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    const clusterId = activeCluster.id;
    const orgId = activeOrg.org_id;
    const snap = debouncedCriteria;
    const sf = sortField;
    const so = sortOrder;

    const datasource: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const f = serializeFilterModel(colFiltersRef.current);

          const res = await archiverApi.results({
            cluster_id: clusterId,
            org_id: orgId,
            stale_activity_days: snap.staleActivityDays,
            stale_modified_days: snap.staleModifiedDays,
            types:              f.types ?? snap.types,
            exclude_tags:       snap.excludeTags.length ? snap.excludeTags : undefined,
            filter_tags:        snap.filterTags.length  ? snap.filterTags  : undefined,
            search:             f.search ?? snap.search ?? undefined,
            stale_operator:     snap.staleOperator,
            owner_guid:         f.owner_name_search ? undefined : (snap.ownerGuid || undefined),
            exclude_owner_guids: snap.excludeOwnerGuids.length ? snap.excludeOwnerGuids : undefined,
            owner_name_search:  f.owner_name_search,
            tag_search:         f.tag_search,
            days_unused_min:    f.days_unused_min,
            days_unused_max:    f.days_unused_max,
            views_min:          f.views_min,
            views_max:          f.views_max,
            last_accessed_before: f.last_accessed_before,
            last_accessed_after:  f.last_accessed_after,
            modified_before:      f.modified_before,
            modified_after:       f.modified_after,
            created_before:       f.created_before,
            created_after:        f.created_after,
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
    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, debouncedCriteria, sortField, sortOrder]);

  useEffect(() => { reloadGrid(); }, [reloadGrid, syncVersion]);

  const handleGridReady = useCallback(() => {
    reloadGrid();
  }, [reloadGrid]);

  // eslint-disable-next-line @typescript-eslint/no-empty-function
  const handleGridSizeChanged = useCallback(() => {}, []);

  const handleSortChanged = useCallback(() => {
    const cols = gridRef.current?.api.getColumnState() ?? [];
    const sorted = cols.find((c) => c.sort);
    if (sorted?.colId) {
      setSortField(sorted.colId);
      setSortOrder(sorted.sort as "asc" | "desc");
    }
  }, []);

  const handleSelectionChanged = useCallback(() => {
    const rows = gridRef.current?.api.getSelectedRows() ?? [];
    setSelectedGuids(new Set(rows.map((r) => r.ts_guid)));
    const tagSet = new Set<string>();
    for (const r of rows) for (const t of r.tags ?? []) tagSet.add(t);
    setSelectedTags([...tagSet].sort());
  }, []);

  // Resolved tag name: custom input wins over dropdown selection
  const resolvedTagName = showCustomInput ? (customTagName.trim() || "INACTIVE") : (tagName || "INACTIVE");

  const showToast = (msg: string, ok: boolean) => {
    setToast({ msg, ok });
    setTimeout(() => setToast(null), 4000);
  };

  // Actions
  const handleTag = async (action: "tag" | "untag", guids: string[], untagName?: string) => {
    if (!activeCluster?.id || activeOrg?.org_id == null || !guids.length) return;
    const tagForAction = action === "tag" ? resolvedTagName : (untagName ?? "");
    if (action === "untag" && !tagForAction) return;
    try {
      const { job_id } = await archiverApi.execute({
        cluster_id: activeCluster.id,
        org_id: activeOrg.org_id,
        object_ids: guids,
        action,
        tag_name: tagForAction,
        create_tag_if_missing: action === "tag",
      });
      pollUntilDone(job_id, (job) => {
        const result = (job as any).result;
        const verb = action === "tag" ? "tag" : "untag";
        const pastVerb = action === "tag" ? "Tagged" : "Removed";
        const tagLabel = action === "tag" ? resolvedTagName : tagForAction;

        if (job.status === "COMPLETE") {
          const succeeded = result?.succeeded ?? guids.length;
          const plural = succeeded !== 1 ? "s" : "";
          showToast(
            action === "tag"
              ? `${pastVerb} ${succeeded} object${plural} as "${tagLabel}"`
              : `${pastVerb} "${tagLabel}" from ${succeeded} object${plural}`,
            true,
          );
        } else if (job.status === "PARTIAL") {
          const succeeded = result?.succeeded ?? 0;
          const failed = result?.failed ?? 0;
          showToast(
            `Partially ${verb}ged "${tagLabel}": ${succeeded} succeeded, ${failed} failed`,
            false,
          );
        } else {
          // FAILED — no chunks ran, or outer exception
          const reason = job.error ?? "unknown error";
          showToast(`Failed to ${verb} "${tagLabel}": ${reason}`, false);
        }
        reloadGrid();
      });
    } catch (err: any) {
      showToast(err.message ?? "Failed to start tag job", false);
    }
  };

  const pollUntilDone = (jobId: string, onDone: (job: Job) => void) => {
    const iv = setInterval(async () => {
      try {
        const job = await jobsApi.get(jobId);
        if (job.status === "COMPLETE" || job.status === "FAILED" || job.status === "PARTIAL") {
          clearInterval(iv);
          onDone(job);
        }
      } catch {
        clearInterval(iv);
      }
    }, 2000);
  };

  const handleDeleteSelected = () => {
    if (!selectedGuids.size) return;
    setPendingDeleteGuids([...selectedGuids]);
    setDryRunOpen(true);
  };

  const handleDryRunClose = (reloadNeeded: boolean) => {
    setDryRunOpen(false);
    setPendingDeleteGuids([]);
    if (reloadNeeded) {
      gridRef.current?.api.deselectAll();
      setSelectedGuids(new Set());
      reloadGrid();
    }
  };


  // Inject JSX cell renderers — columns.ts is plain .ts so can't use JSX there
  const polishedColumns = useMemo(() =>
    ARCHIVER_COLUMNS.map((col: any) => {
      if (col.field === "object_type") {
        return {
          ...col,
          cellRenderer: (p: { value: string }) => (
            <span style={{
              padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
              fontFamily: "Geist, sans-serif",
              background: p.value === "LIVEBOARD" ? "#EDE9FE" : "#F3F4F6",
              color:      p.value === "LIVEBOARD" ? "#6D28D9"  : "#374151",
            }}>
              {p.value === "LIVEBOARD" ? "Liveboard" : "Answer"}
            </span>
          ),
        };
      }
      if (col.field === "tags") {
        return {
          ...col,
          valueFormatter: undefined,
          cellRenderer: (p: { value: string[] | null }) => {
            const tags = p.value ?? [];
            if (!tags.length) return <span style={{ color: "#D8CFC8", fontFamily: "Geist, sans-serif" }}>—</span>;
            return (
              <div style={{ display: "flex", gap: 4, alignItems: "center", height: "100%", flexWrap: "wrap" }}>
                {tags.slice(0, 2).map((t: string) => (
                  <span key={t} style={{
                    display: "inline-block", lineHeight: "18px",
                    padding: "0 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: "#F3F4F6", color: "#374151", fontFamily: "Geist, sans-serif",
                    whiteSpace: "nowrap",
                  }}>{t}</span>
                ))}
                {tags.length > 2 && (
                  <span style={{
                    display: "inline-block", lineHeight: "18px",
                    padding: "0 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: "#F3F4F6", color: "#374151", fontFamily: "Geist, sans-serif",
                  }}>
                    +{tags.length - 2}
                  </span>
                )}
              </div>
            );
          },
        };
      }
      return col;
    }),
  // eslint-disable-next-line react-hooks/exhaustive-deps
  []);

  const displayCount = total == null
    ? (previewTotal != null ? `${previewTotal.toLocaleString()} stale objects` : "")
    : `${total.toLocaleString()} stale objects${criteria.filterTags.length ? " (filtered)" : ""}`;

  const isFiltered = criteria.staleActivityDays !== DEFAULT_CRITERIA.staleActivityDays
    || criteria.staleModifiedDays !== DEFAULT_CRITERIA.staleModifiedDays
    || criteria.types.length !== DEFAULT_CRITERIA.types.length
    || !!criteria.search
    || criteria.excludeTags.length > 0
    || criteria.filterTags.length > 0
    || !!criteria.ownerGuid
    || criteria.excludeOwnerGuids.length > 0;

  // Tags available in dropdown but not already active as filters
  const tagPickerOptions = availableTags.filter(
    (t) => !criteria.filterTags.includes(t.name)
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, padding: 24, gap: 12, overflow: "hidden" }}>

      {/* ── Criteria toolbar ─────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", flexShrink: 0 }}>

        {/* Search by name */}
        <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 300 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#7A7068" }} />
          <input
            value={criteria.search}
            onChange={(e) => setCriteria({ ...criteria, search: e.target.value })}
            placeholder="Search by name…"
            style={{
              width: "100%", paddingLeft: 30, paddingRight: 10,
              height: 34, borderRadius: 6, border: "1px solid #E8E1D5",
              background: "#FAF8F4", fontSize: 13, color: "#1A1714",
              fontFamily: "Geist, sans-serif", outline: "none",
            }}
          />
        </div>

        <div style={{ width: 1, height: 18, background: "#E8E1D5", margin: "0 2px" }} />

        {/* Stale criteria pill — collapses Last Accessed + AND/OR + Last Modified */}
        <StaleCriteriaPill
          staleActivityDays={criteria.staleActivityDays}
          staleModifiedDays={criteria.staleModifiedDays}
          staleOperator={criteria.staleOperator}
          onApply={(v) => setCriteria({ ...criteria, ...v })}
          onReset={() => setCriteria({
            ...criteria,
            staleActivityDays: DEFAULT_CRITERIA.staleActivityDays,
            staleModifiedDays: DEFAULT_CRITERIA.staleModifiedDays,
            staleOperator: DEFAULT_CRITERIA.staleOperator,
          })}
        />

        <div style={{ width: 1, height: 18, background: "#E8E1D5", margin: "0 2px" }} />

        {/* Type toggles */}
        {(["LIVEBOARD", "ANSWER"] as const).map((t) => {
          const active = criteria.types.includes(t);
          return (
            <button key={t}
              onClick={() => setCriteria({ ...criteria, types: active ? criteria.types.filter((x) => x !== t) : [...criteria.types, t] })}
              style={{
                padding: "4px 10px", borderRadius: 20, fontSize: 12, cursor: "pointer",
                fontFamily: "Geist, sans-serif", fontWeight: active ? 500 : 400,
                background: active ? "#EDE9FE" : "#FAF8F4",
                border: `1px solid ${active ? "#8B5CF6" : "#E8E1D5"}`,
                color: active ? "#6D28D9" : "#7A7068",
              }}
            >
              {t === "LIVEBOARD" ? "Liveboard" : "Answer"}
            </button>
          );
        })}

        <div style={{ width: 1, height: 18, background: "#E8E1D5", margin: "0 2px" }} />

        {/* ── Filter-by-tag pills ──────────────────────────────────────── */}
        {criteria.filterTags.map((tag) => (
          <span key={tag} style={{
            display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px",
            borderRadius: 20, fontSize: 12, fontFamily: "Geist, sans-serif",
            background: "#FEF3C7", border: "1px solid #FCD34D", color: "#92400E",
          }}>
            {tag}
            <button
              onClick={() => setCriteria({ ...criteria, filterTags: criteria.filterTags.filter((t) => t !== tag) })}
              style={{ background: "none", border: "none", cursor: "pointer", padding: 0, lineHeight: 1, color: "#92400E" }}
            >
              <X size={10} />
            </button>
          </span>
        ))}

        {/* + Filter by tag button / dropdown */}
        <div style={{ position: "relative" }}>
          <button
            onClick={() => setTagPickerOpen((o) => !o)}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
              borderRadius: 20, fontSize: 12, cursor: "pointer", fontFamily: "Geist, sans-serif",
              background: tagPickerOpen ? "#FEF3C7" : "#FAF8F4",
              border: `1px solid ${tagPickerOpen ? "#FCD34D" : "#E8E1D5"}`,
              color: tagPickerOpen ? "#92400E" : "#7A7068",
            }}
          >
            + Filter by tag
          </button>
          {tagPickerOpen && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 50,
              background: "#fff", border: "1px solid #E8E1D5", borderRadius: 8,
              boxShadow: "0 4px 16px rgba(0,0,0,0.10)", minWidth: 180, maxHeight: 240, overflowY: "auto",
            }}>
              {tagPickerOptions.length === 0 ? (
                <div style={{ padding: "10px 14px", fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                  No more tags available
                </div>
              ) : tagPickerOptions.map((t) => (
                <button
                  key={t.ts_guid}
                  onClick={() => {
                    setCriteria({ ...criteria, filterTags: [...criteria.filterTags, t.name] });
                    setTagPickerOpen(false);
                  }}
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    padding: "8px 14px", fontSize: 13, fontFamily: "Geist, sans-serif",
                    background: "none", border: "none", cursor: "pointer", color: "#1A1714",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#F2EDE3")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                >
                  {t.name}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Exclude tags */}
        <input
          value={criteria.excludeTags.join(", ")}
          placeholder="Exclude tags…"
          onChange={(e) => setCriteria({ ...criteria, excludeTags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
          style={{ width: 140, height: 32, padding: "0 10px", borderRadius: 6, border: "1px solid #E8E1D5", background: "#FAF8F4", fontSize: 12, fontFamily: "Geist, sans-serif", outline: "none" }}
        />

        {/* Reset */}
        {isFiltered && (
          <button onClick={() => { setCriteria(DEFAULT_CRITERIA); setTagPickerOpen(false); }} style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            borderRadius: 6, border: "1px solid #E8E1D5", background: "transparent",
            fontSize: 12, color: "#7A7068", cursor: "pointer", fontFamily: "Geist, sans-serif",
          }}>
            <X size={11} /> Reset
          </button>
        )}

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace", whiteSpace: "nowrap" }}>
          {displayCount}
        </span>

        <button
          onClick={() => gridRef.current?.api.exportDataAsCsv({ fileName: "stale-content.csv" })}
          style={{
            display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
            borderRadius: 6, border: "1px solid #E8E1D5", background: "#FAF8F4",
            fontSize: 12, fontWeight: 500, color: "#1A1714", cursor: "pointer",
            fontFamily: "Geist, sans-serif",
          }}
        >
          <Download size={13} /> Export CSV
        </button>
      </div>

      {/* ── Toast notification ───────────────────────────────────────────── */}
      {toast && (
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          padding: "8px 14px", borderRadius: 6, flexShrink: 0,
          background: toast.ok ? "#D1FAE5" : "#FEF2F2",
          border: `1px solid ${toast.ok ? "#6EE7B7" : "#FECACA"}`,
          color: toast.ok ? "#065F46" : "#991B1B",
          fontSize: 13, fontFamily: "Geist, sans-serif",
        }}>
          {toast.msg}
          <button onClick={() => setToast(null)} style={{ background: "none", border: "none", cursor: "pointer", padding: 0, marginLeft: 12, color: "inherit" }}>
            <X size={12} />
          </button>
        </div>
      )}

      {/* ── Selection bar (shown when rows are checked) ──────────────────── */}
      {selectedGuids.size > 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
          background: "#EDE9FE", border: "1px solid #C4B5FD", borderRadius: 6, flexShrink: 0,
          flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 13, color: "#6D28D9", fontWeight: 500, fontFamily: "Geist, sans-serif", whiteSpace: "nowrap" }}>
            {selectedGuids.size} selected
          </span>

          {/* Tag name selector */}
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ fontSize: 12, color: "#6D28D9", fontFamily: "Geist, sans-serif" }}>Tag:</span>
            {showCustomInput ? (
              <input
                autoFocus
                value={customTagName}
                placeholder="New tag name…"
                onChange={(e) => setCustomTagName(e.target.value)}
                onBlur={() => { if (!customTagName.trim()) { setShowCustomInput(false); } }}
                style={{
                  height: 28, padding: "0 8px", borderRadius: 5, width: 140,
                  border: "1px solid #8B5CF6", background: "#fff", fontSize: 12,
                  fontFamily: "Geist, sans-serif", outline: "none", color: "#1A1714",
                }}
              />
            ) : (
              <select
                value={tagName}
                onChange={(e) => {
                  if (e.target.value === "__new__") { setShowCustomInput(true); setCustomTagName(""); }
                  else { setTagName(e.target.value); setShowCustomInput(false); }
                }}
                style={{
                  height: 28, padding: "0 6px", borderRadius: 5,
                  border: "1px solid #8B5CF6", background: "#fff", fontSize: 12,
                  fontFamily: "Geist, sans-serif", color: "#1A1714", cursor: "pointer",
                }}
              >
                <option value="Stale">Stale</option>
                {availableTags
                  .filter((t) => t.name !== "Stale")
                  .map((t) => <option key={t.ts_guid} value={t.name}>{t.name}</option>)}
                <option value="__new__">+ New tag…</option>
              </select>
            )}
          </div>

          <ActionButton onClick={() => handleTag("tag", [...selectedGuids])}>
            Apply Tag
          </ActionButton>

          {selectedTags.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, paddingLeft: 4 }}>
              <span style={{ fontSize: 11, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                Remove:
              </span>
              {selectedTags.map((t) => (
                <button
                  key={t}
                  onClick={() => handleTag("untag", [...selectedGuids], t)}
                  title={`Remove "${t}" from selected`}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    height: 22, padding: "0 4px 0 10px",
                    borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: "#FEF2F2", color: "#991B1B", border: "1px solid #FECACA",
                    fontFamily: "Geist, sans-serif", cursor: "pointer", whiteSpace: "nowrap",
                    transition: "background 120ms ease, border-color 120ms ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = "#FEE2E2";
                    e.currentTarget.style.borderColor = "#FCA5A5";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = "#FEF2F2";
                    e.currentTarget.style.borderColor = "#FECACA";
                  }}
                >
                  {t}
                  <X size={11} strokeWidth={2.5} />
                </button>
              ))}
            </div>
          )}

          <div style={{ width: 1, height: 18, background: "#C4B5FD" }} />

          <ActionButton
            onClick={handleDeleteSelected}
            style={{ color: "#991B1B", borderColor: "#FECACA", background: "#FEF2F2" }}
          >
            Delete selected
          </ActionButton>

          <div style={{ flex: 1 }} />

          <button
            onClick={() => gridRef.current?.api.deselectAll()}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 8px",
              borderRadius: 4, border: "1px solid #E8E1D5", background: "transparent",
              fontSize: 12, color: "#7A7068", cursor: "pointer", fontFamily: "Geist, sans-serif",
            }}
          >
            <X size={11} /> Clear
          </button>
        </div>
      )}

      {/* ── Hint (shown when nothing selected) ─────────────────────────── */}
      {selectedGuids.size === 0 && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "7px 12px",
          background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 6,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, color: "#C4B5FD" }}>☑</span>
          <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif", lineHeight: 1.5 }}>
            Check rows to select objects — then <strong style={{ color: "#6D28D9" }}>Apply Tag</strong> to mark them, or{" "}
            <strong style={{ color: "#991B1B" }}>Delete selected</strong> for a safety-checked deletion with TML backup.
          </span>
        </div>
      )}

      {/* ── AG Grid (full width, same as metadata.tsx) ────────────────────── */}
      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<ArchiverItem>
          ref={gridRef}
          columnDefs={polishedColumns}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          defaultColDef={{
            resizable: true,
            sortable: true,
            sortingOrder: ["asc", "desc"],
          }}
          rowSelection="multiple"
          suppressRowClickSelection
          onGridReady={handleGridReady}
          onGridSizeChanged={handleGridSizeChanged}
          onSortChanged={handleSortChanged}
          onFilterChanged={() => {
            colFiltersRef.current = gridRef.current?.api?.getFilterModel() ?? {};
            gridRef.current?.api?.purgeInfiniteCache();
          }}
          onSelectionChanged={handleSelectionChanged}
          overlayNoRowsTemplate="No stale content found. Adjust the criteria or sync metadata first."
        />
      </div>

      {/* DryRun modal */}
      {dryRunOpen && activeCluster && activeOrg != null && (
        <DryRunModal
          objectIds={pendingDeleteGuids}
          clusterId={activeCluster.id}
          orgId={activeOrg.org_id}
          onClose={handleDryRunClose}
          onViewHistory={onViewHistory}
        />
      )}
    </div>
  );
}

// ── History tab ─────────────────────────────────────────────────────────────────

const HISTORY_PAGE_SIZE = 200;

const TYPE_LABELS_HIST: Record<string, string> = { LIVEBOARD: "Liveboard", ANSWER: "Answer" };

const HISTORY_COLUMNS = [
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 180,
    sortable: true,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 120,
    sortable: true,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    filterValueGetter: (p: { data?: { object_type?: string } }) =>
      TYPE_LABELS_HIST[p.data?.object_type ?? ""] ?? p.data?.object_type,
    cellRenderer: (p: { value: string }) => (
      <span style={{
        padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
        fontFamily: "Geist, sans-serif",
        background: p.value === "LIVEBOARD" ? "#EDE9FE" : "#F3F4F6",
        color:      p.value === "LIVEBOARD" ? "#6D28D9"  : "#374151",
      }}>
        {TYPE_LABELS_HIST[p.value] ?? p.value}
      </span>
    ),
  },
  {
    field: "owner_name",
    headerName: "Owner",
    flex: 2,
    minWidth: 140,
    sortable: true,
  },
  {
    field: "archived_at",
    headerName: "Deleted On",
    width: 150,
    sortable: true,
    valueFormatter: (p: { value: string }) => {
      if (!p.value) return "—";
      const d = new Date(p.value);
      const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
      if (days === 0) return "Today";
      if (days === 1) return "Yesterday";
      if (days < 30) return `${days}d ago`;
      if (days < 365) return `${Math.floor(days / 30)}mo ago`;
      return `${Math.floor(days / 365)}y ago`;
    },
  },
  {
    colId: "deleted_by",
    headerName: "Deleted By",
    width: 120,
    sortable: false,
    filter: false,
    cellRenderer: () => (
      <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>Admin</span>
    ),
  },
  {
    colId: "tml",
    headerName: "Backup",
    width: 100,
    sortable: false,
    filter: false,
    resizable: false,
    // cellRenderer injected below to close over clusterId
  },
];

function HistoryTab() {
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact<ArchiveRecordFlatItem>>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("archived_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  // Held in a ref — see ArchiveTab for rationale (keeps filter popup open).
  const colFiltersRef = useRef<Record<string, any>>({});

  const clusterId = activeCluster?.id ?? "";

  // Inject the TML download renderer (needs clusterId at runtime)
  const tmlRenderer = useCallback((params: { data?: ArchiveRecordFlatItem }) => {
    if (!params.data) return null;
    const { id, name, tml_export_status } = params.data;
    if (tml_export_status !== "SUCCESS") {
      return <span style={{ color: "#C4B5FD", fontSize: 12 }}>—</span>;
    }
    return (
      <a
        href={`/api/v1/archiver/download/${id}?cluster_id=${clusterId}`}
        download
        title={`Download TML for "${name}"`}
        onClick={(e) => e.stopPropagation()}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = "#F9F7F2";
          e.currentTarget.style.borderColor = "#D8CFC8";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "#FFFFFF";
          e.currentTarget.style.borderColor = "#E8E1D5";
        }}
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          padding: "3px 9px", borderRadius: 6, fontSize: 11, fontWeight: 500,
          lineHeight: "16px",
          border: "1px solid #E8E1D5", background: "#FFFFFF", color: "#3F3A34",
          textDecoration: "none", fontFamily: "Geist, sans-serif",
          boxShadow: "0 1px 0 rgba(0,0,0,0.02)",
          transition: "background 120ms ease, border-color 120ms ease",
        }}
      >
        <Download size={11} strokeWidth={2} />
        <span>TML</span>
      </a>
    );
  }, [clusterId]);

  const columnsWithTml = HISTORY_COLUMNS.map((col: any) =>
    col.colId === "tml" ? { ...col, cellRenderer: tmlRenderer } : col
  );

  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    const cid = activeCluster.id;
    const oid = activeOrg.org_id;
    const sf = sortField;
    const so = sortOrder;

    const datasource: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const f = serializeFilterModel(colFiltersRef.current);
          const res = await archiverApi.allRecords({
            cluster_id: cid,
            org_id: oid,
            sort_field: sf,
            sort_order: so,
            search:            f.search,
            types:             f.types,
            owner_name_search: f.owner_name_search,
            archived_before:   f.archived_before,
            archived_after:    f.archived_after,
            record_offset: params.startRow,
            page_size: HISTORY_PAGE_SIZE,
          });
          setTotal(res.total);
          params.successCallback(res.items, res.total);
        } catch {
          params.failCallback();
        }
      },
    };
    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, sortField, sortOrder]);

  useEffect(() => { reloadGrid(); }, [reloadGrid]);

  const handleGridReady = useCallback(() => {
    reloadGrid();
    setTimeout(() => gridRef.current?.api?.sizeColumnsToFit(), 0);
  }, [reloadGrid]);

  const handleGridSizeChanged = useCallback(() => {
    gridRef.current?.api?.sizeColumnsToFit();
  }, []);

  const handleSortChanged = useCallback(() => {
    const cols = gridRef.current?.api.getColumnState() ?? [];
    const sorted = cols.find((c) => c.sort);
    if (sorted?.colId) {
      setSortField(sorted.colId);
      setSortOrder(sorted.sort as "asc" | "desc");
    }
  }, []);

  const displayCount = total == null ? "" : `${total.toLocaleString()} deleted object${total !== 1 ? "s" : ""}`;

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, padding: 24, gap: 12, overflow: "hidden" }}>

      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace" }}>
          {displayCount}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => gridRef.current?.api.exportDataAsCsv({ fileName: "deleted-objects.csv" })}
          style={{
            display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
            borderRadius: 6, border: "1px solid #E8E1D5", background: "#FAF8F4",
            fontSize: 12, fontWeight: 500, color: "#1A1714", cursor: "pointer",
            fontFamily: "Geist, sans-serif",
          }}
        >
          <Download size={13} /> Export CSV
        </button>
      </div>

      {/* AG Grid */}
      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<ArchiveRecordFlatItem>
          ref={gridRef}
          columnDefs={columnsWithTml}
          rowModelType="infinite"
          cacheBlockSize={HISTORY_PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={HISTORY_PAGE_SIZE}
          defaultColDef={{
            resizable: true,
            sortable: true,
            sortingOrder: ["asc", "desc"],
            wrapHeaderText: true,
            autoHeaderHeight: true,
          }}
          onGridReady={handleGridReady}
          onGridSizeChanged={handleGridSizeChanged}
          onSortChanged={handleSortChanged}
          onFilterChanged={() => {
            colFiltersRef.current = gridRef.current?.api?.getFilterModel() ?? {};
            gridRef.current?.api?.purgeInfiniteCache();
          }}
          overlayNoRowsTemplate="No deleted objects yet. Delete stale content from the Archive tab."
        />
      </div>
    </div>
  );
}

function ActionButton({ children, onClick, style }: { children: React.ReactNode; onClick: () => void; style?: React.CSSProperties }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 12px", borderRadius: 6, fontSize: 12, fontWeight: 500,
        cursor: "pointer", border: "1px solid #E8E1D5", background: "#fff",
        color: "#1A1714", fontFamily: "Geist, sans-serif", ...style,
      }}
    >
      {children}
    </button>
  );
}
