import { useCallback, useEffect, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { Download, Search, X } from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { METADATA_COLUMNS } from "@/components/MetadataGrid/columns";
import { metadataApi } from "@/lib/api";
import type { MetadataObject } from "@/lib/types";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

const OBJECT_TYPES = ["LIVEBOARD", "ANSWER", "LOGICAL_TABLE", "WORKSHEET", "TABLE"];
const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard", ANSWER: "Answer",
  LOGICAL_TABLE: "Worksheet", WORKSHEET: "Worksheet", TABLE: "Table",
};

export default function MetadataPage() {
  const { activeCluster, activeOrg } = useShell();
  const gridRef = useRef<AgGridReact<MetadataObject>>(null);
  const [rows, setRows]           = useState<MetadataObject[]>([]);
  const [total, setTotal]         = useState(0);
  const [loading, setLoading]     = useState(false);
  const [search, setSearch]       = useState("");
  const [selectedTypes, setTypes] = useState<string[]>([]);
  const [staleDays, setStaleDays] = useState<number | undefined>();
  const [selected, setSelected]   = useState<MetadataObject[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await metadataApi.list({
        cluster_id: activeCluster?.id ?? "",
        org_id: activeOrg?.org_id ?? 0,
        search: search || undefined,
        types: selectedTypes.length ? selectedTypes : undefined,
        stale_days: staleDays,
        page_size: 2000,
      });
      setRows(res.items);
      setTotal(res.total);
    } catch {
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, [activeCluster?.id, activeOrg?.org_id, search, selectedTypes, staleDays]);

  useEffect(() => {
    if (activeCluster && activeOrg) load();
  }, [load, activeCluster?.id, activeOrg?.org_id]);

  const exportCsv = () => gridRef.current?.api.exportDataAsCsv({ fileName: "metadata.csv" });

  const clearFilters = () => { setSearch(""); setTypes([]); setStaleDays(undefined); };
  const hasFilters = search || selectedTypes.length > 0 || staleDays != null;

  return (
    <AppShell pageTitle="Metadata" entityType="metadata">
      <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 24, gap: 16 }}>

        {/* Toolbar */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>

          {/* Search box */}
          <div style={{ position: "relative", flex: "1 1 240px", maxWidth: 320 }}>
            <Search size={13} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "#7A7068" }} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name…"
              style={{
                width: "100%", paddingLeft: 30, paddingRight: 10,
                height: 34, borderRadius: 6, border: "1px solid #E8E1D5",
                background: "#FAF8F4", fontSize: 13, color: "#1A1714",
                fontFamily: "Geist, sans-serif", outline: "none",
              }}
            />
          </div>

          {/* Type filter pills */}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {OBJECT_TYPES.map((t) => {
              const active = selectedTypes.includes(t);
              return (
                <button key={t} onClick={() => setTypes(active ? selectedTypes.filter((x) => x !== t) : [...selectedTypes, t])}
                  style={{
                    padding: "4px 10px", borderRadius: 20, fontSize: 12, cursor: "pointer",
                    fontFamily: "Geist, sans-serif", fontWeight: active ? 500 : 400,
                    background: active ? "#EDE9FE" : "#FAF8F4",
                    border: `1px solid ${active ? "#8B5CF6" : "#E8E1D5"}`,
                    color: active ? "#6D28D9" : "#7A7068",
                  }}
                >
                  {TYPE_LABELS[t]}
                </button>
              );
            })}
          </div>

          {/* Stale filter */}
          <select
            value={staleDays ?? ""}
            onChange={(e) => setStaleDays(e.target.value ? Number(e.target.value) : undefined)}
            style={{
              height: 34, padding: "0 10px", borderRadius: 6, border: "1px solid #E8E1D5",
              background: "#FAF8F4", fontSize: 13, color: staleDays ? "#6D28D9" : "#7A7068",
              fontFamily: "Geist, sans-serif", cursor: "pointer",
            }}
          >
            <option value="">All activity</option>
            <option value="30">Unused 30+ days</option>
            <option value="60">Unused 60+ days</option>
            <option value="90">Unused 90+ days</option>
            <option value="180">Unused 180+ days</option>
          </select>

          {/* Clear filters */}
          {hasFilters && (
            <button onClick={clearFilters} style={{
              display: "flex", alignItems: "center", gap: 4, padding: "4px 10px",
              borderRadius: 6, border: "1px solid #E8E1D5", background: "transparent",
              fontSize: 12, color: "#7A7068", cursor: "pointer", fontFamily: "Geist, sans-serif",
            }}>
              <X size={11} /> Clear
            </button>
          )}

          <div style={{ flex: 1 }} />

          {/* Row count */}
          <span style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist Mono, monospace" }}>
            {loading ? "Loading…" : `${rows.length.toLocaleString()} of ${total.toLocaleString()}`}
          </span>

          {/* Export CSV */}
          <button onClick={exportCsv} style={{
            display: "flex", alignItems: "center", gap: 5, padding: "6px 12px",
            borderRadius: 6, border: "1px solid #E8E1D5", background: "#FAF8F4",
            fontSize: 12, fontWeight: 500, color: "#1A1714", cursor: "pointer",
            fontFamily: "Geist, sans-serif",
          }}>
            <Download size={13} /> Export CSV
          </button>
        </div>

        {/* AG Grid */}
        <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
          <AgGridReact<MetadataObject>
            ref={gridRef}
            rowData={rows}
            columnDefs={METADATA_COLUMNS}
            rowSelection="multiple"
            onSelectionChanged={() => setSelected(gridRef.current?.api.getSelectedRows() ?? [])}
            overlayNoRowsTemplate={loading ? "Loading…" : "No content found. Sync metadata first."}
            defaultColDef={{ resizable: true, sortable: true }}
          />
        </div>

        {/* Bulk action bar — appears on row selection */}
        {selected.length > 0 && (
          <div style={{
            position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
            background: "#1A1714", color: "white", borderRadius: 10,
            padding: "10px 20px", display: "flex", alignItems: "center", gap: 16,
            boxShadow: "0 8px 24px rgba(0,0,0,0.2)", fontFamily: "Geist, sans-serif",
            zIndex: 100,
          }}>
            <span style={{ fontSize: 13, fontWeight: 500 }}>
              {selected.length} selected
            </span>
            <div style={{ width: 1, height: 16, background: "#3A3633" }} />
            <button style={{ fontSize: 13, color: "#C4B5FD", background: "none", border: "none", cursor: "pointer" }}>
              Tag
            </button>
            <button style={{ fontSize: 13, color: "#C4B5FD", background: "none", border: "none", cursor: "pointer" }}>
              Share
            </button>
            <button style={{ fontSize: 13, color: "#FCA5A5", background: "none", border: "none", cursor: "pointer" }}>
              Archive
            </button>
            <button onClick={() => gridRef.current?.api.deselectAll()}
              style={{ fontSize: 12, color: "#7A7068", background: "none", border: "none", cursor: "pointer" }}>
              <X size={14} />
            </button>
          </div>
        )}
      </div>
    </AppShell>
  );
}
