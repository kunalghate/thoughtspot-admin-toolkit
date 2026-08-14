/**
 * Read-only group detail drawer for the Groups page.
 *
 * Opens on row click; shows the group's description, privileges, and member
 * users from the local cache (no live API calls — sync refreshes it).
 */
import { useEffect, useState } from "react";
import { X, ShieldCheck, Users } from "lucide-react";
import { groupsApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { GroupDetail } from "@/lib/types";

interface Props {
  clusterId: string;
  tsGuid: string;
  groupName: string;
  onClose: () => void;
}

export default function GroupDetailDrawer({ clusterId, tsGuid, groupName, onClose }: Props) {
  const [detail, setDetail] = useState<GroupDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    setDetail(null);
    setError(null);
    setLoading(true);
    groupsApi.get(tsGuid, clusterId)
      .then(setDetail)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load group"))
      .finally(() => setLoading(false));
  }, [tsGuid, clusterId]);

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 200 }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 400,
        background: theme.color.surface, boxShadow: theme.shadow.lg,
        zIndex: 201, display: "flex", flexDirection: "column",
        fontFamily: theme.font.sans,
      }}>

        {/* Header */}
        <div style={{
          padding: "20px 24px", borderBottom: `1px solid ${theme.color.border}`,
          display: "flex", alignItems: "flex-start", gap: 12,
        }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 11, color: theme.color.textMuted, marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.05em" }}>
              Group
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, wordBreak: "break-word" }}>
              {detail?.display_name || groupName}
            </div>
            {detail && detail.name !== detail.display_name && (
              <div style={{ fontSize: 12, color: theme.color.textMuted, marginTop: 4 }}>
                {detail.name}
              </div>
            )}
          </div>
          <button onClick={onClose} style={{
            background: "none", border: "none", cursor: "pointer",
            color: theme.color.textMuted, padding: 4, flexShrink: 0,
          }}>
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
          {loading && (
            <div style={{ color: theme.color.textMuted, fontSize: 13, textAlign: "center", marginTop: 40 }}>
              Loading group…
            </div>
          )}

          {error && (
            <div style={{
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
              padding: "12px 16px", fontSize: 13, color: theme.color.danger,
            }}>
              {error}
            </div>
          )}

          {!loading && !error && detail && (
            <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
              {detail.description && (
                <p style={{ margin: 0, fontSize: 13, color: theme.color.textSecondary, lineHeight: 1.6 }}>
                  {detail.description}
                </p>
              )}

              {/* Privileges */}
              <div>
                <SectionLabel icon={<ShieldCheck size={14} />} text="Privileges" />
                {detail.privileges.length === 0 ? (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>No privileges.</div>
                ) : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {detail.privileges.map((priv) => (
                      <span key={priv} style={{
                        padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 500,
                        background: theme.color.accentSoft, color: theme.color.accent2,
                      }}>{priv}</span>
                    ))}
                  </div>
                )}
              </div>

              {/* Members */}
              <div>
                <SectionLabel icon={<Users size={14} />} text={`Members (${detail.member_count})`} />
                {detail.member_count === 0 ? (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>No members.</div>
                ) : detail.members.length === 0 ? (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>
                    Member profiles not cached yet — run a users sync to see them.
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {detail.members.map((m) => (
                      <div key={m.ts_guid} style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                        padding: "8px 12px", borderRadius: 8,
                        border: `1px solid ${theme.color.border}`,
                      }}>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 13, color: theme.color.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {m.display_name || m.username}
                          </div>
                          <div style={{ fontSize: 11, color: theme.color.textMuted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {m.email || m.username}
                          </div>
                        </div>
                        <span style={{
                          padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 600, flexShrink: 0,
                          background: m.status === "ACTIVE" ? theme.color.successSoft : theme.color.dangerSoft,
                          color: m.status === "ACTIVE" ? theme.color.success : theme.color.danger,
                        }}>{m.status}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function SectionLabel({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      fontSize: 12, fontWeight: 600, color: theme.color.textMuted,
      textTransform: "uppercase", letterSpacing: "0.05em",
      marginBottom: 10,
    }}>
      {icon} {text}
    </div>
  );
}
