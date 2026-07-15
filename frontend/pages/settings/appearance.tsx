import { Check, Monitor } from "lucide-react";
import AppShell from "@/components/Shell";
import { SettingsTabs } from "@/components/SettingsTabs";
import { theme, useTheme, type ThemePreference } from "@/lib/theme";

/**
 * Appearance settings — switch the app theme.
 *
 * This page is intentionally thin: it just reads the theme registry and calls
 * `setPreference`. Adding a new theme in `styles/theme.css` + `lib/theme/registry.ts`
 * makes it appear here automatically — no changes needed to this file.
 */
export default function AppearancePage() {
  const { preference, resolvedId, themes, setPreference } = useTheme();

  // "System" plus one card per registered theme.
  const options: { key: ThemePreference; label: string; hint: string; swatch: [string, string, string] | null; system?: boolean }[] = [
    { key: "system", label: "System", hint: "Follow your OS light/dark setting", swatch: null, system: true },
    ...themes.map((t) => ({
      key: t.id as ThemePreference,
      label: t.label,
      hint: t.mode === "dark" ? "Dark" : "Light",
      swatch: t.swatch,
    })),
  ];

  return (
    <AppShell pageTitle="Settings">
      <div style={{ padding: "28px 32px", maxWidth: 760 }}>
        <SettingsTabs current="appearance" />

        <h2 style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, margin: "0 0 4px", fontFamily: theme.font.sans }}>
          Theme
        </h2>
        <p style={{ fontSize: 13, color: theme.color.textMuted, margin: "0 0 20px", fontFamily: theme.font.sans }}>
          Choose how the toolkit looks. Your choice is saved on this device.
        </p>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 14 }}>
          {options.map((opt) => {
            const selected = preference === opt.key;
            // When "System" is selected, mark which concrete theme is currently active.
            const isActiveResolved = opt.system && preference === "system"
              ? false
              : opt.key === resolvedId && preference === "system";

            return (
              <button
                key={opt.key}
                onClick={() => setPreference(opt.key)}
                style={{
                  position: "relative",
                  display: "flex", flexDirection: "column", gap: 12,
                  padding: 16, textAlign: "left", cursor: "pointer",
                  background: theme.color.surface,
                  border: `1px solid ${selected ? theme.color.accent : theme.color.border}`,
                  borderRadius: theme.radius.card,
                  boxShadow: selected ? theme.focus.ring : theme.shadow.xs,
                  transition: "border-color .18s, box-shadow .18s",
                  fontFamily: theme.font.sans,
                }}
              >
                {/* Preview swatch */}
                <div style={{ display: "flex", alignItems: "center", gap: 10, height: 44 }}>
                  {opt.swatch ? (
                    <div style={{
                      flex: 1, height: 44, borderRadius: 7, overflow: "hidden",
                      border: `1px solid ${theme.color.border}`,
                      background: opt.swatch[0], position: "relative",
                    }}>
                      <div style={{
                        position: "absolute", left: 10, top: 10, bottom: 10, width: 6, borderRadius: 3,
                        background: `linear-gradient(180deg, ${opt.swatch[1]}, ${opt.swatch[2]})`,
                      }} />
                      <div style={{
                        position: "absolute", right: 10, top: 12, height: 10, width: 46, borderRadius: 5,
                        background: `linear-gradient(135deg, ${opt.swatch[1]}, ${opt.swatch[2]})`,
                      }} />
                      <div style={{
                        position: "absolute", right: 10, bottom: 12, height: 6, width: 30, borderRadius: 3,
                        background: opt.swatch[2], opacity: 0.5,
                      }} />
                    </div>
                  ) : (
                    <div style={{
                      flex: 1, height: 44, borderRadius: 7,
                      border: `1px dashed ${theme.color.borderLight}`,
                      display: "grid", placeItems: "center", color: theme.color.textMuted,
                    }}>
                      <Monitor size={20} />
                    </div>
                  )}
                </div>

                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: theme.color.textPrimary }}>
                      {opt.label}
                    </div>
                    <div style={{ fontSize: 11, color: theme.color.textMuted, marginTop: 1 }}>
                      {opt.hint}{isActiveResolved ? " · active now" : ""}
                    </div>
                  </div>
                  {selected && (
                    <div style={{
                      width: 20, height: 20, borderRadius: theme.radius.pill,
                      background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
                      display: "grid", placeItems: "center", flexShrink: 0,
                    }}>
                      <Check size={12} color={theme.color.onAccent} strokeWidth={3} />
                    </div>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </AppShell>
  );
}
