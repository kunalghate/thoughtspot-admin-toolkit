/**
 * Typed design-token accessor.
 *
 * Every value here is a `var(--token)` reference resolved by `styles/theme.css`.
 * Components import `theme` and reference tokens in inline styles:
 *
 *   import { theme } from "@/lib/theme";
 *   <div style={{ color: theme.color.textPrimary, background: theme.color.surface }} />
 *
 * Because the values are CSS variables, switching the active theme (via
 * <ThemeProvider>, which flips `data-theme` on <html>) instantly re-skins the
 * whole app with no component re-render.
 *
 * RULES (see docs/dev/DESIGN.md):
 *   - Never hardcode a hex in a component — always go through `theme.*`.
 *   - Filled controls (primary buttons, logo tile, progress bars) use
 *     `theme.gradient.accent` — despite the legacy name it resolves to the
 *     SOLID `--primary` fill. Gradients and glows are retired.
 *   - `theme.color.accent` (cyan) is for text, borders, focus, and dots only.
 *   - Every focusable control gets `theme.focus.ring` on :focus / :focus-visible.
 */

export const theme = {
  color: {
    // Surfaces
    bg: "var(--bg)",
    surface: "var(--surface)",
    surface2: "var(--surface-2)",
    surface3: "var(--surface-3)",

    // Borders
    border: "var(--border)",
    borderLight: "var(--border-light)",

    // Brand
    navy: "var(--navy)",
    accent: "var(--accent)",
    accent2: "var(--accent-2)",
    accentSoft: "var(--accent-soft)",
    accentBorder: "var(--accent-border)",
    violetSoft: "var(--violet-soft)",
    violetBorder: "var(--violet-border)",

    // Text
    textPrimary: "var(--text-primary)",
    textSecondary: "var(--text-secondary)",
    textMuted: "var(--text-muted)",

    // Status
    success: "var(--success)",
    successSoft: "var(--success-soft)",
    successBorder: "var(--success-border)",
    warn: "var(--warn)",
    warnSoft: "var(--warn-soft)",
    warnBorder: "var(--warn-border)",
    danger: "var(--danger)",
    dangerSoft: "var(--danger-soft)",
    dangerBorder: "var(--danger-border)",

    // Code + chrome
    codeBg: "var(--code-bg)",
    codeFg: "var(--code-fg)",
    overlay: "var(--overlay)",

    /** White text/icons that sit on a gradient or solid accent fill. */
    onAccent: "var(--on-accent)",
  },

  gradient: {
    /** Solid primary fill for filled controls (legacy name, no gradient). */
    accent: "var(--gradient-accent)",
    /** Same solid fill (legacy name, kept for compatibility). */
    accentVertical: "var(--gradient-accent-vertical)",
  },

  shadow: {
    xs: "var(--shadow-xs)",
    sm: "var(--shadow-sm)",
    md: "var(--shadow-md)",
    lg: "var(--shadow-lg)",
    /** Subtle elevation for primary buttons (legacy name — glows are retired). */
    glowAccent: "var(--glow-accent)",
  },

  /** The soft cyan focus halo — spread on :focus / :focus-visible. */
  focus: {
    ring: "0 0 0 3px var(--accent-soft)",
    borderColor: "var(--accent)",
  },

  /** Radius ladder: cards 9px, controls 7–8px, pills 999px. */
  radius: {
    card: 9,
    control: 8,
    pill: 999,
  },

  font: {
    sans: "var(--sans)",
    mono: "var(--mono)",
  },
} as const;

export type Theme = typeof theme;
