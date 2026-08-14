/**
 * Read-only user audit drawer for the Users page.
 *
 * Opens on row click; shows profile, org/group membership, effective
 * privileges (union of group privileges — "what they can do"), owned-object
 * count, and a lazily loaded live "Access" section ("what they can see").
 * The access fetch hits the ThoughtSpot API, so it only runs on demand.
 */
import { useEffect, useRef, useState } from "react";
import { X, ShieldCheck, UsersRound, Eye, Pencil, Shield } from "lucide-react";
import { usersApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { UserAccessResponse, UserDetail, UserListItem } from "@/lib/types";

interface Props {
  clusterId: string;
  orgId: number;
  user: UserListItem;
  onClose: () => void;
}

export default function UserDetailDrawer({ clusterId, orgId, user, onClose }: Props) {
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [access, setAccess] = useState<UserAccessResponse | null>(null);
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);

  // Which user the drawer is currently showing — the access fetch is manual,
  // so it can outlive the row it was started from.
  const userGuidRef = useRef(user.ts_guid);
  userGuidRef.current = user.ts_guid;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  // One drawer instance is reused across rows, so a slow fetch for user A can
  // land after the admin has already clicked user B. On an audit surface that
  // would show A's groups and privileges under B's name — drop stale responses.
  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    setAccess(null);
    setAccessError(null);
    setLoading(true);
    usersApi.get(user.ts_guid, clusterId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load user"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [user.ts_guid, clusterId]);

  const loadAccess = () => {
    const requestedGuid = user.ts_guid;
    setAccessError(null);
    setAccessLoading(true);
    usersApi.access(requestedGuid, clusterId, orgId)
      .then((a) => { if (requestedGuid === userGuidRef.current) setAccess(a); })
      .catch((e) => {
        if (requestedGuid === userGuidRef.current) {
          setAccessError(e instanceof Error ? e.message : "Failed to load access");
        }
      })
      .finally(() => { if (requestedGuid === userGuidRef.current) setAccessLoading(false); });
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 200 }}
      />

      {/* Drawer */}
      <div style={{
        position: "fixed", top: 0, right: 0, bottom: 0, width: 420,
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
              User
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, wordBreak: "break-word" }}>
                {user.display_name || user.username}
              </span>
              <span style={{
                padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 600,
                background: user.status === "ACTIVE" ? theme.color.successSoft : theme.color.dangerSoft,
                color: user.status === "ACTIVE" ? theme.color.success : theme.color.danger,
              }}>{user.status}</span>
              {detail?.is_admin && (
                <span style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 600,
                  background: theme.color.accentSoft, color: theme.color.accent2,
                }}><Shield size={10} /> Cluster admin</span>
              )}
            </div>
            <div style={{ fontSize: 12, color: theme.color.textMuted, marginTop: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {user.username}{user.email ? ` · ${user.email}` : ""}
            </div>
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
              Loading user…
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

              {/* At-a-glance stats */}
              <div style={{ display: "flex", gap: 8 }}>
                <Stat label="Owned objects" value={detail.owned_object_count} />
                <Stat label="Groups" value={detail.group_details.length} />
                <Stat label="Orgs" value={detail.org_ids.length} />
              </div>

              {/* Effective privileges */}
              <div>
                <SectionLabel icon={<ShieldCheck size={14} />} text="Can do — effective privileges" />
                {detail.privileges.length === 0 ? (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>
                    {detail.group_details.length === 0
                      ? "No group data — sync groups to see privileges."
                      : "No privileges granted through groups."}
                  </div>
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

              {/* Groups */}
              <div>
                <SectionLabel icon={<UsersRound size={14} />} text={`Groups (${detail.group_details.length})`} />
                {detail.group_details.length === 0 ? (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>Not a member of any group.</div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {detail.group_details.map((g) => (
                      <div key={g.ts_guid} style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                        padding: "8px 12px", borderRadius: 8,
                        border: `1px solid ${theme.color.border}`,
                      }}>
                        <span style={{ fontSize: 13, color: theme.color.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {g.display_name || g.name}
                        </span>
                        <span
                          title={g.privileges.join(", ")}
                          style={{ fontSize: 11, color: theme.color.textMuted, flexShrink: 0 }}
                        >
                          {g.privileges.length} privilege{g.privileges.length === 1 ? "" : "s"}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Live access */}
              <div>
                <SectionLabel icon={<Eye size={14} />} text="Can see — shared access" />
                {access == null && !accessLoading && (
                  <>
                    <button
                      onClick={loadAccess}
                      style={{
                        padding: "6px 14px", borderRadius: 6, fontSize: 12, fontWeight: 500,
                        border: `1px solid ${theme.color.violetBorder}`, background: theme.color.surface,
                        color: theme.color.accent2, cursor: "pointer", fontFamily: theme.font.sans,
                      }}
                    >
                      Load access from ThoughtSpot
                    </button>
                    <p style={{ margin: "6px 0 0", fontSize: 11, color: theme.color.textMuted }}>
                      Live API call — lists every object shared with this user directly or via groups.
                    </p>
                  </>
                )}
                {accessLoading && (
                  <div style={{ fontSize: 13, color: theme.color.textMuted }}>Fetching live access…</div>
                )}
                {accessError && (
                  <div style={{
                    background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
                    padding: "10px 14px", fontSize: 12, color: theme.color.danger,
                  }}>
                    {accessError}
                  </div>
                )}
                {access && (
                  access.total === 0 ? (
                    <div style={{ fontSize: 13, color: theme.color.textMuted }}>
                      Nothing is explicitly shared with this user.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      <div style={{ fontSize: 12, color: theme.color.textMuted }}>
                        {access.total} object{access.total === 1 ? "" : "s"} —{" "}
                        {Object.entries(access.by_type).map(([t, n]) => `${n} ${t.toLowerCase().replace(/_/g, " ")}`).join(", ")}
                        {access.items.some((i) => i.metadata_type === "LOGICAL_COLUMN") &&
                          " (column-level entries counted above, not listed)"}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {access.items.filter((i) => i.metadata_type !== "LOGICAL_COLUMN").map((item) => (
                          <div key={item.metadata_id} style={{
                            display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                            padding: "8px 12px", borderRadius: 8,
                            border: `1px solid ${theme.color.border}`,
                          }}>
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontSize: 13, color: theme.color.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {item.metadata_name}
                              </div>
                              <div style={{ fontSize: 11, color: theme.color.textMuted }}>
                                {item.metadata_type.replace(/_/g, " ")}
                              </div>
                            </div>
                            <span style={{
                              display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0,
                              padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 500,
                              background: item.share_mode === "MODIFY" ? theme.color.successSoft : theme.color.accentSoft,
                              color: item.share_mode === "MODIFY" ? theme.color.success : theme.color.accent2,
                            }}>
                              {item.share_mode === "MODIFY" ? <Pencil size={10} /> : <Eye size={10} />}
                              {item.share_mode === "MODIFY" ? "Edit" : "View"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div style={{
      flex: 1, padding: "10px 12px", borderRadius: 8,
      border: `1px solid ${theme.color.border}`,
    }}>
      <div style={{ fontSize: 18, fontWeight: 600, color: theme.color.textPrimary }}>{value}</div>
      <div style={{ fontSize: 11, color: theme.color.textMuted }}>{label}</div>
    </div>
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
