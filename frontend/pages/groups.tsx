/**
 * Group Management page (read-only v1).
 *
 * Grid of cached groups with search, server-side sort, and member counts.
 * Row click opens a detail drawer (privileges + member users). Data comes
 * from the local cache; the topbar Sync button refreshes it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, IDatasource, IGetRowsParams, RowClickedEvent } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import GroupDetailDrawer from "@/components/Groups/GroupDetailDrawer";
import { groupsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { GroupListItem } from "@/lib/types";

const PAGE_SIZE = 200;

export default function GroupsPage() {
  // Bumped when a sync finishes so the grid reloads without a manual refresh.
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Groups" entityType="groups" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
      <GroupsContent syncVersion={syncVersion} />
    </AppShell>
  );
}

function GroupsContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("name");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");
  const [selectedGroup, setSelectedGroup] = useState<GroupListItem | null>(null);

  const gridRef = useRef<AgGridReact<GroupListItem>>(null);

  // Debounce typing → search so we don't refetch on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Server-side pagination via AG Grid's infinite model — same pattern as the
  // Users grid. Changing filters/sort rebuilds the datasource (refetch from 0).
  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id) return;
    const clusterId = activeCluster.id;
    const orgId = activeOrg?.org_id ?? undefined;
    const toolbarSearch = search.trim() || undefined;
    const sf = sortField;
    const so = sortOrder;

    const datasource: IDatasource = {
      getRows: async (params: IGetRowsParams) => {
        try {
          const res = await groupsApi.list({
            cluster_id: clusterId,
            org_id: orgId,
            search: toolbarSearch,
            sort_field: sf,
            sort_order: so,
            record_offset: params.startRow,
            page_size: PAGE_SIZE,
          });
          setTotal(res.total);
          setError(null);
          params.successCallback(res.items, res.total);
        } catch (e) {
          setError(e instanceof Error ? e.message : String(e));
          params.failCallback();
        }
      },
    };
    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, search, sortField, sortOrder]);

  // Rebuild on mount, filter/cluster/org/sort change, and after a sync finishes.
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

  const handleRowClicked = useCallback((e: RowClickedEvent<GroupListItem>) => {
    if (e.data) setSelectedGroup(e.data);
  }, []);

  const columns = useMemo<ColDef<GroupListItem>[]>(() => [
    { field: "name", headerName: "Name", flex: 1, minWidth: 160 },
    { field: "display_name", headerName: "Display name", flex: 1, minWidth: 160 },
    { field: "description", headerName: "Description", flex: 2, minWidth: 200, sortable: false },
    {
      field: "privileges",
      headerName: "Privileges",
      width: 120,
      sortable: false, // not in the server-side sort whitelist
      cellRenderer: (p: { value: string[] }) => (
        <span title={(p.value ?? []).join(", ")}>{(p.value ?? []).length}</span>
      ),
    },
    { field: "member_count", headerName: "Members", width: 120 },
    {
      field: "modified_at", headerName: "Modified", width: 140,
      valueFormatter: (p) => p.value ? new Date(p.value as string).toLocaleDateString() : "",
    },
    {
      field: "synced_at", headerName: "Synced", width: 140,
      valueFormatter: (p) => p.value ? new Date(p.value as string).toLocaleDateString() : "",
    },
  ], []);

  if (!activeCluster) {
    return <EmptyState message="Select a cluster from the topbar to start." />;
  }

  return (
    <div style={{
      display: "flex", flexDirection: "column", height: "100%",
      padding: 24, gap: 12, overflow: "hidden", minHeight: 0,
    }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <input
          type="text"
          placeholder="Search by name, display name, or description…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          style={{
            flex: 1, padding: "7px 12px", fontSize: 13, fontFamily: theme.font.sans,
            border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
            outline: "none",
          }}
        />
        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
          {total == null ? "Loading…" : `${total.toLocaleString()} group${total === 1 ? "" : "s"}`}
        </span>
      </div>

      {error && (
        <div style={{
          padding: "10px 14px", fontSize: 12, fontFamily: theme.font.sans,
          background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6,
          color: theme.color.danger,
        }}><strong>Error:</strong> {error}</div>
      )}

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, width: "100%" }}>
        <AgGridReact<GroupListItem>
          ref={gridRef}
          columnDefs={columns}
          rowModelType="infinite"
          cacheBlockSize={PAGE_SIZE}
          maxBlocksInCache={10}
          infiniteInitialRowCount={PAGE_SIZE}
          defaultColDef={{ resizable: true, sortable: true, sortingOrder: ["asc", "desc"] }}
          onGridReady={handleGridReady}
          onSortChanged={handleSortChanged}
          onRowClicked={handleRowClicked}
          overlayNoRowsTemplate="No groups. Sync groups first."
        />
      </div>

      {selectedGroup && (
        <GroupDetailDrawer
          clusterId={activeCluster.id}
          tsGuid={selectedGroup.ts_guid}
          groupName={selectedGroup.display_name || selectedGroup.name}
          onClose={() => setSelectedGroup(null)}
        />
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div style={{
      height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
      fontSize: 14, color: theme.color.textMuted, fontFamily: theme.font.sans,
    }}>{message}</div>
  );
}
