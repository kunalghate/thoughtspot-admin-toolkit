/**
 * Downstream mode intake — browse the synced metadata cache as an AG Grid,
 * filter with a quick-search box, click a row to pick that object as the
 * deletion root. Reuses the shared metadata sync (no extra fetch path):
 * data comes from `metadataApi.list()` over `CachedMetadata`.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { AgGridReact } from "ag-grid-react";
import type { GridApi } from "ag-grid-community";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import { OBJECT_COLUMNS } from "@/components/Deleter/columns";
import DependentsDrawer from "@/components/DeleterIntake/DependentsDrawer";
import { metadataApi } from "@/lib/api";
import type { DeleterResolveResponse, MetadataObject, RootSearchItem } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD:          "Liveboard",
  ANSWER:             "Answer",
  WORKSHEET:          "Worksheet",
  ONE_TO_ONE_LOGICAL: "Table",
  AGGR_WORKSHEET:     "Agg Worksheet",
  SQL_VIEW:           "SQL View",
  USER_DEFINED:       "User Defined",
};

// Chip filters — mirrors /metadata's OBJECT_TYPES exactly so the picker
// surface is consistent with the rest of the app. Picking a leaf type
// (Liveboard/Answer) as a deletion root resolves to no dependents, which
// the preview drawer reports honestly.
const TYPE_CHIPS: { label: string; types: string[] }[] = [
  { label: "Liveboard",     types: ["LIVEBOARD"] },
  { label: "Answer",        types: ["ANSWER"] },
  { label: "Worksheet",     types: ["WORKSHEET"] },
  { label: "Table",         types: ["ONE_TO_ONE_LOGICAL"] },
  { label: "Agg Worksheet", types: ["AGGR_WORKSHEET"] },
  { label: "SQL View",      types: ["SQL_VIEW"] },
  { label: "User Defined",  types: ["USER_DEFINED"] },
];

const PICKER_TYPES = TYPE_CHIPS.flatMap((c) => c.types);

interface Props {
  clusterId: string;
  orgId: number;
  pickedRoot: RootSearchItem | null;
  onPick: (root: RootSearchItem | null, prefetched?: DeleterResolveResponse) => void;
}

export function DownstreamPicker({ clusterId, orgId, pickedRoot, onPick }: Props) {
  const [rows, setRows] = useState<MetadataObject[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [selectedChips, setSelectedChips] = useState<string[]>([]);
  const [previewObject, setPreviewObject] = useState<RootSearchItem | null>(null);
  const gridRef = useRef<AgGridReact<MetadataObject>>(null);

  // Drop the leading checkbox column from the shared defs — the picker is single-click.
  const columns = useMemo(() => OBJECT_COLUMNS.slice(1), []);

  // Effective types passed to the server: chip-selected types if any, else
  // all picker-supported types. Server-side filtering keeps `total` accurate
  // and avoids the page_size=1000 ceiling clipping a chip-narrowed view.
  const effectiveTypes = useMemo(
    () =>
      selectedChips.length === 0
        ? PICKER_TYPES
        : TYPE_CHIPS.filter((c) => selectedChips.includes(c.label)).flatMap((c) => c.types),
    [selectedChips],
  );
  const effectiveTypesKey = effectiveTypes.join(",");

  useEffect(() => {
    if (pickedRoot) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    metadataApi
      .list({ cluster_id: clusterId, org_id: orgId, types: effectiveTypes, page_size: 1000 })
      .then((res) => {
        if (cancelled) return;
        setRows(res.items);
        setTotal(res.total);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setRows([]);
        setTotal(0);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [clusterId, orgId, pickedRoot, effectiveTypesKey]);

  useEffect(() => {
    gridRef.current?.api?.setGridOption("quickFilterText", filter);
  }, [filter]);

  if (pickedRoot) {
    return (
      <div style={{
        display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
        background: "#F5F0FF", border: "1px solid #C4B5FD", borderRadius: 6,
      }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "#5B21B6" }}>
          Root: {pickedRoot.name}
        </span>
        <span style={{ fontSize: 12, color: "#7A7068" }}>
          ({TYPE_LABELS[pickedRoot.object_type] ?? pickedRoot.object_type} · {pickedRoot.owner_name})
        </span>
        <button
          onClick={() => onPick(null)}
          style={{
            marginLeft: "auto", padding: "4px 10px", fontSize: 12,
            background: "transparent", border: "1px solid #C4B5FD", borderRadius: 4,
            color: "#6D28D9", cursor: "pointer", fontFamily: "Geist, sans-serif",
          }}
        >Change root</button>
      </div>
    );
  }

  function handleRowClicked(e: { data?: MetadataObject }) {
    const r = e.data;
    if (!r) return;
    setPreviewObject({
      ts_guid: r.ts_guid,
      name: r.name,
      object_type: r.object_type,
      owner_name: r.owner_name,
    });
  }

  function handleGridReady(params: { api: GridApi<MetadataObject> }) {
    if (filter) params.api.setGridOption("quickFilterText", filter);
  }

  const showCount = !loading && !error;
  const emptyOverlay = total === 0
    ? "No synced objects found. Sync metadata first if the cache is empty."
    : "No rows match the filter.";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, flex: 1, minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 480 }}>
          <Search size={14} style={{
            position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)",
            color: "#9CA3AF", pointerEvents: "none",
          }} />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter synced objects…"
            style={{
              width: "100%", padding: "9px 12px 9px 34px", fontSize: 13,
              border: "1px solid #E8E1D5", borderRadius: 6,
              fontFamily: "Geist, sans-serif", outline: "none", background: "white",
            }}
          />
          {filter && (
            <button
              onClick={() => setFilter("")}
              style={{
                position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
                background: "transparent", border: "none", color: "#9CA3AF", cursor: "pointer",
                padding: 4,
              }}
            ><X size={14} /></button>
          )}
        </div>
        {/* Type chip filters — same pattern as /metadata */}
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {TYPE_CHIPS.map(({ label }) => {
            const active = selectedChips.includes(label);
            return (
              <button
                key={label}
                onClick={() =>
                  setSelectedChips((prev) =>
                    active ? prev.filter((x) => x !== label) : [...prev, label],
                  )
                }
                style={{
                  padding: "4px 10px", borderRadius: 20, fontSize: 12, cursor: "pointer",
                  fontFamily: "Geist, sans-serif", fontWeight: active ? 500 : 400,
                  background: active ? "#EDE9FE" : "#FAF8F4",
                  border: `1px solid ${active ? "#8B5CF6" : "#E8E1D5"}`,
                  color: active ? "#6D28D9" : "#7A7068",
                }}
              >
                {label}
              </button>
            );
          })}
          {selectedChips.length > 0 && (
            <button
              onClick={() => setSelectedChips([])}
              style={{
                display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
                borderRadius: 6, border: "1px solid #E8E1D5", background: "transparent",
                fontSize: 12, color: "#7A7068", cursor: "pointer", fontFamily: "Geist, sans-serif",
              }}
            >
              <X size={11} /> Clear
            </button>
          )}
        </div>

        <div style={{ flex: 1 }} />

        <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace", whiteSpace: "nowrap" }}>
          {loading && "Loading synced metadata…"}
          {error && <span style={{ color: "#991B1B" }}>Error: {error}</span>}
          {showCount && (
            <>
              <strong style={{ color: "#0F0E0D" }}>{total.toLocaleString()}</strong>
              {selectedChips.length > 0 ? " matching object" : " synced object"}{total === 1 ? "" : "s"}
            </>
          )}
        </span>
      </div>

      <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
        <AgGridReact<MetadataObject>
          ref={gridRef}
          rowData={rows}
          columnDefs={columns}
          defaultColDef={{ resizable: true, sortable: true }}
          rowSelection="single"
          suppressRowClickSelection={false}
          onRowClicked={handleRowClicked}
          onGridReady={handleGridReady}
          overlayNoRowsTemplate={`<span style="font-family: Geist, sans-serif; font-size: 12px; color: #7A7068;">${emptyOverlay}</span>`}
        />
      </div>

      <DependentsDrawer
        object={previewObject}
        clusterId={clusterId}
        orgId={orgId}
        onClose={() => {
          setPreviewObject(null);
          gridRef.current?.api?.deselectAll();
        }}
        onCommit={(root, prefetched) => {
          setPreviewObject(null);
          onPick(root, prefetched);
        }}
      />
    </div>
  );
}
