import Link from "next/link";

type SettingsTab = "connections" | "diagnostics";

const TABS: { key: SettingsTab; href: string; label: string }[] = [
  { key: "connections", href: "/settings/connections", label: "Connections" },
  { key: "diagnostics", href: "/settings/diagnostics", label: "Diagnostics" },
];

export function SettingsTabs({ current }: { current: SettingsTab }) {
  return (
    <div style={{
      display: "flex", gap: 6, marginBottom: 22,
      borderBottom: "1px solid #E8E1D5", paddingBottom: 10,
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
              color: active ? "#6D28D9" : "#574F47",
              background: active ? "#EDE9FE" : "transparent",
              borderRadius: 6, textDecoration: "none",
              fontFamily: "Geist, sans-serif",
            }}
          >{label}</Link>
        );
      })}
    </div>
  );
}
