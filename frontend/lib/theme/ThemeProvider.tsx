/**
 * ThemeProvider — owns the active theme and persists the user's preference.
 *
 * Wrap the app once (in _app.tsx). Any component can then read/switch the theme:
 *
 *   const { preference, resolvedId, setPreference, themes } = useTheme();
 *   setPreference("compendium-dark");   // or "system"
 *
 * The provider writes `data-theme` on <html>, which re-points every CSS variable
 * in theme.css. A tiny inline script in _document.tsx sets `data-theme` before
 * first paint from the same localStorage key, so there's no flash of the wrong
 * theme on load.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  DEFAULT_THEME_ID,
  THEME_STORAGE_KEY,
  THEMES,
  resolvePreference,
  type ThemeDef,
  type ThemePreference,
} from "./registry";

interface ThemeContextValue {
  /** The saved preference: a concrete theme id or "system". */
  preference: ThemePreference;
  /** The concrete theme id currently applied to <html>. */
  resolvedId: string;
  /** All selectable themes (for a switcher UI). */
  themes: ThemeDef[];
  /** Change and persist the preference. Pass "system" to follow the OS. */
  setPreference: (pref: ThemePreference) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function prefersDark(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function readStoredPreference(): ThemePreference {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "system") return "system";
    if (raw && THEMES.some((t) => t.id === raw)) return raw;
  } catch {
    /* localStorage unavailable */
  }
  return DEFAULT_THEME_ID;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  // Start from the default so server and first client render agree (the
  // _document script has already painted the right theme). We reconcile with
  // localStorage in the effect below.
  const [preference, setPreferenceState] = useState<ThemePreference>(DEFAULT_THEME_ID);

  const applyPreference = useCallback((pref: ThemePreference) => {
    const resolved = resolvePreference(pref, prefersDark());
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", resolved);
    }
  }, []);

  // On mount, adopt the stored preference.
  useEffect(() => {
    const stored = readStoredPreference();
    setPreferenceState(stored);
    applyPreference(stored);
  }, [applyPreference]);

  // While on "system", follow live OS changes.
  useEffect(() => {
    if (preference !== "system" || typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyPreference("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [preference, applyPreference]);

  const setPreference = useCallback(
    (pref: ThemePreference) => {
      setPreferenceState(pref);
      applyPreference(pref);
      try {
        localStorage.setItem(THEME_STORAGE_KEY, pref);
      } catch {
        /* localStorage unavailable — theme still applies for this session */
      }
    },
    [applyPreference],
  );

  const value = useMemo<ThemeContextValue>(
    () => ({
      preference,
      resolvedId: resolvePreference(preference, prefersDark()),
      themes: THEMES,
      setPreference,
    }),
    [preference, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    // Safe fallback so a component used outside the provider never crashes.
    return {
      preference: DEFAULT_THEME_ID,
      resolvedId: DEFAULT_THEME_ID,
      themes: THEMES,
      setPreference: () => undefined,
    };
  }
  return ctx;
}
