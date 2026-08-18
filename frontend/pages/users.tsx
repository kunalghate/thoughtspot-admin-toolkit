/**
 * User Management page.
 *
 * Left: user grid (filtered by search + status).
 * Action bar (when rows selected): three offboarding actions:
 *   - Transfer ownership   (single user only)
 *   - Transfer sharing     (single user only)
 *   - Delete user(s)       (one or many)
 * "History" tab shows past UserActionRecords.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { CellClickedEvent, ColDef } from "ag-grid-community";
import { Users, History, ShieldCheck, Trash2, ArrowRightLeft } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { TransferOwnershipModal } from "@/components/Users/TransferOwnershipModal";
import { TransferSharingModal } from "@/components/Users/TransferSharingModal";
import { DeleteUsersModal } from "@/components/Users/DeleteUsersModal";
import { UsersHistoryTab } from "@/components/Users/HistoryTab";
import UserDetailDrawer from "@/components/Users/UserDetailDrawer";
import { usersApi } from "@/lib/api";
import { createGuardedDatasource } from "@/lib/gridDatasource";
import { theme } from "@/lib/theme";
import type { UserListItem } from "@/lib/types";

const PAGE_SIZE = 200;

type Tab = "users" | "history";
type ActionKind = "transfer" | "transfer_sharing" | "delete" | null;

export default function UsersPage() {
  // Bumped when a sync finishes so the grid reloads without a manual refresh.
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Users" entityType="users" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
      <UsersContent syncVersion={syncVersion} />
    </AppShell>
  );
}

function UsersContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();
  const [tab, setTab] = useState<Tab>("users");

  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"" | "ACTIVE" | "INACTIVE">("");
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [sortField, setSortField] = useState("username");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("asc");

  const gridRef = useRef<AgGridReact<UserListItem>>(null);
  // Generation counter for the active datasource — see the same guard on the
  // Groups page. Drops responses from a superseded datasource so a slow
  // org-less first fetch can't overwrite `total` with the cluster-wide count.
  const loadIdRef = useRef(0);
  const [selected, setSelected] = useState<UserListItem[]>([]);
  const [action, setAction] = useState<ActionKind>(null);
  const [detailUser, setDetailUser] = useState<UserListItem | null>(null);

  // Debounce typing → search so we don't refetch on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(t);
  }, [searchInput]);

  // Server-side pagination: AG Grid's infinite model fetches one page per scroll
  // block, so the grid is never truncated to a fixed cap. Filters/sort live in
  // toolbar state; changing them rebuilds the datasource (refetch from row 0).
  const reloadGrid = useCallback(() => {
    if (!activeCluster?.id) return;
    const clusterId = activeCluster.id;
    const orgId = activeOrg?.org_id ?? undefined;
    const toolbarSearch = search.trim() || undefined;
    const toolbarStatus = status || undefined;
    const sf = sortField;
    const so = sortOrder;

    const datasource = createGuardedDatasource({
      loadIdRef,
      fetchPage: (startRow) => usersApi.list({
        cluster_id: clusterId,
        org_id: orgId,
        search: toolbarSearch,
        status: toolbarStatus,
        sort_field: sf,
        sort_order: so,
        record_offset: startRow,
        page_size: PAGE_SIZE,
      }),
      onLoaded: (res) => { setTotal(res.total); setError(null); },
      onError: (e) => setError(e instanceof Error ? e.message : String(e)),
    });
    // New result set → drop any stale selection so an action can't target a row
    // that has scrolled out of the filtered view.
    gridRef.current?.api?.deselectAll();
    gridRef.current?.api?.setGridOption("datasource", datasource);
  }, [activeCluster?.id, activeOrg?.org_id, search, status, sortField, sortOrder]);

  // Rebuild on mount, filter/cluster/org/sort change, and after a sync finishes.
  useEffect(() => { reloadGrid(); }, [reloadGrid, syncVersion]);

  const handleGridReady = useCallback(() => { reloadGrid(); }, [reloadGrid]);

  // Re-fetch the current view after a write (transfer/delete) lands.
  const refresh = useCallback(() => {
    gridRef.current?.api?.deselectAll();
    gridRef.current?.api?.purgeInfiniteCache();
  }, []);

  const handleSortChanged = useCallback(() => {
    const cols = gridRef.current?.api.getColumnState() ?? [];
    const sorted = cols.find((c) => c.sort);
    if (sorted?.colId) {
      setSortField(sorted.colId);
      setSortOrder(sorted.sort as "asc" | "desc");
    }
  }, []);

  const columns = useMemo<ColDef<UserListItem>[]>(() => [
    // Checkbox is the only way to select — row clicks are suppressed so a
    // stray click can't arm the destructive action bar. Same pattern as the
    // archiver/sharing/deleter grids (see Deleter/columns.ts).
    {
      colId: "checkbox",
      checkboxSelection: true,
      width: 40,
      sortable: false,
      resizable: false,
      pinned: "left",
      suppressSizeToFit: true,
    },
    { field: "username", headerName: "Username", flex: 1, minWidth: 160 },
    { field: "display_name", headerName: "Display name", flex: 1, minWidth: 160 },
    { field: "email", headerName: "Email", flex: 2, minWidth: 220 },
    {
      field: "status",
      headerName: "Status",
      width: 110,
      cellRenderer: (p: { value: string }) => (
        <span style={{
          padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 600,
          background: p.value === "ACTIVE" ? theme.color.successSoft : theme.color.dangerSoft,
          color: p.value === "ACTIVE" ? theme.color.success : theme.color.danger,
        }}>{p.value}</span>
      ),
    },
    {
      field: "created_at", headerName: "Created", width: 140,
      valueFormatter: (p) => p.value ? new Date(p.value as string).toLocaleDateString() : "",
    },
  ], []);

  function handleSelectionChanged() {
    setSelected(gridRef.current?.api?.getSelectedRows() ?? []);
  }

  // Row click (outside the checkbox column) opens the read-only audit drawer.
  // Selection stays checkbox-only, so this can't arm the destructive action bar.
  const handleCellClicked = useCallback((e: CellClickedEvent<UserListItem>) => {
    if (e.colDef.colId === "checkbox" || !e.data) return;
    setDetailUser(e.data);
  }, []);

  if (!activeCluster) {
    return <EmptyState message="Select an instance from the topbar to start." />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{
        display: "flex", borderBottom: `1px solid ${theme.color.border}`,
        background: theme.color.surface, paddingLeft: 24, flexShrink: 0,
      }}>
        {[
          { id: "users" as Tab, label: "Users", icon: Users },
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
              borderBottom: tab === id ? `2px solid ${theme.color.accent2}` : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {tab === "history" ? (
        <UsersHistoryTab clusterId={activeCluster.id} orgId={activeOrg?.org_id} />
      ) : (
        <div style={{
          display: "flex", flexDirection: "column", flex: 1,
          padding: 24, gap: 12, overflow: "hidden", minHeight: 0,
        }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
            <input
              type="text"
              placeholder="Search by username, display name, or email…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              style={{
                flex: 1, padding: "7px 12px", fontSize: 13, fontFamily: theme.font.sans,
                border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
                color: theme.color.textPrimary, outline: "none",
              }}
            />
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as "" | "ACTIVE" | "INACTIVE")}
              style={{
                padding: "7px 10px", fontSize: 13, fontFamily: theme.font.sans,
                border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
                color: theme.color.textPrimary, cursor: "pointer",
              }}
            >
              <option value="">All statuses</option>
              <option value="ACTIVE">Active only</option>
              <option value="INACTIVE">Inactive only</option>
            </select>
            <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              {total == null ? "Loading…" : `${total.toLocaleString()} user${total === 1 ? "" : "s"}`}
            </span>
          </div>

          {error && (
            <div style={{
              padding: "10px 14px", fontSize: 12, fontFamily: theme.font.sans,
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6,
              color: theme.color.danger,
            }}><strong>Error:</strong> {error}</div>
          )}

          {selected.length > 0 && (
            <div style={{
              display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
              background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6,
              flexShrink: 0, flexWrap: "wrap",
            }}>
              <span style={{
                fontSize: 13, color: theme.color.accent2, fontWeight: 500,
                fontFamily: theme.font.sans, whiteSpace: "nowrap",
              }}>{selected.length} selected</span>

              <div style={{ flex: 1 }} />

              <ActionButton
                disabled={selected.length !== 1}
                title={selected.length === 1 ? "" : "Transfer ownership works on one user at a time"}
                onClick={() => setAction("transfer")}
                icon={<ArrowRightLeft size={12} />}
                label="Transfer ownership…"
              />
              <ActionButton
                disabled={selected.length !== 1}
                title={selected.length === 1 ? "" : "Transfer sharing works on one user at a time"}
                onClick={() => setAction("transfer_sharing")}
                icon={<ShieldCheck size={12} />}
                label="Transfer sharing…"
              />
              <ActionButton
                onClick={() => setAction("delete")}
                variant="danger"
                icon={<Trash2 size={12} />}
                label={`Delete ${selected.length} user${selected.length === 1 ? "" : "s"}…`}
              />
            </div>
          )}

          <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, width: "100%" }}>
            <AgGridReact<UserListItem>
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
              onSelectionChanged={handleSelectionChanged}
              onCellClicked={handleCellClicked}
              overlayNoRowsTemplate="No users. Sync users first."
            />
          </div>

          {detailUser && (
            <UserDetailDrawer
              clusterId={activeCluster.id}
              orgId={activeOrg?.org_id ?? 0}
              user={detailUser}
              onClose={() => setDetailUser(null)}
            />
          )}

          {action === "transfer" && selected.length === 1 && activeOrg != null && (
            <TransferOwnershipModal
              clusterId={activeCluster.id}
              orgId={activeOrg.org_id}
              fromUser={selected[0]}
              onClose={(reloadNeeded) => {
                setAction(null);
                if (reloadNeeded) refresh();
              }}
            />
          )}
          {action === "transfer_sharing" && selected.length === 1 && activeOrg != null && (
            <TransferSharingModal
              clusterId={activeCluster.id}
              orgId={activeOrg.org_id}
              fromUser={selected[0]}
              onClose={() => setAction(null)}
            />
          )}
          {action === "delete" && activeOrg != null && (
            <DeleteUsersModal
              clusterId={activeCluster.id}
              orgId={activeOrg.org_id}
              users={selected}
              onClose={(reloadNeeded) => {
                setAction(null);
                if (reloadNeeded) {
                  setSelected([]);
                  refresh();
                }
              }}
            />
          )}
        </div>
      )}
    </div>
  );
}

function ActionButton({
  onClick, disabled, title, icon, label, variant,
}: {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  icon: React.ReactNode;
  label: string;
  variant?: "danger";
}) {
  const isDanger = variant === "danger";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "5px 12px", borderRadius: 6, fontSize: 12, fontWeight: 500,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        border: `1px solid ${isDanger ? theme.color.dangerBorder : theme.color.violetBorder}`,
        background: isDanger ? theme.color.dangerSoft : theme.color.surface,
        color: isDanger ? theme.color.danger : theme.color.accent2,
        fontFamily: theme.font.sans,
      }}
    >
      {icon} {label}
    </button>
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
