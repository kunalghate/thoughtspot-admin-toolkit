/**
 * Shared AG Grid column defs for the metadata-object grids used by both
 * the Archiver and the Bulk Deleter pages, and by the DryRunModal.
 *
 * Typed as `ColDef[]` (i.e. ColDef<any>) so it accepts both ArchiverItem
 * (LIVEBOARD | ANSWER, narrower object_type) and DeleterItem (wider
 * object_type) without per-page casts.
 */
import type { ColDef } from "ag-grid-community";
import React from "react";
import { formatDate } from "@/lib/utils";
import { theme } from "@/lib/theme";

// Type chip styling — mirrors /metadata. Covers both vocabularies that flow
// through these grids: the metadata cache vocab (ONE_TO_ONE_LOGICAL, AGGR_*,
// etc.) used by the Downstream picker, and the deleter-resolution vocab
// (TABLE/MODEL/VIEW) emitted by the deletion-resolve services.
const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  WORKSHEET:          "Worksheet",
  LOGICAL_TABLE:      "Worksheet",      // legacy cached records
  ONE_TO_ONE_LOGICAL: "Table",
  TABLE:              "Table",
  MODEL:              "Model",
  VIEW:               "View",
  AGGR_WORKSHEET:     "Agg Worksheet",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "User Defined",
};

const TYPE_COLORS: Record<string, { bg: string; fg: string }> = {
  LIVEBOARD:          { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // purple
  ANSWER:             { bg: theme.color.surface3, fg: theme.color.textSecondary }, // neutral gray
  WORKSHEET:          { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // blue
  LOGICAL_TABLE:      { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // blue (legacy)
  ONE_TO_ONE_LOGICAL: { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // cyan (Table)
  TABLE:              { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // cyan
  MODEL:              { bg: theme.color.successSoft, fg: theme.color.success }, // green
  VIEW:               { bg: theme.color.warnSoft, fg: theme.color.warn }, // orange
  AGGR_WORKSHEET:     { bg: theme.color.successSoft, fg: theme.color.success }, // green
  SQL_VIEW:           { bg: theme.color.warnSoft, fg: theme.color.warn }, // orange
  USER_DEFINED:       { bg: theme.color.surface3, fg: theme.color.textSecondary }, // neutral gray
};

// Data objects (Worksheets, Tables, Models, etc.) don't have meaningful
// "last accessed" or "view count" — those stats only apply to consumed
// objects like Liveboards/Answers. Mirrors /metadata's formatter rule.
const DATA_OBJECT_TYPES = new Set([
  "WORKSHEET", "LOGICAL_TABLE", "ONE_TO_ONE_LOGICAL", "TABLE",
  "MODEL", "VIEW",
  "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED",
]);
const isDataObject = (type: string | undefined) => !!type && DATA_OBJECT_TYPES.has(type);

function TypeChip({ value }: { value: string }) {
  const colors = TYPE_COLORS[value] ?? TYPE_COLORS.USER_DEFINED;
  return React.createElement(
    "span",
    {
      style: {
        display: "inline-block", lineHeight: "18px",
        padding: "0 10px", borderRadius: 20, fontSize: 11, fontWeight: 500,
        background: colors.bg, color: colors.fg, fontFamily: theme.font.sans,
        whiteSpace: "nowrap",
      },
    },
    TYPE_LABELS[value] ?? value,
  );
}

export const OBJECT_COLUMNS: ColDef[] = [
  // ── Checkbox (pinned) ────────────────────────────────────────────────────
  {
    colId: "checkbox",
    checkboxSelection: true,
    // No headerCheckboxSelection: select-all is unsupported with the infinite
    // row model (AG Grid ignores it and logs an error) — rows are checked
    // individually, as the instruction bar above each grid says.
    width: 40,
    sortable: false,
    resizable: false,
    pinned: "left",
    suppressSizeToFit: true,
  },
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 200,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 130,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    filterValueGetter: (p) => TYPE_LABELS[p.data?.object_type as string] ?? p.data?.object_type,
    cellRenderer: (p: { value: string }) => TypeChip({ value: p.value }),
  },
  {
    field: "owner_name",
    headerName: "Owner",
    flex: 2,
    minWidth: 160,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
  },
  {
    field: "tags",
    headerName: "Tags",
    flex: 1,
    minWidth: 100,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => (p.value as string[] | null)?.join(", ") ?? "",
    sortable: false,
  },
  {
    field: "last_accessed_at",
    headerName: "Last Accessed",
    width: 150,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : formatDate(p.value),
  },
  {
    field: "days_unused",
    headerName: "Days Unused",
    width: 150,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) =>
      isDataObject(p.data?.object_type) ? "—" : (p.value != null ? p.value.toLocaleString() : "—"),
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : (p.value ?? 0).toLocaleString(),
  },
  {
    field: "modified_at",
    headerName: "Modified",
    width: 130,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 130,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => formatDate(p.value),
  },
];

export { serializeFilterModel } from "@/lib/agGridFilters";
export type { SerializedFilters } from "@/lib/agGridFilters";
