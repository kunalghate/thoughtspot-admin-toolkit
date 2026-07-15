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
import { ReactFlow, Background, Controls, Handle, Position, MarkerType, type Node, type Edge, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle } from "lucide-react";
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
  return (
    <div
      title={`${data.label}\n${s.label}${data.ownerName ? ` · ${data.ownerName}` : ""}\n${data.guid}`}
      style={{
        minWidth: 150, maxWidth: 190, padding: "8px 10px",
        borderRadius: theme.radius.control, fontFamily: theme.font.sans,
        background: data.isRoot ? s.color : s.soft,
        color: data.isRoot ? theme.color.onAccent : theme.color.textPrimary,
        border: `${data.isRoot ? 2 : 1}px ${data.accessible ? "solid" : "dashed"} ${s.border}`,
        opacity: data.accessible ? 1 : 0.6,
        boxShadow: data.isRoot ? theme.shadow.sm : "none",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ width: 8, height: 8, borderRadius: 2, background: data.isRoot ? theme.color.onAccent : s.color, flexShrink: 0 }} />
        <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em", opacity: 0.8 }}>{s.label}</span>
      </div>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {data.label}
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

function CountNodeCard({ data }: NodeProps<Node<CountNodeData>>) {
  const s = styleFor(data.nodeType);
  return (
    <div
      title="Click to browse all"
      style={{
        minWidth: 150, padding: "10px 12px", borderRadius: theme.radius.control,
        fontFamily: theme.font.sans, textAlign: "center", cursor: "pointer",
        background: s.soft, color: theme.color.textPrimary,
        border: `1px dashed ${s.border}`,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div style={{ fontSize: 13, fontWeight: 700, color: s.color }}>{data.label}</div>
      <div style={{ fontSize: 10, color: theme.color.textMuted, marginTop: 2 }}>Click to browse</div>
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

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 10, padding: "8px 14px",
          borderBottom: `1px solid ${theme.color.border}`, background: theme.color.surface, flexShrink: 0,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
          {data.root.name}
        </span>
        <span
          style={{
            fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: theme.radius.pill,
            background: theme.color.accentSoft, color: theme.color.accent2, fontFamily: theme.font.sans,
          }}
        >
          {data.impact.downstream_count} downstream object{data.impact.downstream_count === 1 ? "" : "s"} affected
        </span>
        {data.capped && (
          <span
            title="Some consumers are collapsed — open a count node to browse them all."
            style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: theme.color.warn, fontFamily: theme.font.sans }}
          >
            <AlertTriangle size={12} /> large fan-out collapsed
          </span>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
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
        </ReactFlow>
      </div>
    </div>
  );
}
