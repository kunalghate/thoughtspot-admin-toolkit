/**
 * Relationship Visualizer — tabbed lineage explorer.
 *
 * Left: searchable object list per tab (Logical Tables / Answers / Liveboards).
 * Right: the neighborhood-scoped React Flow graph for the selected object, with
 * cross-tab jump (click a consumer → re-root + switch tab, pinned), a breadcrumb
 * of navigation history, and a fan-out drawer for collapsed consumer counts.
 *
 * Reads are SQLite-backed and cached per (guid, version) so re-selecting an
 * object is instant; the cache invalidates only after a rebuild.
 */
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GitFork, Loader2, ChevronRight, Hammer } from "lucide-react";
import { relationshipsApi, syncApi, jobsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { LineageGraphResponse, RootKind, TopologyItem, TopologyResponse } from "@/lib/types";
import { ObjectList } from "./ObjectList";
import { ConsumersDrawer } from "./ConsumersDrawer";
import { ColumnsGrid } from "./ColumnsGrid";
import { Legend } from "./Legend";
import { rootKindFor, tabForNodeType, type ExplorerTab } from "./nodeStyles";

const LineageFlow = dynamic(() => import("./LineageFlow"), {
  ssr: false,
  loading: () => <PanelSpinner label="Loading graph…" />,
});

const TABS: { id: ExplorerTab; label: string }[] = [
  { id: "logical_tables", label: "Logical Tables" },
  { id: "answers", label: "Answers" },
  { id: "liveboards", label: "Liveboards" },
];

interface Crumb { guid: string; name: string; tab: ExplorerTab; }

export function RelationshipsView({
  clusterId,
  orgId,
  syncVersion,
}: {
  clusterId: string;
  orgId: number;
  syncVersion: number;
}) {
  // Internal version = parent sync bumps + in-panel builds. Bumping invalidates
  // topology, the graph cache, and re-derives lineage-sync status.
  const [version, setVersion] = useState(syncVersion);
  useEffect(() => { setVersion((v) => Math.max(v, syncVersion)); }, [syncVersion]);

  const [topology, setTopology] = useState<TopologyResponse | null>(null);
  const [topoError, setTopoError] = useState<string | null>(null);
  const [depStatus, setDepStatus] = useState<string>("NOT_SYNCED");

  const [tab, setTab] = useState<ExplorerTab>("logical_tables");
  const [selected, setSelected] = useState<TopologyItem | null>(null);
  const [graph, setGraph] = useState<LineageGraphResponse | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);

  const [pinned, setPinned] = useState<Set<string>>(new Set());
  const [breadcrumb, setBreadcrumb] = useState<Crumb[]>([]);
  const [detailTab, setDetailTab] = useState<"flow" | "columns">("flow");
  const [drawerType, setDrawerType] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildProgress, setBuildProgress] = useState<number | null>(null);

  const graphCache = useRef<Map<string, LineageGraphResponse>>(new Map());
  const guidToItem = useMemo(() => {
    const m = new Map<string, TopologyItem>();
    if (topology) {
      [...topology.logical_tables, ...topology.answers, ...topology.liveboards].forEach((i) => m.set(i.ts_guid, i));
    }
    return m;
  }, [topology]);

  // ── Load topology + lineage-sync status ──
  useEffect(() => {
    let cancelled = false;
    graphCache.current.clear();
    Promise.all([
      relationshipsApi.topology(clusterId, orgId),
      syncApi.status(clusterId, orgId).catch(() => []),
    ])
      .then(([topo, logs]) => {
        if (cancelled) return;
        setTopology(topo);
        setTopoError(null);
        const dep = logs.find((l) => l.entity_type === "dependencies");
        setDepStatus(dep?.status ?? "NOT_SYNCED");
      })
      .catch((e) => { if (!cancelled) setTopoError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [clusterId, orgId, version]);

  // ── Load the selected object's graph (cached per guid+version) ──
  const loadGraph = useCallback(async (item: TopologyItem) => {
    const rootKind: RootKind = rootKindFor(item.node_type);
    const key = `${version}:${rootKind}:${item.ts_guid}`;
    const cached = graphCache.current.get(key);
    if (cached) { setGraph(cached); setGraphError(null); setGraphLoading(false); return; }
    setGraphLoading(true);
    setGraphError(null);
    try {
      const g = await relationshipsApi.graph(rootKind, item.ts_guid, clusterId, orgId);
      graphCache.current.set(key, g);
      setGraph(g);
    } catch (e) {
      setGraph(null);
      setGraphError(e instanceof Error ? e.message : String(e));
    } finally {
      setGraphLoading(false);
    }
  }, [clusterId, orgId, version]);

  useEffect(() => {
    if (selected) loadGraph(selected);
    else setGraph(null);
  }, [selected, loadGraph]);

  // ── Selection + cross-tab jump ──
  const selectItem = useCallback((item: TopologyItem, pushCrumb = true) => {
    setTab(tabForNodeType(item.node_type));
    setSelected(item);
    setPinned((prev) => new Set(prev).add(item.ts_guid));
    if (pushCrumb) {
      setBreadcrumb((prev) => {
        if (prev.length && prev[prev.length - 1].guid === item.ts_guid) return prev;
        return [...prev, { guid: item.ts_guid, name: item.name, tab: tabForNodeType(item.node_type) }];
      });
    }
  }, []);

  const onJump = useCallback((guid: string, _nodeType: string) => {
    const item = guidToItem.get(guid);
    if (item) selectItem(item);
  }, [guidToItem, selectItem]);

  // ── In-panel "Build lineage" (mirrors the topbar Sync, self-contained) ──
  const triggerBuild = useCallback(async () => {
    setBuilding(true);
    setBuildProgress(null);
    try {
      const { job_id } = await syncApi.trigger(clusterId, orgId, "dependencies");
      const TERMINAL = ["COMPLETE", "FAILED", "PARTIAL"];
      const poll = setInterval(async () => {
        try {
          const job = await jobsApi.get(job_id);
          setBuildProgress(job.progress);
          if (!TERMINAL.includes(job.status)) return;
          clearInterval(poll);
          setBuilding(false);
          setBuildProgress(null);
          setVersion((v) => v + 1); // invalidate caches → refetch topology + graph
        } catch {
          clearInterval(poll);
          setBuilding(false);
          setBuildProgress(null);
        }
      }, 2000);
    } catch {
      setBuilding(false);
      setBuildProgress(null);
    }
  }, [clusterId, orgId]);

  const items = topology ? topology[tab] : [];
  const lineageBuilt = depStatus === "SUCCESS";

  if (topoError) {
    return <CenteredMessage><strong>Couldn&apos;t load topology.</strong> {topoError}</CenteredMessage>;
  }
  if (!topology) {
    return <PanelSpinner label="Loading objects…" />;
  }
  const totalObjects = topology.logical_tables.length + topology.answers.length + topology.liveboards.length;
  if (totalObjects === 0) {
    return (
      <CenteredMessage>
        <GitFork size={28} style={{ color: theme.color.accent }} />
        <div style={{ marginTop: 10, fontWeight: 600, color: theme.color.textPrimary }}>No metadata cached yet</div>
        <div style={{ marginTop: 4 }}>Sync <strong>Metadata</strong> first, then build lineage here.</div>
      </CenteredMessage>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Tabs + breadcrumb */}
      <div style={{ display: "flex", alignItems: "center", borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface, flexShrink: 0 }}>
        <div style={{ display: "flex", paddingLeft: 12 }}>
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              style={{
                display: "flex", alignItems: "center", gap: 6, padding: "10px 16px",
                fontSize: 13, fontWeight: tab === t.id ? 600 : 400, fontFamily: theme.font.sans,
                cursor: "pointer", border: "none", background: "transparent",
                color: tab === t.id ? theme.color.accent2 : theme.color.textMuted,
                borderBottom: tab === t.id ? `2px solid ${theme.color.accent2}` : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              {t.label}
              <span style={{ fontSize: 11, color: theme.color.textMuted }}>{topology[t.id].length}</span>
            </button>
          ))}
        </div>
        {breadcrumb.length > 0 && (
          <div style={{ display: "flex", alignItems: "center", gap: 2, marginLeft: "auto", padding: "0 14px", overflow: "hidden" }}>
            {breadcrumb.slice(-4).map((c, i, arr) => (
              <span key={`${c.guid}-${i}`} style={{ display: "inline-flex", alignItems: "center", gap: 2, minWidth: 0 }}>
                {i > 0 && <ChevronRight size={12} style={{ color: theme.color.textMuted }} />}
                <button
                  onClick={() => { const it = guidToItem.get(c.guid); if (it) selectItem(it, false); }}
                  style={{
                    background: "transparent", border: "none", cursor: "pointer", padding: "2px 4px",
                    fontSize: 12, fontFamily: theme.font.sans, maxWidth: 130, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap",
                    color: i === arr.length - 1 ? theme.color.textPrimary : theme.color.textMuted,
                    fontWeight: i === arr.length - 1 ? 600 : 400,
                  }}
                >
                  {c.name}
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Body: list + detail */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <div style={{ width: 280, flexShrink: 0, borderRight: `1px solid ${theme.color.border}`, background: theme.color.surface, minHeight: 0 }}>
          <ObjectList
            items={items}
            selectedGuid={selected?.ts_guid ?? null}
            pinnedGuids={pinned}
            showSubtypeFilter={tab === "logical_tables"}
            onSelect={(it) => selectItem(it)}
          />
        </div>

        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
          {selected && graph && (graph.nodes.length > 1 || lineageBuilt) && !graphLoading && !graphError && (
            <div style={{ display: "flex", gap: 2, padding: "6px 12px 0", borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface, flexShrink: 0 }}>
              {(["flow", "columns"] as const).map((st) => (
                <button
                  key={st}
                  onClick={() => setDetailTab(st)}
                  style={{
                    padding: "6px 14px", fontSize: 12.5, fontWeight: detailTab === st ? 600 : 400,
                    fontFamily: theme.font.sans, cursor: "pointer", border: "none", background: "transparent",
                    color: detailTab === st ? theme.color.accent2 : theme.color.textMuted,
                    borderBottom: detailTab === st ? `2px solid ${theme.color.accent2}` : "2px solid transparent",
                    marginBottom: -1, textTransform: "capitalize",
                  }}
                >
                  {st === "flow" ? "Flow" : "Columns"}
                  {st === "columns" && graph.columns.length > 0 && (
                    <span style={{ marginLeft: 6, fontSize: 11, color: theme.color.textMuted }}>{graph.columns.length}</span>
                  )}
                </button>
              ))}
            </div>
          )}
          <div style={{ flex: 1, minHeight: 0 }}>
            {!selected ? (
              <CenteredMessage>
                <GitFork size={28} style={{ color: theme.color.accent }} />
                <div style={{ marginTop: 10 }}>Select an object to see its lineage.</div>
              </CenteredMessage>
            ) : graphLoading ? (
              <PanelSpinner label="Loading graph…" />
            ) : graphError ? (
              <CenteredMessage><strong>Couldn&apos;t load lineage.</strong> {graphError}</CenteredMessage>
            ) : graph && (graph.nodes.length > 1 || lineageBuilt) ? (
              detailTab === "flow" ? (
                <LineageFlow data={graph} onJump={onJump} onOpenDrawer={setDrawerType} />
              ) : (
                <ColumnsGrid columns={graph.columns} onJump={onJump} />
              )
            ) : (
              <BuildLineagePrompt building={building} progress={buildProgress} onBuild={triggerBuild} />
            )}
          </div>
          <Legend />
        </div>
      </div>

      {drawerType && selected && (
        <ConsumersDrawer
          rootKind={rootKindFor(selected.node_type)}
          guid={selected.ts_guid}
          consumerType={drawerType}
          clusterId={clusterId}
          orgId={orgId}
          onJump={onJump}
          onClose={() => setDrawerType(null)}
        />
      )}
    </div>
  );
}

// ── Small presentational helpers ────────────────────────────────────────────────

function PanelSpinner({ label }: { label: string }) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, color: theme.color.textMuted, fontFamily: theme.font.sans, fontSize: 13 }}>
      <Loader2 size={22} className="spin" style={{ animation: "spin 1s linear infinite" }} />
      {label}
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  );
}

function CenteredMessage({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: 32, fontSize: 14, color: theme.color.textMuted, fontFamily: theme.font.sans, maxWidth: 480, margin: "0 auto" }}>
      {children}
    </div>
  );
}

