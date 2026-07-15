import { useEffect, useState } from "react";
import { X, Users, User, Shield, Pencil, WifiOff } from "lucide-react";
import { metadataApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { useShell } from "@/components/Shell";
import type { MetadataObject, Permission } from "@/lib/types";

interface Props {
  object: MetadataObject | null;
  clusterId: string;
  orgId: number;
  onClose: () => void;
}

const SHARE_MODE_LABEL: Record<string, string> = {
  READ_ONLY: "View",
  MODIFY:    "Edit",
};

const SHARE_MODE_COLORS: Record<string, { bg: string; color: string }> = {
  READ_ONLY: { bg: theme.color.accentSoft, color: theme.color.accent2 },
  MODIFY:    { bg: theme.color.successSoft, color: theme.color.success },
};

export default function PermissionDrawer({ object, clusterId, orgId, onClose }: Props) {
  const { clusterOnline } = useShell();
  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const isOfflineError = clusterOnline === false;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!object) return;
    setPermissions([]);
    setError(null);
    setLoading(true);

    metadataApi.permissions(object.ts_guid, clusterId, orgId)
      .then((res) => setPermissions(res.permissions))
      .catch((e) => setError(e.message ?? "Failed to load permissions"))
      .finally(() => setLoading(false));
  }, [object?.ts_guid, clusterId, orgId]);

  if (!object) return null;

  const users  = permissions.filter((p) => p.principal_type === "USER");
  const groups = permissions.filter((p) => p.principal_type === "USER_GROUP");

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, background: theme.color.overlay, zIndex: 200,
        }}
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
              Permissions
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, wordBreak: "break-word" }}>
              {object.name}
            </div>
            <div style={{ fontSize: 12, color: theme.color.textMuted, marginTop: 4 }}>
              {object.owner_name && `Owner: ${object.owner_name}`}
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
              Loading permissions…
            </div>
          )}

          {error && isOfflineError && (
            <div style={{
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
              padding: "16px", display: "flex", flexDirection: "column", gap: 8,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, color: theme.color.danger }}>
                <WifiOff size={16} />
                <span style={{ fontSize: 13, fontWeight: 600 }}>Cannot connect to ThoughtSpot</span>
              </div>
              <p style={{ margin: 0, fontSize: 12, color: theme.color.danger }}>
                Permissions are fetched live and are unavailable while the cluster is offline.
                Check your network connection and retry.
              </p>
            </div>
          )}

          {error && !isOfflineError && (
            <div style={{
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`, borderRadius: 8,
              padding: "12px 16px", fontSize: 13, color: theme.color.danger,
            }}>
              {error}
            </div>
          )}

          {!loading && !error && permissions.length === 0 && (
            <div style={{ color: theme.color.textMuted, fontSize: 13, textAlign: "center", marginTop: 40 }}>
              No sharing permissions found.
            </div>
          )}

          {!loading && !error && permissions.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>

              {/* Groups */}
              {groups.length > 0 && (
                <Section
                  title="Groups"
                  icon={<Users size={14} />}
                  items={groups}
                />
              )}

              {/* Users */}
              {users.length > 0 && (
                <Section
                  title="Users"
                  icon={<User size={14} />}
                  items={users}
                />
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        {!loading && !error && (
          <div style={{
            padding: "12px 24px", borderTop: `1px solid ${theme.color.border}`,
            fontSize: 12, color: theme.color.textMuted,
          }}>
            {permissions.length} principal{permissions.length !== 1 ? "s" : ""} with access
          </div>
        )}
      </div>
    </>
  );
}

function Section({ title, icon, items }: { title: string; icon: React.ReactNode; items: Permission[] }) {
  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 12, fontWeight: 600, color: theme.color.textMuted,
        textTransform: "uppercase", letterSpacing: "0.05em",
        marginBottom: 10,
      }}>
        {icon} {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {items.map((p) => {
          const badge = SHARE_MODE_COLORS[p.share_mode] ?? { bg: theme.color.surface3, color: theme.color.textSecondary };
          const BadgeIcon = p.share_mode === "MODIFY" ? Pencil : Shield;
          return (
            <div key={p.principal_id} style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "8px 12px", borderRadius: 8, background: theme.color.surface,
              border: `1px solid ${theme.color.border}`,
            }}>
              <span style={{ fontSize: 13, color: theme.color.textPrimary, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.principal_name}
              </span>
              <span style={{
                display: "flex", alignItems: "center", gap: 4,
                padding: "2px 8px", borderRadius: 12, fontSize: 11, fontWeight: 500,
                background: badge.bg, color: badge.color, flexShrink: 0, marginLeft: 8,
              }}>
                <BadgeIcon size={10} />
                {SHARE_MODE_LABEL[p.share_mode] ?? p.share_mode}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
