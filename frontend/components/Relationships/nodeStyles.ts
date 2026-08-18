/**
 * Node-type presentation for the lineage graph + legend + left list.
 *
 * Six coarse node types, each mapped onto the app's warm semantic tokens plus a
 * lucide icon so the graph, legend, and left list all read as one system (React
 * Flow's default blue is bypassed entirely). No raw hexes — every colour routes
 * through `theme.*`.
 */
import { Plug, Database, Table2, Boxes, BarChart3, LayoutDashboard, type LucideIcon } from "lucide-react";
import { theme } from "@/lib/theme";
import type { LineageNodeType, RootKind } from "@/lib/types";

export interface NodeStyle {
  label: string;
  /** Solid accent — dot, border, text, icon. */
  color: string;
  /** Soft fill behind the node body. */
  soft: string;
  border: string;
  /** Type glyph shown in the graph node, legend, and left list. */
  Icon: LucideIcon;
}

export const NODE_STYLES: Record<LineageNodeType, NodeStyle> = {
  CONNECTION:    { label: "Connection",    color: theme.color.textMuted,      soft: theme.color.surface3,   border: theme.color.border,        Icon: Plug },
  DB_TABLE:      { label: "DB Table",      color: theme.color.accent,         soft: theme.color.accentSoft,  border: theme.color.accentBorder,  Icon: Database },
  LOGICAL_TABLE: { label: "Logical Table", color: theme.color.textSecondary,  soft: theme.color.surface2,   border: theme.color.borderLight,   Icon: Table2 },
  MODEL:         { label: "Model",         color: theme.color.accent2,        soft: theme.color.violetSoft,  border: theme.color.violetBorder,  Icon: Boxes },
  ANSWER:        { label: "Answer",        color: theme.color.success,        soft: theme.color.successSoft, border: theme.color.successBorder, Icon: BarChart3 },
  LIVEBOARD:     { label: "Liveboard",     color: theme.color.warn,           soft: theme.color.warnSoft,    border: theme.color.warnBorder,    Icon: LayoutDashboard },
};

/** Pipeline order, upstream (sources) → downstream (consumers). */
export const NODE_STYLE_ORDER: LineageNodeType[] = [
  "CONNECTION", "DB_TABLE", "LOGICAL_TABLE", "MODEL", "ANSWER", "LIVEBOARD",
];

export function styleFor(nodeType: string): NodeStyle {
  return NODE_STYLES[nodeType as LineageNodeType] ?? NODE_STYLES.LOGICAL_TABLE;
}

/** The graph endpoint's root_kind segment for a given node type. */
export function rootKindFor(nodeType: string): RootKind {
  if (nodeType === "ANSWER") return "answer";
  if (nodeType === "LIVEBOARD") return "liveboard";
  if (nodeType === "CONNECTION") return "connection";
  return "model";
}

export type ExplorerTab = "logical_tables" | "answers" | "liveboards" | "connections";

export function tabForNodeType(nodeType: string): ExplorerTab {
  if (nodeType === "ANSWER") return "answers";
  if (nodeType === "LIVEBOARD") return "liveboards";
  if (nodeType === "CONNECTION") return "connections";
  return "logical_tables";
}
