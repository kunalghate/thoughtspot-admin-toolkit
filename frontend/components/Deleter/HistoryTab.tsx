/**
 * HistoryTab — shared deletion-history grid for both Archiver and Bulk Deleter.
 *
 * Both features feed the same archive_records table, so the History view
 * is identical for both: paginated grid of deleted objects with TML-backup
 * download links and CSV export.
 *
 * Backed by GET /api/v1/archiver/records (which returns rows from any job
 * that wrote to archive_records, regardless of the originating feature).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, IDatasource, IGetRowsParams } from "ag-grid-community";
import { Download } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { useShell } from "@/components/Shell";
import { serializeFilterModel } from "@/components/Deleter/columns";
import { archiverApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { typeLabel } from "@/lib/objectTypes";
import type { ArchiveRecordFlatItem } from "@/lib/types";

const HISTORY_PAGE_SIZE = 200;

const HISTORY_COLUMNS: ColDef[] = [
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
      typeLabel(p.data?.object_type),
    cellRenderer: (p: { value: string }) => (
      <span style={{
        padding: "2px 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
        fontFamily: theme.font.sans,
        background: p.value === "LIVEBOARD" ? theme.color.accentSoft : theme.color.surface3,
        color:      p.value === "LIVEBOARD" ? theme.color.accent2  : theme.color.textSecondary,
      }}>
        {typeLabel(p.value)}
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
      <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>Admin</span>
    ),
  },
  {
    colId: "tml",
    headerName: "Backup",
    width: 100,
    sortable: false,
    filter: false,
    resizable: false,
    // cellRenderer injected at render time (needs clusterId)
  },
];

export function HistoryTab() {
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact<ArchiveRecordFlatItem>>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("archived_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const colFiltersRef = useRef<Record<string, unknown>>({});

  const clusterId = activeCluster?.id ?? "";

  const tmlRenderer = useCallback((params: { data?: ArchiveRecordFlatItem }) => {
    if (!params.data) return null;
    const { id, name, tml_export_status } = params.data;
    if (tml_export_status !== "SUCCESS") {
      return <span style={{ color: theme.color.textMuted, fontSize: 12 }}>—</span>;
    }
    return (
      <a
        href={`/api/v1/archiver/download/${id}?cluster_id=${clusterId}`}
        download
        title={`Download TML for "${name}"`}
        onClick={(e) => e.stopPropagation()}
        style={{
          display: "inline-flex", alignItems: "center", gap: 5,
          padding: "3px 9px", borderRadius: 6, fontSize: 11, fontWeight: 500,
          lineHeight: "16px",
          border: `1px solid ${theme.color.border}`, background: theme.color.surface, color: theme.color.textSecondary,
          textDecoration: "none", fontFamily: theme.font.sans,
          boxShadow: theme.shadow.xs,
          transition: "background 120ms ease, border-color 120ms ease",
        }}
      >
        <Download size={11} strokeWidth={2} />
        <span>TML</span>
      </a>
    );
  }, [clusterId]);

  const columnsWithTml = HISTORY_COLUMNS.map((col) =>
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
            search: f.search,
            types: f.types,
            owner_name_search: f.owner_name_search,
            archived_before: f.archived_before,
            archived_after: f.archived_after,
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

  const displayCount = total == null
    ? ""
    : `${total.toLocaleString()} deleted object${total !== 1 ? "s" : ""}`;

  return (
    <div style={{
      display: "flex", flexDirection: "column", flex: 1,
      padding: 24, gap: 12, overflow: "hidden",
    }}>
      {/* Toolbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono }}>
          {displayCount}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={() => gridRef.current?.api.exportDataAsCsv({ fileName: "deleted-objects.csv" })}
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
          overlayNoRowsTemplate="No deleted objects yet."
        />
      </div>
    </div>
  );
}
