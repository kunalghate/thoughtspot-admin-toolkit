/**
 * The 3-layer column map as an AG Grid (client-side row model — one object's
 * columns are bounded, so no infinite model). Columns:
 *   Connection | DB Table | DB Column | Table Column | Model Column | Used by
 * "Used by" renders clickable chips that jump to the consuming answer/liveboard.
 */
import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
import { Columns3 } from "lucide-react";
import { theme } from "@/lib/theme";
import type { ColumnLineageRow, ColumnUsedBy } from "@/lib/types";
import { styleFor } from "./nodeStyles";

function UsedByChips(params: { value: ColumnUsedBy[]; context: { onJump: (guid: string, nodeType: string) => void } }) {
  const list = params.value ?? [];
  if (list.length === 0) return <span style={{ color: theme.color.textMuted }}>—</span>;
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4, alignItems: "center" }}>
      {list.map((u) => {
        const s = styleFor(u.node_type);
        const UIcon = s.Icon;
        return (
          <button
            key={u.guid}
            onClick={() => params.context.onJump(u.guid, u.node_type)}
            title={`Open ${u.name}`}
            style={{
              display: "inline-flex", alignItems: "center", gap: 5, maxWidth: 180,
              padding: "1px 8px", borderRadius: theme.radius.pill, cursor: "pointer",
              border: `1px solid ${s.border}`, background: s.soft, color: s.color,
              fontSize: 11, fontFamily: theme.font.sans, lineHeight: 1.6,
            }}
          >
            <UIcon size={11} style={{ flexShrink: 0 }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</span>
          </button>
        );
      })}
    </span>
  );
}

const dash = (p: { value: unknown }) => (p.value ? String(p.value) : "—");

/** Table Column cell: formula columns get a labelled pill instead of a bare dash. */
function TableColumnCell(params: { value: string; data?: ColumnLineageRow }) {
  if (params.data?.is_formula) {
    return (
      <span
        title="Computed column — defined by a formula in the model, so it has no source table or DB column"
        style={{
          display: "inline-flex", alignItems: "center", gap: 5, padding: "1px 8px",
          borderRadius: theme.radius.pill, border: `1px solid ${theme.color.border}`,
          background: theme.color.surface2, color: theme.color.textSecondary,
          fontSize: 11, fontFamily: theme.font.sans, lineHeight: 1.6,
        }}
      >
        <span style={{ fontFamily: theme.font.mono, fontStyle: "italic" }}>ƒ</span>
        Formula
      </span>
    );
  }
  return <span>{params.value || "—"}</span>;
}

export function ColumnsGrid({
  columns,
  onJump,
}: {
  columns: ColumnLineageRow[];
  onJump: (guid: string, nodeType: string) => void;
}) {
  const colDefs = useMemo<ColDef<ColumnLineageRow>[]>(() => [
    { field: "connection_name", headerName: "Connection", flex: 1, minWidth: 130, valueFormatter: dash },
    { field: "db_table", headerName: "DB Table", flex: 1, minWidth: 120, valueFormatter: dash },
    { field: "db_column_name", headerName: "DB Column", flex: 1, minWidth: 120, valueFormatter: dash },
    { field: "table_column_name", headerName: "Table Column", flex: 1, minWidth: 120, cellRenderer: TableColumnCell },
    { field: "model_column_name", headerName: "Model Column", flex: 1, minWidth: 130 },
    {
      field: "used_by", headerName: "Used by", flex: 2, minWidth: 200,
      cellRenderer: UsedByChips, autoHeight: true, sortable: false,
    },
  ], []);

  if (columns.length === 0) {
    return (
      <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, padding: 24, textAlign: "center", fontFamily: theme.font.sans, maxWidth: 420, margin: "0 auto" }}>
        <div style={{ width: 48, height: 48, borderRadius: 12, background: theme.color.surface2, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Columns3 size={22} style={{ color: theme.color.textMuted }} />
        </div>
        <div style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary }}>No column map yet</div>
        <div style={{ fontSize: 12.5, color: theme.color.textMuted, lineHeight: 1.6 }}>
          The column-level map (database column → table column → model column, and where each is used)
          fills in when lineage is built for logical tables and liveboards.
        </div>
      </div>
    );
  }

  return (
    <div className="ag-theme-alpine" style={{ height: "100%", width: "100%" }}>
      <AgGridReact<ColumnLineageRow>
        rowData={columns}
        columnDefs={colDefs}
        context={{ onJump }}
        defaultColDef={{ resizable: true, sortable: true }}
        suppressCellFocus
        headerHeight={38}
      />
    </div>
  );
}
