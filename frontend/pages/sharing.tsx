/**
 * Bulk Sharing page.
 *
 * Archiver-style: browse a server-paginated grid of all metadata objects,
 * filter + checkbox-select the ones to share, then the selection bar opens
 * a ShareModal (pick principals + access level → preview → confirm → execute).
 *
 * Deep link: arriving with `?guids=g1,g2…` (e.g. from the metadata grid)
 * opens the ShareModal directly on that set.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/router";
import { AgGridReact } from "ag-grid-react";
import type { IDatasource, IGetRowsParams } from "ag-grid-community";
import { Download, Search, X, Share2, History } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { OBJECT_COLUMNS } from "@/components/Deleter/columns";
import { SharingHistoryTab } from "@/components/Sharing/HistoryTab";
import { ShareModal } from "@/components/Sharing/ShareModal";
import { metadataApi } from "@/lib/api";
import { serializeFilterModel } from "@/lib/agGridFilters";
import { metadataEmptyMessage, noRowsOverlay, withHiddenSuffix } from "@/lib/gridEmptyState";
import { theme } from "@/lib/theme";
import type { MetadataObject } from "@/lib/types";
import { TYPE_LABELS } from "@/lib/objectTypes";

const PAGE_SIZE = 200;

const OBJECT_TYPES = ["LIVEBOARD", "ANSWER", "WORKSHEET", "ONE_TO_ONE_LOGICAL", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED"];

type Tab = "share" | "history";

// ── Page wrapper ───────────────────────────────────────────────────────────────

export default function SharingPage() {
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell
      pageTitle="Bulk Sharing"
      entityType="metadata"
      onSyncComplete={() => setSyncVersion((v) => v + 1)}
    >
      <SharingContent syncVersion={syncVersion} />
    </AppShell>
  );
}

// ── Content ──────────────────────────────────────────────────────────────────

function SharingContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();
  const [tab, setTab] = useState<Tab>("share");

  if (!activeCluster) {
    return (
      <div style={{
        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 14, color: theme.color.textMuted, fontFamily: theme.font.sans,
      }}>Select an instance from the topbar to start.</div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Tabs */}
      <div style={{
        display: "flex", borderBottom: `1px solid ${theme.color.border}`,
        background: theme.color.surface, paddingLeft: 24, flexShrink: 0,
      }}>
        {[
          { id: "share" as Tab, label: "Share", icon: Share2 },
          { id: "history" as Tab, label: "History", icon: History },
        ].map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 18px", fontSize: 13, fontWeight: tab === id ? 600 : 400,
              fontFamily: theme.font.sans, cursor: "pointer", border: "none",
              background: "transparent", color: tab === id ? theme.color.accent2 : theme.color.textMuted,
              borderBottom: tab === id ? `2px solid ${theme.color.accent}` : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {tab === "history" ? (
        <SharingHistoryTab clusterId={activeCluster.id} orgId={activeOrg?.org_id} />
      ) : (
        <ShareTab syncVersion={syncVersion} />
      )}
    </div>
  );
}

// ── Share tab (grid + selection bar) ───────────────────────────────────────────

