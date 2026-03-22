import { useEffect, useState } from "react";
import { Plus, Trash2, CheckCircle, XCircle, Loader, Radio } from "lucide-react";
import AppShell from "@/components/Shell";
import { clustersApi } from "@/lib/api";
import type { Cluster } from "@/lib/types";

type AuthType = "basic" | "trusted" | "bearer";

const AUTH_LABELS: Record<AuthType, string> = {
  basic:   "Basic (username + password)",
  trusted: "Trusted Auth (secret key)",
  bearer:  "Bearer token",
};

const AUTH_SECRET_LABEL: Record<AuthType, string> = {
  basic:   "Password",
  trusted: "Secret key",
  bearer:  "Bearer token",
};

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ConnectionsPage() {
  const [clusters, setClusters]   = useState<Cluster[]>([]);
  const [loading, setLoading]     = useState(true);
  const [showForm, setShowForm]   = useState(false);

  const reload = () => {
    clustersApi.list().then(setClusters).finally(() => setLoading(false));
  };

  useEffect(() => { reload(); }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Remove this cluster? This cannot be undone.")) return;
    await clustersApi.delete(id);
    reload();
  };

  const handleActivate = async (id: string) => {
    await fetch(`/api/v1/clusters/${id}/activate`, { method: "POST" });
    reload();
  };

  return (
    <AppShell pageTitle="Settings — Connections">
      <div style={{ padding: 28, maxWidth: 760 }}>

        {/* Header row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div>
            <h2 style={{ fontSize: 15, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif", margin: 0 }}>
              ThoughtSpot Clusters
            </h2>
            <p style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif", marginTop: 4 }}>
              Connect to one or more ThoughtSpot instances. Credentials are stored securely in your OS keychain.
            </p>
          </div>
          {/* Only show when clusters exist — empty state has its own CTA */}
          {clusters.length > 0 && (
            <button
              onClick={() => setShowForm(true)}
              style={{
                display: "flex", alignItems: "center", gap: 6, padding: "7px 14px",
                background: "#8B5CF6", color: "white", border: "none", borderRadius: 6,
                fontSize: 13, fontWeight: 500, cursor: "pointer", fontFamily: "Geist, sans-serif",
                flexShrink: 0,
              }}
            >
              <Plus size={14} /> Add cluster
            </button>
          )}
        </div>

        {/* Cluster list */}
        {loading ? (
          <p style={{ fontSize: 13, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>Loading…</p>
        ) : clusters.length === 0 ? (
          <EmptyState onAdd={() => setShowForm(true)} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {clusters.map((c) => (
              <ClusterRow
                key={c.id}
                cluster={c}
                onDelete={() => handleDelete(c.id)}
                onActivate={() => handleActivate(c.id)}
              />
            ))}
          </div>
        )}

        {/* Add cluster slide-in panel */}
        {showForm && (
          <AddClusterPanel
            onClose={() => setShowForm(false)}
            onSaved={() => { setShowForm(false); reload(); }}
          />
        )}
      </div>
    </AppShell>
  );
}

// ── Cluster row ───────────────────────────────────────────────────────────────

function ClusterRow({ cluster, onDelete, onActivate }: {
  cluster: Cluster;
  onDelete: () => void;
  onActivate: () => void;
}) {
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [testError, setTestError] = useState("");

  const handleTest = async () => {
    setTestState("testing");
    setTestError("");
    try {
      const res = await clustersApi.testConnection(cluster.id);
      setTestState(res.success ? "ok" : "fail");
      if (!res.success) setTestError(res.error ?? "Connection failed");
    } catch (e: any) {
      setTestState("fail");
      setTestError(e.message ?? "Connection failed");
    }
  };

  return (
    <div style={{
      background: "#FAF8F4", border: `1px solid ${cluster.is_active ? "#8B5CF6" : "#E8E1D5"}`,
      borderRadius: 8, padding: "16px 20px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>

        {/* Active indicator */}
        <div style={{
          width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
          background: cluster.is_active ? "#8B5CF6" : "#E8E1D5",
        }} />

        {/* Cluster info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
              {cluster.name}
            </span>
            {cluster.is_active && (
              <span style={{
                fontSize: 10, fontWeight: 500, color: "#6D28D9",
                background: "#EDE9FE", padding: "1px 7px", borderRadius: 10,
                fontFamily: "Geist, sans-serif", textTransform: "uppercase", letterSpacing: "0.05em",
              }}>
                Active
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif", marginTop: 2 }}>
            {cluster.url} · {cluster.username} · {AUTH_LABELS[cluster.auth_type as AuthType] ?? cluster.auth_type}
          </div>
        </div>

        {/* Test connection */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {testState === "ok" && <CheckCircle size={14} color="#059669" />}
          {testState === "fail" && <XCircle size={14} color="#DC2626" />}
          <button
            onClick={handleTest}
            disabled={testState === "testing"}
            style={{
              padding: "5px 11px", borderRadius: 5, fontSize: 12, fontWeight: 500,
              border: "1px solid #E8E1D5", background: "transparent", cursor: "pointer",
              color: "#1A1714", fontFamily: "Geist, sans-serif",
              opacity: testState === "testing" ? 0.6 : 1,
            }}
          >
            {testState === "testing" ? "Testing…" : "Test connection"}
          </button>
        </div>

        {/* Activate */}
        {!cluster.is_active && (
          <button
            onClick={onActivate}
            style={{
              padding: "5px 11px", borderRadius: 5, fontSize: 12, fontWeight: 500,
              border: "1px solid #8B5CF6", background: "transparent", cursor: "pointer",
              color: "#8B5CF6", fontFamily: "Geist, sans-serif",
            }}
          >
            Set active
          </button>
        )}

        {/* Delete */}
        <button
          onClick={onDelete}
          style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: "#A89E96" }}
        >
          <Trash2 size={14} />
        </button>
      </div>

      {testState === "fail" && testError && (
        <p style={{ margin: "8px 0 0 20px", fontSize: 12, color: "#DC2626", fontFamily: "Geist, sans-serif" }}>
          {testError}
        </p>
      )}
    </div>
  );
}

// ── Empty state ───────────────────────────────────────────────────────────────

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <div style={{
      border: "2px dashed #E8E1D5", borderRadius: 10, padding: "48px 32px",
      textAlign: "center",
    }}>
      <h3 style={{ fontSize: 15, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif", margin: "0 0 8px" }}>
        No clusters configured
      </h3>
      <p style={{ fontSize: 13, color: "#7A7068", fontFamily: "Geist, sans-serif", margin: "0 0 20px" }}>
        Add your first ThoughtSpot instance to get started.
      </p>
      <button
        onClick={onAdd}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 16px",
          background: "#8B5CF6", color: "white", border: "none", borderRadius: 6,
          fontSize: 13, fontWeight: 500, cursor: "pointer", fontFamily: "Geist, sans-serif",
        }}
      >
        <Plus size={14} /> Add cluster
      </button>
    </div>
  );
}

// ── Add cluster panel ─────────────────────────────────────────────────────────

function AddClusterPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({
    name: "", url: "", username: "", auth_type: "basic" as AuthType, credential: "",
  });
  const [testState, setTestState] = useState<"idle" | "testing" | "ok" | "fail">("idle");
  const [testMsg, setTestMsg]     = useState("");
  const [saving, setSaving]       = useState(false);
  const [error, setError]         = useState("");

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleTest = async () => {
    setTestState("testing");
    setTestMsg("");
    // Create a temp cluster to test, then delete it
    const tempId = `temp-${Date.now()}`;
    try {
      await clustersApi.create({ ...form, id: tempId });
      const res = await clustersApi.testConnection(tempId);
      await clustersApi.delete(tempId);
      if (res.success) {
        setTestState("ok");
        setTestMsg(`Connected — ThoughtSpot ${res.ts_version ?? ""}`);
      } else {
        setTestState("fail");
        setTestMsg(res.error ?? "Connection failed");
      }
    } catch (e: any) {
      try { await clustersApi.delete(tempId); } catch {}
      setTestState("fail");
      setTestMsg(e.message ?? "Connection failed");
    }
  };

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      const id = form.name.toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "");
      await clustersApi.create({ ...form, id });
      onSaved();
    } catch (e: any) {
      setError(e.message ?? "Failed to save cluster");
    } finally {
      setSaving(false);
    }
  };

  const canSave = form.name && form.url && form.username && form.credential;

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.3)", zIndex: 40 }} />

      {/* Panel */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 420,
        background: "#FAF8F4", borderLeft: "1px solid #E8E1D5",
        zIndex: 50, display: "flex", flexDirection: "column",
        boxShadow: "-8px 0 32px rgba(0,0,0,0.08)",
      }}>
        {/* Header */}
        <div style={{ padding: "20px 24px 16px", borderBottom: "1px solid #E8E1D5" }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
            Add cluster
          </h3>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
            Credentials are saved to your OS keychain.
          </p>
        </div>

        {/* Form */}
        <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px", display: "flex", flexDirection: "column", gap: 16 }}>

          <Field label="Name" hint="e.g. Production, Staging">
            <input value={form.name} onChange={set("name")} placeholder="Production" {...inputStyle} />
          </Field>

          <Field label="ThoughtSpot URL" hint="Must be HTTPS">
            <input value={form.url} onChange={set("url")} placeholder="https://company.thoughtspot.cloud" type="url" {...inputStyle} />
          </Field>

          <Field label="Username">
            <input value={form.username} onChange={set("username")} placeholder="admin@company.com" {...inputStyle} />
          </Field>

          <Field label="Auth method">
            <select value={form.auth_type} onChange={set("auth_type")} {...inputStyle}>
              <option value="basic">Basic (username + password)</option>
              <option value="trusted">Trusted Auth (secret key)</option>
              <option value="bearer">Bearer token</option>
            </select>
          </Field>

          <Field label={AUTH_SECRET_LABEL[form.auth_type]}>
            <input value={form.credential} onChange={set("credential")} type="password" placeholder="••••••••" {...inputStyle} />
          </Field>

          {/* Test result */}
          {testState !== "idle" && (
            <div style={{
              padding: "10px 12px", borderRadius: 6, fontSize: 12, fontFamily: "Geist, sans-serif",
              background: testState === "ok" ? "#D1FAE5" : testState === "fail" ? "#FEE2E2" : "#F0EBE3",
              color: testState === "ok" ? "#059669" : testState === "fail" ? "#DC2626" : "#7A7068",
            }}>
              {testState === "testing" ? "Testing connection…" : testMsg}
            </div>
          )}

          {error && (
            <div style={{ padding: "10px 12px", borderRadius: 6, fontSize: 12, color: "#DC2626", background: "#FEE2E2", fontFamily: "Geist, sans-serif" }}>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "16px 24px", borderTop: "1px solid #E8E1D5", display: "flex", gap: 10 }}>
          <button
            onClick={handleTest}
            disabled={!canSave || testState === "testing"}
            style={{
              flex: 1, padding: "8px 0", borderRadius: 6, fontSize: 13, fontWeight: 500,
              border: "1px solid #E8E1D5", background: "transparent", cursor: "pointer",
              color: "#1A1714", fontFamily: "Geist, sans-serif",
              opacity: !canSave || testState === "testing" ? 0.5 : 1,
            }}
          >
            Test connection
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave || saving}
            style={{
              flex: 1, padding: "8px 0", borderRadius: 6, fontSize: 13, fontWeight: 500,
              border: "none", background: "#8B5CF6", color: "white", cursor: "pointer",
              fontFamily: "Geist, sans-serif",
              opacity: !canSave || saving ? 0.5 : 1,
            }}
          >
            {saving ? "Saving…" : "Save cluster"}
          </button>
        </div>
      </div>
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label style={{ fontSize: 12, fontWeight: 500, color: "#1A1714", fontFamily: "Geist, sans-serif", display: "block", marginBottom: 6 }}>
        {label}
        {hint && <span style={{ fontWeight: 400, color: "#7A7068", marginLeft: 6 }}>{hint}</span>}
      </label>
      {children}
    </div>
  );
}

const inputStyle = {
  style: {
    width: "100%", height: 34, padding: "0 10px", borderRadius: 6,
    border: "1px solid #E8E1D5", background: "#FFFFFF",
    fontSize: 13, color: "#1A1714", fontFamily: "Geist, sans-serif",
    outline: "none", boxSizing: "border-box" as const,
  }
};
