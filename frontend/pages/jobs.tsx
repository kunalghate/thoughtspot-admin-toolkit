import AppShell from "@/components/Shell";
import { Briefcase } from "lucide-react";

export default function JobsPage() {
  return (
    <AppShell pageTitle="Jobs">
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
            background: "#FAF8F4", border: "1px solid #E8E1D5",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <Briefcase size={32} style={{ color: "#8B5CF6" }} />
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 8 }}>
              <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
                Jobs
              </h2>
              <span style={{
                padding: "2px 8px", borderRadius: 20, fontSize: 11, fontWeight: 600,
                background: "#EDE9FE", color: "#6D28D9", fontFamily: "Geist, sans-serif",
                letterSpacing: "0.04em",
              }}>
                Coming soon
              </span>
            </div>
            <p style={{
              margin: 0, fontSize: 14, color: "#7A7068",
              fontFamily: "Geist, sans-serif", lineHeight: 1.6,
            }}>
              Monitor all background jobs — syncs, archives, tag operations, and restores. View progress, status, and error details in one place.
            </p>
          </div>
        </div>
      </div>
    </AppShell>
  );
}
