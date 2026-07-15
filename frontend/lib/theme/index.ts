/**
 * Theme system — public surface.
 *
 *   import { theme, ThemeProvider, useTheme, THEMES } from "@/lib/theme";
 *
 * - `theme`          typed design tokens (var(--…) refs) for inline styles
 * - `ThemeProvider`  wraps the app, owns/persists the active theme
 * - `useTheme`       read + switch the theme from any component
 * - `THEMES`         the registry of available themes
 *
 * See styles/theme.css for the CSS-variable source of truth.
 */

export { theme, type Theme } from "./tokens";
export { ThemeProvider, useTheme } from "./ThemeProvider";
export {
  THEMES,
  DEFAULT_THEME_ID,
  THEME_STORAGE_KEY,
  getThemeDef,
  themeForMode,
  resolvePreference,
  type ThemeDef,
  type ThemeMode,
  type ThemePreference,
} from "./registry";
