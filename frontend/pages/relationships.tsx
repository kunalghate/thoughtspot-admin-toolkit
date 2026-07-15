import AppShell from "@/components/Shell";
import { GitFork } from "lucide-react";
import { theme } from "@/lib/theme";

export default function RelationshipsPage() {
  return (
    <AppShell pageTitle="Relationships">
      <ComingSoon
        icon={<GitFork size={32} style={{ color: theme.color.accent }} />}
        title="Relationship Visualizer"
        description="Explore dependencies between Liveboards, Answers, Worksheets, and Tables. Visualize lineage graphs and understand the impact of changes before you make them."
      />
    </AppShell>
  );
}

function ComingSoon({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
  return (
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
          {icon}
        </div>
        <div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 8 }}>
            <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
              {title}
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
            {description}
          </p>
        </div>
      </div>
    </div>
  );
}
