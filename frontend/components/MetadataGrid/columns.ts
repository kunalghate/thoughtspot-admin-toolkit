import type { ColDef } from "ag-grid-community";
import type { MetadataObject } from "@/lib/types";

export const METADATA_COLUMNS: ColDef<MetadataObject>[] = [
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 220,
    filter: "agTextColumnFilter",
    sort: "asc",
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 140,
    filter: "agSetColumnFilter",
    filterParams: {
      values: ["LIVEBOARD", "ANSWER", "LOGICAL_TABLE", "WORKSHEET", "TABLE"],
    },
    cellRenderer: (p: { value: string }) => TYPE_LABELS[p.value] ?? p.value,
  },
  {
    field: "owner_name",
    headerName: "Owner",
    flex: 2,
    minWidth: 160,
    filter: "agTextColumnFilter",
  },
  {
    field: "tags",
    headerName: "Tags",
    flex: 2,
    minWidth: 140,
    filter: "agTextColumnFilter",
    valueFormatter: (p) => (p.value as string[] | null)?.join(", ") ?? "",
    sortable: false,
  },
  {
    field: "last_accessed_at",
    headerName: "Last Accessed",
    width: 160,
    filter: "agDateColumnFilter",
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : formatDate(p.value),
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : (p.value ?? 0).toLocaleString(),
  },
  {
    field: "modified_at",
    headerName: "Modified",
    width: 160,
    filter: "agDateColumnFilter",
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 160,
    filter: "agDateColumnFilter",
    valueFormatter: (p) => formatDate(p.value),
  },
];

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  LOGICAL_TABLE:      "Worksheet",      // legacy cached records
  WORKSHEET:          "Worksheet",
  ONE_TO_ONE_LOGICAL: "Table",
  AGGR_WORKSHEET:     "Agg Worksheet",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "User Defined",
};

const DATA_OBJECT_TYPES = new Set(["WORKSHEET", "ONE_TO_ONE_LOGICAL", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED", "LOGICAL_TABLE"]);
const isDataObject = (type: string | undefined) => !!type && DATA_OBJECT_TYPES.has(type);

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  const days = Math.floor((Date.now() - d.getTime()) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 30)  return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}
