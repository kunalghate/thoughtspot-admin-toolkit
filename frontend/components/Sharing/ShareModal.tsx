/**
 * ShareModal — launched from the Bulk Sharing grid's selection bar.
 *
 * Holds the "who + access level + preview/confirm" step for a fixed set of
 * selected object GUIDs. Mirrors the Deleter's DryRunModal shell:
 *   compose (pick principals + access level) → preview (matrix + type-SHARE) → execute
 *
 * Changing any share parameter (principals / mode / notify) invalidates a
 * stale preview, so the operator always confirms against the matrix they see.
 * On execute it starts a background share job and navigates to /jobs.
 */
import { useCallback, useState } from "react";
import { X, Share2, ChevronRight } from "lucide-react";

import { sharingApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { PrincipalPicker } from "./PrincipalPicker";
import { SharingPreviewTable } from "./PreviewTable";
import type {
  PrincipalPickerItem,
  SharePermissionMode,
  SharingPreviewResponse,
} from "@/lib/types";

interface Props {
  objectGuids: string[];
  clusterId: string;
  orgId: number;
  onClose: () => void;
}

export function ShareModal({ objectGuids, clusterId, orgId, onClose }: Props) {
  const [principals, setPrincipals] = useState<PrincipalPickerItem[]>([]);
  const [mode, setMode] = useState<SharePermissionMode>("READ_ONLY");
  const [notify, setNotify] = useState(false);

  const [preview, setPreview] = useState<SharingPreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");

  const canPreview = principals.length > 0 && objectGuids.length > 0;
  const busy = previewing || executing;

  // Any change to the share parameters invalidates a stale preview.
  function resetPreview() {
    setPreview(null);
    setConfirmText("");
  }

  const runPreview = useCallback(async () => {
    setPreviewing(true);
    setError(null);
    setPreview(null);
    try {
      const res = await sharingApi.preview({
        cluster_id: clusterId,
        org_id: orgId,
        object_guids: objectGuids,
        principal_guids: principals.map((p) => p.ts_guid),
        mode,
      });
      setPreview(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPreviewing(false);
    }
  }, [clusterId, orgId, objectGuids, principals, mode]);

  async function handleExecute() {
    setExecuting(true);
    setError(null);
    try {
      const res = await sharingApi.execute({
        cluster_id: clusterId,
        org_id: orgId,
        object_guids: objectGuids,
        principal_guids: principals.map((p) => p.ts_guid),
        mode,
        notify,
      });
      window.location.href = `/jobs?highlight=${res.job_id}`;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setExecuting(false);
    }
  }

  const canApply = !!preview && confirmText === "SHARE" && !executing && preview.will_change_count > 0;

  return (
    <>
      {/* Backdrop */}
      <div
        style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 300 }}
        onClick={busy ? undefined : onClose}
      />

      {/* Panel */}
      <div
        data-testid="share-modal"
        style={{
          position: "fixed", top: "50%", left: "50%", transform: "translate(-50%, -50%)",
          width: 720, maxHeight: "85vh", background: theme.color.surface, borderRadius: 10, zIndex: 301,
          display: "flex", flexDirection: "column", boxShadow: theme.shadow.lg,
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", alignItems: "center", gap: 8, padding: "16px 20px",
          borderBottom: `1px solid ${theme.color.border}`, flexShrink: 0,
        }}>
          <Share2 size={16} style={{ color: theme.color.accent }} />
          <span style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans, flex: 1 }}>
            Share {objectGuids.length.toLocaleString()} object{objectGuids.length === 1 ? "" : "s"}
          </span>
          <button
            onClick={busy ? undefined : onClose}
            style={{ background: "none", border: "none", cursor: busy ? "not-allowed" : "pointer", color: theme.color.textMuted, padding: 4 }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflowY: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 16, minHeight: 0 }}>
          {error && (
            <div style={{
              padding: "10px 14px", fontSize: 12, background: theme.color.dangerSoft,
              border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6, color: theme.color.danger,
              fontFamily: theme.font.sans,
            }}>
              <strong>Error:</strong> {error}
            </div>
          )}

          <Section title="Share with">
            <PrincipalPicker
              clusterId={clusterId}
              orgId={orgId}
              selected={principals}
              onChange={(next) => { setPrincipals(next); resetPreview(); }}
            />
          </Section>

          <Section title="Access level">
            <div style={{ display: "flex", gap: 6 }}>
              {(["READ_ONLY", "MODIFY", "NO_ACCESS"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => { setMode(m); resetPreview(); }}
                  style={{
                    flex: 1, padding: "8px 6px", fontSize: 12, fontWeight: 600,
                    border: `1px solid ${mode === m ? theme.color.accent : theme.color.border}`,
                    background: mode === m ? theme.color.accentSoft : theme.color.surface,
                    color: mode === m ? theme.color.accent2 : theme.color.textMuted,
                    borderRadius: 6, cursor: "pointer", fontFamily: theme.font.sans,
                  }}
                >
                  {m === "READ_ONLY" ? "View" : m === "MODIFY" ? "Edit" : "Remove"}
                </button>
              ))}
            </div>

            <label style={{
              display: "flex", alignItems: "center", gap: 6, marginTop: 10,
              fontSize: 12, color: theme.color.textPrimary, fontFamily: theme.font.sans,
            }}>
              <input
                type="checkbox" checked={notify}
                onChange={(e) => { setNotify(e.target.checked); resetPreview(); }}
              />
              Notify recipients by email
            </label>
          </Section>

          {/* Preview trigger (shown until a preview exists for the current params) */}
          {!preview && (
            <button
              disabled={!canPreview || previewing}
              onClick={runPreview}
              style={{
                display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                padding: "10px 16px", fontSize: 13, fontWeight: 600,
                background: canPreview && !previewing ? theme.gradient.accent : theme.color.violetBorder,
                boxShadow: canPreview && !previewing ? theme.shadow.glowAccent : "none",
                color: theme.color.onAccent, border: "none", borderRadius: 6,
                cursor: canPreview && !previewing ? "pointer" : "not-allowed",
                fontFamily: theme.font.sans,
              }}
            >
              {previewing ? "Building preview…" : "Preview changes"} <ChevronRight size={14} />
            </button>
          )}

          {/* Preview result */}
          {preview && (
            <>
              <div style={{
                display: "flex", alignItems: "center", gap: 12, padding: "10px 14px",
                background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6, flexShrink: 0,
              }}>
                <div style={{ flex: 1, fontSize: 13, color: theme.color.accent2, fontFamily: theme.font.sans }}>
                  <strong>{preview.will_change_count}</strong> of {preview.total} pair
                  {preview.total === 1 ? "" : "s"} will change.
                </div>
              </div>

              <div style={{ display: "flex", flexDirection: "column", minHeight: 160, maxHeight: 360 }}>
                <SharingPreviewTable rows={preview.items} />
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10, padding: "12px 20px",
          borderTop: `1px solid ${theme.color.border}`, flexShrink: 0,
        }}>
          <div style={{ flex: 1 }} />
          {preview && (
            <input
              type="text"
              placeholder="Type SHARE to confirm"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              style={{
                padding: "6px 10px", fontSize: 12, width: 180,
                border: `1px solid ${theme.color.violetBorder}`, borderRadius: 4,
                fontFamily: theme.font.mono,
                background: theme.color.surface, color: theme.color.textPrimary,
              }}
            />
          )}
          <button
            onClick={busy ? undefined : onClose}
            style={{
              padding: "6px 12px", fontSize: 12, fontWeight: 500, background: theme.color.surface,
              border: `1px solid ${theme.color.border}`, borderRadius: 6, color: theme.color.textMuted,
              cursor: busy ? "not-allowed" : "pointer", fontFamily: theme.font.sans,
            }}
          >
            Cancel
          </button>
          <button
            disabled={!canApply}
            onClick={handleExecute}
            style={{
              padding: "6px 16px", fontSize: 12, fontWeight: 600,
              background: canApply ? theme.gradient.accent : theme.color.violetBorder, color: theme.color.onAccent,
              boxShadow: canApply ? theme.shadow.glowAccent : "none",
              border: "none", borderRadius: 6,
              cursor: canApply ? "pointer" : "not-allowed", fontFamily: theme.font.sans,
            }}
          >
            {executing ? "Starting…" : "Apply"}
          </button>
        </div>
      </div>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{
        fontSize: 11, fontWeight: 600, color: theme.color.textMuted, textTransform: "uppercase",
        letterSpacing: "0.04em", marginBottom: 8, fontFamily: theme.font.sans,
      }}>{title}</div>
      {children}
    </div>
  );
}
