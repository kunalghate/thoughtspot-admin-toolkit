/**
 * Node-type presentation for the lineage graph + legend + left list.
 *
 * Six coarse node types, each mapped onto the app's warm semantic tokens so the
 * graph reads as one system (React Flow's default blue is bypassed entirely).
 * No raw hexes — every value routes through `theme.*`.
 */
import { theme } from "@/lib/theme";
import type { LineageNodeType, RootKind } from "@/lib/types";

export interface NodeStyle {
  label: string;
  /** Solid accent — dot, border, text. */
  color: string;
  /** Soft fill behind the node body. */
  soft: string;
  border: string;
}

export const NODE_STYLES: Record<LineageNodeType, NodeStyle> = {
  CONNECTION:    { label: "Connection",    color: theme.color.textMuted,      soft: theme.color.surface3,   border: theme.color.border },
  DB_TABLE:      { label: "DB Table",      color: theme.color.accent,         soft: theme.color.accentSoft,  border: theme.color.accentBorder },
  LOGICAL_TABLE: { label: "Logical Table", color: theme.color.textSecondary,  soft: theme.color.surface2,   border: theme.color.borderLight },
  MODEL:         { label: "Model",         color: theme.color.accent2,        soft: theme.color.violetSoft,  border: theme.color.violetBorder },
  ANSWER:        { label: "Answer",        color: theme.color.success,        soft: theme.color.successSoft, border: theme.color.successBorder },
  LIVEBOARD:     { label: "Liveboard",     color: theme.color.warn,           soft: theme.color.warnSoft,    border: theme.color.warnBorder },
};

/** The three explorer tabs, in pipeline order (producers → consumers). */
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
  return "model";
}

export type ExplorerTab = "logical_tables" | "answers" | "liveboards";

export function tabForNodeType(nodeType: string): ExplorerTab {
  if (nodeType === "ANSWER") return "answers";
  if (nodeType === "LIVEBOARD") return "liveboards";
  return "logical_tables";
}
