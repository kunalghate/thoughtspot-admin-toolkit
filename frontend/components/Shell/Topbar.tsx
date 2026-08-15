import { useState } from "react";
import { ChevronDown, RefreshCw, WifiOff } from "lucide-react";
import { theme } from "@/lib/theme";
import type { EntityType, Org, SyncLog } from "@/lib/types";

const ENTITY_LABELS: Partial<Record<EntityType, string>> = {
  metadata: "Metadata",
  users: "Users",
  groups: "Groups",
  tags: "Tags",
  dependencies: "Lineage",
};

interface TopbarProps {
  pageTitle: string;
  entityType?: EntityType;
  clusterName: string;
  activeOrg: Org | null;
  orgs: Org[];
  syncLog: SyncLog | null;
  syncProgress: { processed: number; total: number } | null;
  onOrgChange: (org: Org) => void;
  onSync: () => void;
  isSyncing: boolean;
  clusterOnline: boolean | null;
  now: number;
}

export default function Topbar({
  pageTitle, entityType, clusterName, activeOrg, orgs,
  syncLog, syncProgress, onOrgChange, onSync, isSyncing,
  clusterOnline,
  now,
}: TopbarProps) {
  const [orgDropdownOpen, setOrgDropdownOpen] = useState(false);

  const syncLabel = buildSyncLabel(syncLog, isSyncing, syncProgress, now);
  const syncColor = buildSyncColor(syncLog, isSyncing, now);
  const syncButtonLabel = isSyncing ? "Syncing…" : `Sync ${ENTITY_LABELS[entityType!] ?? ""}`;

  const isOffline = clusterOnline === false;
  const isChecking = clusterOnline === null;
  const syncDisabled = isSyncing || isOffline || isChecking;

  return (
    <header style={{
      height: 52, background: theme.color.surface,
      borderBottom: `1px solid ${theme.color.border}`,
      display: "flex", alignItems: "center",
      padding: "0 24px", gap: 12, flexShrink: 0,
    }}>
      {/* Page title + optional offline badge */}
      <div style={{ flex: 1, display: "flex", alignItems: "center", gap: 10 }}>
        <h1 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
          {pageTitle}
        </h1>
        {isOffline && (
          <div style={{
            display: "flex", alignItems: "center", gap: 5,
            padding: "3px 10px", borderRadius: 20,
            background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`,
            fontSize: 12, color: theme.color.danger, fontFamily: theme.font.sans,
          }}>
            <WifiOff size={11} />
            Cluster offline — showing cached data
          </div>
        )}
      </div>

      {/* Sync indicator + button — only on pages with a syncable entity.
          Pages like Jobs or Settings have nothing to sync; showing a red
          "Never synced" pill and a dead button there is just misleading. */}
      {entityType && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 96 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 6, height: 6, borderRadius: "50%", background: syncColor }} />
              <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
                {syncLabel}
              </span>
            </div>
            {isSyncing && <SyncProgressBar progress={syncProgress} />}
          </div>

          <button
            onClick={onSync}
            disabled={syncDisabled}
            title={isOffline ? "Cannot sync — cluster is offline" : undefined}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 11px", borderRadius: 6, border: "none",
              background: theme.gradient.accent, color: theme.color.onAccent,
              boxShadow: syncDisabled ? "none" : theme.shadow.glowAccent,
              cursor: syncDisabled ? "default" : "pointer",
              fontSize: 12, fontWeight: 600, fontFamily: theme.font.sans,
              opacity: syncDisabled ? 0.5 : 1,
            }}
          >
            <RefreshCw size={12} style={{ animation: isSyncing ? "spin 1s linear infinite" : "none" }} />
            {syncButtonLabel}
          </button>
        </>
      )}

      {/* Org selector */}
      <div style={{ position: "relative" }}>
        <button
          onClick={() => setOrgDropdownOpen((o) => !o)}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "5px 10px", borderRadius: 6, cursor: "pointer",
            border: `1px solid ${theme.color.borderLight}`, background: theme.color.surface,
            fontFamily: theme.font.sans,
          }}
        >
          {/* Cluster name — static, non-clickable */}
          <span style={{ fontSize: 11, color: theme.color.textMuted, fontWeight: 400 }}>
            {clusterName}
          </span>
          <span style={{ color: theme.color.border }}>·</span>
          {/* Active org — clickable */}
          <span style={{ fontSize: 12, color: theme.color.textPrimary, fontWeight: 500 }}>
            {activeOrg?.name ?? "All Orgs"}
          </span>
          <ChevronDown size={12} style={{ color: theme.color.textMuted }} />
        </button>

        {orgDropdownOpen && (
          <OrgDropdown
            orgs={orgs}
            activeOrg={activeOrg}
            onSelect={(org) => { onOrgChange(org); setOrgDropdownOpen(false); }}
            onClose={() => setOrgDropdownOpen(false)}
          />
        )}
      </div>
    </header>
  );
}

function OrgDropdown({ orgs, activeOrg, onSelect, onClose }: {
  orgs: Org[];
  activeOrg: Org | null;
  onSelect: (org: Org) => void;
  onClose: () => void;
}) {
  const [orgSearch, setOrgSearch] = useState("");
  const filtered = orgs.filter((o) =>
    o.name.toLowerCase().includes(orgSearch.toLowerCase())
  );

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: "fixed", inset: 0, zIndex: 10 }} />

      <div style={{
        position: "absolute", right: 0, top: "calc(100% + 6px)",
        background: theme.color.surface, border: `1px solid ${theme.color.border}`,
        borderRadius: 8, boxShadow: theme.shadow.md,
        minWidth: 220, zIndex: 20, overflow: "hidden",
        fontFamily: theme.font.sans,
      }}>
        <div style={{ padding: "8px 12px 6px", borderBottom: `1px solid ${theme.color.border}` }}>
          <span style={{ fontSize: 10, color: theme.color.textMuted, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 500 }}>
            Switch Org
          </span>
        </div>

        {orgs.length > 8 && (
          <div style={{ padding: "6px 10px", borderBottom: `1px solid ${theme.color.border}` }}>
            <input
              autoFocus
              placeholder="Search orgs…"
              value={orgSearch}
              onChange={(e) => setOrgSearch(e.target.value)}
              style={{
                width: "100%", padding: "5px 8px", fontSize: 12,
                border: `1px solid ${theme.color.borderLight}`, borderRadius: 5,
                background: theme.color.bg, color: theme.color.textPrimary, outline: "none",
                fontFamily: theme.font.sans, boxSizing: "border-box",
              }}
            />
          </div>
        )}

        <div style={{ maxHeight: 280, overflowY: "auto" }}>
          {filtered.map((org) => {
            const active = activeOrg?.org_id === org.org_id;
            return (
              <button
                key={org.org_id}
                onClick={() => onSelect(org)}
                style={{
                  width: "100%", display: "flex", alignItems: "center",
                  justifyContent: "space-between", padding: "8px 12px",
                  background: active ? theme.color.accentSoft : "transparent",
                  border: "none", cursor: "pointer", textAlign: "left",
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500, color: active ? theme.color.accent2 : theme.color.textPrimary }}>
                    {org.name}
                  </div>
                  {org.org_id === 0 && (
                    <div style={{ fontSize: 10, color: theme.color.textMuted }}>Primary org</div>
                  )}
                </div>
                {active && <span style={{ fontSize: 12, color: theme.color.accent }}>✓</span>}
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildSyncLabel(
  log: SyncLog | null,
  isSyncing: boolean,
  progress: { processed: number; total: number } | null,
  now: number,
): string {
  if (isSyncing) {
    if (progress && progress.total > 0) return `Syncing ${progress.processed.toLocaleString()} / ${progress.total.toLocaleString()}`;
    // No grand total from the TS API — show the live running count instead.
    if (progress && progress.processed > 0) return `Syncing ${progress.processed.toLocaleString()}…`;
    return "Syncing…";
  }
  if (!log || log.status === "NOT_SYNCED") return "Never synced";
  if (log.status === "FAILED") return "Sync failed";
  // An IN_PROGRESS marker is written before the sync deletes the cache and is
  // only cleared when it finishes. `isSyncing` above is per-mount local state,
  // so after a reload or in a second tab it is false while a perfectly healthy
  // sync is still running — this branch is the ONLY thing that reports it.
  // It must not claim "interrupted": a stranded marker and a running sync are
  // indistinguishable from here, and "Syncing…" is the honest reading of both.
  if (log.status === "IN_PROGRESS") return "Syncing…";
  if (!log.synced_at) return "Unknown";

  const minutes = Math.floor((now - new Date(log.synced_at + "Z").getTime()) / 60_000);
  if (minutes < 2)  return "Synced just now";
  if (minutes < 60) return `Synced ${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24)   return `Synced ${hours}h ago`;
  return `Synced ${Math.floor(hours / 24)}d ago`;
}

function buildSyncColor(log: SyncLog | null, isSyncing: boolean, now: number): string {
  if (isSyncing) return theme.color.accent;
  if (!log || log.status === "NOT_SYNCED") return theme.color.danger;
  if (log.status === "FAILED") return theme.color.danger;
  // Same token as the local isSyncing branch above — in-flight, not alarming.
  if (log.status === "IN_PROGRESS") return theme.color.accent;
  if (!log.synced_at) return theme.color.textMuted;

  const hours = (now - new Date(log.synced_at + "Z").getTime()) / 3_600_000;
  if (hours < 1) return theme.color.success;
  if (hours < 6) return theme.color.textMuted;
  return theme.color.warn;
}

/**
 * Thin progress bar shown under the sync indicator while a sync runs.
 * Determinate (filled to %) when the job reports a total; otherwise an
 * indeterminate sliding segment, since the TS search API gives no grand total.
 */
function SyncProgressBar({ progress }: { progress: { processed: number; total: number } | null }) {
  const determinate = !!progress && progress.total > 0;
  const pct = determinate ? Math.min(100, Math.round((progress!.processed / progress!.total) * 100)) : 0;

  return (
    <div
      style={{
        width: "100%", height: 3, borderRadius: 2,
        background: theme.color.surface3, overflow: "hidden", position: "relative",
      }}
    >
      {determinate ? (
        <div
          style={{
            width: `${pct}%`, height: "100%", background: theme.gradient.accent,
            borderRadius: 2, transition: "width 240ms ease",
          }}
        />
      ) : (
        <div
          style={{
            position: "absolute", top: 0, left: 0, height: "100%", width: "40%",
            background: theme.gradient.accent, borderRadius: 2,
            animation: "syncSlide 1.1s ease-in-out infinite",
          }}
        />
      )}
    </div>
  );
}
