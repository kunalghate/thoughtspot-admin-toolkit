/**
 * Lineage explorer (the "Relationships" component/route name is historical —
 * the API entity is `dependencies`, the UI calls all of it Lineage).
 *
 * Left "Browse" panel: a labeled segmented control (Logical Tables / Answers /
 * Liveboards) scoping a searchable object list with a "Recently viewed" section.
 * Right detail panel: a persistent object header (name · type · owner · impact),
 * a "Path" trail of graph navigation (list click = new path, node jump = append,
 * crumb click = backtrack), and Lineage graph / Column map sub-tabs. Collapsed
 * consumer counts open a fan-out drawer.
 *
 * Reads are SQLite-backed and cached per (guid, version) so re-selecting an
 * object is instant; the cache invalidates only after a rebuild.
 */
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  GitFork, Loader2, ChevronRight, Hammer, History, X, AlertTriangle,
  Table2, BarChart3, LayoutDashboard, Plug,
} from "lucide-react";
import { relationshipsApi, syncApi, jobsApi } from "@/lib/api";
import { useToast } from "../Toast";
import { theme } from "@/lib/theme";
import type { LineageGraphResponse, RootKind, TopologyItem, TopologyResponse } from "@/lib/types";
import { ObjectList } from "./ObjectList";
import { ConsumersDrawer } from "./ConsumersDrawer";
import { ColumnsGrid } from "./ColumnsGrid";
import { Legend } from "./Legend";
import { rootKindFor, styleFor, tabForNodeType, type ExplorerTab } from "./nodeStyles";

const LineageFlow = dynamic(() => import("./LineageFlow"), {
  ssr: false,
  loading: () => <PanelSpinner label="Loading graph…" />,
});

const TABS: { id: ExplorerTab; label: string; icon: typeof Table2 }[] = [
  { id: "logical_tables", label: "Logical Tables", icon: Table2 },
  { id: "answers", label: "Answers", icon: BarChart3 },
  { id: "liveboards", label: "Liveboards", icon: LayoutDashboard },
  { id: "connections", label: "Connections", icon: Plug },
];

const MAX_RECENT = 8;
const MAX_TRAIL_SHOWN = 5;

