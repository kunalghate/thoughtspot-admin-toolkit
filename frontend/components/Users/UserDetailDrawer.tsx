/**
 * Read-only user audit drawer for the Users page.
 *
 * Opens on row click; shows profile, org/group membership, effective
 * privileges (union of group privileges — "what they can do"), owned-object
 * count, and a lazily loaded live "Access" section ("what they can see").
 * The access fetch hits the ThoughtSpot API, so it only runs on demand.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { X, ShieldCheck, UsersRound, Eye, Pencil, Shield, Search, RotateCw } from "lucide-react";
import { usersApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { SharingPermissionItem, UserAccessResponse, UserDetail, UserListItem } from "@/lib/types";

interface Props {
  clusterId: string;
  orgId: number;
  user: UserListItem;
  onClose: () => void;
}

/**
 * Fetched access, kept for the lifetime of the page.
 *
 * fetch-permissions walks every ACL for the principal and routinely takes tens
 * of seconds, so paying it again just because the admin clicked another row and
 * came back is the single worst thing this drawer can do. Keyed by
 * cluster+org+user; the Refresh button is the escape hatch when it goes stale.
 */
const accessCache = new Map<string, UserAccessResponse>();
const accessKey = (clusterId: string, orgId: number, guid: string) => `${clusterId}:${orgId}:${guid}`;

// Column-level grants are counted in the summary but never listed — a single
// worksheet share expands to hundreds of them and buries everything else.
const isListable = (i: SharingPermissionItem) => i.metadata_type !== "LOGICAL_COLUMN";

const prettyType = (t: string) => t.replace(/_/g, " ").toLowerCase();

