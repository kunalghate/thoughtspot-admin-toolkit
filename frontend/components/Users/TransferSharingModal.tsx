/**
 * TransferSharingModal — re-share everything the source user can see to a target.
 *
 * Steps:
 *   1. Pick target user
 *   2. Preview (LIVE API call): what can the source user see?
 *   3. Confirm + execute (typed SHARE)
 *
 * Refuses to target an admin (server-side enforced; we surface the 422 message).
 */
import { useState } from "react";

import { UserPicker } from "@/components/Users/UserPicker";
import {
  Modal, FromToBar, Label, Footer, PrimaryButton, SecondaryButton, ErrorBox,
} from "@/components/Users/TransferOwnershipModal";
import { usersApi } from "@/lib/api";
import type { UserListItem, SharingPermissionItem } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboard", ANSWER: "Answer", LOGICAL_TABLE: "Table",
  WORKSHEET: "Worksheet",
};

type Step = "pick-target" | "preview" | "confirming" | "submitting";

export function TransferSharingModal({
  clusterId,
  orgId,
  fromUser,
  onClose,
}: {
  clusterId: string;
  orgId: number;
  fromUser: UserListItem;
  onClose: () => void;
}) {
  const [step, setStep] = useState<Step>("pick-target");
  const [target, setTarget] = useState<UserListItem | null>(null);
  const [items, setItems] = useState<SharingPermissionItem[]>([]);
  const [byType, setByType] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [notify, setNotify] = useState(false);

  async function fetchPreview(t: UserListItem) {
    setLoading(true);
    setError(null);
    try {
      const res = await usersApi.transferSharingPreview({
        cluster_id: clusterId,
        org_id: orgId,
        from_user_guid: fromUser.ts_guid,
        to_user_identifier: t.username,
      });
      setItems(res.items);
      setByType(res.by_type);
      setStep("preview");
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit() {
    if (!target) return;
    setStep("submitting");
    setError(null);
    try {
      const res = await usersApi.transferSharingExecute({
        cluster_id: clusterId,
        org_id: orgId,
        from_user_guid: fromUser.ts_guid,
        to_user_identifier: target.username,
        notify,
      });
      onClose();
      window.location.href = `/jobs?highlight=${res.job_id}`;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setError(msg);
      setStep("confirming");
    }
  }

  return (
    <Modal onClose={onClose} title="Transfer sharing">
      <FromToBar fromUser={fromUser} target={target} />

      {error && <ErrorBox>{error}</ErrorBox>}

      {step === "pick-target" && (
        <>
          <Label>Pick the user who should receive the same access</Label>
          <UserPicker
            clusterId={clusterId}
            orgId={orgId}
            picked={target}
            excludeGuid={fromUser.ts_guid}
            onPick={(u) => { setTarget(u); if (u) void fetchPreview(u); }}
            placeholder="Search for a replacement user…"
          />
          <div style={{
            marginTop: 12, padding: "8px 12px", fontSize: 11, color: "#7A7068",
            background: "#FAF8F4", border: "1px solid #E8E1D5", borderRadius: 4,
            fontFamily: "Geist, sans-serif",
          }}>
            Cluster admins are rejected — they already see everything.
          </div>
          <Footer>
            <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
          </Footer>
        </>
      )}

      {(step === "preview" || step === "confirming" || step === "submitting") && (
        <>
          <Label>Objects to be shared</Label>
          {loading && <div style={hintStyle}>Fetching what {fromUser.username} can see…</div>}
          {!loading && (
            <>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
                {Object.entries(byType).map(([t, n]) => (
                  <span key={t} style={{
                    padding: "3px 10px", fontSize: 11, fontWeight: 500, borderRadius: 14,
                    background: "#F5F0FF", color: "#6D28D9",
                    fontFamily: "Geist, sans-serif",
                  }}>{TYPE_LABELS[t] ?? t}: {n}</span>
                ))}
              </div>

              <div style={{
                maxHeight: 240, overflowY: "auto", border: "1px solid #E8E1D5",
                borderRadius: 6, background: "white",
              }}>
                {items.length === 0 ? (
                  <div style={{ padding: 16, fontSize: 12, color: "#7A7068" }}>
                    Source user has no shared objects (already a member of everything via groups, or no access).
                  </div>
                ) : items.slice(0, 100).map((i) => (
                  <div key={i.metadata_id} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 12px", fontSize: 12, borderBottom: "1px solid #F2EDE3",
                  }}>
                    <span style={{
                      padding: "1px 7px", borderRadius: 10, fontSize: 10, fontWeight: 600,
                      background: "#F3F4F6", color: "#374151",
                    }}>{TYPE_LABELS[i.metadata_type] ?? i.metadata_type}</span>
                    <span style={{ flex: 1, color: "#1A1714" }}>{i.metadata_name}</span>
                    <span style={{
                      padding: "1px 7px", borderRadius: 10, fontSize: 10, fontWeight: 600,
                      background: i.share_mode === "MODIFY" ? "#FEF3C7" : "#D1FAE5",
                      color: i.share_mode === "MODIFY" ? "#92400E" : "#065F46",
                    }}>{i.share_mode}</span>
                  </div>
                ))}
                {items.length > 100 && (
                  <div style={{ padding: "6px 12px", fontSize: 11, color: "#7A7068", fontStyle: "italic" }}>
                    …and {items.length - 100} more
                  </div>
                )}
              </div>

              <div style={{ ...hintStyle, marginTop: 10 }}>
                <strong>{items.length}</strong> share{items.length === 1 ? "" : "s"} will be applied to{" "}
                <strong>{target?.display_name || target?.username}</strong>.
              </div>

              <label style={{
                display: "flex", alignItems: "center", gap: 6, marginTop: 10,
                fontSize: 12, color: "#1A1714", fontFamily: "Geist, sans-serif",
              }}>
                <input
                  type="checkbox" checked={notify}
                  onChange={(e) => setNotify(e.target.checked)}
                />
                Notify {target?.display_name || target?.username} by email on each share
              </label>

              {step === "confirming" && (
                <div style={{ marginTop: 12 }}>
                  <Label>Type <code style={codeStyle}>SHARE</code> to confirm</Label>
                  <input
                    type="text"
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    placeholder="SHARE"
                    style={{
                      width: "100%", padding: "8px 12px", fontSize: 13,
                      border: "1px solid #E8E1D5", borderRadius: 6,
                      fontFamily: "Geist Mono, ui-monospace, monospace",
                    }}
                  />
                </div>
              )}
            </>
          )}

          <Footer>
            <SecondaryButton onClick={onClose}>Cancel</SecondaryButton>
            {step === "preview" && (
              <PrimaryButton
                disabled={items.length === 0 || loading}
                onClick={() => setStep("confirming")}
              >
                Continue
              </PrimaryButton>
            )}
            {step === "confirming" && (
              <PrimaryButton
                disabled={confirmText !== "SHARE"}
                onClick={handleSubmit}
              >
                Share to {items.length} object{items.length === 1 ? "" : "s"}
              </PrimaryButton>
            )}
            {step === "submitting" && <SecondaryButton disabled>Starting…</SecondaryButton>}
          </Footer>
        </>
      )}
    </Modal>
  );
}

const hintStyle: React.CSSProperties = {
  padding: "8px 12px", fontSize: 12, color: "#5B21B6",
  background: "#F5F0FF", border: "1px solid #DDD6FE", borderRadius: 6,
  fontFamily: "Geist, sans-serif",
};

const codeStyle: React.CSSProperties = {
  padding: "1px 6px", background: "#F3F4F6", borderRadius: 3,
  fontFamily: "Geist Mono, ui-monospace, monospace", fontSize: 11,
};
