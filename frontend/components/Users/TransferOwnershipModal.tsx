/**
 * TransferOwnershipModal — wizard that reassigns ownership of every object
 * a user owns to a chosen replacement.
 *
 * Steps:
 *   1. Pick target user
 *   2. Preview the objects that will move (with optional type filter)
 *   3. Typed `TRANSFER` confirm
 *   4. Kick background job, then close and redirect to /jobs for live progress
 */
import { useCallback, useEffect, useState } from "react";
import { X, ArrowRight } from "lucide-react";

import { UserPicker } from "@/components/Users/UserPicker";
import { usersApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { UserListItem, TransferObjectItem } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard", ANSWER: "Answer", WORKSHEET: "Worksheet",
  ONE_TO_ONE_LOGICAL: "Table", AGGR_WORKSHEET: "Aggregate", SQL_VIEW: "View",
  USER_DEFINED: "Custom",
};

type Step = "pick-target" | "preview" | "confirming" | "submitting";

export function TransferOwnershipModal({
  clusterId,
  orgId,
  fromUser,
  onClose,
}: {
  clusterId: string;
  orgId: number;
  fromUser: UserListItem;
  onClose: (reloadNeeded: boolean) => void;
}) {
  const [step, setStep] = useState<Step>("pick-target");
  const [target, setTarget] = useState<UserListItem | null>(null);
  const [items, setItems] = useState<TransferObjectItem[]>([]);
  const [byType, setByType] = useState<Record<string, number>>({});
  const [typeFilter, setTypeFilter] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");

  const loadPreview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await usersApi.transferPreview({
        cluster_id: clusterId,
        org_id: orgId,
        from_user_guid: fromUser.ts_guid,
        object_types: typeFilter.length > 0 ? typeFilter : undefined,
      });
      setItems(res.items);
      setByType(res.by_type);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [clusterId, orgId, fromUser.ts_guid, typeFilter]);

  useEffect(() => {
    if (step === "preview" || step === "confirming") void loadPreview();
  }, [step, loadPreview]);

  async function handleSubmit() {
    if (!target) return;
    setStep("submitting");
    setError(null);
    try {
      const res = await usersApi.transferExecute({
        cluster_id: clusterId,
        org_id: orgId,
        from_user_guid: fromUser.ts_guid,
        to_user_identifier: target.username,
        object_ids: items.map((i) => i.ts_guid),
      });
      onClose(true);
      window.location.href = `/jobs?highlight=${res.job_id}`;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setStep("confirming");
    }
  }

  return (
    <Modal onClose={() => onClose(false)} title="Transfer ownership">
      <FromToBar fromUser={fromUser} target={target} />

      {step === "pick-target" && (
        <>
          <Label>Pick the user who should receive ownership</Label>
          <UserPicker
            clusterId={clusterId}
            orgId={orgId}
            picked={target}
            excludeGuid={fromUser.ts_guid}
            onPick={setTarget}
            placeholder="Search by username, display name, or email…"
          />
          <Footer>
            <SecondaryButton onClick={() => onClose(false)}>Cancel</SecondaryButton>
            <PrimaryButton disabled={!target} onClick={() => setStep("preview")}>
              Next: Preview objects
            </PrimaryButton>
          </Footer>
        </>
      )}

      {(step === "preview" || step === "confirming" || step === "submitting") && (
        <>
          <Label>Objects that will move</Label>
          {loading && <div style={hintStyle}>Loading preview…</div>}
          {!loading && error && <ErrorBox>{error}</ErrorBox>}
          {!loading && !error && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                {Object.entries(byType).map(([t, n]) => (
                  <button
                    key={t}
                    onClick={() => {
                      // Changing the filter changes the object set, so any typed
                      // confirmation no longer matches what's shown — invalidate it.
                      setConfirmText("");
                      setTypeFilter((prev) =>
                        prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]
                      );
                    }}
                    style={{
                      display: "flex", alignItems: "center", gap: 6,
                      padding: "4px 10px", fontSize: 11, fontWeight: 500,
                      borderRadius: 14, border: "1px solid",
                      cursor: "pointer", fontFamily: theme.font.sans,
                      borderColor: typeFilter.includes(t) ? theme.color.accent : theme.color.border,
                      background: typeFilter.includes(t) ? theme.color.accentSoft : theme.color.surface,
                      color: typeFilter.includes(t) ? theme.color.accent2 : theme.color.textMuted,
                    }}
                  >
                    {TYPE_LABELS[t] ?? t} <strong>{n}</strong>
                  </button>
                ))}
              </div>

              <div style={{
                maxHeight: 240, overflowY: "auto", border: `1px solid ${theme.color.border}`,
                borderRadius: 6, background: theme.color.surface,
              }}>
                {items.length === 0 ? (
                  <div style={{ padding: 16, fontSize: 12, color: theme.color.textMuted }}>
                    No objects owned by this user.
                  </div>
                ) : items.slice(0, 100).map((i) => (
                  <div key={i.ts_guid} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 12px", fontSize: 12, borderBottom: `1px solid ${theme.color.bg}`,
                  }}>
                    <span style={{
                      padding: "1px 7px", borderRadius: 10, fontSize: 10, fontWeight: 600,
                      background: theme.color.surface3, color: theme.color.textSecondary,
                    }}>{TYPE_LABELS[i.object_type] ?? i.object_type}</span>
                    <span style={{ fontFamily: theme.font.sans, color: theme.color.textPrimary }}>{i.name}</span>
                  </div>
                ))}
                {items.length > 100 && (
                  <div style={{ padding: "6px 12px", fontSize: 11, color: theme.color.textMuted, fontStyle: "italic" }}>
                    …and {items.length - 100} more
                  </div>
                )}
              </div>

              <div style={{ ...hintStyle, marginTop: 10 }}>
                <strong>{items.length}</strong> object{items.length === 1 ? "" : "s"} will be reassigned to{" "}
                <strong>{target?.display_name || target?.username}</strong>.
              </div>

              {step === "confirming" && (
                <div style={{ marginTop: 12 }}>
                  <Label>Type <code style={codeStyle}>TRANSFER</code> to confirm</Label>
                  <input
                    type="text"
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    placeholder="TRANSFER"
                    style={{
                      width: "100%", padding: "8px 12px", fontSize: 13,
                      border: `1px solid ${theme.color.border}`, borderRadius: 6,
                      fontFamily: theme.font.mono,
                      background: theme.color.surface, color: theme.color.textPrimary,
                    }}
                  />
                </div>
              )}
            </>
          )}

          <Footer>
            <SecondaryButton onClick={() => onClose(false)}>Cancel</SecondaryButton>
            {step === "preview" && (
              <PrimaryButton
                disabled={items.length === 0}
                onClick={() => setStep("confirming")}
              >
                Continue
              </PrimaryButton>
            )}
            {step === "confirming" && (
              <PrimaryButton
                disabled={confirmText !== "TRANSFER" || items.length === 0}
                onClick={handleSubmit}
              >
                Transfer {items.length} object{items.length === 1 ? "" : "s"}
              </PrimaryButton>
            )}
            {step === "submitting" && <SecondaryButton disabled>Starting…</SecondaryButton>}
          </Footer>
        </>
      )}
    </Modal>
  );
}

