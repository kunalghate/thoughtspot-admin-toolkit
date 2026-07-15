import { useEffect, useState } from "react";
import { Plus, Trash2, CheckCircle, XCircle, Pencil } from "lucide-react";
import AppShell from "@/components/Shell";
import { SettingsTabs } from "@/components/SettingsTabs";
import { theme } from "@/lib/theme";
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
  const [clusters, setClusters]     = useState<Cluster[]>([]);
  const [loading, setLoading]       = useState(true);
  const [showForm, setShowForm]     = useState(false);
  const [editingCluster, setEditing] = useState<Cluster | null>(null);

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
        <SettingsTabs current="connections" />

        {/* Header row */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div>
            <h2 style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans, margin: 0 }}>
              ThoughtSpot Clusters
            </h2>
            <p style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, marginTop: 4 }}>
              Connect to one or more ThoughtSpot instances. Credentials are stored securely in your OS keychain.
            </p>
          </div>
          {/* Only show when clusters exist — empty state has its own CTA */}
          {clusters.length > 0 && (
            <button
              onClick={() => setShowForm(true)}
              style={{
                display: "flex", alignItems: "center", gap: 6, padding: "7px 14px",
                background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
                color: theme.color.onAccent, border: "none", borderRadius: 6,
                fontSize: 13, fontWeight: 500, cursor: "pointer", fontFamily: theme.font.sans,
                flexShrink: 0,
              }}
            >
              <Plus size={14} /> Add cluster
            </button>
          )}
        </div>

        {/* Cluster list */}
        {loading ? (
          <p style={{ fontSize: 13, color: theme.color.textMuted, fontFamily: theme.font.sans }}>Loading…</p>
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
                onEdit={() => setEditing(c)}
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

        {/* Edit cluster slide-in panel */}
        {editingCluster && (
          <EditClusterPanel
            cluster={editingCluster}
            onClose={() => setEditing(null)}
            onSaved={() => { setEditing(null); reload(); }}
          />
        )}
      </div>
    </AppShell>
  );
}

// ── Cluster row ───────────────────────────────────────────────────────────────

