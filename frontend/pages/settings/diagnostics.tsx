import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import AppShell from "@/components/Shell";
import { SettingsTabs } from "@/components/SettingsTabs";
import { diagnosticsApi } from "@/lib/api";

export default function DiagnosticsPage() {
  const [logs, setLogs] = useState<string>("");
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  const loadLogs = async () => {
    setLoadingLogs(true);
    setLogError(null);
    try {
      const text = await diagnosticsApi.tailLogs(500);
      setLogs(text);
    } catch (e) {
      setLogError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingLogs(false);
    }
  };

  useEffect(() => { loadLogs(); }, []);

  return (
    <AppShell pageTitle="Settings — Diagnostics">
      <div style={{ padding: 28, maxWidth: 920 }}>
        <SettingsTabs current="diagnostics" />

        {/* Support bundle */}
        <section style={{
          background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 8,
          padding: 18, marginBottom: 22,
        }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1A1714", margin: 0, fontFamily: "Geist, sans-serif" }}>
            Hit a bug? Send us a support bundle.
          </h3>
          <p style={{ fontSize: 12, color: "#574F47", lineHeight: 1.6, margin: "8px 0 14px", fontFamily: "Geist, sans-serif" }}>
            Click the button to download a zip with recent application logs,
            the most recent failed jobs (with full tracebacks), and the app
            version. It does <strong>not</strong> include passwords, API
            tokens, or your ThoughtSpot data — open it before sending if
            you'd like to verify.
          </p>
          <a
            href={diagnosticsApi.bundleUrl()}
            style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              padding: "8px 14px", fontSize: 13, fontWeight: 500,
              background: "#8B5CF6", border: "1px solid #6D28D9", borderRadius: 6,
              color: "white", textDecoration: "none",
              fontFamily: "Geist, sans-serif",
            }}
          >
            <Download size={14} />
            Download support bundle
          </a>
        </section>

        {/* Recent logs */}
        <section style={{
          background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 8,
          padding: 18,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: "#1A1714", margin: 0, fontFamily: "Geist, sans-serif" }}>
                Recent logs (last 500 lines)
              </h3>
              <p style={{ fontSize: 11, color: "#7A7068", margin: "4px 0 0", fontFamily: "Geist, sans-serif" }}>
                Tail of <code style={{ fontSize: 11 }}>~/.ts-admin-toolkit/logs/app.log</code>.
              </p>
            </div>
            <button
              onClick={loadLogs}
              disabled={loadingLogs}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "6px 12px", fontSize: 12, fontWeight: 500,
                background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 6,
                cursor: loadingLogs ? "wait" : "pointer",
                color: "#1A1714", fontFamily: "Geist, sans-serif",
                opacity: loadingLogs ? 0.6 : 1,
              }}
            >
              <RefreshCw size={12} />
              {loadingLogs ? "Loading…" : "Refresh"}
            </button>
          </div>

          {logError ? (
            <div style={{
              padding: 12, background: "#FEF2F2", border: "1px solid #FECACA",
              borderRadius: 6, color: "#991B1B", fontSize: 12,
              fontFamily: "Geist, sans-serif",
            }}>
              Couldn't load logs: {logError}
            </div>
          ) : (
            <pre style={{
              background: "#1A1714", color: "#F3F0EA",
              padding: 14, borderRadius: 6, fontSize: 11.5, lineHeight: 1.5,
              fontFamily: "Geist Mono, monospace",
              maxHeight: 480, overflow: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              margin: 0,
            }}>{logs || "(no log file yet)"}</pre>
          )}
        </section>
      </div>
    </AppShell>
  );
}


