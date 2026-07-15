/**
 * The lineage graph — React Flow with a deterministic, manual layered layout.
 *
 * The pipeline has fixed layers (Connection → DB Table → Logical Table → Model →
 * Answer/Liveboard), so x is assigned by layer and y by index-in-layer — instant,
 * stable, no dagre/elk. Edges are drawn producer→consumer (left→right) so the
 * arrowheads read as data flow. Large consumer fan-outs collapse into a single
 * count node that opens the drawer; the Flow stays legible.
 *
 * Loaded via next/dynamic({ ssr: false }) — React Flow needs the DOM.
 */
import { useMemo } from "react";
import { ReactFlow, Background, Controls, Panel, Handle, Position, MarkerType, type Node, type Edge, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Crosshair, Users, MoveRight } from "lucide-react";
import { theme } from "@/lib/theme";
import type { LineageGraphResponse } from "@/lib/types";
import { styleFor } from "./nodeStyles";

const X_GAP = 230;
const Y_GAP = 84;
// Consumer types with more than this many members collapse into one count node.
const COLLAPSE_THRESHOLD = 8;

interface LineageNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  guid: string;
  ownerName: string;
  accessible: boolean;
  isRoot: boolean;
}
interface CountNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  consumerType: string;
}

function LineageNodeCard({ data }: NodeProps<Node<LineageNodeData>>) {
  const s = styleFor(data.nodeType);
  const Icon = s.Icon;
  const clickable = !data.isRoot;
  return (
    <div
      className={`rv-node${clickable ? " rv-node--clickable" : ""}`}
      title={
        `${data.label}\n${s.label}${data.ownerName ? ` · ${data.ownerName}` : ""}` +
        (clickable ? "\n\nClick to make this the focus of the graph" : "\nYou are viewing this object")
      }
      style={{
        position: "relative",
        minWidth: 150, maxWidth: 190, padding: "7px 10px",
        borderRadius: theme.radius.control, fontFamily: theme.font.sans,
        background: data.isRoot ? s.color : s.soft,
        color: data.isRoot ? theme.color.onAccent : theme.color.textPrimary,
        border: `${data.isRoot ? 2 : 1}px ${data.accessible ? "solid" : "dashed"} ${data.isRoot ? s.color : s.border}`,
        opacity: data.accessible ? 1 : 0.6,
        boxShadow: data.isRoot ? theme.shadow.md : theme.shadow.xs,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Icon size={12} style={{ color: data.isRoot ? theme.color.onAccent : s.color, flexShrink: 0, opacity: data.isRoot ? 0.9 : 1 }} />
        <span style={{ fontSize: 9.5, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 600, opacity: 0.75, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.label}</span>
        {data.isRoot && (
          <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: 8.5, fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", padding: "1px 6px", borderRadius: theme.radius.pill, background: theme.color.onAccent, color: s.color }}>
            Viewing
          </span>
        )}
      </div>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {data.label}
      </div>
      {clickable && (
        <span
          className="rv-focus"
          style={{
            position: "absolute", top: -9, right: 8, display: "inline-flex", alignItems: "center", gap: 3,
            fontSize: 9.5, fontWeight: 700, color: theme.color.onAccent, background: s.color,
            borderRadius: theme.radius.pill, padding: "2px 7px", boxShadow: theme.shadow.sm, whiteSpace: "nowrap",
          }}
        >
          <Crosshair size={9} /> Explore
        </span>
      )}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

function CountNodeCard({ data }: NodeProps<Node<CountNodeData>>) {
  const s = styleFor(data.nodeType);
  const Icon = s.Icon;
  return (
    <div
      className="rv-node rv-node--clickable"
      title="Click to browse the full list of these consumers"
      style={{
        position: "relative", minWidth: 150, padding: "9px 12px", borderRadius: theme.radius.control,
        fontFamily: theme.font.sans, textAlign: "center",
        background: s.soft, color: theme.color.textPrimary,
        border: `1px dashed ${s.border}`, boxShadow: theme.shadow.xs,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontSize: 13, fontWeight: 700, color: s.color }}>
        <Icon size={12} style={{ flexShrink: 0 }} /> {data.label}
      </div>
      <div style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 10, color: theme.color.textMuted, marginTop: 2 }}>
        <Users size={9} /> Click to browse all
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// Hoisted (stable identity) per React Flow's perf guidance.
const nodeTypes = { lineage: LineageNodeCard, count: CountNodeCard };

function buildFlow(
  data: LineageGraphResponse,
): { nodes: Node[]; edges: Edge[] } {
  const rootGuid = data.root.guid;
  const nodeByGuid = new Map(data.nodes.map((n) => [n.guid, n]));

  // Direct consumers of the root: edges where target === root.
  const consumerEdges = data.edges.filter((e) => e.target === rootGuid);
  const consumersByType = new Map<string, string[]>();
  for (const e of consumerEdges) {
    const src = nodeByGuid.get(e.source);
    if (!src) continue;
    const list = consumersByType.get(src.node_type) ?? [];
    list.push(src.guid);
    consumersByType.set(src.node_type, list);
  }

  // Collapse a consumer type when its true total exceeds the threshold.
  const collapsedTypes = new Set<string>();
  for (const [type, guids] of consumersByType) {
    const total = data.consumer_totals[type] ?? guids.length;
    if (total > COLLAPSE_THRESHOLD) collapsedTypes.add(type);
  }
  const collapsedConsumerGuids = new Set<string>();
  for (const [type, guids] of consumersByType) {
    if (collapsedTypes.has(type)) guids.forEach((g) => collapsedConsumerGuids.add(g));
  }

  // ── Nodes: individual (non-collapsed) + one count node per collapsed type ──
  const rendered = data.nodes.filter((n) => !collapsedConsumerGuids.has(n.guid));
  const flowNodes: Node[] = rendered.map((n) => ({
    id: n.guid,
    type: "lineage",
    position: { x: 0, y: 0 },
    data: {
      label: n.name || n.guid, nodeType: n.node_type, guid: n.guid,
      ownerName: n.owner_name, accessible: n.accessible, isRoot: n.guid === rootGuid,
    } satisfies LineageNodeData,
  }));

  const countNodeLayer: Record<string, number> = {};
  for (const type of collapsedTypes) {
    const sampleGuid = consumersByType.get(type)?.[0];
    const layer = (sampleGuid ? nodeByGuid.get(sampleGuid)?.layer : undefined) ?? 4;
    const total = data.consumer_totals[type] ?? consumersByType.get(type)?.length ?? 0;
    countNodeLayer[`count-${type}`] = layer;
    flowNodes.push({
      id: `count-${type}`,
      type: "count",
      position: { x: 0, y: 0 },
      data: { label: `${total} ${styleFor(type).label}s`, nodeType: type, consumerType: type } satisfies CountNodeData,
    });
  }

  // ── Layered layout: x by layer, y by index-in-layer ──
  const layerOf = (id: string): number => {
    if (id.startsWith("count-")) return countNodeLayer[id] ?? 4;
    return nodeByGuid.get(id)?.layer ?? 2;
  };
  const byLayer = new Map<number, Node[]>();
  for (const n of flowNodes) {
    const l = layerOf(n.id);
    const arr = byLayer.get(l) ?? [];
    arr.push(n);
    byLayer.set(l, arr);
  }
  for (const [layer, arr] of byLayer) {
    arr.sort((a, b) => String(a.data.label).localeCompare(String(b.data.label)));
    const offset = ((arr.length - 1) * Y_GAP) / 2;
    arr.forEach((n, i) => {
      n.position = { x: layer * X_GAP, y: i * Y_GAP - offset };
    });
  }

  // ── Edges: draw producer→consumer (target→source) so arrows point downstream ──
  const flowEdges: Edge[] = [];
  const seen = new Set<string>();
  const addEdge = (source: string, target: string) => {
    const key = `${source}->${target}`;
    if (seen.has(key)) return;
    seen.add(key);
    flowEdges.push({
      id: key, source, target, type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color: theme.color.textMuted },
      style: { stroke: theme.color.textMuted, strokeWidth: 1.4 },
    });
  };
  const renderedIds = new Set(flowNodes.map((n) => n.id));
  for (const e of data.edges) {
    if (collapsedConsumerGuids.has(e.source)) continue; // handled by count edge below
    if (renderedIds.has(e.target) && renderedIds.has(e.source)) addEdge(e.target, e.source);
  }
  for (const type of collapsedTypes) {
    addEdge(rootGuid, `count-${type}`);
  }

  return { nodes: flowNodes, edges: flowEdges };
}

export default function LineageFlow({
  data,
  onJump,
  onOpenDrawer,
}: {
  data: LineageGraphResponse;
  onJump: (guid: string, nodeType: string) => void;
  onOpenDrawer: (consumerType: string) => void;
}) {
  const { nodes, edges } = useMemo(() => buildFlow(data), [data]);

  const onNodeClick = (_e: React.MouseEvent, node: Node) => {
    if (node.type === "count") {
      onOpenDrawer((node.data as CountNodeData).consumerType);
      return;
    }
    const d = node.data as LineageNodeData;
    if (!d.isRoot) onJump(d.guid, d.nodeType);
  };

  const isolated = nodes.length <= 1;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <style>{`
        .rv-node { transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease; }
        .rv-node--clickable { cursor: pointer; }
        .rv-node--clickable:hover { transform: translateY(-2px); box-shadow: ${theme.shadow.md}; border-color: ${theme.color.accent2}; }
        .rv-node .rv-focus { opacity: 0; transform: translateY(2px); transition: opacity .12s ease, transform .12s ease; pointer-events: none; }
        .rv-node--clickable:hover .rv-focus { opacity: 1; transform: translateY(0); }
      `}</style>
      <div style={{ flex: 1, minHeight: 0, position: "relative" }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          onlyRenderVisibleElements
          fitView
          proOptions={{ hideAttribution: true }}
          minZoom={0.2}
          nodesDraggable={false}
          nodesConnectable={false}
          style={{ background: theme.color.bg }}
        >
          <Background color={theme.color.border} gap={20} />
          <Controls showInteractive={false} />
          {!isolated && (
            <Panel position="top-left">
              <div
                title="The graph reads left to right: an object's data sources are on its left, and the objects that consume it (and are affected when it changes) are on its right."
                style={{
                  display: "inline-flex", alignItems: "center", gap: 8, padding: "5px 10px",
                  borderRadius: theme.radius.pill, background: theme.color.surface,
                  border: `1px solid ${theme.color.border}`, boxShadow: theme.shadow.sm,
                  fontFamily: theme.font.sans, fontSize: 11, color: theme.color.textSecondary,
                }}
              >
                <span style={{ fontWeight: 600 }}>Sources</span>
                <MoveRight size={13} style={{ color: theme.color.textMuted }} />
                <span style={{ fontWeight: 600 }}>Consumers</span>
              </div>
            </Panel>
          )}
          {isolated && (
            <Panel position="top-center">
              <div
                style={{
                  marginTop: 8, padding: "8px 14px", borderRadius: theme.radius.control,
                  background: theme.color.surface, border: `1px solid ${theme.color.border}`,
                  boxShadow: theme.shadow.sm, fontFamily: theme.font.sans, fontSize: 12,
                  color: theme.color.textMuted, textAlign: "center", maxWidth: 320,
                }}
              >
                No related objects found — nothing else in this cluster links to this one.
              </div>
            </Panel>
          )}
        </ReactFlow>
      </div>
    </div>
  );
}
