import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { GridReadyEvent, RowClickedEvent, SortChangedEvent } from "ag-grid-community";
import { Download, Search, X } from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { METADATA_COLUMNS } from "@/components/MetadataGrid/columns";
import PermissionDrawer from "@/components/MetadataGrid/PermissionDrawer";
import { metadataApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { createGuardedDatasource } from "@/lib/gridDatasource";
import { intersectTypes, serializeFilterModel } from "@/lib/agGridFilters";
import { metadataEmptyMessage, noRowsOverlay, withHiddenSuffix } from "@/lib/gridEmptyState";
import type { MetadataObject } from "@/lib/types";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
import { OBJECT_TYPES, TYPE_LABELS } from "@/lib/objectTypes";

const PAGE_SIZE = 200;


// ── Page wrapper ───────────────────────────────────────────────────────────────

export default function MetadataPage() {
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Metadata" entityType="metadata" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
      <MetadataContent syncVersion={syncVersion} />
    </AppShell>
  );
}

// ── Inner content — useShell() is valid here because we're inside the provider ─

function MetadataContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact>(null);
  // Generation counter for the active datasource — see the same guard on the
  // Groups page. Drops responses from a superseded datasource.
  const loadIdRef = useRef(0);

  const [total, setTotal]             = useState<number | null>(null);
  const [hiddenSystem, setHidden]     = useState(0);
  const [search, setSearch]           = useState("");
  const [selectedTypes, setTypes]     = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState(""); // raw input, debounced into search
  const [drawerObject, setDrawerObject] = useState<MetadataObject | null>(null);
  const [sortField, setSortField]     = useState("modified_at");
  const [sortOrder, setSortOrder]     = useState<"asc" | "desc">("desc");
  const [colFilters, setColFilters]   = useState<Record<string, any>>({});

  // Debounce search input → search state
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Build and set a new datasource whenever cluster/org/search/types/sort change
  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;

    const clusterId = activeCluster.id;
    const orgId = activeOrg.org_id;
    const toolbarTypes  = selectedTypes.length > 0 ? selectedTypes : undefined;
    const toolbarSearch = search.trim() || undefined;

    const f = serializeFilterModel(colFilters);

    setTotal(null); // "Loading…" until the first block lands — never a stale count

    const datasource = createGuardedDatasource({
      loadIdRef,
      fetchPage: (startRow) => metadataApi.list({
        cluster_id: clusterId,
        org_id: orgId,
        types:               intersectTypes(f.types, toolbarTypes),
        search:              f.search ?? toolbarSearch,
        owner_name_search:   f.owner_name_search,
        tag_search:          f.tag_search,
        views_min:           f.views_min,
        views_max:           f.views_max,
        last_accessed_before: f.last_accessed_before,
        last_accessed_after:  f.last_accessed_after,
        modified_before:     f.modified_before,
        modified_after:      f.modified_after,
        created_before:      f.created_before,
        created_after:       f.created_after,
        sort_field: sortField,
        sort_order: sortOrder,
        record_offset: startRow,
        page_size: PAGE_SIZE,
      }),
      onLoaded: (res) => {
        setTotal(res.total);
        setHidden(res.hidden_system_count ?? 0);
      },
    });

    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, search, selectedTypes, sortField, sortOrder, colFilters]);

  const handleGridReady = useCallback((e: GridReadyEvent) => {
    e.api.applyColumnState({
      state: [{ colId: "modified_at", sort: "desc" }],
      defaultState: { sort: null },
    });
  }, []);

  const handleSortChanged = useCallback((e: SortChangedEvent) => {
    const sortModel = e.api.getColumnState().find((c) => c.sort);
    if (sortModel) {
      setSortField(sortModel.colId);
      setSortOrder(sortModel.sort as "asc" | "desc");
    } else {
      setSortField("name");
      setSortOrder("asc");
    }
  }, []);

  // Column filter changed → capture model into state (closure-safe), then reload
  const handleFilterChanged = useCallback(() => {
    const fm = gridRef.current?.api?.getFilterModel() ?? {};
    setColFilters(fm);          // triggers reloadGrid via useEffect([reloadGrid])
  }, []);

  useEffect(() => { reloadGrid(); }, [reloadGrid, syncVersion]);

  const exportCsv = () => gridRef.current?.api.exportDataAsCsv({ fileName: "metadata.csv" });
  const clearFilters = () => { setSearchInput(""); setSearch(""); setTypes([]); };
  const hasFilters = searchInput || selectedTypes.length > 0;
  const isFiltered = Boolean(search) || selectedTypes.length > 0 || Object.keys(colFilters).length > 0;

  const displayCount = useMemo(() => {
    if (total === null) return "Loading…";
    const filtered = total.toLocaleString();
    const label = selectedTypes.length > 0 || search ? `${filtered} results` : `${filtered} objects`;
    // Always surface what the System User filter withheld. An admin who sees
    // fewer objects here than ThoughtSpot shows them deserves the reason.
    return withHiddenSuffix(label, hiddenSystem);
  }, [total, selectedTypes, search, hiddenSystem]);

  // A blank table right after a green "Synced just now" reads as a broken sync —
  // say which of the empty-cases actually happened.
  const emptyMessage = useMemo(
    () => metadataEmptyMessage({ hiddenSystem, isFiltered }),
    [isFiltered, hiddenSystem],
  );

  // The infinite row model never shows the no-rows overlay on its own — drive it.
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
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 24, gap: 16 }}>

      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>

        {/* Search box */}
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

        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono }}>
          {displayCount}
        </span>

        <button onClick={exportCsv} style={{
          display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
          borderRadius: 6, border: `1px solid ${theme.color.border}`, background: theme.color.surface,
          fontSize: 12, fontWeight: 500, color: theme.color.textPrimary, cursor: "pointer",
          fontFamily: theme.font.sans,
        }}>
          <Download size={13} /> Export CSV
        </button>
      </div>

      {/* AG Grid — Infinite Row Model */}
      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact
          ref={gridRef}
          columnDefs={METADATA_COLUMNS}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          defaultColDef={{ resizable: true, sortable: true, sortingOrder: ["asc", "desc"] }}
          onGridReady={handleGridReady}
          onSortChanged={handleSortChanged}
          onFilterChanged={handleFilterChanged}
          rowSelection="single"
          rowStyle={{ cursor: "pointer" }}
          onRowClicked={(e: RowClickedEvent<MetadataObject>) => {
            if (e.data) setDrawerObject(e.data);
          }}
        />
      </div>

      {/* Permission drawer */}
      {activeCluster && activeOrg != null && (
        <PermissionDrawer
          object={drawerObject}
          clusterId={activeCluster.id}
          orgId={activeOrg.org_id}
          onClose={() => {
            setDrawerObject(null);
            gridRef.current?.api?.deselectAll();
          }}
        />
      )}
    </div>
  );
}