// ── Shared modal pieces ─────────────────────────────────────────────────────
// (re-exported for the other two modals — see DeleteUsersModal / TransferSharingModal)

export function Modal({
  children, onClose, title,
}: {
  children: React.ReactNode;
  onClose: () => void;
  title: string;
}) {
  return (
    <div style={{
      position: "fixed", inset: 0, background: theme.color.overlay,
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 50, fontFamily: theme.font.sans,
    }} onClick={onClose}>
      <div style={{
        width: 600, maxHeight: "85vh", overflowY: "auto",
        background: theme.color.surface, border: `1px solid ${theme.color.border}`,
        borderRadius: 10, padding: 24,
      }} onClick={(e) => e.stopPropagation()}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 16,
        }}>
          <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: theme.color.textPrimary }}>
            {title}
          </h2>
          <button
            onClick={onClose}
            style={{
              padding: 4, border: "none", background: "transparent",
              cursor: "pointer", color: theme.color.textMuted,
            }}
          ><X size={16} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function FromToBar({
  fromUser, target,
}: {
  fromUser: UserListItem;
  target: UserListItem | null;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
      background: theme.color.surface, border: `1px solid ${theme.color.border}`, borderRadius: 6,
      marginBottom: 16, fontSize: 12,
    }}>
      <div>
        <div style={{ fontWeight: 600, color: theme.color.textPrimary }}>{fromUser.display_name || fromUser.username}</div>
        <div style={{ color: theme.color.textMuted }}>{fromUser.email || fromUser.username}</div>
      </div>
      <ArrowRight size={14} style={{ color: theme.color.textMuted }} />
      <div>
        {target ? (
          <>
            <div style={{ fontWeight: 600, color: theme.color.textPrimary }}>{target.display_name || target.username}</div>
            <div style={{ color: theme.color.textMuted }}>{target.email || target.username}</div>
          </>
        ) : (
          <span style={{ color: theme.color.textMuted, fontStyle: "italic" }}>pick a recipient below…</span>
        )}
      </div>
    </div>
  );
}