function BuildLineagePrompt({ building, progress, onBuild }: { building: boolean; progress: number | null; onBuild: () => void }) {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", textAlign: "center", padding: 32, fontFamily: theme.font.sans, maxWidth: 460, margin: "0 auto", gap: 12 }}>
      <div style={{ width: 56, height: 56, borderRadius: 14, background: theme.color.accentSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Hammer size={26} style={{ color: theme.color.accent2 }} />
      </div>
      <div style={{ fontSize: 16, fontWeight: 600, color: theme.color.textPrimary }}>No lineage built yet</div>
      <div style={{ fontSize: 13, color: theme.color.textMuted, lineHeight: 1.6 }}>
        Build the lineage graph to trace this object&apos;s dependencies. The first build sweeps the
        dependency API (seconds for the object graph) and can take a few minutes on large clusters —
        requires a <strong>Metadata</strong> sync first.
      </div>
      <button
        onClick={onBuild}
        disabled={building}
        style={{
          marginTop: 4, display: "inline-flex", alignItems: "center", gap: 8, padding: "9px 18px",
          border: "none", borderRadius: theme.radius.control, cursor: building ? "wait" : "pointer",
          background: theme.gradient.accent, color: theme.color.onAccent, fontSize: 13, fontWeight: 600,
          fontFamily: theme.font.sans, boxShadow: theme.shadow.glowAccent,
        }}
      >
        {building ? (
          <>
            <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} />
            Building{progress != null ? ` — ${progress}` : "…"}
          </>
        ) : (
          <>
            <Hammer size={14} /> Build lineage
          </>
        )}
        <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      </button>
    </div>
  );
}
