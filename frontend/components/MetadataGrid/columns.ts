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

function ConnectionCell({ value, mixed }: { value: string; mixed?: boolean }) {
  if (!value) return "—";
  if (!mixed) return value;
  return React.createElement(
    "span",
    { style: { display: "inline-flex", alignItems: "center", gap: 6, minWidth: 0 } },
    React.createElement("span", { style: { overflow: "hidden", textOverflow: "ellipsis" } }, value),
    React.createElement(
      "span",
      {
        style: {
          display: "inline-block", flexShrink: 0, lineHeight: "16px",
          padding: "0 6px", borderRadius: 20, fontSize: 10, fontWeight: 600,
          background: theme.color.warnSoft, color: theme.color.warn, fontFamily: theme.font.sans,
          whiteSpace: "nowrap",
        },
      },
      "Mixed",
    ),
  );
}

export const METADATA_COLUMNS: ColDef<MetadataObject>[] = [
  // ── Checkbox (pinned) ────────────────────────────────────────────────────
  // Selection is checkbox-only (the page sets suppressRowClickSelection), so
  // clicking a row still opens the permissions drawer and cannot arm a bulk
  // action by accident.
  {
    colId: "checkbox",
    checkboxSelection: true,
    // No headerCheckboxSelection: select-all is unsupported with the infinite
    // row model (AG Grid ignores it and logs an error).
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
  // ── Where a table actually comes from ────────────────────────────────────
  // ThoughtSpot's own UI makes this genuinely hard to find, which is why it is
  // here at all. Every LOGICAL_TABLE subtype gets its connection straight from
  // the metadata sync. Liveboards and Answers sit on models rather than on a
  // connection directly, so their `connection_guid` is instead DERIVED by
  // walking the lineage graph (lineage_service.derive_content_connections) —
  // which needs a Relationships (dependencies) sync to have run at least once.
  // An object reachable from more than one connection shows one of them plus
  // a "Mixed" badge rather than silently picking one.
  //
  // Both the filter and the sort are served by a join on ts_connections
  // (metadata_service.search) — the row itself stores only the GUID, so neither
  // can be answered from the cached row alone. Filtering by an exact connection
  // is the toolbar's connection picker, which sets connection_guid.
  {
    field: "connection_name",
    headerName: "Connection",
    width: 180,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    cellRenderer: (p: { value: string; data?: MetadataObject }) =>
      ConnectionCell({ value: p.value, mixed: p.data?.connection_is_mixed }),
    // The cell shows a name or nothing — never a GUID, which reads as an
    // unreadable name rather than as "unresolved". The GUID is still on the
    // row, so hovering an unresolved cell recovers it (and says what to do).
    tooltipValueGetter: (p) => {
      if (p.value) {
        return p.data?.connection_is_mixed
          ? `${p.value} — and at least one other connection (this object's lineage spans more than one)`
          : (p.value as string);
      }
      if (isDataObject(p.data?.object_type)) {
        const guid = p.data?.connection_guid;
        if (!guid) return "No connection recorded — re-sync metadata to fill this in";
        // A metadata sync refreshes the connection cache too, so a GUID still
        // unresolved after one is a source /connection/search does not return
        // (Analyst Studio, CSV uploads) rather than a cache that is simply old.
        return `Connection ${guid} is not one ThoughtSpot lists — its source has no connection entry`;
      }
      return "No connection resolved — run a Relationships (dependencies) sync to derive this from its lineage";
    },
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
  {
    field: "db_name",
    headerName: "Database",
    width: 150,
    filter: false,
    hide: true,
    valueFormatter: (p) => p.value || "—",
  },
  {
    field: "db_schema",
    headerName: "Schema",
    width: 140,
    filter: false,
    hide: true,
    valueFormatter: (p) => p.value || "—",
  },
  {
    field: "db_table",
    headerName: "External table",
    width: 180,
    filter: false,
    hide: true,
    valueFormatter: (p) => p.value || "—",
  },
];


const DATA_OBJECT_TYPES = new Set(["WORKSHEET", "ONE_TO_ONE_LOGICAL", "DATASET", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED", "LOGICAL_TABLE"]);
const isDataObject = (type: string | undefined) => !!type && DATA_OBJECT_TYPES.has(type);