export function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 600, color: theme.color.textMuted, textTransform: "uppercase",
      letterSpacing: "0.04em", marginBottom: 6, fontFamily: theme.font.sans,
    }}>{children}</div>
  );
}

export function Footer({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 20,
    }}>{children}</div>
  );
}

export function PrimaryButton({
  children, onClick, disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "8px 16px", fontSize: 13, fontWeight: 600,
        background: disabled ? theme.color.violetBorder : theme.gradient.accent, color: theme.color.onAccent,
        border: "none", borderRadius: 6,
        boxShadow: disabled ? "none" : theme.shadow.glowAccent,
        cursor: disabled ? "not-allowed" : "pointer",
        fontFamily: theme.font.sans,
      }}
    >{children}</button>
  );
}

export function SecondaryButton({
  children, onClick, disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "8px 16px", fontSize: 13, fontWeight: 500,
        background: theme.color.surface, color: theme.color.textPrimary,
        border: `1px solid ${theme.color.border}`, borderRadius: 6,
        cursor: disabled ? "not-allowed" : "pointer",
        fontFamily: theme.font.sans,
      }}
    >{children}</button>
  );
}

export function DangerButton({
  children, onClick, disabled,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: "8px 16px", fontSize: 13, fontWeight: 600,
        background: disabled ? theme.color.dangerBorder : theme.color.danger, color: theme.color.onAccent,
        border: "none", borderRadius: 6,
        cursor: disabled ? "not-allowed" : "pointer",
        fontFamily: theme.font.sans,
      }}
    >{children}</button>
  );
}

export function ErrorBox({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: "10px 14px", fontSize: 12, background: theme.color.dangerSoft,
      border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 6, color: theme.color.danger,
      fontFamily: theme.font.sans,
    }}><strong>Error:</strong> {children}</div>
  );
}

const hintStyle: React.CSSProperties = {
  padding: "8px 12px", fontSize: 12, color: theme.color.accent2,
  background: theme.color.accentSoft, border: `1px solid ${theme.color.violetBorder}`, borderRadius: 6,
  fontFamily: theme.font.sans,
};

const codeStyle: React.CSSProperties = {
  padding: "1px 6px", background: theme.color.surface3, borderRadius: 3,
  fontFamily: theme.font.mono, fontSize: 11,
};