interface Crumb { guid: string; name: string; }

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

  const [recent, setRecent] = useState<string[]>([]);
  const [trail, setTrail] = useState<Crumb[]>([]);
  const [detailTab, setDetailTab] = useState<"flow" | "columns">("flow");
  const [drawerType, setDrawerType] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildProgress, setBuildProgress] = useState<number | null>(null);

  const toast = useToast();
  const graphCache = useRef<Map<string, LineageGraphResponse>>(new Map());
  const indexedAnswers = useRef<Set<string>>(new Set());
  const guidToItem = useMemo(() => {
    const m = new Map<string, TopologyItem>();
    if (topology) {
      [...topology.logical_tables, ...topology.answers, ...topology.liveboards, ...topology.connections].forEach(
        (i) => m.set(i.ts_guid, i),
      );
    }
    return m;
  }, [topology]);

  // ── Load topology + lineage-sync status ──
  useEffect(() => {
    let cancelled = false;
    graphCache.current.clear();
    indexedAnswers.current.clear();
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
      // Lazily index a saved answer's column usage on first open (memoized
      // server-side; 1 TML export). Best-effort — the graph still renders if it fails.
      if (rootKind === "answer" && !indexedAnswers.current.has(`${version}:${item.ts_guid}`)) {
        indexedAnswers.current.add(`${version}:${item.ts_guid}`);
        try { await relationshipsApi.indexAnswer(item.ts_guid, clusterId, orgId); } catch { /* keep object-level view */ }
      }
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

  // ── Selection: list click starts a new path; graph jump appends to it ──
  const applySelection = useCallback((item: TopologyItem) => {
    setTab(tabForNodeType(item.node_type));
    setSelected(item);
    setRecent((prev) => [item.ts_guid, ...prev.filter((g) => g !== item.ts_guid)].slice(0, MAX_RECENT));
  }, []);

  const selectFromList = useCallback((item: TopologyItem) => {
    applySelection(item);
    setTrail([{ guid: item.ts_guid, name: item.name }]);
  }, [applySelection]);

  const onJump = useCallback((guid: string, _nodeType: string) => {
    const item = guidToItem.get(guid);
    if (!item) return;
    applySelection(item);
    setTrail((prev) => {
      if (prev.length && prev[prev.length - 1].guid === guid) return prev;
      return [...prev, { guid, name: item.name }];
    });
  }, [guidToItem, applySelection]);

  const goToCrumb = useCallback((index: number) => {
    const crumb = trail[index];
    const item = crumb && guidToItem.get(crumb.guid);
    if (!item) return;
    applySelection(item);
    setTrail((prev) => prev.slice(0, index + 1));
  }, [trail, guidToItem, applySelection]);

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

  const runDeepIndex = useCallback(async () => {
    try {
      await relationshipsApi.deepIndex(clusterId, orgId);
      toast.success("Column index build started", {
        hint: "Crawling all saved answers in the background — track it on the Jobs page.",
      });
    } catch (e) {
      toast.error("Couldn't start the column index build", { hint: e instanceof Error ? e.message : String(e) });
    }
  }, [clusterId, orgId, toast]);

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

  const graphReady = !!graph && (graph.nodes.length > 1 || lineageBuilt);
  const showDetailTabs = !!selected && graphReady && !graphLoading && !graphError;
  const shownTrail = trail.slice(-MAX_TRAIL_SHOWN);
  const trailOffset = trail.length - shownTrail.length;

  return (
    <div style={{ display: "flex", flex: 1, minHeight: 0, height: "100%" }}>
      {/* ── Left: Browse panel ── */}
      <div style={{ width: 300, flexShrink: 0, borderRight: `1px solid ${theme.color.border}`, background: theme.color.surface, minHeight: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 12px 0", flexShrink: 0 }}>
          <div style={{ fontSize: 11, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.color.textMuted, fontFamily: theme.font.sans, marginBottom: 8 }}>
            Browse
          </div>
          {/* 2x2 grid, not a single row: four tabs across a 300px rail clipped the
              longest labels ("Logical Tables" wrapped, "Connections" truncated). */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 3, padding: 3, background: theme.color.surface2, borderRadius: theme.radius.control }}>
            {TABS.map((t) => {
              const active = tab === t.id;
              const Icon = t.icon;
              return (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  title={`${t.label} (${topology[t.id].length.toLocaleString()})`}
                  style={{
                    display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
                    padding: "7px 4px", border: "none", cursor: "pointer", minWidth: 0,
                    borderRadius: theme.radius.control - 2,
                    background: active ? theme.color.surface : "transparent",
                    boxShadow: active ? theme.shadow.xs : "none",
                    fontFamily: theme.font.sans,
                  }}
                >
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 11.5, fontWeight: active ? 600 : 500, color: active ? theme.color.textPrimary : theme.color.textMuted, maxWidth: "100%", overflow: "hidden", whiteSpace: "nowrap" }}>
                    <Icon size={12} style={{ color: active ? theme.color.accent2 : theme.color.textMuted, flexShrink: 0 }} />
                    {t.label}
                  </span>
                  <span style={{ fontSize: 10.5, color: theme.color.textMuted }}>{topology[t.id].length.toLocaleString()}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div style={{ flex: 1, minHeight: 0 }}>
          <ObjectList
            items={items}
            selectedGuid={selected?.ts_guid ?? null}
            recentGuids={recent}
            showSubtypeFilter={tab === "logical_tables"}
            onSelect={selectFromList}
          />
        </div>

        {tab === "answers" && (
          <button
            onClick={runDeepIndex}
            title="Runs a background job that scans every saved answer once, so the Column map's “Used by” is complete without opening each answer."
            style={{
              display: "flex", alignItems: "center", gap: 9, textAlign: "left",
              margin: 10, padding: "8px 11px", border: `1px solid ${theme.color.border}`,
              borderRadius: theme.radius.control, background: theme.color.bg, cursor: "pointer",
              fontFamily: theme.font.sans,
            }}
          >
            <Hammer size={14} style={{ color: theme.color.accent2, flexShrink: 0 }} />
            <span style={{ minWidth: 0 }}>
              <span style={{ display: "block", fontSize: 12, fontWeight: 600, color: theme.color.textPrimary }}>Build column index</span>
              <span style={{ display: "block", fontSize: 11, color: theme.color.textMuted }}>Scan all answers in the background</span>
            </span>
          </button>
        )}
      </div>

      {/* ── Right: detail panel ── */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {/* Path trail — appears once you navigate deeper than one object */}
        {trail.length > 1 && (
          <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "5px 14px", borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface2, flexShrink: 0, fontFamily: theme.font.sans }}>
            <History size={12} style={{ color: theme.color.textMuted, flexShrink: 0 }} />
            <span style={{ fontSize: 10.5, fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.color.textMuted, flexShrink: 0 }}>
              Path
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 2, minWidth: 0, overflow: "hidden" }}>
              {trailOffset > 0 && <span style={{ fontSize: 12, color: theme.color.textMuted }}>…</span>}
              {shownTrail.map((c, i, arr) => (
                <span key={`${c.guid}-${trailOffset + i}`} style={{ display: "inline-flex", alignItems: "center", gap: 2, minWidth: 0 }}>
                  {(i > 0 || trailOffset > 0) && <ChevronRight size={12} style={{ color: theme.color.textMuted, flexShrink: 0 }} />}
                  <button
                    onClick={() => goToCrumb(trailOffset + i)}
                    title={i === arr.length - 1 ? c.name : `Back to ${c.name}`}
                    style={{
                      background: "transparent", border: "none", cursor: "pointer", padding: "2px 4px",
                      fontSize: 12, fontFamily: theme.font.sans, maxWidth: 150, overflow: "hidden",
                      textOverflow: "ellipsis", whiteSpace: "nowrap",
                      color: i === arr.length - 1 ? theme.color.textPrimary : theme.color.textMuted,
                      fontWeight: i === arr.length - 1 ? 600 : 400,
                      textDecoration: i === arr.length - 1 ? "none" : "underline",
                      textDecorationColor: theme.color.borderLight,
                      textUnderlineOffset: 3,
                    }}
                  >
                    {c.name}
                  </button>
                </span>
              ))}
            </div>
            <button
              onClick={() => setTrail((prev) => prev.slice(-1))}
              title="Clear the navigation path"
              style={{
                marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0,
                background: "transparent", border: "none", cursor: "pointer", padding: "2px 4px",
                fontSize: 11, fontFamily: theme.font.sans, color: theme.color.textMuted,
              }}
            >
              <X size={11} /> Clear
            </button>
          </div>
        )}

        {/* Object header — one row: identity · view switch · impact. */}
        {selected && (() => {
          const s = styleFor(selected.node_type);
          const SIcon = s.Icon;
          const downstream = graph?.impact.downstream_count ?? 0;
          return (
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 16px", borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface, flexShrink: 0, fontFamily: theme.font.sans, minWidth: 0 }}>
              <SIcon size={16} style={{ color: s.color, flexShrink: 0 }} />
              <span style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {selected.name}
              </span>
              <span
                style={{
                  fontSize: 10.5, fontWeight: 600, padding: "1px 8px", borderRadius: theme.radius.pill, flexShrink: 0,
                  background: s.soft, color: s.color, border: `1px solid ${s.border}`,
                }}
              >
                {selected.subtype}
              </span>
              {selected.owner_name && (
                <span style={{ fontSize: 12, color: theme.color.textMuted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 40 }}>
                  Owner: {selected.owner_name}
                </span>
              )}
              <span style={{ flex: 1 }} />
              {showDetailTabs && graph && (
                <>
                  {graph.capped && (
                    <span
                      title="Some consumers are grouped into count nodes — click one to browse the full list."
                      style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: theme.color.warn, flexShrink: 0 }}
                    >
                      <AlertTriangle size={12} /> large fan-out grouped
                    </span>
                  )}
                  <span
                    title="Objects that consume this one, directly or indirectly. Anything here is affected if this object changes."
                    style={{
                      fontSize: 11, fontWeight: 600, padding: "2px 9px", borderRadius: theme.radius.pill, flexShrink: 0,
                      background: downstream > 0 ? theme.color.accentSoft : theme.color.surface2,
                      color: downstream > 0 ? theme.color.accent2 : theme.color.textMuted,
                    }}
                  >
                    {downstream > 0
                      ? `${downstream.toLocaleString()} downstream object${downstream === 1 ? "" : "s"}`
                      : "No downstream objects"}
                  </span>
                  {/* View switch — segmented control matching the Browse panel. */}
                  <div style={{ display: "flex", gap: 3, padding: 3, background: theme.color.surface2, borderRadius: theme.radius.control, flexShrink: 0 }}>
                    {([["flow", "Lineage graph"], ["columns", "Column map"]] as const).map(([id, label]) => {
                      const activeView = detailTab === id;
                      return (
                        <button
                          key={id}
                          onClick={() => setDetailTab(id)}
                          style={{
                            display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 12px",
                            border: "none", cursor: "pointer", borderRadius: theme.radius.control - 2,
                            background: activeView ? theme.color.surface : "transparent",
                            boxShadow: activeView ? theme.shadow.xs : "none",
                            fontSize: 12, fontWeight: activeView ? 600 : 500, fontFamily: theme.font.sans,
                            color: activeView ? theme.color.textPrimary : theme.color.textMuted,
                          }}
                        >
                          {label}
                          {id === "columns" && graph.columns.length > 0 && (
                            <span style={{ fontSize: 10.5, color: theme.color.textMuted }}>{graph.columns.length}</span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          );
        })()}

        <div style={{ flex: 1, minHeight: 0 }}>
          {!selected ? (
            <GettingStarted />
          ) : graphLoading ? (
            <PanelSpinner label="Loading graph…" />
          ) : graphError ? (
            <CenteredMessage><strong>Couldn&apos;t load lineage.</strong> {graphError}</CenteredMessage>
          ) : graph && graphReady ? (
            detailTab === "flow" ? (
              <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
                <div style={{ flex: 1, minHeight: 0 }}>
                  <LineageFlow data={graph} onJump={onJump} onOpenDrawer={setDrawerType} />
                </div>
                <Legend />
              </div>
            ) : (
              <ColumnsGrid columns={graph.columns} onJump={onJump} />
            )
          ) : (
            <BuildLineagePrompt building={building} progress={buildProgress} onBuild={triggerBuild} />
          )}
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

const STEPS: [string, React.ReactNode][] = [
  ["Pick a type", <>Choose <strong>Logical Tables</strong>, <strong>Answers</strong>, or <strong>Liveboards</strong> in the Browse panel.</>],
  ["Select an object", <>See where its data comes from and every object that depends on it.</>],
  ["Follow the graph", <>Click any node to explore it next — your <strong>Path</strong> shows at the top so you can backtrack.</>],
];

function GettingStarted() {
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: 32, fontFamily: theme.font.sans, maxWidth: 440, margin: "0 auto", gap: 14 }}>
      <div style={{ width: 52, height: 52, borderRadius: 14, background: theme.color.accentSoft, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <GitFork size={24} style={{ color: theme.color.accent2 }} />
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary }}>Explore your data lineage</div>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12, width: "100%" }}>
        {STEPS.map(([title, body], i) => (
          <li key={title} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <span style={{ width: 20, height: 20, borderRadius: theme.radius.pill, flexShrink: 0, background: theme.color.surface2, border: `1px solid ${theme.color.border}`, display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 600, color: theme.color.textSecondary }}>
              {i + 1}
            </span>
            <span style={{ fontSize: 13, lineHeight: 1.5, color: theme.color.textMuted }}>
              <strong style={{ color: theme.color.textPrimary, fontWeight: 600 }}>{title}.</strong> {body}
            </span>
          </li>
        ))}
      </ol>
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
        dependency API (seconds for the object graph) and can take a few minutes on large instances —
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
            {progress != null && progress > 0 ? `Building… ${progress.toLocaleString()} objects processed` : "Building…"}
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
