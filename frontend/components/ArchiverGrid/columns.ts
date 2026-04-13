import type { ColDef } from "ag-grid-community";
import type { ArchiverItem } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard",
  ANSWER:    "Answer",
};

export const ARCHIVER_COLUMNS: ColDef<ArchiverItem>[] = [
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
  // ── Matches Metadata Explorer column layout exactly ───────────────────────
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 200,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true },
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 130,
    filter: "agSetColumnFilter",
    filterParams: { values: ["LIVEBOARD", "ANSWER"] },
    cellRenderer: (p: { value: string }) => TYPE_LABELS[p.value] ?? p.value,
  },
  {
    field: "owner_name",
    headerName: "Owner",
    flex: 2,
    minWidth: 160,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true },
  },
  {
    field: "tags",
    headerName: "Tags",
    flex: 1,
    minWidth: 100,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true },
    valueFormatter: (p) => (p.value as string[] | null)?.join(", ") ?? "",
    sortable: false,
  },
  {
    field: "last_accessed_at",
    headerName: "Last Accessed",
    width: 150,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
    valueFormatter: (p) => formatDate(p.value),
  },
  // ── Archiver-specific ─────────────────────────────────────────────────────
  {
    field: "days_unused",
    headerName: "Days Unused",
    width: 150,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true },
    valueFormatter: (p) => p.value != null ? p.value.toLocaleString() : "—",
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan"], suppressAndOrCondition: true },
    valueFormatter: (p) => (p.value ?? 0).toLocaleString(),
  },
  {
    field: "modified_at",
    headerName: "Modified",
    width: 130,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 130,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
    valueFormatter: (p) => formatDate(p.value),
  },
];
