/**
 * Shared AG Grid column defs for the metadata-object grids used by both
 * the Archiver and the Bulk Deleter pages, and by the DryRunModal.
 *
 * Typed as `ColDef[]` (i.e. ColDef<any>) so it accepts both ArchiverItem
 * (LIVEBOARD | ANSWER, narrower object_type) and DeleterItem (wider
 * object_type) without per-page casts.
 */
import type { ColDef } from "ag-grid-community";
import { formatDate } from "@/lib/utils";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard",
  ANSWER:    "Answer",
  WORKSHEET: "Worksheet",
  TABLE:     "Table",
  MODEL:     "Model",
  VIEW:      "View",
};

export const OBJECT_COLUMNS: ColDef[] = [
  // ── Checkbox (pinned) ────────────────────────────────────────────────────
  {
    colId: "checkbox",
    checkboxSelection: true,
    headerCheckboxSelection: true,
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
    cellRenderer: (p: { value: string }) => TYPE_LABELS[p.value] ?? p.value,
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
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "days_unused",
    headerName: "Days Unused",
    width: 150,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => p.value != null ? p.value.toLocaleString() : "—",
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true, buttons: ["reset", "apply"], closeOnApply: true },
    valueFormatter: (p) => (p.value ?? 0).toLocaleString(),
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
