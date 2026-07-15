import Link from "next/link";
import { theme } from "@/lib/theme";

type SettingsTab = "connections" | "diagnostics" | "appearance";

const TABS: { key: SettingsTab; href: string; label: string }[] = [
  { key: "connections", href: "/settings/connections", label: "Connections" },
  { key: "diagnostics", href: "/settings/diagnostics", label: "Diagnostics" },
  { key: "appearance", href: "/settings/appearance", label: "Appearance" },
];

export function SettingsTabs({ current }: { current: SettingsTab }) {
  return (
    <div style={{
      display: "flex", gap: 6, marginBottom: 22,
      borderBottom: `1px solid ${theme.color.border}`, paddingBottom: 10,
    }}>
      {TABS.map(({ key, href, label }) => {
        const active = current === key;
        return (
          <Link
            key={key}
            href={href}
            style={{
              padding: "8px 14px",
              fontSize: 13, fontWeight: active ? 600 : 500,
              color: active ? theme.color.accent2 : theme.color.textSecondary,
              background: active ? theme.color.accentSoft : "transparent",
              borderRadius: 6, textDecoration: "none",
              fontFamily: theme.font.sans,
            }}
          >{label}</Link>
        );
      })}
    </div>
  );
}
