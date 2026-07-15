import { useEffect, useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import AppShell from "@/components/Shell";
import { SettingsTabs } from "@/components/SettingsTabs";
import { theme } from "@/lib/theme";
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
          background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 8,
          padding: 18, marginBottom: 22,
        }}>
          <h3 style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, margin: 0, fontFamily: theme.font.sans }}>
            Hit a bug? Send us a support bundle.
          </h3>
          <p style={{ fontSize: 12, color: theme.color.textSecondary, lineHeight: 1.6, margin: "8px 0 14px", fontFamily: theme.font.sans }}>
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
              background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
              border: `1px solid ${theme.color.accent}`, borderRadius: 6,
              color: theme.color.onAccent, textDecoration: "none",
              fontFamily: theme.font.sans,
            }}
          >
            <Download size={14} />
            Download support bundle
          </a>
        </section>

        {/* Recent logs */}
        <section style={{
          background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 8,
          padding: 18,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
            <div>
              <h3 style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, margin: 0, fontFamily: theme.font.sans }}>
                Recent logs (last 500 lines)
              </h3>
              <p style={{ fontSize: 11, color: theme.color.textMuted, margin: "4px 0 0", fontFamily: theme.font.sans }}>
                Tail of <code style={{ fontSize: 11 }}>~/.ts-admin-toolkit/logs/app.log</code>.
              </p>
            </div>
            <button
              onClick={loadLogs}
              disabled={loadingLogs}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                padding: "6px 12px", fontSize: 12, fontWeight: 500,
                background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
                cursor: loadingLogs ? "wait" : "pointer",
                color: theme.color.textPrimary, fontFamily: theme.font.sans,
                opacity: loadingLogs ? 0.6 : 1,
              }}
            >
              <RefreshCw size={12} />
              {loadingLogs ? "Loading…" : "Refresh"}
            </button>
          </div>

          {logError ? (
            <div style={{
              padding: 12, background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`,
              borderRadius: 6, color: theme.color.danger, fontSize: 12,
              fontFamily: theme.font.sans,
            }}>
              Couldn't load logs: {logError}
            </div>
          ) : (
            <pre style={{
              background: theme.color.codeBg, color: theme.color.codeFg,
              padding: 14, borderRadius: 6, fontSize: 11.5, lineHeight: 1.5,
              fontFamily: theme.font.mono,
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


