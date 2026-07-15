/** Node-type + "inaccessible" key for the lineage graph. */
import { theme } from "@/lib/theme";
import { NODE_STYLE_ORDER, NODE_STYLES } from "./nodeStyles";

export function Legend() {
  return (
    <div
      style={{
        display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14,
        padding: "6px 12px", fontSize: 11, fontFamily: theme.font.sans,
        color: theme.color.textMuted,
        background: theme.color.surface, borderTop: `1px solid ${theme.color.border}`,
      }}
    >
      {NODE_STYLE_ORDER.map((nt) => {
        const s = NODE_STYLES[nt];
        const Icon = s.Icon;
        return (
          <span key={nt} style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
            <Icon size={12} style={{ color: s.color, flexShrink: 0 }} />
            {s.label}
          </span>
        );
      })}
      <span style={{ display: "inline-flex", alignItems: "center", gap: 5, opacity: 0.55 }}>
        <span
          style={{
            width: 11, height: 11, borderRadius: 3, display: "inline-block",
            border: `1px dashed ${theme.color.textMuted}`,
          }}
        />
        Inaccessible
      </span>
    </div>
  );
}
