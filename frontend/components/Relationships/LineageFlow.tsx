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
import { useEffect, useMemo, useState } from "react";
import { ReactFlow, Background, Controls, Panel, Handle, Position, MarkerType, type Node, type Edge, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Crosshair, Users, MoveRight, ChevronsLeftRight } from "lucide-react";
import { theme } from "@/lib/theme";
import type { LineageGraphResponse } from "@/lib/types";
import { styleFor } from "./nodeStyles";

// X_GAP must exceed the node maxWidth (190) by enough to leave a real gutter —
// edges are drawn in that space, and at the old 230 they ran along the box
// borders and read as if they touched the cards.
const X_GAP = 320;
const Y_GAP = 84;
// Consumer types with more than this many members collapse into one count node.
const COLLAPSE_THRESHOLD = 8;
// Source layers wider than this collapse into one expandable node. A model with
// 20 source tables is a 1,700px ladder otherwise, and "20 DB Tables → Model" is
// the more useful first read anyway. Unlike consumers (which page through a
// drawer) every source is already in the response, so expanding is local state.
const SOURCE_COLLAPSE_THRESHOLD = 8;

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
interface MoreNodeData extends Record<string, unknown> {
  label: string;
  nodeType: string;
  layer: number;
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

function MoreNodeCard({ data }: NodeProps<Node<MoreNodeData>>) {
  const s = styleFor(data.nodeType);
  const Icon = s.Icon;
  return (
    <div
      className="rv-node rv-node--clickable"
      title="Click to show every one of these sources"
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
        <ChevronsLeftRight size={9} /> Click to expand
      </div>
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}

// Hoisted (stable identity) per React Flow's perf guidance.
const nodeTypes = { lineage: LineageNodeCard, count: CountNodeCard, more: MoreNodeCard };

function buildFlow(
  data: LineageGraphResponse,
  expandedGroups: Set<string>,
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

  for (const type of collapsedTypes) {
    const total = data.consumer_totals[type] ?? consumersByType.get(type)?.length ?? 0;
    flowNodes.push({
      id: `count-${type}`,
      type: "count",
      position: { x: 0, y: 0 },
      data: { label: `${total} ${styleFor(type).label}s`, nodeType: type, consumerType: type } satisfies CountNodeData,
    });
  }

  // ── Layered layout: x by DISTANCE FROM THE ROOT, y by index-in-layer ──
  //
  // Layering by pipeline type put every MODEL in one column, so a model built on
  // other models drew its edges *inside* that column — they had to loop backward
  // around the cards, which is unreadable and suggests a relationship that runs
  // the wrong way. Layer by longest path instead: the root is 0, and an ancestor
  // sits one column further left than the furthest node it feeds. A node is then
  // always strictly left of everything it produces for, so no edge is ever
  // horizontal-within-a-column and none run backwards.
  //
  // Direct consumers keep the single column immediately right of the root
  // whatever their type — an answer built straight on a table is one hop
  // downstream, and parking it in the answer layer would route its edge across
  // the model column, drawing an arrow that reads as model→answer when no such
  // dependency exists.
  const dist = new Map<string, number>([[rootGuid, 0]]);
  // Relax producer edges until stable. Bounded by node count: lineage is a DAG,
  // but a cycle in cached edges must not spin here.
  for (let pass = 0; pass <= data.nodes.length; pass++) {
    let changed = false;
    for (const e of data.edges) {
      const consumerDist = dist.get(e.source); // e.source consumes e.target
      if (consumerDist === undefined) continue;
      if ((dist.get(e.target) ?? -1) < consumerDist + 1) {
        dist.set(e.target, consumerDist + 1);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const maxDist = Math.max(0, ...[...dist.values()]);
  const rootLayer = maxDist;
  const consumerLayer = rootLayer + 1;
  const layerById = new Map<string, number>();
  for (const n of flowNodes) {
    const d = dist.get(n.id);
    // An ancestor is placed by distance; everything else is a direct consumer.
    layerById.set(n.id, d === undefined ? consumerLayer : maxDist - d);
  }

  // ── Collapse wide SOURCE columns into one expandable node ──
  //
  // Consumers already collapse (into a paged drawer). Sources could not, so a
  // model with twenty tables rendered as a ladder taller than the viewport. Here
  // every member is already in the response, so the collapsed node expands in
  // place — the [+]/[-] progressive disclosure every lineage tool uses — rather
  // than opening a drawer. Grouped per (layer, type) so a mixed column only
  // folds the part that is actually wide.
  const hiddenToMore = new Map<string, string>();
  const moreNodes: Node[] = [];
  const upstreamGroups = new Map<string, Node[]>();
  for (const n of flowNodes) {
    const layer = layerById.get(n.id) ?? 2;
    if (layer >= consumerLayer || n.id === rootGuid || n.id.startsWith("count-")) continue;
    const key = `${layer}::${String(n.data.nodeType)}`;
    const arr = upstreamGroups.get(key) ?? [];
    arr.push(n);
    upstreamGroups.set(key, arr);
  }
  for (const [key, members] of upstreamGroups) {
    if (members.length <= SOURCE_COLLAPSE_THRESHOLD || expandedGroups.has(key)) continue;
    const [layerStr, type] = key.split("::");
    const moreId = `more-${key}`;
    members.forEach((m) => hiddenToMore.set(m.id, moreId));
    moreNodes.push({
      id: moreId,
      type: "more",
      position: { x: 0, y: 0 },
      data: { label: `${members.length} ${styleFor(type).label}s`, nodeType: type, layer: Number(layerStr) } satisfies MoreNodeData,
    });
    layerById.set(moreId, Number(layerStr));
  }
  const laidOut = flowNodes.filter((n) => !hiddenToMore.has(n.id)).concat(moreNodes);

  const byLayer = new Map<number, Node[]>();
  for (const n of laidOut) {
    const l = layerById.get(n.id) ?? 2;
    const arr = byLayer.get(l) ?? [];
    arr.push(n);
    byLayer.set(l, arr);
  }
  for (const [layer, arr] of byLayer) {
    // Group by node type inside the column so mixed consumer types stay legible.
    arr.sort(
      (a, b) =>
        String(a.data.nodeType).localeCompare(String(b.data.nodeType)) ||
        String(a.data.label).localeCompare(String(b.data.label)),
    );
    const offset = ((arr.length - 1) * Y_GAP) / 2;
    arr.forEach((n, i) => {
      n.position = { x: layer * X_GAP, y: i * Y_GAP - offset };
    });
  }

  // ── Edges: draw producer→consumer (target→source) so arrows point downstream ──
  //
  // Bezier ("default"), not smoothstep. Smoothstep routes every edge between the
  // same two columns through a vertical segment at the SAME midpoint x, so a
  // fan-out of four collapsed onto one indistinguishable rail — you could not
  // tell which source fed which target. Bezier control points derive from both
  // endpoints, so edges sharing a source still separate.
  const flowEdges: Edge[] = [];
  const seen = new Set<string>();
  const addEdge = (source: string, target: string) => {
    if (source === target) return;
    const key = `${source}->${target}`;
    if (seen.has(key)) return;
    seen.add(key);
    flowEdges.push({
      id: key, source, target, type: "default",
      markerEnd: { type: MarkerType.ArrowClosed, color: theme.color.textMuted },
      style: { stroke: theme.color.textMuted, strokeWidth: 1.4 },
    });
  };
  const renderedIds = new Set(laidOut.map((n) => n.id));
  // A collapsed source's edges are re-pointed at the node that stands in for it.
  const resolve = (id: string) => hiddenToMore.get(id) ?? id;
  for (const e of data.edges) {
    if (collapsedConsumerGuids.has(e.source)) continue; // handled by count edge below
    const target = resolve(e.target);
    const source = resolve(e.source);
    if (renderedIds.has(target) && renderedIds.has(source)) addEdge(target, source);
  }
  for (const type of collapsedTypes) {
    addEdge(rootGuid, `count-${type}`);
  }

  return { nodes: laidOut, edges: flowEdges };
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
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [hovered, setHovered] = useState<string | null>(null);

  // A new root is a new graph — collapsed sources should not stay expanded from
  // whatever the user was looking at before.
  useEffect(() => setExpandedGroups(new Set()), [data.root.guid]);

  const { nodes, edges } = useMemo(() => buildFlow(data, expandedGroups), [data, expandedGroups]);

  // ── Hover: dim everything not on the hovered node's own path ──
  //
  // The density mitigation that hides nothing. With a dozen edges crossing a
  // column, following one chain by eye is guesswork; hovering makes exactly the
  // hovered node's own producers and consumers stand out.
  const neighbours = useMemo(() => {
    if (!hovered) return null;
    const keep = new Set<string>([hovered]);
    for (const e of edges) {
      if (e.source === hovered) keep.add(e.target);
      else if (e.target === hovered) keep.add(e.source);
    }
    return keep;
  }, [hovered, edges]);

  const shownNodes = useMemo(
    () =>
      neighbours
        ? nodes.map((n) => (neighbours.has(n.id) ? n : { ...n, style: { ...n.style, opacity: 0.25 } }))
        : nodes,
    [nodes, neighbours],
  );
  const shownEdges = useMemo(() => {
    if (!hovered) return edges;
    return edges.map((e) => {
      const on = e.source === hovered || e.target === hovered;
      return on
        ? {
            ...e,
            style: { ...e.style, stroke: theme.color.accent, strokeWidth: 2.2 },
            markerEnd: { type: MarkerType.ArrowClosed, color: theme.color.accent },
            zIndex: 1,
          }
        : { ...e, style: { ...e.style, opacity: 0.15 } };
    });
  }, [edges, hovered]);

  const onNodeClick = (_e: React.MouseEvent, node: Node) => {
    if (node.type === "count") {
      onOpenDrawer((node.data as CountNodeData).consumerType);
      return;
    }
    if (node.type === "more") {
      const key = node.id.slice("more-".length);
      setExpandedGroups((prev) => new Set(prev).add(key));
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
          nodes={shownNodes}
          edges={shownEdges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          onNodeMouseEnter={(_e, n) => setHovered(n.id)}
          onNodeMouseLeave={() => setHovered(null)}
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
                No related objects found — nothing else in this instance links to this one.
              </div>
            </Panel>
          )}
        </ReactFlow>
      </div>
    </div>
  );
}
