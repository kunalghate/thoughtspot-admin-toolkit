import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent, SortChangedEvent } from "ag-grid-community";
import { Download, Search, X } from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { METADATA_COLUMNS } from "@/components/MetadataGrid/columns";
import PermissionDrawer from "@/components/MetadataGrid/PermissionDrawer";
import { metadataApi } from "@/lib/api";
import type { MetadataObject } from "@/lib/types";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

const PAGE_SIZE = 200;

const OBJECT_TYPES = ["LIVEBOARD", "ANSWER", "WORKSHEET", "ONE_TO_ONE_LOGICAL", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED"];
const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  WORKSHEET:          "Worksheet",
  LOGICAL_TABLE:      "Worksheet",        // legacy cached records
  ONE_TO_ONE_LOGICAL: "Table",
  AGGR_WORKSHEET:     "Agg Worksheet",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "User Defined",
};

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

  const [total, setTotal]             = useState<number | null>(null);
  const [search, setSearch]           = useState("");
  const [selectedTypes, setTypes]     = useState<string[]>([]);
  const [searchInput, setSearchInput] = useState(""); // raw input, debounced into search
  const [drawerObject, setDrawerObject] = useState<MetadataObject | null>(null);
  const [sortField, setSortField]     = useState("modified_at");
  const [sortOrder, setSortOrder]     = useState<"asc" | "desc">("desc");

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
    const types = selectedTypes.length > 0 ? selectedTypes : undefined;
    const searchTerm = search.trim() || undefined;

    const datasource: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const res = await metadataApi.list({
            cluster_id: clusterId,
            org_id: orgId,
            types,
            search: searchTerm,
            sort_field: sortField,
            sort_order: sortOrder,
            record_offset: params.startRow,
            page_size: PAGE_SIZE,
          });
          setTotal(res.total);
          // lastRow tells AG Grid the total so it stops requesting more pages
          params.successCallback(res.items, res.total);
        } catch {
          params.failCallback();
        }
      },
    };

    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, search, selectedTypes, sortField, sortOrder]);

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

  useEffect(() => { reloadGrid(); }, [reloadGrid, syncVersion]);

  const exportCsv = () => gridRef.current?.api.exportDataAsCsv({ fileName: "metadata.csv" });
  const clearFilters = () => { setSearchInput(""); setSearch(""); setTypes([]); };
  const hasFilters = searchInput || selectedTypes.length > 0;

  const displayCount = useMemo(() => {
    if (total === null) return "Loading…";
    const filtered = total.toLocaleString();
    return selectedTypes.length > 0 || search ? `${filtered} results` : `${filtered} objects`;
  }, [total, selectedTypes, search]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 24, gap: 16 }}>

      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>

        {/* Search box */}
        <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 320 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#7A7068" }} />
          <input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Search by name…"
            style={{
              width: "100%", paddingLeft: 30, paddingRight: 10,
              height: 34, borderRadius: 6, border: "1px solid #E8E1D5",
              background: "#FAF8F4", fontSize: 13, color: "#1A1714",
              fontFamily: "Geist, sans-serif", outline: "none",
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
                  fontFamily: "Geist, sans-serif", fontWeight: active ? 500 : 400,
                  background: active ? "#EDE9FE" : "#FAF8F4",
                  border: `1px solid ${active ? "#8B5CF6" : "#E8E1D5"}`,
                  color: active ? "#6D28D9" : "#7A7068",
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
            borderRadius: 6, border: "1px solid #E8E1D5", background: "transparent",
            fontSize: 12, color: "#7A7068", cursor: "pointer", fontFamily: "Geist, sans-serif",
          }}>
            <X size={11} /> Clear
          </button>
        )}

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace" }}>
          {displayCount}
        </span>

        <button onClick={exportCsv} style={{
          display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
          borderRadius: 6, border: "1px solid #E8E1D5", background: "#FAF8F4",
          fontSize: 12, fontWeight: 500, color: "#1A1714", cursor: "pointer",
          fontFamily: "Geist, sans-serif",
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
          overlayNoRowsTemplate="No content found. Sync metadata first."
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
