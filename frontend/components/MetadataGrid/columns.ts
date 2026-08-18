import type { ColDef } from "ag-grid-community";
import React from "react";
import type { MetadataObject } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { theme } from "@/lib/theme";
import { TYPE_LABELS } from "@/lib/objectTypes";

// ── Type chip styling (shared with Archiver) ─────────────────────────────
const TYPE_COLORS: Record<string, { bg: string; fg: string }> = {
  LIVEBOARD:          { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // purple
  ANSWER:             { bg: theme.color.surface3, fg: theme.color.textSecondary }, // neutral gray
  WORKSHEET:          { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // blue
  LOGICAL_TABLE:      { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // blue (legacy)
  ONE_TO_ONE_LOGICAL: { bg: theme.color.accentSoft, fg: theme.color.accent2 }, // cyan (Table)
  AGGR_WORKSHEET:     { bg: theme.color.successSoft, fg: theme.color.success }, // green
  SQL_VIEW:           { bg: theme.color.warnSoft, fg: theme.color.warn }, // orange
  USER_DEFINED:       { bg: theme.color.surface3, fg: theme.color.textSecondary }, // neutral gray
};

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

export const METADATA_COLUMNS: ColDef<MetadataObject>[] = [
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 220,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 140,
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
    flex: 2,
    minWidth: 140,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => (p.value as string[] | null)?.join(", ") ?? "",
    sortable: false,
  },
  {
    field: "last_accessed_at",
    headerName: "Last Accessed",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : formatDate(p.value),
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan", "equals"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : (p.value ?? 0).toLocaleString(),
  },
  {
    field: "modified_at",
    headerName: "Modified",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => formatDate(p.value),
  },
];


const DATA_OBJECT_TYPES = new Set(["WORKSHEET", "ONE_TO_ONE_LOGICAL", "DATASET", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED", "LOGICAL_TABLE"]);
const isDataObject = (type: string | undefined) => !!type && DATA_OBJECT_TYPES.has(type);