export default function UserDetailDrawer({ clusterId, orgId, user, onClose }: Props) {
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [access, setAccess] = useState<UserAccessResponse | null>(
    () => accessCache.get(accessKey(clusterId, orgId, user.ts_guid)) ?? null,
  );
  const [accessLoading, setAccessLoading] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // Filters over the fetched list (491 rows on a real cluster is unreadable flat).
  const [accessQuery, setAccessQuery] = useState("");
  const [accessType, setAccessType] = useState<string>("");

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
    setAccess(accessCache.get(accessKey(clusterId, orgId, user.ts_guid)) ?? null);
    setAccessError(null);
    setAccessQuery("");
    setAccessType("");
    setLoading(true);
    usersApi.get(user.ts_guid, clusterId)
      .then((d) => { if (!cancelled) setDetail(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load user"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [user.ts_guid, clusterId, orgId]);

  // The fetch has no server-side progress to report, so show elapsed time —
  // enough for the admin to tell "slow" apart from "hung".
  useEffect(() => {
    if (!accessLoading) return;
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [accessLoading]);

  const loadAccess = () => {
    const requestedGuid = user.ts_guid;
    const key = accessKey(clusterId, orgId, requestedGuid);
    setAccessError(null);
    setAccessLoading(true);
    usersApi.access(requestedGuid, clusterId, orgId)
      .then((a) => {
        // Cache regardless of which row is open now — the answer is still
        // correct for the user it was requested for.
        accessCache.set(key, a);
        if (requestedGuid === userGuidRef.current) setAccess(a);
      })
      .catch((e) => {
        if (requestedGuid === userGuidRef.current) {
          setAccessError(e instanceof Error ? e.message : "Failed to load access");
        }
      })
      .finally(() => { if (requestedGuid === userGuidRef.current) setAccessLoading(false); });
  };

  // Listable rows bucketed by type, largest bucket first, with the active
  // search + type filter applied.
  const accessGroups = useMemo(() => {
    if (!access) return [];
    const q = accessQuery.trim().toLowerCase();
    const buckets = new Map<string, SharingPermissionItem[]>();
    for (const item of access.items) {
      if (!isListable(item)) continue;
      if (accessType && item.metadata_type !== accessType) continue;
      if (q && !item.metadata_name.toLowerCase().includes(q)) continue;
      const bucket = buckets.get(item.metadata_type);
      if (bucket) bucket.push(item);
      else buckets.set(item.metadata_type, [item]);
    }
    return [...buckets.entries()]
      .map(([type, items]) => ({
        type,
        items: items.sort((a, b) => a.metadata_name.localeCompare(b.metadata_name)),
      }))
      .sort((a, b) => b.items.length - a.items.length);
  }, [access, accessQuery, accessType]);

  const listableTypes = useMemo(() => {
    if (!access) return [];
    return Object.entries(access.by_type)
      .filter(([t]) => t !== "LOGICAL_COLUMN")
      .sort((a, b) => b[1] - a[1]);
  }, [access]);

  const shownCount = accessGroups.reduce((n, g) => n + g.items.length, 0);

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
                      Takes up to a minute or two on content-heavy orgs.
                    </p>
                  </>
                )}
                {accessLoading && (
                  <div>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: theme.color.textMuted }}>
                      <RotateCw size={13} style={{ animation: "spin 1.4s linear infinite" }} />
                      Fetching live access… {elapsed}s
                    </div>
                    <p style={{ margin: "6px 0 0", fontSize: 11, color: theme.color.textMuted }}>
                      ThoughtSpot walks every permission for this user — this can take a minute or
                      two. The result is kept for this session, so reopening the user is instant.
                    </p>
                    <style>{"@keyframes spin { to { transform: rotate(360deg) } }"}</style>
                  </div>
                )}
                {accessError && (
                  <div style={{
                    background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
                    padding: "10px 14px", fontSize: 12, color: theme.color.danger,
                  }}>
                    {accessError}
                  </div>
                )}
                {access && !accessLoading && (
                  access.total === 0 ? (
                    <div style={{ fontSize: 13, color: theme.color.textMuted }}>
                      Nothing is explicitly shared with this user.
                    </div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 8 }}>
                        <div style={{ fontSize: 12, color: theme.color.textMuted }}>
                          {access.total} object{access.total === 1 ? "" : "s"}
                          {access.by_type.LOGICAL_COLUMN
                            ? ` · ${access.by_type.LOGICAL_COLUMN} column-level grants counted, not listed`
                            : ""}
                        </div>
                        <button
                          onClick={loadAccess}
                          title="Re-fetch from ThoughtSpot"
                          style={{
                            display: "inline-flex", alignItems: "center", gap: 4, flexShrink: 0,
                            background: "none", border: "none", padding: 0, cursor: "pointer",
                            fontSize: 11, color: theme.color.accent2, fontFamily: theme.font.sans,
                          }}
                        >
                          <RotateCw size={11} /> Refresh
                        </button>
                      </div>

                      {/* Search */}
                      <div style={{
                        display: "flex", alignItems: "center", gap: 6,
                        border: `1px solid ${theme.color.border}`, background: theme.color.surface,
                        borderRadius: 6, padding: "6px 10px",
                      }}>
                        <Search size={14} style={{ color: theme.color.textMuted, flexShrink: 0 }} />
                        <input
                          type="text"
                          value={accessQuery}
                          placeholder="Search shared objects…"
                          onChange={(e) => setAccessQuery(e.target.value)}
                          style={{
                            flex: 1, minWidth: 0, fontSize: 13, fontFamily: theme.font.sans,
                            border: "none", outline: "none", background: "transparent",
                            color: theme.color.textPrimary,
                          }}
                        />
                      </div>

                      {/* Type filter */}
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                        <TypeChip
                          label="All"
                          active={accessType === ""}
                          onClick={() => setAccessType("")}
                        />
                        {listableTypes.map(([type, count]) => (
                          <TypeChip
                            key={type}
                            label={`${prettyType(type)} ${count}`}
                            active={accessType === type}
                            onClick={() => setAccessType(accessType === type ? "" : type)}
                          />
                        ))}
                      </div>

                      {shownCount === 0 ? (
                        <div style={{ fontSize: 13, color: theme.color.textMuted }}>
                          No shared objects match this filter.
                        </div>
                      ) : (
                        accessGroups.map((group) => (
                          <div key={group.type}>
                            <div style={{
                              fontSize: 11, fontWeight: 600, color: theme.color.textMuted,
                              textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 6,
                            }}>
                              {prettyType(group.type)} ({group.items.length})
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                              {group.items.map((item) => (
                                <div key={item.metadata_id} style={{
                                  display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
                                  padding: "8px 12px", borderRadius: 8,
                                  border: `1px solid ${theme.color.border}`,
                                }}>
                                  <div
                                    title={item.metadata_name}
                                    style={{
                                      minWidth: 0, fontSize: 13, color: theme.color.textPrimary,
                                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                    }}
                                  >
                                    {item.metadata_name}
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
                        ))
                      )}
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

function TypeChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 500,
        cursor: "pointer", fontFamily: theme.font.sans, textTransform: "capitalize",
        border: `1px solid ${active ? theme.color.violetBorder : theme.color.border}`,
        background: active ? theme.color.accentSoft : theme.color.surface,
        color: active ? theme.color.accent2 : theme.color.textMuted,
      }}
    >
      {label}
    </button>
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
