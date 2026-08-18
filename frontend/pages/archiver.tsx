import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { IDatasource, IGetRowsParams } from "ag-grid-community";
import { Download, X, Search, ChevronDown, Clock } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { OBJECT_COLUMNS as ARCHIVER_COLUMNS, serializeFilterModel } from "@/components/Deleter/columns";
import { intersectTypes } from "@/lib/agGridFilters";
import { DryRunModal } from "@/components/Deleter/DryRunModal";
import { archiverApi, jobsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { ArchiverItem, Job } from "@/lib/types";

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
  // History was moved to /jobs (shared between Archiver + Bulk Deleter).
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <ArchiveTab
        syncVersion={syncVersion}
        onViewHistory={() => { window.location.href = "/jobs"; }}
      />
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

  // Spell out what each number means — "90d AND 90d" alone is unreadable.
  const summary = `unused ${props.staleActivityDays}d ${props.staleOperator} unmodified ${props.staleModifiedDays}d`;

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
          fontSize: 12, fontWeight: 500, fontFamily: theme.font.sans,
          background: open ? theme.color.accentSoft : theme.color.surface,
          border: `1px solid ${open ? theme.color.violetBorder : theme.color.border}`,
          color: open ? theme.color.accent2 : theme.color.textPrimary,
          cursor: "pointer", whiteSpace: "nowrap",
          transition: "background 120ms ease, border-color 120ms ease, color 120ms ease",
        }}
        onMouseEnter={(e) => { if (!open) e.currentTarget.style.borderColor = theme.color.borderLight; }}
        onMouseLeave={(e) => { if (!open) e.currentTarget.style.borderColor = theme.color.border; }}
      >
        <Clock size={12} strokeWidth={2} />
        <span style={{ color: theme.color.textMuted, fontWeight: 400 }}>Stale:</span>
        <span>{summary}</span>
        <ChevronDown size={12} color={theme.color.textMuted} />
      </button>

      {open && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 50,
          width: 260, background: theme.color.surface,
          border: `1px solid ${theme.color.border}`, borderRadius: 10,
          boxShadow: theme.shadow.md,
          fontFamily: theme.font.sans,
          animation: "popoverIn 140ms ease-out",
          overflow: "hidden",
        }}>
          {/* Header */}
          <div style={{
            padding: "10px 14px", borderBottom: `1px solid ${theme.color.border}`,
            display: "flex", alignItems: "center", gap: 6,
          }}>
            <Clock size={12} color={theme.color.accent} strokeWidth={2.25} />
            <span style={{ fontSize: 12, fontWeight: 600, color: theme.color.textPrimary, letterSpacing: "0.01em" }}>
              Stale criteria
            </span>
          </div>

          {/* Body */}
          <div style={{ padding: "14px 14px 12px", display: "flex", flexDirection: "column", gap: 12 }}>
            {/* Last Accessed row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <span style={{ fontSize: 12, color: theme.color.textSecondary, fontWeight: 500 }}>Last Accessed ≥</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number" min={1} value={draft.staleActivityDays}
                  onChange={(e) => setDraft({ ...draft, staleActivityDays: Math.max(1, Number(e.target.value)) })}
                  onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
                  className="stale-num"
                  style={{
                    width: 64, height: 30, padding: "0 10px", borderRadius: 6,
                    border: `1px solid ${theme.color.border}`, background: theme.color.surface,
                    fontSize: 13, fontFamily: theme.font.sans, color: theme.color.textPrimary,
                    outline: "none", textAlign: "right",
                  }}
                />
                <span style={{ fontSize: 11, color: theme.color.textMuted, width: 28 }}>days</span>
              </div>
            </div>

            {/* AND/OR unified segmented control */}
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{ flex: 1, height: 1, background: theme.color.border }} />
              <div style={{
                display: "inline-flex", padding: 2, borderRadius: 999,
                background: theme.color.surface2, border: `1px solid ${theme.color.border}`,
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
                        borderRadius: 999, fontFamily: theme.font.sans,
                        background: active ? theme.gradient.accent : "transparent",
                        color: active ? theme.color.onAccent : theme.color.textMuted,
                        border: "none", cursor: "pointer",
                        boxShadow: active ? theme.shadow.glowAccent : "none",
                        transition: "background 140ms ease, color 140ms ease",
                      }}
                    >
                      {op}
                    </button>
                  );
                })}
              </div>
              <div style={{ flex: 1, height: 1, background: theme.color.border }} />
            </div>

            {/* Last Modified row */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
              <span style={{ fontSize: 12, color: theme.color.textSecondary, fontWeight: 500 }}>Last Modified ≥</span>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  type="number" min={1} value={draft.staleModifiedDays}
                  onChange={(e) => setDraft({ ...draft, staleModifiedDays: Math.max(1, Number(e.target.value)) })}
                  onKeyDown={(e) => { if (e.key === "Enter") apply(); }}
                  className="stale-num"
                  style={{
                    width: 64, height: 30, padding: "0 10px", borderRadius: 6,
                    border: `1px solid ${theme.color.border}`, background: theme.color.surface,
                    fontSize: 13, fontFamily: theme.font.sans, color: theme.color.textPrimary,
                    outline: "none", textAlign: "right",
                  }}
                />
                <span style={{ fontSize: 11, color: theme.color.textMuted, width: 28 }}>days</span>
              </div>
            </div>
          </div>

          {/* Footer */}
          <div style={{
            display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8,
            padding: "8px 12px", borderTop: `1px solid ${theme.color.border}`, background: theme.color.surface,
          }}>
            <button
              onClick={reset}
              style={{
                height: 30, padding: "0 12px", borderRadius: 6,
                border: "1px solid transparent", background: "transparent",
                fontSize: 12, fontWeight: 500, color: theme.color.textMuted,
                fontFamily: theme.font.sans, cursor: "pointer",
                transition: "background 120ms ease, color 120ms ease",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = theme.color.surface2; e.currentTarget.style.color = theme.color.textPrimary; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = theme.color.textMuted; }}
            >
              Reset
            </button>
            <button
              onClick={apply}
              style={{
                height: 30, padding: "0 14px", borderRadius: 6,
                border: `1px solid ${theme.color.accent}`, background: theme.gradient.accent,
                fontSize: 12, fontWeight: 600, color: theme.color.onAccent,
                fontFamily: theme.font.sans, cursor: "pointer", minWidth: 64,
                boxShadow: theme.shadow.glowAccent,
                transition: "background 120ms ease, border-color 120ms ease",
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = theme.gradient.accent; e.currentTarget.style.borderColor = theme.color.accent; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = theme.gradient.accent; e.currentTarget.style.borderColor = theme.color.accent; }}
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
  // Tag/untag are live writes — never fire them straight off a button click.
  // The pending action is confirmed (with its count) in a small dialog first.
  const [pendingTag, setPendingTag] = useState<
    { action: "tag" | "untag"; guids: string[]; tag: string } | null
  >(null);

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
            types:              intersectTypes(f.types, snap.types),
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
  const handleTag = async (action: "tag" | "untag", guids: string[], explicitTag?: string) => {
    if (!activeCluster?.id || activeOrg?.org_id == null || !guids.length) return;
    const tagForAction = action === "tag" ? (explicitTag ?? resolvedTagName) : (explicitTag ?? "");
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
        const tagLabel = tagForAction;

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
              fontFamily: theme.font.sans,
              background: p.value === "LIVEBOARD" ? theme.color.accentSoft : theme.color.surface3,
              color:      p.value === "LIVEBOARD" ? theme.color.accent2  : theme.color.textSecondary,
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
            if (!tags.length) return <span style={{ color: theme.color.textMuted, fontFamily: theme.font.sans }}>—</span>;
            return (
              <div style={{ display: "flex", gap: 4, alignItems: "center", height: "100%", flexWrap: "wrap" }}>
                {tags.slice(0, 2).map((t: string) => (
                  <span key={t} style={{
                    display: "inline-block", lineHeight: "18px",
                    padding: "0 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: theme.color.surface3, color: theme.color.textSecondary, fontFamily: theme.font.sans,
                    whiteSpace: "nowrap",
                  }}>{t}</span>
                ))}
                {tags.length > 2 && (
                  <span style={{
                    display: "inline-block", lineHeight: "18px",
                    padding: "0 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: theme.color.surface3, color: theme.color.textSecondary, fontFamily: theme.font.sans,
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
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: theme.color.textMuted }} />
          <input
            value={criteria.search}
            onChange={(e) => setCriteria({ ...criteria, search: e.target.value })}
            placeholder="Search by name…"
            style={{
              width: "100%", paddingLeft: 30, paddingRight: 10,
              height: 34, borderRadius: 6, border: `1px solid ${theme.color.border}`,
              background: theme.color.surface, fontSize: 13, color: theme.color.textPrimary,
              fontFamily: theme.font.sans, outline: "none",
            }}
          />
        </div>

        <div style={{ width: 1, height: 18, background: theme.color.border, margin: "0 2px" }} />

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

        <div style={{ width: 1, height: 18, background: theme.color.border, margin: "0 2px" }} />

        {/* Type toggles */}
        {(["LIVEBOARD", "ANSWER"] as const).map((t) => {
          const active = criteria.types.includes(t);
          return (
            <button key={t}
              onClick={() => setCriteria({ ...criteria, types: active ? criteria.types.filter((x) => x !== t) : [...criteria.types, t] })}
              style={{
                padding: "4px 10px", borderRadius: 20, fontSize: 12, cursor: "pointer",
                fontFamily: theme.font.sans, fontWeight: active ? 500 : 400,
                background: active ? theme.color.accentSoft : theme.color.surface,
                border: `1px solid ${active ? theme.color.accent : theme.color.border}`,
                color: active ? theme.color.accent2 : theme.color.textMuted,
              }}
            >
              {t === "LIVEBOARD" ? "Liveboard" : "Answer"}
            </button>
          );
        })}

        <div style={{ width: 1, height: 18, background: theme.color.border, margin: "0 2px" }} />

        {/* ── Filter-by-tag pills ──────────────────────────────────────── */}
        {criteria.filterTags.map((tag) => (
          <span key={tag} style={{
            display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px",
            borderRadius: 20, fontSize: 12, fontFamily: theme.font.sans,
            background: theme.color.warnSoft, border: `1px solid ${theme.color.warnBorder}`, color: theme.color.warn,
          }}>
            {tag}
            <button
              onClick={() => setCriteria({ ...criteria, filterTags: criteria.filterTags.filter((t) => t !== tag) })}
              style={{ background: "none", border: "none", cursor: "pointer", padding: 0, lineHeight: 1, color: theme.color.warn }}
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
              borderRadius: 20, fontSize: 12, cursor: "pointer", fontFamily: theme.font.sans,
              background: tagPickerOpen ? theme.color.warnSoft : theme.color.surface,
              border: `1px solid ${tagPickerOpen ? theme.color.warnBorder : theme.color.border}`,
              color: tagPickerOpen ? theme.color.warn : theme.color.textMuted,
            }}
          >
            + Filter by tag
          </button>
          {tagPickerOpen && (
            <div style={{
              position: "absolute", top: "calc(100% + 4px)", left: 0, zIndex: 50,
              background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 8,
              boxShadow: theme.shadow.md, minWidth: 180, maxHeight: 240, overflowY: "auto",
            }}>
              {tagPickerOptions.length === 0 ? (
                <div style={{ padding: "10px 14px", fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
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
                    padding: "8px 14px", fontSize: 13, fontFamily: theme.font.sans,
                    background: "none", border: "none", cursor: "pointer", color: theme.color.textPrimary,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = theme.color.bg)}
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
          style={{ width: 140, height: 32, padding: "0 10px", borderRadius: 6, border: `1px solid ${theme.color.border}`, background: theme.color.surface, color: theme.color.textPrimary, fontSize: 12, fontFamily: theme.font.sans, outline: "none" }}
        />

        {/* Reset */}
        {isFiltered && (
          <button onClick={() => { setCriteria(DEFAULT_CRITERIA); setTagPickerOpen(false); }} style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            borderRadius: 6, border: `1px solid ${theme.color.border}`, background: "transparent",
            fontSize: 12, color: theme.color.textMuted, cursor: "pointer", fontFamily: theme.font.sans,
          }}>
            <X size={11} /> Reset
          </button>
        )}

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono, whiteSpace: "nowrap" }}>
          {displayCount}
        </span>

        <button
          onClick={() => gridRef.current?.api.exportDataAsCsv({ fileName: "stale-content.csv" })}
          style={{
            display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
            borderRadius: 6, border: `1px solid ${theme.color.border}`, background: theme.color.surface,
            fontSize: 12, fontWeight: 500, color: theme.color.textPrimary, cursor: "pointer",
            fontFamily: theme.font.sans,
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
          background: toast.ok ? theme.color.successSoft : theme.color.dangerSoft,
          border: `1px solid ${toast.ok ? theme.color.successBorder : theme.color.dangerBorder}`,
          color: toast.ok ? theme.color.success : theme.color.danger,
          fontSize: 13, fontFamily: theme.font.sans,
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
          background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6, flexShrink: 0,
          flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 13, color: theme.color.accent2, fontWeight: 500, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
            {selectedGuids.size} selected
          </span>

          {/* Tag name selector */}
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ fontSize: 12, color: theme.color.accent2, fontFamily: theme.font.sans }}>Tag:</span>
            {showCustomInput ? (
              <input
                autoFocus
                value={customTagName}
                placeholder="New tag name…"
                onChange={(e) => setCustomTagName(e.target.value)}
                onBlur={() => { if (!customTagName.trim()) { setShowCustomInput(false); } }}
                style={{
                  height: 28, padding: "0 8px", borderRadius: 5, width: 140,
                  border: `1px solid ${theme.color.accent}`, background: theme.color.surface, fontSize: 12,
                  fontFamily: theme.font.sans, outline: "none", color: theme.color.textPrimary,
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
                  border: `1px solid ${theme.color.accent}`, background: theme.color.surface, fontSize: 12,
                  fontFamily: theme.font.sans, color: theme.color.textPrimary, cursor: "pointer",
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

          <ActionButton onClick={() => setPendingTag({ action: "tag", guids: [...selectedGuids], tag: resolvedTagName })}>
            Apply Tag…
          </ActionButton>

          {selectedTags.length > 0 && (
            <div style={{ display: "flex", alignItems: "center", gap: 6, paddingLeft: 4 }}>
              <span style={{ fontSize: 11, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
                Remove:
              </span>
              {selectedTags.map((t) => (
                <button
                  key={t}
                  onClick={() => setPendingTag({ action: "untag", guids: [...selectedGuids], tag: t })}
                  title={`Remove "${t}" from selected`}
                  style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    height: 22, padding: "0 4px 0 10px",
                    borderRadius: 20, fontSize: 11, fontWeight: 500,
                    background: theme.color.dangerSoft, color: theme.color.danger, border: `1px solid ${theme.color.dangerBorder}`,
                    fontFamily: theme.font.sans, cursor: "pointer", whiteSpace: "nowrap",
                    transition: "background 120ms ease, border-color 120ms ease",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.background = theme.color.dangerSoft;
                    e.currentTarget.style.borderColor = theme.color.dangerBorder;
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = theme.color.dangerSoft;
                    e.currentTarget.style.borderColor = theme.color.dangerBorder;
                  }}
                >
                  {t}
                  <X size={11} strokeWidth={2.5} />
                </button>
              ))}
            </div>
          )}

          <div style={{ width: 1, height: 18, background: theme.color.violetBorder }} />

          <ActionButton
            data-testid="open-dryrun-modal"
            onClick={handleDeleteSelected}
            style={{ color: theme.color.danger, borderColor: theme.color.dangerBorder, background: theme.color.dangerSoft }}
          >
            Delete selected
          </ActionButton>

          <div style={{ flex: 1 }} />

          <button
            onClick={() => gridRef.current?.api.deselectAll()}
            style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 8px",
              borderRadius: 4, border: `1px solid ${theme.color.border}`, background: "transparent",
              fontSize: 12, color: theme.color.textMuted, cursor: "pointer", fontFamily: theme.font.sans,
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
          background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, color: theme.color.textMuted }}>☑</span>
          <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, lineHeight: 1.5 }}>
            Check rows to select objects — then <strong style={{ color: theme.color.accent2 }}>Apply Tag</strong> to mark them, or{" "}
            <strong style={{ color: theme.color.danger }}>Delete selected</strong> for a safety-checked deletion with TML backup.
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
          api={{
            dryrun: archiverApi.dryrun,
            dryrunObjects: archiverApi.dryrunObjects,
            execute: (body) => archiverApi.execute({ ...body, action: "delete" }),
          }}
          objectIds={pendingDeleteGuids}
          clusterId={activeCluster.id}
          orgId={activeOrg.org_id}
          onClose={handleDryRunClose}
          onViewHistory={onViewHistory}
        />
      )}

      {/* Tag/untag confirmation */}
      {pendingTag && (
        <ConfirmTagModal
          pending={pendingTag}
          onCancel={() => setPendingTag(null)}
          onConfirm={() => {
            handleTag(pendingTag.action, pendingTag.guids, pendingTag.tag);
            setPendingTag(null);
          }}
        />
      )}
    </div>
  );
}

function ConfirmTagModal({ pending, onCancel, onConfirm }: {
  pending: { action: "tag" | "untag"; guids: string[]; tag: string };
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  const n = pending.guids.length;
  const plural = n !== 1 ? "s" : "";
  const message =
    pending.action === "tag"
      ? <>Tag <strong>{n} object{plural}</strong> as “{pending.tag}”?</>
      : <>Remove “{pending.tag}” from <strong>{n} object{plural}</strong>?</>;

  return (
    <>
      <div onClick={onCancel} style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 300 }} />
      <div role="dialog" aria-modal="true" style={{
        position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
        width: 380, background: theme.color.surface, borderRadius: 10, zIndex: 301,
        boxShadow: theme.shadow.lg, padding: 20, fontFamily: theme.font.sans,
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, marginBottom: 8 }}>
          {pending.action === "tag" ? "Apply tag" : "Remove tag"}
        </div>
        <p style={{ margin: "0 0 16px", fontSize: 13, color: theme.color.textSecondary, lineHeight: 1.5 }}>
          {message} This changes the objects on your ThoughtSpot instance immediately.
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button onClick={onCancel} style={{
            padding: "7px 14px", borderRadius: 6, fontSize: 13, cursor: "pointer",
            border: `1px solid ${theme.color.border}`, background: theme.color.surface,
            color: theme.color.textMuted, fontFamily: theme.font.sans,
          }}>
            Cancel
          </button>
          <button autoFocus onClick={onConfirm} style={{
            padding: "7px 14px", borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: "pointer",
            border: "none", background: theme.gradient.accent, color: theme.color.onAccent,
            fontFamily: theme.font.sans,
          }}>
            {pending.action === "tag" ? `Tag ${n} object${plural}` : `Remove tag`}
          </button>
        </div>
      </div>
    </>
  );
}

// HistoryTab is now shared with the Bulk Deleter — see @/components/Deleter/HistoryTab.

function ActionButton({
  children,
  onClick,
  style,
  ...rest
}: {
  children: React.ReactNode;
  onClick: () => void;
  style?: React.CSSProperties;
} & Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onClick" | "style" | "children">) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "5px 12px", borderRadius: 6, fontSize: 12, fontWeight: 500,
        cursor: "pointer", border: `1px solid ${theme.color.border}`, background: theme.color.surface,
        color: theme.color.textPrimary, fontFamily: theme.font.sans, ...style,
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
