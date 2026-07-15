/**
 * The 3-layer column map as an AG Grid (client-side row model — one object's
 * columns are bounded, so no infinite model). Columns:
 *   DB Table | DB Column | Table Column | Model Column | Used by
 * "Used by" renders clickable chips that jump to the consuming answer/liveboard.
 */
import { useMemo } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";
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
        return (
          <button
            key={u.guid}
            onClick={() => params.context.onJump(u.guid, u.node_type)}
            title={`Jump to ${u.name}`}
            style={{
              display: "inline-flex", alignItems: "center", gap: 4, maxWidth: 180,
              padding: "1px 8px", borderRadius: theme.radius.pill, cursor: "pointer",
              border: `1px solid ${s.border}`, background: s.soft, color: s.color,
              fontSize: 11, fontFamily: theme.font.sans, lineHeight: 1.6,
            }}
          >
            <span style={{ width: 6, height: 6, borderRadius: 2, background: s.color, flexShrink: 0 }} />
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</span>
          </button>
        );
      })}
    </span>
  );
}

const dash = (p: { value: unknown }) => (p.value ? String(p.value) : "—");

export function ColumnsGrid({
  columns,
  onJump,
}: {
  columns: ColumnLineageRow[];
  onJump: (guid: string, nodeType: string) => void;
}) {
  const colDefs = useMemo<ColDef<ColumnLineageRow>[]>(() => [
    { field: "db_table", headerName: "DB Table", flex: 1, minWidth: 120, valueFormatter: dash },
    { field: "db_column_name", headerName: "DB Column", flex: 1, minWidth: 120, valueFormatter: dash },
    { field: "table_column_name", headerName: "Table Column", flex: 1, minWidth: 120, valueFormatter: dash },
    { field: "model_column_name", headerName: "Model Column", flex: 1, minWidth: 130 },
    {
      field: "used_by", headerName: "Used by", flex: 2, minWidth: 200,
      cellRenderer: UsedByChips, autoHeight: true, sortable: false,
    },
  ], []);

  if (columns.length === 0) {
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center", padding: 24, textAlign: "center", fontSize: 13, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
        No column-level lineage for this object yet. Build lineage (the column map exports TML for
        logical tables &amp; liveboards) to populate the 3-layer map.
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