function ClusterRow({ cluster, onDelete, onActivate, onEdit }: {
  cluster: Cluster;
  onDelete: () => void;
  onActivate: () => void;
  onEdit: () => void;
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
      background: theme.color.surface, border: `1px solid ${cluster.is_active ? theme.color.accent : theme.color.border}`,
      borderRadius: 8, padding: "16px 20px",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>

        {/* Active indicator */}
        <div style={{
          width: 8, height: 8, borderRadius: "50%", flexShrink: 0,
          background: cluster.is_active ? theme.color.accent : theme.color.border,
        }} />

        {/* Cluster info */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
              {cluster.name}
            </span>
            {cluster.is_active && (
              <span style={{
                fontSize: 10, fontWeight: 500, color: theme.color.accent2,
                background: theme.color.accentSoft, padding: "1px 7px", borderRadius: 10,
                fontFamily: theme.font.sans, textTransform: "uppercase", letterSpacing: "0.05em",
              }}>
                Active
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, marginTop: 2 }}>
            {cluster.url} · {cluster.username} · {AUTH_LABELS[cluster.auth_type as AuthType] ?? cluster.auth_type}
          </div>
        </div>

        {/* Test connection */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          {testState === "ok" && <CheckCircle size={14} color={theme.color.success} />}
          {testState === "fail" && <XCircle size={14} color={theme.color.danger} />}
          <button
            onClick={handleTest}
            disabled={testState === "testing"}
            style={{
              padding: "5px 11px", borderRadius: 5, fontSize: 12, fontWeight: 500,
              border: `1px solid ${theme.color.border}`, background: "transparent", cursor: "pointer",
              color: theme.color.textPrimary, fontFamily: theme.font.sans,
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
              border: `1px solid ${theme.color.accent}`, background: "transparent", cursor: "pointer",
              color: theme.color.accent2, fontFamily: theme.font.sans,
            }}
          >
            Set active
          </button>
        )}

        {/* Edit */}
        <button
          onClick={onEdit}
          style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: theme.color.textMuted }}
          title="Edit cluster"
        >
          <Pencil size={14} />
        </button>

        {/* Delete */}
        <button
          onClick={onDelete}
          style={{ background: "none", border: "none", cursor: "pointer", padding: 4, color: theme.color.textMuted }}
          title="Remove cluster"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {testState === "fail" && testError && (
        <p style={{ margin: "8px 0 0 20px", fontSize: 12, color: theme.color.danger, fontFamily: theme.font.sans }}>
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
      border: `2px dashed ${theme.color.border}`, borderRadius: 10, padding: "48px 32px",
      textAlign: "center",
    }}>
      <h3 style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans, margin: "0 0 8px" }}>
        No clusters configured
      </h3>
      <p style={{ fontSize: 13, color: theme.color.textMuted, fontFamily: theme.font.sans, margin: "0 0 20px" }}>
        Add your first ThoughtSpot instance to get started.
      </p>
      <button
        onClick={onAdd}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6, padding: "8px 16px",
          background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
          color: theme.color.onAccent, border: "none", borderRadius: 6,
          fontSize: 13, fontWeight: 500, cursor: "pointer", fontFamily: theme.font.sans,
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
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 40 }} />

      {/* Panel */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 420,
        background: theme.color.surface, borderLeft: `1px solid ${theme.color.border}`,
        zIndex: 50, display: "flex", flexDirection: "column",
        boxShadow: theme.shadow.lg,
      }}>
        {/* Header */}
        <div style={{ padding: "20px 24px 16px", borderBottom: `1px solid ${theme.color.border}` }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
            Add cluster
          </h3>
          <p style={{ margin: "4px 0 0", fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
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
              padding: "10px 12px", borderRadius: 6, fontSize: 12, fontFamily: theme.font.sans,
              background: testState === "ok" ? theme.color.successSoft : testState === "fail" ? theme.color.dangerSoft : theme.color.surface2,
              color: testState === "ok" ? theme.color.success : testState === "fail" ? theme.color.danger : theme.color.textMuted,
            }}>
              {testState === "testing" ? "Testing connection…" : testMsg}
            </div>
          )}

          {error && (
            <div style={{ padding: "10px 12px", borderRadius: 6, fontSize: 12, color: theme.color.danger, background: theme.color.dangerSoft, fontFamily: theme.font.sans }}>
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "16px 24px", borderTop: `1px solid ${theme.color.border}`, display: "flex", gap: 10 }}>
          <button
            onClick={handleTest}
            disabled={!canSave || testState === "testing"}
            style={{
              flex: 1, padding: "8px 0", borderRadius: 6, fontSize: 13, fontWeight: 500,
              border: `1px solid ${theme.color.border}`, background: "transparent", cursor: "pointer",
              color: theme.color.textPrimary, fontFamily: theme.font.sans,
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
              border: "none", background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
              color: theme.color.onAccent, cursor: "pointer",
              fontFamily: theme.font.sans,
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

// ── Edit cluster panel ────────────────────────────────────────────────────────

function EditClusterPanel({ cluster, onClose, onSaved }: {
  cluster: Cluster;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [form, setForm] = useState({
    name: cluster.name,
    url: cluster.url,
    username: cluster.username,
    auth_type: cluster.auth_type as AuthType,
    credential: "",   // blank = keep existing
  });
  const [saving, setSaving] = useState(false);
  const [error, setError]   = useState("");

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const handleSave = async () => {
    setError("");
    setSaving(true);
    try {
      await clustersApi.update(cluster.id, {
        name: form.name,
        url: form.url,
        username: form.username,
        auth_type: form.auth_type,
        credential: form.credential || undefined,  // omit if blank → keep existing
      });
      onSaved();
    } catch (e: any) {
      setError(e.message ?? "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const panelStyle: React.CSSProperties = {
    position: "fixed", inset: 0, zIndex: 50,
    display: "flex", justifyContent: "flex-end",
  };

  return (
    <>
      <div onClick={onClose} style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 49 }} />
      <div style={panelStyle}>
        <div style={{
          width: 420, height: "100%", background: theme.color.surface,
          borderLeft: `1px solid ${theme.color.border}`, padding: 28,
          display: "flex", flexDirection: "column", gap: 20,
          overflowY: "auto", zIndex: 50, fontFamily: theme.font.sans,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: theme.color.textPrimary }}>Edit cluster</h3>
            <button onClick={onClose} style={{ background: "none", border: "none", cursor: "pointer", color: theme.color.textMuted, fontSize: 18 }}>✕</button>
          </div>

          <Field label="Display name">
            <input value={form.name} onChange={set("name")} placeholder="Production" {...inputStyle} />
          </Field>
          <Field label="ThoughtSpot URL">
            <input value={form.url} onChange={set("url")} placeholder="https://company.thoughtspot.cloud" {...inputStyle} />
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
          <Field
            label={AUTH_SECRET_LABEL[form.auth_type]}
            hint="(leave blank to keep existing)"
          >
            <input
              value={form.credential} onChange={set("credential")}
              type="password" placeholder="Enter new value to rotate…"
              {...inputStyle}
            />
          </Field>

          {error && <p style={{ margin: 0, fontSize: 12, color: theme.color.danger }}>{error}</p>}

          <div style={{ display: "flex", gap: 8, marginTop: "auto" }}>
            <button onClick={onClose} style={{
              flex: 1, padding: "8px 0", borderRadius: 6, fontSize: 13, fontWeight: 500,
              border: `1px solid ${theme.color.border}`, background: "transparent", cursor: "pointer",
              color: theme.color.textPrimary, fontFamily: theme.font.sans,
            }}>
              Cancel
            </button>
            <button onClick={handleSave} disabled={saving} style={{
              flex: 1, padding: "8px 0", borderRadius: 6, fontSize: 13, fontWeight: 500,
              border: "none", background: theme.gradient.accent, boxShadow: theme.shadow.glowAccent,
              color: theme.color.onAccent, cursor: "pointer",
              fontFamily: theme.font.sans, opacity: saving ? 0.6 : 1,
            }}>
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label style={{ fontSize: 12, fontWeight: 500, color: theme.color.textPrimary, fontFamily: theme.font.sans, display: "block", marginBottom: 6 }}>
        {label}
        {hint && <span style={{ fontWeight: 400, color: theme.color.textMuted, marginLeft: 6 }}>{hint}</span>}
      </label>
      {children}
    </div>
  );
}

const inputStyle = {
  style: {
    width: "100%", height: 34, padding: "0 10px", borderRadius: 6,
    border: `1px solid ${theme.color.border}`, background: theme.color.surface,
    fontSize: 13, color: theme.color.textPrimary, fontFamily: theme.font.sans,
    outline: "none", boxSizing: "border-box" as const,
  }
};
