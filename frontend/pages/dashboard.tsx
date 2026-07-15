import AppShell from "@/components/Shell";
import { LayoutDashboard } from "lucide-react";
import { theme } from "@/lib/theme";

export default function DashboardPage() {
  return (
    <AppShell pageTitle="Dashboard">
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        height: "100%", padding: 48,
      }}>
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center", gap: 16,
          maxWidth: 440, textAlign: "center",
        }}>
          <div style={{
            width: 64, height: 64, borderRadius: 16,
            background: theme.color.surface, border: `1px solid ${theme.color.border}`,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <LayoutDashboard size={32} style={{ color: theme.color.accent }} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 8 }}>
              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
                Dashboard
              </h2>
              <span style={{
                padding: "2px 8px", borderRadius: 20, fontSize: 11, fontWeight: 600,
                background: theme.color.accentSoft, color: theme.color.accent2, fontFamily: theme.font.sans,
                letterSpacing: "0.04em",
              }}>
                Coming soon
              </span>
            </div>
            <p style={{
              margin: 0, fontSize: 14, color: theme.color.textMuted,
              fontFamily: theme.font.sans, lineHeight: 1.6,
            }}>
              An overview of your ThoughtSpot cluster — sync status, stale content counts, active users, and recent admin activity at a glance.
            </p>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
