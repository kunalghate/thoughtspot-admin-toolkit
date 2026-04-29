/**
 * Bulk Deleter page.
 *
 * Three "intake modes" produce a list of CachedMetadata rows; the user
 * deselects any rows they want to keep; then "Delete N items" funnels
 * into the (Phase 4) DryRunModal that actually runs the TML-backup-then-
 * delete pipeline.
 *
 * Intake modes:
 *   - downstream: pick a root → resolve dependents
 *   - tag:        pick a tag → resolve tagged items
 *   - list:       paste/upload GUIDs → resolve cache hits + report unrecognized
 *
 * Phase 3 wires intake + grid + selection. Phase 4 hooks the modal.
 */
import { useEffect, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { GridApi } from "ag-grid-community";
import { GitBranch, Tag, ClipboardList } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { OBJECT_COLUMNS } from "@/components/Deleter/columns";
import { DryRunModal } from "@/components/Deleter/DryRunModal";
import { DownstreamPicker } from "@/components/DeleterIntake/DownstreamPicker";
import { TagPicker } from "@/components/DeleterIntake/TagPicker";
import { ListPaste } from "@/components/DeleterIntake/ListPaste";
import { deleterApi } from "@/lib/api";
import type { DeleterItem, DeleterMode, RootSearchItem } from "@/lib/types";

const MODES: { id: DeleterMode; label: string; icon: typeof GitBranch; tagline: string }[] = [
  { id: "downstream", label: "Downstream", icon: GitBranch,    tagline: "Delete everything that depends on a root object." },
  { id: "tag",        label: "From Tag",   icon: Tag,           tagline: "Delete everything tagged with a label." },
  { id: "list",       label: "From List",  icon: ClipboardList, tagline: "Delete a specific list of GUIDs." },
];

export default function DeleterPage() {
  return (
    <AppShell pageTitle="Bulk Delete" entityType="metadata">
      <DeleterContent />
    </AppShell>
  );
}

function DeleterContent() {
  const { activeCluster, activeOrg } = useShell();
  const [mode, setMode] = useState<DeleterMode>("downstream");

  // Per-mode intake state (kept across mode switches; cleared on cluster/org change)
  const [pickedRoot, setPickedRoot] = useState<RootSearchItem | null>(null);
  const [pickedTag, setPickedTag] = useState<string | null>(null);

  // Resolved set + UI state
  const [items, setItems] = useState<DeleterItem[]>([]);
  const [unrecognized, setUnrecognized] = useState<string[]>([]);
  const [resolving, setResolving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [byType, setByType] = useState<Record<string, number>>({});

  // Selection state — driven by AG Grid checkboxes
  const gridRef = useRef<AgGridReact<DeleterItem>>(null);
  const [selectedCount, setSelectedCount] = useState(0);

  // Dry-run modal state
  const [dryRunOpen, setDryRunOpen] = useState(false);
  const [pendingDeleteGuids, setPendingDeleteGuids] = useState<string[]>([]);

  // Reset everything if cluster/org changes
  useEffect(() => {
    setPickedRoot(null);
    setPickedTag(null);
    setItems([]);
    setUnrecognized([]);
    setError(null);
    setByType({});
  }, [activeCluster?.id, activeOrg?.org_id]);

  // Reset intake state when switching modes (but keep per-mode picks alive)
  function switchMode(next: DeleterMode) {
    setMode(next);
    setError(null);
  }

  // ── Resolve handlers ─────────────────────────────────────────────────────

  async function resolveDownstream(root: RootSearchItem) {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    setResolving(true);
    setError(null);
    try {
      const res = await deleterApi.resolveDownstream({
        cluster_id: activeCluster.id,
        org_id: activeOrg.org_id,
        root_guid: root.ts_guid,
        root_type: root.object_type,
      });
      setItems(res.items);
      setByType(res.by_type);
      setUnrecognized([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setResolving(false);
    }
  }

  async function resolveTag(tagName: string) {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    setResolving(true);
    setError(null);
    try {
      const res = await deleterApi.resolveTag({
        cluster_id: activeCluster.id,
        org_id: activeOrg.org_id,
        tag_name: tagName,
      });
      setItems(res.items);
      setByType(res.by_type);
      setUnrecognized([]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setResolving(false);
    }
  }

  async function resolveList(guids: string[]) {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    setResolving(true);
    setError(null);
    try {
      const res = await deleterApi.resolveList({
        cluster_id: activeCluster.id,
        org_id: activeOrg.org_id,
        guids,
      });
      setItems(res.items);
      setByType(res.by_type);
      setUnrecognized(res.unrecognized ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setItems([]);
    } finally {
      setResolving(false);
    }
  }

  // ── Pick handlers fan out to resolve fns ─────────────────────────────────

  function handlePickRoot(r: RootSearchItem | null) {
    setPickedRoot(r);
    if (r) void resolveDownstream(r);
    else setItems([]);
  }

  function handlePickTag(t: string | null) {
    setPickedTag(t);
    if (t) void resolveTag(t);
    else setItems([]);
  }

  function clearList() {
    setItems([]);
    setUnrecognized([]);
  }

  // ── Grid wiring ──────────────────────────────────────────────────────────

  const columns = OBJECT_COLUMNS;

  function handleGridReady(params: { api: GridApi<DeleterItem> }) {
    // Pre-select all rows so the user starts in "delete all" mode and deselects exceptions.
    setTimeout(() => params.api.selectAll(), 0);
  }

  function handleSelectionChanged() {
    const api = gridRef.current?.api;
    setSelectedCount(api ? api.getSelectedRows().length : 0);
  }

  // Pre-select after each new resolve
  useEffect(() => {
    if (items.length > 0) {
      setTimeout(() => gridRef.current?.api?.selectAll(), 0);
    } else {
      setSelectedCount(0);
    }
  }, [items]);

  function handleDeleteClick() {
    const selected = gridRef.current?.api?.getSelectedRows() ?? [];
    if (selected.length === 0) return;
    setPendingDeleteGuids(selected.map((r) => r.ts_guid));
    setDryRunOpen(true);
  }

  function handleDryRunClose(reloadNeeded: boolean) {
    setDryRunOpen(false);
    setPendingDeleteGuids([]);
    if (reloadNeeded) {
      // Items were deleted — drop them from the grid.
      const deletedSet = new Set(pendingDeleteGuids);
      setItems((prev) => prev.filter((i) => !deletedSet.has(i.ts_guid)));
    }
  }

  // ── Render ───────────────────────────────────────────────────────────────

  if (!activeCluster) {
    return <EmptyState message="Select a cluster from the topbar to start." />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* ── Mode tab strip ──────────────────────────────────────────────── */}
      <div style={{
        display: "flex", gap: 0, borderBottom: "1px solid #E8E1D5",
        background: "#FAF8F4", paddingLeft: 24, flexShrink: 0,
      }}>
        {MODES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => switchMode(id)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 18px", fontSize: 13, fontWeight: mode === id ? 600 : 400,
              fontFamily: "Geist, sans-serif", cursor: "pointer", border: "none",
              background: "transparent", color: mode === id ? "#6D28D9" : "#7A7068",
              borderBottom: mode === id ? "2px solid #8B5CF6" : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", flexDirection: "column", flex: 1,
        padding: 24, gap: 16, overflow: "hidden", minHeight: 0,
      }}>
        {/* Mode tagline */}
        <div style={{
          fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif",
        }}>
          {MODES.find((m) => m.id === mode)?.tagline}
        </div>

        {/* Per-mode intake widget */}
        {mode === "downstream" && (
          <DownstreamPicker
            clusterId={activeCluster.id}
            orgId={activeOrg?.org_id ?? 0}
            pickedRoot={pickedRoot}
            onPick={handlePickRoot}
          />
        )}
        {mode === "tag" && (
          <TagPicker
            clusterId={activeCluster.id}
            orgId={activeOrg?.org_id ?? 0}
            pickedTag={pickedTag}
            onPick={handlePickTag}
          />
        )}
        {mode === "list" && (
          <ListPaste
            onSubmit={resolveList}
            onClear={clearList}
            hasResolved={items.length > 0 || unrecognized.length > 0}
          />
        )}

        {/* Resolve summary banner */}
        {error && (
          <div style={{
            padding: "10px 14px", fontSize: 12, fontFamily: "Geist, sans-serif",
            background: "#FEF2F2", border: "1px solid #FCA5A5", borderRadius: 6,
            color: "#991B1B",
          }}>
            <strong>Error:</strong> {error}
          </div>
        )}
        {!error && (resolving || items.length > 0 || unrecognized.length > 0) && (
          <ResolveSummary
            resolving={resolving}
            total={items.length}
            byType={byType}
            unrecognized={unrecognized}
          />
        )}

        {/* Grid */}
        {items.length > 0 && (
          <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, width: "100%" }}>
            <AgGridReact<DeleterItem>
              ref={gridRef}
              rowData={items}
              columnDefs={columns}
              defaultColDef={{ resizable: true, sortable: true }}
              rowSelection="multiple"
              suppressRowClickSelection
              onGridReady={handleGridReady}
              onSelectionChanged={handleSelectionChanged}
              overlayNoRowsTemplate="No items resolved."
            />
          </div>
        )}

        {/* DryRun modal */}
        {dryRunOpen && activeOrg != null && (
          <DryRunModal
            api={{
              dryrun: deleterApi.dryrun,
              dryrunObjects: deleterApi.dryrunObjects,
              execute: deleterApi.execute,
            }}
            objectIds={pendingDeleteGuids}
            clusterId={activeCluster.id}
            orgId={activeOrg.org_id}
            onClose={handleDryRunClose}
            onViewHistory={() => { window.location.href = "/archiver?tab=history"; }}
          />
        )}

        {/* Action bar */}
        {items.length > 0 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
            background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 6,
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 13, color: "#0F0E0D", fontFamily: "Geist, sans-serif" }}>
              <strong>{selectedCount}</strong> of {items.length} selected
            </span>
            <span style={{ fontSize: 12, color: "#7A7068" }}>
              · TML backup will be taken before each delete (restorable from History)
            </span>
            <button
              disabled={selectedCount === 0}
              onClick={handleDeleteClick}
              style={{
                marginLeft: "auto", padding: "8px 16px", fontSize: 13, fontWeight: 600,
                background: selectedCount === 0 ? "#E8E1D5" : "#991B1B",
                color: selectedCount === 0 ? "#9CA3AF" : "white",
                border: "none", borderRadius: 6,
                cursor: selectedCount === 0 ? "not-allowed" : "pointer",
                fontFamily: "Geist, sans-serif",
              }}
            >
              Delete {selectedCount} item{selectedCount === 1 ? "" : "s"}…
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function ResolveSummary({
  resolving, total, byType, unrecognized,
}: {
  resolving: boolean;
  total: number;
  byType: Record<string, number>;
  unrecognized: string[];
}) {
  const TYPE_LABELS: Record<string, string> = {
    LIVEBOARD: "Liveboard", ANSWER: "Answer",
    WORKSHEET: "Worksheet", TABLE: "Table", MODEL: "Model", VIEW: "View",
  };
  const breakdown = Object.entries(byType)
    .map(([t, n]) => `${n} ${TYPE_LABELS[t] ?? t}${n > 1 ? "s" : ""}`)
    .join(", ");

  return (
    <div style={{
      padding: "10px 14px", fontSize: 13, fontFamily: "Geist, sans-serif",
      background: "#F5F0FF", border: "1px solid #DDD6FE", borderRadius: 6,
      color: "#5B21B6",
    }}>
      {resolving && <span>Resolving…</span>}
      {!resolving && total === 0 && unrecognized.length === 0 && <span>No items matched.</span>}
      {!resolving && total > 0 && (
        <>
          <strong>Resolved {total} item{total === 1 ? "" : "s"}</strong>
          {breakdown && <span style={{ color: "#7A7068", marginLeft: 8 }}>({breakdown})</span>}
        </>
      )}
      {unrecognized.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ cursor: "pointer", color: "#991B1B" }}>
            {unrecognized.length} unrecognized GUID{unrecognized.length === 1 ? "" : "s"}
          </summary>
          <pre style={{
            marginTop: 6, padding: 8, background: "white", border: "1px solid #FCA5A5",
            borderRadius: 4, fontSize: 11, color: "#7A7068", maxHeight: 120, overflowY: "auto",
          }}>{unrecognized.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <AppShell pageTitle="Bulk Delete" entityType="metadata">
      <div style={{
        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 14, color: "#7A7068", fontFamily: "Geist, sans-serif",
      }}>{message}</div>
    </AppShell>
  );
}
