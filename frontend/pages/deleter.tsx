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
import Link from "next/link";
import { AgGridReact } from "ag-grid-react";
import type { GridApi } from "ag-grid-community";
import { GitBranch, Tag, ClipboardList, History, X } from "lucide-react";
import "ag-grid-community/styles/ag-grid.css";
import "ag-grid-community/styles/ag-theme-alpine.css";

import AppShell, { useShell } from "@/components/Shell";
import { OBJECT_COLUMNS } from "@/components/Deleter/columns";
import { DryRunModal } from "@/components/Deleter/DryRunModal";
import { DownstreamPicker } from "@/components/DeleterIntake/DownstreamPicker";
import { TagPicker } from "@/components/DeleterIntake/TagPicker";
import { ListPaste } from "@/components/DeleterIntake/ListPaste";
import { deleterApi } from "@/lib/api";
import { typeLabel, typeLabelPlural } from "@/lib/objectTypes";
import { theme } from "@/lib/theme";
import type { DeleterItem, DeleterMode, DeleterResolveResponse, RootSearchItem } from "@/lib/types";

const MODES: { id: DeleterMode; label: string; icon: typeof GitBranch; tagline: string }[] = [
  { id: "downstream", label: "Downstream", icon: GitBranch,    tagline: "Delete everything that depends on a root object." },
  { id: "tag",        label: "From Tag",   icon: Tag,           tagline: "Delete everything tagged with a label." },
  { id: "list",       label: "From List",  icon: ClipboardList, tagline: "Delete a specific list of GUIDs." },
];

export default function DeleterPage() {
  // History was moved to /jobs (shared with Archiver).
  return (
    <AppShell pageTitle="Bulk Delete" entityType="metadata">
      <DeleterContent onViewHistory={() => { window.location.href = "/jobs"; }} />
    </AppShell>
  );
}

