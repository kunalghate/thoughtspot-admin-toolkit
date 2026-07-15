/**
 * Theme registry — the list of themes the app offers.
 *
 * To add a theme:
 *   1. Add its `:root[data-theme="<id>"] { … }` block in `styles/theme.css`.
 *   2. Add an entry here. The switcher UI and <ThemeProvider> read from this list;
 *      nothing else needs to change.
 */

export type ThemeMode = "light" | "dark";

export interface ThemeDef {
  /** Stable id — matches the `[data-theme="<id>"]` selector in theme.css. */
  id: string;
  /** Human label shown in the theme switcher. */
  label: string;
  /** Whether this theme is light or dark (drives the "System" auto-pick). */
  mode: ThemeMode;
  /** Three swatch colors [bg, accent, accent-2] for a preview chip. */
  swatch: [string, string, string];
}

export const THEMES: ThemeDef[] = [
  {
    id: "compendium-light",
    label: "Compendium Light",
    mode: "light",
    swatch: ["#f5fafd", "#00c9de", "#6366f1"],
  },
  {
    id: "compendium-dark",
    label: "Compendium Dark",
    mode: "dark",
    swatch: ["#0a0f24", "#00c9de", "#818cf8"],
  },
];

/** The theme applied when nothing is stored / before JS hydrates. */
export const DEFAULT_THEME_ID = "compendium-light";

/** localStorage key holding the user's saved preference. */
export const THEME_STORAGE_KEY = "ts_admin_theme";

/** A preference is either a concrete theme id or "system" (follow the OS). */
export type ThemePreference = string | "system";

export function getThemeDef(id: string): ThemeDef | undefined {
  return THEMES.find((t) => t.id === id);
}

/** Pick the light or dark theme that matches an OS `prefers-color-scheme`. */
export function themeForMode(mode: ThemeMode): ThemeDef {
  return THEMES.find((t) => t.mode === mode) ?? THEMES[0];
}

/** Resolve a saved preference to a concrete theme id, using the OS when "system". */
export function resolvePreference(pref: ThemePreference, prefersDark: boolean): string {
  if (pref === "system") return themeForMode(prefersDark ? "dark" : "light").id;
  return getThemeDef(pref)?.id ?? DEFAULT_THEME_ID;
}
