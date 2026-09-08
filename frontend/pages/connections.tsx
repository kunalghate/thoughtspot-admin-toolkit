/**
 * Data Connections page.
 *
 * The two questions ThoughtSpot's own UI makes hard, answered in one grid:
 *   - what does this connection point at, and how much sits on it?
 *   - which connections have nothing on them at all?
 *
 * Client-side row model on purpose. A cluster has hundreds of connections
 * (measured: 1,307 on se-demo), not hundreds of thousands, and "which of these
 * is empty" is a question about the whole list — it cannot be answered a page
 * at a time. So the endpoint returns everything and the grid sorts and filters
 * locally, which also makes the empty-connection filter instant.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, RowClickedEvent } from "ag-grid-community";
import { useRouter } from "next/router";
import { Search, X } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { connectionsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { formatDay } from "@/lib/utils";
import type { DataConnection } from "@/lib/types";

/** SNOWFLAKE → Snowflake, GOOGLE_BIGQUERY → Google BigQuery. */
const WAREHOUSE_LABELS: Record<string, string> = {
  SNOWFLAKE: "Snowflake",
  DATABRICKS: "Databricks",
  GOOGLE_BIGQUERY: "Google BigQuery",
  AMAZON_REDSHIFT: "Amazon Redshift",
  AZURE_SYNAPSE: "Azure Synapse",
  SQLSERVER: "SQL Server",
  POSTGRES: "Postgres",
  GCP_POSTGRESQL: "GCP PostgreSQL",
  STARBURST: "Starburst",
  TERADATA: "Teradata",
  ORACLE_ADW: "Oracle ADW",
  MODE: "Mode",
  FALCON: "Falcon",
};

const warehouseLabel = (t: string) =>
  WAREHOUSE_LABELS[t] ?? (t ? t.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()) : "—");

export default function ConnectionsPage() {
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Connections" entityType="connections" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
      <ConnectionsContent syncVersion={syncVersion} />
    </AppShell>
  );
}

function ConnectionsContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();
  const router = useRouter();
  const gridRef = useRef<AgGridReact<DataConnection>>(null);

  const [rows, setRows] = useState<DataConnection[] | null>(null);
  const [linkedRows, setLinkedRows] = useState(0);
  const [metadataRows, setMetadataRows] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [emptyOnly, setEmptyOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput), 250);
    return () => clearTimeout(t);
  }, [searchInput]);

  const load = useCallback(async () => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    setError(null);
    try {
      const res = await connectionsApi.list({
        cluster_id: activeCluster.id,
        org_id: activeOrg.org_id,
        search: search.trim() || undefined,
      });
      setRows(res.items);
      setLinkedRows(res.linked_rows);
      setMetadataRows(res.metadata_rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setRows([]);
    }
  }, [activeCluster?.id, activeOrg?.org_id, search]);

  useEffect(() => { void load(); }, [load, syncVersion]);

  const visible = useMemo(
    () => (emptyOnly ? (rows ?? []).filter((r) => r.object_count === 0) : (rows ?? [])),
    [rows, emptyOnly],
  );

  const columns = useMemo<ColDef<DataConnection>[]>(() => [
    { field: "name", headerName: "Name", flex: 2, minWidth: 200 },
    {
      field: "data_warehouse_type",
      headerName: "Type",
      width: 160,
      valueFormatter: (p) => warehouseLabel(p.value as string),
    },
    { field: "description", headerName: "Description", flex: 2, minWidth: 180, valueFormatter: (p) => p.value || "—" },
    {
      // The number the admin came here for. Zero is called out rather than
      // rendered as a quiet "0" among hundreds of rows.
      field: "object_count",
      headerName: "Objects",
      width: 130,
      cellRenderer: (p: { value: number }) =>
        p.value === 0 ? (
          <span style={{
            padding: "1px 8px", borderRadius: 10, fontSize: 10.5, fontWeight: 600,
            background: theme.color.warnSoft ?? theme.color.surface3, color: theme.color.warn,
            fontFamily: theme.font.sans,
          }}>
            none
          </span>
        ) : (
          <span style={{ fontFamily: theme.font.mono }}>{p.value.toLocaleString()}</span>
        ),
    },
    {
      headerName: "Tables",
      width: 110,
      valueGetter: (p) => p.data?.counts_by_type?.ONE_TO_ONE_LOGICAL ?? 0,
      valueFormatter: (p) => (p.value as number).toLocaleString(),
    },
    {
      field: "synced_at",
      headerName: "Synced",
      width: 130,
      valueFormatter: (p) => formatDay(p.value as string),
    },
  ], []);

  if (!activeCluster) return <EmptyState message="Select an instance from the topbar to start." />;

  const emptyCount = (rows ?? []).filter((r) => r.object_count === 0).length;
  // Connections are cached, but the counts come from the METADATA cache. A
  // cluster that has not re-synced metadata since the linkage columns landed
  // would show every connection as unused — say so instead of lying.
  const countsUnavailable = rows !== null && rows.length > 0 && linkedRows === 0 && metadataRows > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 24, gap: 12, overflow: "hidden", minHeight: 0 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
        <div style={{ position: "relative", flex: 1 }}>
          <Search size={13} style={{ position: "absolute", left: 10, top: 9, color: theme.color.textMuted }} />
          <input
            type="text"
            placeholder="Search by name, description, or warehouse type…"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            style={{
              width: "100%", padding: "7px 12px 7px 30px", fontSize: 13, fontFamily: theme.font.sans,
              border: `1px solid ${theme.color.border}`, borderRadius: 6, background: theme.color.surface,
              color: theme.color.textPrimary, outline: "none",
            }}
          />
        </div>

        <button
          data-testid="empty-only"
          onClick={() => setEmptyOnly((v) => !v)}
          title="Connections with no cached objects — candidates for cleanup"
          style={{
            display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
            borderRadius: 6, border: `1px solid ${emptyOnly ? theme.color.accent : theme.color.border}`,
            background: emptyOnly ? theme.color.accentSoft : theme.color.surface,
            fontSize: 12, fontWeight: 500, cursor: "pointer", fontFamily: theme.font.sans,
            color: emptyOnly ? theme.color.accent2 : theme.color.textPrimary, whiteSpace: "nowrap",
          }}
        >
          {emptyOnly && <X size={11} />} No objects{rows ? ` (${emptyCount.toLocaleString()})` : ""}
        </button>

        <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
          {rows == null ? "Loading…" : `${visible.length.toLocaleString()} connection${visible.length === 1 ? "" : "s"}`}
        </span>
      </div>

      {countsUnavailable && (
        <div style={{
          padding: "10px 14px", fontSize: 12, fontFamily: theme.font.sans, borderRadius: 6,
          background: theme.color.warnSoft ?? theme.color.surface, border: `1px solid ${theme.color.border}`,
          color: theme.color.textSecondary,
        }}>
          Object counts need a <strong>metadata sync</strong> — this cache predates the connection linkage,
          so every connection currently reads as having none.
        </div>
      )}

      {error && (
        <div style={{
          padding: "10px 14px", fontSize: 12, fontFamily: theme.font.sans, borderRadius: 6,
          background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, color: theme.color.danger,
        }}><strong>Error:</strong> {error}</div>
      )}

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, width: "100%" }}>
        <AgGridReact<DataConnection>
          ref={gridRef}
          rowData={visible}
          columnDefs={columns}
          defaultColDef={{ resizable: true, sortable: true, sortingOrder: ["asc", "desc"] }}
          rowStyle={{ cursor: "pointer" }}
          // A connection is only interesting via its content, so the row click
          // goes where that content already lives rather than duplicating a
          // filtered object list here.
          onRowClicked={(e: RowClickedEvent<DataConnection>) => {
            if (e.data) router.push(`/metadata?connection_guid=${encodeURIComponent(e.data.ts_guid)}`);
          }}
          overlayNoRowsTemplate={
            emptyOnly
              ? "Every connection has objects on it."
              : "No connections cached — run a Connections sync from the topbar."
          }
        />
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "center", height: "100%",
      fontSize: 13, color: theme.color.textMuted, fontFamily: theme.font.sans,
    }}>
      {message}
    </div>
  );
}
