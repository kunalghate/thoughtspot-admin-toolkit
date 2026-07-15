/**
 * DeleteUsersModal — bulk-delete users with typed `DELETE` confirmation.
 *
 * Preview surfaces each user's owned-object count + admin flag so the operator
 * can see what they're about to wipe.
 */
import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";

import {
  Modal, Label, Footer, DangerButton, SecondaryButton, ErrorBox,
} from "@/components/Users/TransferOwnershipModal";
import { usersApi, jobsApi } from "@/lib/api";
import type { UserListItem, DeleteDryRunItem, DeleteDryRunResult } from "@/lib/types";

type Step = "preview" | "confirming" | "submitting";

export function DeleteUsersModal({
  clusterId,
  orgId,
  users,
  onClose,
}: {
  clusterId: string;
  orgId: number;
  users: UserListItem[];
  onClose: (reloadNeeded: boolean) => void;
}) {
  const [step, setStep] = useState<Step>("preview");
  const [items, setItems] = useState<DeleteDryRunItem[]>([]);
  const [unrecognized, setUnrecognized] = useState<string[]>([]);
  const [missingLive, setMissingLive] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmText, setConfirmText] = useState("");

  // Run a LIVE dry-run (verifies upstream existence + impact), then render its
  // result. Polls the job every 2s, mirroring the Deleter's DryRunModal.
  useEffect(() => {
    let cancelled = false;
    let iv: ReturnType<typeof setInterval> | null = null;
    setLoading(true);
    setError(null);

    usersApi
      .deleteDryrun({
        cluster_id: clusterId,
        org_id: orgId,
        user_guids: users.map((u) => u.ts_guid),
        user_identifiers: users.map((u) => u.username),
      })
      .then(({ job_id }) => {
        if (cancelled) return;
        iv = setInterval(async () => {
          try {
            const job = await jobsApi.get(job_id);
            if (job.status === "COMPLETE" || job.status === "PARTIAL" || job.status === "FAILED") {
              if (iv) clearInterval(iv);
              if (cancelled) return;
              if (job.status === "FAILED") {
                setError(job.error ?? "Dry-run failed");
                setLoading(false);
                return;
              }
              const result = (job.result ?? {}) as unknown as DeleteDryRunResult;
              setItems(result.items ?? []);
              setUnrecognized(result.unrecognized ?? []);
              setMissingLive(result.missing_live ?? []);
              setLoading(false);
            }
          } catch {
            if (iv) clearInterval(iv);
            if (!cancelled) {
              setError("Lost connection while checking impact");
              setLoading(false);
            }
          }
        }, 2000);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      if (iv) clearInterval(iv);
    };
  }, [clusterId, orgId, users]);

  const adminCount = items.filter((i) => i.is_admin).length;
  const ownedTotal = items.reduce((acc, i) => acc + i.owned_object_count, 0);

  async function handleSubmit() {
    setStep("submitting");
    setError(null);
    try {
      const res = await usersApi.deleteExecute({
        cluster_id: clusterId,
        org_id: orgId,
        user_guids: items.map((i) => i.ts_guid),
        user_identifiers: items.map((i) => i.username),
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
    <Modal onClose={() => onClose(false)} title={`Delete ${users.length} user${users.length === 1 ? "" : "s"}`}>
      {error && <ErrorBox>{error}</ErrorBox>}

      <Label>Users to delete</Label>
      {loading && <div style={hintStyle}>Checking impact…</div>}
      {!loading && (
        <>
          <div style={{
            maxHeight: 240, overflowY: "auto", border: "1px solid #E8E1D5",
            borderRadius: 6, background: "white",
          }}>
            {items.map((u) => (
              <div key={u.ts_guid} style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "8px 12px", fontSize: 12, borderBottom: "1px solid #F2EDE3",
              }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
                    {u.display_name || u.username}
                  </div>
                  <div style={{ fontSize: 11, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
                    {u.username}{u.email ? ` · ${u.email}` : ""}
                  </div>
                </div>
                {u.is_admin && (
                  <span style={{
                    padding: "2px 8px", fontSize: 10, fontWeight: 600,
                    borderRadius: 10, background: "#FEF3C7", color: "#92400E",
                  }}>ADMIN</span>
                )}
                <span style={{
                  fontSize: 11, color: u.owned_object_count > 0 ? "#92400E" : "#7A7068",
                  fontFamily: "Geist, sans-serif",
                }}>
                  {u.owned_object_count} owned object{u.owned_object_count === 1 ? "" : "s"}
                </span>
              </div>
            ))}
          </div>

          {unrecognized.length > 0 && (
            <div style={{ ...hintStyle, marginTop: 10, color: "#92400E", background: "#FFFBEB", borderColor: "#FCD34D" }}>
              <strong>{unrecognized.length} unrecognized GUID{unrecognized.length === 1 ? "" : "s"}</strong> — these will be skipped:
              <pre style={{
                marginTop: 6, padding: 6, background: "white", borderRadius: 4,
                fontSize: 10, color: "#7A7068", maxHeight: 60, overflowY: "auto",
              }}>{unrecognized.join("\n")}</pre>
            </div>
          )}

          {missingLive.length > 0 && (
            <div style={{ ...hintStyle, marginTop: 10, color: "#92400E", background: "#FFFBEB", borderColor: "#FCD34D" }}>
              <strong>{missingLive.length} user{missingLive.length === 1 ? "" : "s"} no longer exist on the cluster</strong>
              {" "}— already deleted upstream; the delete will simply skip {missingLive.length === 1 ? "it" : "them"}:
              <pre style={{
                marginTop: 6, padding: 6, background: "white", borderRadius: 4,
                fontSize: 10, color: "#7A7068", maxHeight: 60, overflowY: "auto",
              }}>{missingLive.join("\n")}</pre>
            </div>
          )}

          {(ownedTotal > 0 || adminCount > 0) && (
            <div style={{
              display: "flex", alignItems: "flex-start", gap: 8, marginTop: 10,
              padding: "10px 12px", background: "#FEF2F2", border: "1px solid #FCA5A5",
              borderRadius: 6, fontSize: 12, color: "#991B1B",
              fontFamily: "Geist, sans-serif",
            }}>
              <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <div>
                <strong>Warning:</strong>
                {ownedTotal > 0 && (
                  <span> {ownedTotal} object{ownedTotal === 1 ? "" : "s"} will become orphaned. Transfer ownership first to preserve attribution.</span>
                )}
                {adminCount > 0 && (
                  <span> {adminCount} of these {adminCount === 1 ? "is an admin" : "are admins"}.</span>
                )}
              </div>
            </div>
          )}

          {step === "confirming" && (
            <div style={{ marginTop: 12 }}>
              <Label>Type <code style={codeStyle}>DELETE</code> to confirm</Label>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                placeholder="DELETE"
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
        <SecondaryButton onClick={() => onClose(false)}>Cancel</SecondaryButton>
        {step === "preview" && (
          <DangerButton
            disabled={items.length === 0 || loading}
            onClick={() => setStep("confirming")}
          >
            Delete {items.length} user{items.length === 1 ? "" : "s"}
          </DangerButton>
        )}
        {step === "confirming" && (
          <DangerButton
            disabled={confirmText !== "DELETE"}
            onClick={handleSubmit}
          >
            Confirm delete
          </DangerButton>
        )}
        {step === "submitting" && <SecondaryButton disabled>Starting…</SecondaryButton>}
      </Footer>
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