function ShareTab({ syncVersion }: { syncVersion: number }) {
  const router = useRouter();
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact<MetadataObject>>(null);

  const [total, setTotal] = useState<number | null>(null);
  const [hiddenSystem, setHidden] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [selectedTypes, setTypes] = useState<string[]>([]);
  const [sortField, setSortField] = useState("modified_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [selectedGuids, setSelectedGuids] = useState<Set<string>>(new Set());

  // Held in a ref so updating filters doesn't recreate the datasource (which
  // would close an open filter popup mid-typing). On filter change we purge the
  // infinite cache; the existing getRows closure reads the ref.
  const colFiltersRef = useRef<Record<string, any>>({});

  // ShareModal holds the exact GUID set to share (null = closed).
  const [shareGuids, setShareGuids] = useState<string[] | null>(null);

  // Debounce search input → search state
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Deep link: arrive from the metadata grid with `?guids=g1,g2…` → open modal
  useEffect(() => {
    if (router.query.guids && typeof router.query.guids === "string") {
      const guids = router.query.guids.split(",").map((s) => s.trim()).filter(Boolean);
      if (guids.length) setShareGuids(guids);
    }
  }, [router.query.guids]);

  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    const clusterId = activeCluster.id;
    const orgId = activeOrg.org_id;
    const toolbarTypes = selectedTypes.length > 0 ? selectedTypes : undefined;
    const toolbarSearch = search.trim() || undefined;
    const sf = sortField;
    const so = sortOrder;

    const datasource: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const f = serializeFilterModel(colFiltersRef.current);
          const res = await metadataApi.list({
            cluster_id: clusterId,
            org_id: orgId,
            types:                f.types ?? toolbarTypes,
            search:               f.search ?? toolbarSearch,
            owner_name_search:    f.owner_name_search,
            tag_search:           f.tag_search,
            views_min:            f.views_min,
            views_max:            f.views_max,
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
          setHidden(res.hidden_system_count ?? 0);
          params.successCallback(res.items, res.total);
        } catch {
          params.failCallback();
        }
      },
    };
    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, search, selectedTypes, sortField, sortOrder]);

  useEffect(() => { reloadGrid(); }, [reloadGrid, syncVersion]);

  const handleGridReady = useCallback(() => { reloadGrid(); }, [reloadGrid]);

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
  }, []);

  // Reuse the shared object columns, minus `days_unused` (MetadataObject has no
  // such field) — keeps the type chip + tag rendering identical to the Archiver grid.
  const columns = useMemo(() => OBJECT_COLUMNS.filter((c) => c.field !== "days_unused"), []);

  const clearFilters = () => { setSearchInput(""); setSearch(""); setTypes([]); };
  const hasFilters = searchInput || selectedTypes.length > 0;

  const displayCount = total == null
    ? "Loading…"
    : withHiddenSuffix(
        selectedTypes.length > 0 || search ? `${total.toLocaleString()} results` : `${total.toLocaleString()} objects`,
        hiddenSystem,
      );

  // Same empty-grid trap as the Metadata page: this list hides System User
  // content, so an org holding only built-in content shows zero rows and looks
  // like a failed sync. Explain it instead. (Infinite row model never raises the
  // no-rows overlay by itself.)
  const emptyMessage = useMemo(
    () => metadataEmptyMessage({
      hiddenSystem,
      isFiltered: Boolean(search) || selectedTypes.length > 0 || Object.keys(colFiltersRef.current).length > 0,
    }),
    [hiddenSystem, search, selectedTypes],
  );

  useEffect(() => {
    const api = gridRef.current?.api;
    if (!api) return;
    if (total === 0) {
      api.setGridOption("overlayNoRowsTemplate", noRowsOverlay(emptyMessage));
      api.showNoRowsOverlay();
    } else {
      api.hideOverlay();
    }
  }, [total, emptyMessage]);

  return (
    <div style={{ display: "flex", flexDirection: "column", flex: 1, padding: 24, gap: 12, overflow: "hidden", minHeight: 0 }}>

      {/* ── Toolbar ──────────────────────────────────────────────────────── */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", flexShrink: 0 }}>

        {/* Search */}
        <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 320 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: theme.color.textMuted }} />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name…"
            style={{
              width: "100%", paddingLeft: 30, paddingRight: 10,
              height: 34, borderRadius: 6, border: `1px solid ${theme.color.border}`,
              background: theme.color.surface, fontSize: 13, color: theme.color.textPrimary,
              fontFamily: theme.font.sans, outline: "none",
            }}
          />
        </div>

        {/* Type filter pills */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {OBJECT_TYPES.map((t) => {
            const active = selectedTypes.includes(t);
            return (
              <button key={t}
                onClick={() => setTypes(active ? selectedTypes.filter((x) => x !== t) : [...selectedTypes, t])}
                style={{
                  padding: "4px 10px", borderRadius: 20, fontSize: 12, cursor: "pointer",
                  fontFamily: theme.font.sans, fontWeight: active ? 500 : 400,
                  background: active ? theme.color.accentSoft : theme.color.surface,
                  border: `1px solid ${active ? theme.color.accent : theme.color.border}`,
                  color: active ? theme.color.accent2 : theme.color.textMuted,
                }}
              >
                {TYPE_LABELS[t]}
              </button>
            );
          })}
        </div>

        {/* Clear filters */}
        {hasFilters && (
          <button onClick={clearFilters} style={{
            display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
            borderRadius: 6, border: `1px solid ${theme.color.border}`, background: "transparent",
            fontSize: 12, color: theme.color.textMuted, cursor: "pointer", fontFamily: theme.font.sans,
          }}>
            <X size={11} /> Clear
          </button>
        )}

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono, whiteSpace: "nowrap" }}>
          {displayCount}
        </span>

        <button
          onClick={() => gridRef.current?.api.exportDataAsCsv({ fileName: "sharing-objects.csv" })}
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

      {/* ── Selection bar (shown when rows are checked) ──────────────────── */}
      {selectedGuids.size > 0 ? (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
          background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6, flexShrink: 0,
          flexWrap: "wrap",
        }}>
          <span style={{ fontSize: 13, color: theme.color.accent2, fontWeight: 500, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
            {selectedGuids.size} selected
          </span>

          <div style={{ flex: 1 }} />

          <button
            data-testid="open-share-modal"
            onClick={() => setShareGuids([...selectedGuids])}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "5px 14px", borderRadius: 6, fontSize: 12, fontWeight: 600,
              cursor: "pointer", border: `1px solid ${theme.color.accent}`, background: theme.gradient.accent,
              boxShadow: theme.shadow.glowAccent,
              color: theme.color.onAccent, fontFamily: theme.font.sans,
            }}
          >
            <Share2 size={13} /> Share selected…
          </button>

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
      ) : (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "7px 12px",
          background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6, flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, color: theme.color.textMuted }}>☑</span>
          <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, lineHeight: 1.5 }}>
            Check rows to select objects — then <strong style={{ color: theme.color.accent2 }}>Share selected</strong> to
            pick recipients and an access level, preview the diff, and apply.
          </span>
        </div>
      )}

      {/* ── AG Grid ──────────────────────────────────────────────────────── */}
      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<MetadataObject>
          ref={gridRef}
          columnDefs={columns}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          defaultColDef={{ resizable: true, sortable: true, sortingOrder: ["asc", "desc"] }}
          rowSelection="multiple"
          suppressRowClickSelection
          onGridReady={handleGridReady}
          onSortChanged={handleSortChanged}
          onFilterChanged={() => {
            colFiltersRef.current = gridRef.current?.api?.getFilterModel() ?? {};
            gridRef.current?.api?.purgeInfiniteCache();
          }}
          onSelectionChanged={handleSelectionChanged}
        />
      </div>

      {/* ── Share modal ──────────────────────────────────────────────────── */}
      {shareGuids && activeCluster && activeOrg != null && (
        <ShareModal
          objectGuids={shareGuids}
          clusterId={activeCluster.id}
          orgId={activeOrg.org_id}
          onClose={() => setShareGuids(null)}
        />
      )}
    </div>
  );
}
