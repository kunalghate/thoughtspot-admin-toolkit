import Link from "next/link";
import { theme } from "@/lib/theme";

type SettingsTab = "connections" | "diagnostics" | "appearance";

const TABS: { key: SettingsTab; href: string; label: string }[] = [
  { key: "connections", href: "/settings/connections", label: "Connections" },
  { key: "diagnostics", href: "/settings/diagnostics", label: "Diagnostics" },
  { key: "appearance", href: "/settings/appearance", label: "Appearance" },
];

// Underline tabs — same pattern as Users/Sharing/Deleter/Jobs, so the whole
// app has one tab style (see docs/dev/DESIGN.md, "Known debts").
export function SettingsTabs({ current }: { current: SettingsTab }) {
  return (
    <div style={{
      display: "flex", marginBottom: 22,
      borderBottom: `1px solid ${theme.color.border}`,
    }}>
      {TABS.map(({ key, href, label }) => {
        const active = current === key;
        return (
          <Link
            key={key}
            href={href}
            style={{
              padding: "10px 18px",
              fontSize: 13, fontWeight: active ? 600 : 400,
              color: active ? theme.color.accent2 : theme.color.textMuted,
              borderBottom: active ? `2px solid ${theme.color.accent2}` : "2px solid transparent",
              marginBottom: -1, textDecoration: "none",
              fontFamily: theme.font.sans,
            }}
          >{label}</Link>
        );
      })}
    </div>
  );
}
