import type { ColDef } from "ag-grid-community";
import type { MetadataObject } from "@/lib/types";
import { formatDate } from "@/lib/utils";

export const METADATA_COLUMNS: ColDef<MetadataObject>[] = [
  {
    field: "name",
    headerName: "Name",
    flex: 3,
    minWidth: 220,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true },
  },
  {
    field: "object_type",
    headerName: "Type",
    width: 140,
    filter: "agSetColumnFilter",
    filterParams: { values: ["LIVEBOARD", "ANSWER", "WORKSHEET", "ONE_TO_ONE_LOGICAL", "AGGR_WORKSHEET", "SQL_VIEW", "USER_DEFINED"] },
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
    flex: 2,
    minWidth: 140,
    filter: "agTextColumnFilter",
    filterParams: { filterOptions: ["contains"], suppressAndOrCondition: true },
    valueFormatter: (p) => (p.value as string[] | null)?.join(", ") ?? "",
    sortable: false,
  },
  {
    field: "last_accessed_at",
    headerName: "Last Accessed",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : formatDate(p.value),
  },
  {
    field: "view_count",
    headerName: "Views",
    width: 100,
    filter: "agNumberColumnFilter",
    filterParams: { filterOptions: ["greaterThan", "lessThan", "equals"], suppressAndOrCondition: true },
    valueFormatter: (p) => isDataObject(p.data?.object_type) ? "—" : (p.value ?? 0).toLocaleString(),
  },
  {
    field: "modified_at",
    headerName: "Modified",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
    valueFormatter: (p) => formatDate(p.value),
  },
  {
    field: "created_at",
    headerName: "Created",
    width: 160,
    filter: "agDateColumnFilter",
    filterParams: { suppressAndOrCondition: true },
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