function DeleterContent({ onViewHistory }: { onViewHistory: () => void }) {
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

  function handlePickRoot(r: RootSearchItem | null, prefetched?: DeleterResolveResponse) {
    setPickedRoot(r);
    if (!r) {
      setItems([]);
      return;
    }
    if (prefetched) {
      // Drawer already resolved dependents — reuse the response to avoid a
      // duplicate API call and a visible delay before the grid populates.
      setItems(prefetched.items);
      setByType(prefetched.by_type);
      setUnrecognized([]);
      setError(null);
    } else {
      void resolveDownstream(r);
    }
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

  function handleClearSelection() {
    gridRef.current?.api?.deselectAll();
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
    return <EmptyState message="Select an instance from the topbar to start." />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* ── Mode tab strip ──────────────────────────────────────────────── */}
      <div style={{
        display: "flex", gap: 0, borderBottom: `1px solid ${theme.color.border}`,
        background: theme.color.surface, paddingLeft: 24, flexShrink: 0,
      }}>
        {MODES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => switchMode(id)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "10px 18px", fontSize: 13, fontWeight: mode === id ? 600 : 400,
              fontFamily: theme.font.sans, cursor: "pointer", border: "none",
              background: "transparent", color: mode === id ? theme.color.accent2 : theme.color.textMuted,
              borderBottom: mode === id ? `2px solid ${theme.color.accent}` : "2px solid transparent",
              marginBottom: -1,
            }}
          >
            <Icon size={14} /> {label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        {/* Deletions are restorable from TML backups — surface that here,
            where the admin who just deleted something will look first. */}
        <Link href="/jobs?tab=deleted" style={{
          alignSelf: "center", marginRight: 20, display: "inline-flex", alignItems: "center", gap: 5,
          fontSize: 12, color: theme.color.textMuted, textDecoration: "none",
          fontFamily: theme.font.sans,
        }}>
          <History size={12} /> Restore deleted items
        </Link>
      </div>

      {/* ── Body ────────────────────────────────────────────────────────── */}
      <div style={{
        display: "flex", flexDirection: "column", flex: 1,
        padding: 24, gap: 16, overflow: "hidden", minHeight: 0,
      }}>
        {/* Mode tagline */}
        <div style={{
          fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans,
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
          <>
            <TagPicker
              clusterId={activeCluster.id}
              orgId={activeOrg?.org_id ?? 0}
              pickedTag={pickedTag}
              onPick={handlePickTag}
            />
            {pickedTag && (
              <TagOnlyAction
                tagName={pickedTag}
                clusterId={activeCluster.id}
                orgId={activeOrg?.org_id ?? 0}
                onDeleted={() => handlePickTag(null)}
              />
            )}
          </>
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
            padding: "10px 14px", fontSize: 12, fontFamily: theme.font.sans,
            background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6,
            color: theme.color.danger,
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

        {/* Selection bar (shown when rows are checked) */}
        {items.length > 0 && selectedCount > 0 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 8, padding: "8px 12px",
            background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6, flexShrink: 0,
            flexWrap: "wrap",
          }}>
            <span style={{ fontSize: 13, color: theme.color.accent2, fontWeight: 500, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
              {selectedCount} of {items.length} selected
            </span>

            <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              · TML backup will be taken before each delete (restorable from History)
            </span>

            <div style={{ flex: 1 }} />

            <button
              data-testid="open-dryrun-modal"
              onClick={handleDeleteClick}
              style={{
                padding: "5px 12px", borderRadius: 6, fontSize: 12, fontWeight: 500,
                cursor: "pointer", border: `1px solid ${theme.color.dangerBorder}`, background: theme.color.dangerSoft,
                color: theme.color.danger, fontFamily: theme.font.sans,
              }}
            >
              Delete {selectedCount} item{selectedCount === 1 ? "" : "s"}…
            </button>

            <button
              onClick={handleClearSelection}
              style={{
                display: "flex", alignItems: "center", gap: 4, padding: "4px 8px",
                borderRadius: 4, border: `1px solid ${theme.color.border}`, background: "transparent",
                fontSize: 12, color: theme.color.textMuted, cursor: "pointer", fontFamily: theme.font.sans,
              }}
            >
              <X size={11} /> Clear
            </button>
          </div>
        )}

        {/* Hint (shown when nothing selected) */}
        {items.length > 0 && selectedCount === 0 && (
          <div style={{
            display: "flex", alignItems: "center", gap: 8, padding: "7px 12px",
            background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 13, color: theme.color.textMuted }}>☑</span>
            <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, lineHeight: 1.5 }}>
              Check rows to select objects — then{" "}
              <strong style={{ color: theme.color.danger }}>Delete selected</strong> for a safety-checked deletion with TML backup.
            </span>
          </div>
        )}

        {/* Grid */}
        {items.length > 0 && (
          <div className="ag-theme-alpine" style={{ flex: 1, minHeight: 0, height: "100%", width: "100%" }}>
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
            onViewHistory={onViewHistory}
          />
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
  const breakdown = Object.entries(byType)
    .map(([t, n]) => `${n} ${n > 1 ? typeLabelPlural(t) : typeLabel(t)}`)
    .join(", ");

  return (
    <div style={{
      padding: "10px 14px", fontSize: 13, fontFamily: theme.font.sans,
      background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6,
      color: theme.color.accent2,
    }}>
      {resolving && <span>Resolving…</span>}
      {!resolving && total === 0 && unrecognized.length === 0 && <span>No items matched.</span>}
      {!resolving && total > 0 && (
        <>
          <strong>Resolved {total} item{total === 1 ? "" : "s"}</strong>
          {breakdown && <span style={{ color: theme.color.textMuted, marginLeft: 8 }}>({breakdown})</span>}
        </>
      )}
      {unrecognized.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary style={{ cursor: "pointer", color: theme.color.danger }}>
            {unrecognized.length} unrecognized GUID{unrecognized.length === 1 ? "" : "s"}
          </summary>
          <pre style={{
            marginTop: 6, padding: 8, background: theme.color.surface, border: `1px solid ${theme.color.dangerBorder}`,
            borderRadius: 4, fontSize: 11, color: theme.color.textMuted, maxHeight: 120, overflowY: "auto",
          }}>{unrecognized.join("\n")}</pre>
        </details>
      )}
    </div>
  );
}

function TagOnlyAction({
  tagName, clusterId, orgId, onDeleted,
}: {
  tagName: string;
  clusterId: string;
  orgId: number;
  onDeleted: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDeleteTag() {
    setBusy(true);
    setError(null);
    try {
      const res = await deleterApi.deleteTagOnly({ cluster_id: clusterId, org_id: orgId, tag_name: tagName });
      alert(`Tag "${res.tag_name}" deleted. Removed from ${res.removed_from} cached object${res.removed_from === 1 ? "" : "s"}.`);
      setConfirmOpen(false);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
      background: theme.color.warnSoft, border: `1px dashed ${theme.color.warnBorder}`, borderRadius: 6,
      fontFamily: theme.font.sans,
    }}>
      <div style={{ flex: 1, fontSize: 12, color: theme.color.warn }}>
        <strong>Or:</strong> delete <em>just the tag</em> (objects stay; the label is removed from every object).
      </div>
      {!confirmOpen ? (
        <button
          onClick={() => setConfirmOpen(true)}
          style={{
            padding: "6px 12px", fontSize: 12, fontWeight: 500,
            background: theme.color.surface, border: `1px solid ${theme.color.warnBorder}`, borderRadius: 6,
            cursor: "pointer", color: theme.color.warn, fontFamily: theme.font.sans,
          }}
        >Delete tag only</button>
      ) : (
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => setConfirmOpen(false)}
            disabled={busy}
            style={{
              padding: "6px 10px", fontSize: 12, background: theme.color.surface,
              border: `1px solid ${theme.color.border}`, borderRadius: 6, cursor: busy ? "not-allowed" : "pointer",
              color: theme.color.textMuted, fontFamily: theme.font.sans,
            }}
          >Cancel</button>
          <button
            onClick={handleDeleteTag}
            disabled={busy}
            style={{
              padding: "6px 12px", fontSize: 12, fontWeight: 600,
              background: busy ? theme.color.warnBorder : theme.color.warn, color: theme.color.onAccent,
              border: "none", borderRadius: 6, cursor: busy ? "not-allowed" : "pointer",
              fontFamily: theme.font.sans,
            }}
          >{busy ? "Deleting…" : `Confirm delete "${tagName}"`}</button>
        </div>
      )}
      {error && (
        <span style={{ fontSize: 12, color: theme.color.danger }}>{error}</span>
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <AppShell pageTitle="Bulk Delete" entityType="metadata">
      <div style={{
        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 14, color: theme.color.textMuted, fontFamily: theme.font.sans,
      }}>{message}</div>
    </AppShell>
  );
}
