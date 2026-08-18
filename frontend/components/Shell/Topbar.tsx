import { useState } from "react";
import { ChevronDown, RefreshCw, WifiOff } from "lucide-react";
import { theme } from "@/lib/theme";
import UpdatePill from "./UpdatePill";
import { buildSyncColor, buildSyncLabel } from "./Topbar.helpers";
import type { EntityType, Org, SyncLog } from "@/lib/types";

/** Full offline sentence — rendered AND used as the badge's hover title, since
 *  the visible text ellipsises on a narrow viewport. */
const OFFLINE_MESSAGE = "Instance offline — showing cached data";

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
  /** Non-null when a prerequisite is unmet — disables sync and explains why. */
  blockedReason?: string | null;
  onOrgChange: (org: Org) => void;
  onSync: () => void;
  isSyncing: boolean;
  clusterOnline: boolean | null;
  now: number;
}

export default function Topbar({
  pageTitle, entityType, clusterName, activeOrg, orgs,
  syncLog, syncProgress, blockedReason, onOrgChange, onSync, isSyncing,
  clusterOnline,
  now,
}: TopbarProps) {
  const [orgDropdownOpen, setOrgDropdownOpen] = useState(false);

  const syncLabel = buildSyncLabel(syncLog, isSyncing, syncProgress, now, entityType);
  const syncColor = buildSyncColor(syncLog, isSyncing, now);
  const syncButtonLabel = isSyncing ? "Syncing…" : `Sync ${ENTITY_LABELS[entityType!] ?? ""}`;

  const isOffline = clusterOnline === false;
  const isChecking = clusterOnline === null;
  const syncDisabled = isSyncing || isOffline || isChecking || !!blockedReason;

  return (
    <header style={{
      height: 52, background: theme.color.surface,
      borderBottom: `1px solid ${theme.color.border}`,
      display: "flex", alignItems: "center",
      padding: "0 24px", gap: 12, flexShrink: 0,
    }}>
      {/* Page title + optional offline badge */}
      {/* `minWidth: 0` here is what delivers a narrow-viewport deficit INTO this
          column instead of pushing it onto the org selector (which is rigid and
          would be clipped off-screen — measured at 52/176/276px past the right
          edge at 1024/900/800). It is only safe because BOTH children below can
          absorb it: the h1 and the offline badge each ellipsise. Re-adding
          `flexShrink: 0` to the badge without removing this line is exactly the
          bug this replaced — a rigid child in a zero-floor column paints on top
          of the sync indicator.
          Do NOT add `overflow: hidden` here: per CSS Flexbox 4.5 it computes the
          column's automatic minimum size to 0 (`min-width: auto` applies only
          while the computed overflow is `visible`), so it is not the harmless
          belt-and-braces it looks like — it silently duplicates `minWidth: 0`.
          Measured in headless Chromium at 1440/1280/1152/1024/900/800, offline
          and online: no overlap, and the org selector fits at every width. */}
      <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 10 }}>
        <h1 style={{
          margin: 0, fontSize: 15, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans,
          minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {pageTitle}
        </h1>
        {isOffline && (
          // Shrinkable on purpose (no `flexShrink: 0`): the badge is the thing
          // that degrades once the h1 has given up its width, so the sync
          // indicator, the sync button and the org selector never move. The
          // WifiOff icon stays rigid, so the worst case is still a recognisable
          // offline glyph, and `title` keeps the full sentence discoverable.
          <div
            title={OFFLINE_MESSAGE}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "3px 10px", borderRadius: 20,
              background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`,
              fontSize: 12, color: theme.color.danger, fontFamily: theme.font.sans,
              whiteSpace: "nowrap", minWidth: 0, overflow: "hidden",
            }}
          >
            <WifiOff size={11} style={{ flexShrink: 0 }} />
            <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {OFFLINE_MESSAGE}
            </span>
          </div>
        )}
      </div>

      {/* Sync indicator + button — only on pages with a syncable entity.
          Pages like Jobs or Settings have nothing to sync; showing a red
          "Never synced" pill and a dead button there is just misleading. */}
      {entityType && (
        <>
          <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 96, flexShrink: 0 }}>
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
            title={isOffline ? "Cannot sync — instance is offline" : blockedReason ?? undefined}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 11px", borderRadius: 6, border: "none",
              background: theme.gradient.accent, color: theme.color.onAccent,
              boxShadow: syncDisabled ? "none" : theme.shadow.glowAccent,
              cursor: syncDisabled ? "default" : "pointer",
              fontSize: 12, fontWeight: 600, fontFamily: theme.font.sans,
              opacity: syncDisabled ? 0.5 : 1,
              whiteSpace: "nowrap", flexShrink: 0,
            }}
          >
            <RefreshCw size={12} style={{ animation: isSyncing ? "spin 1s linear infinite" : "none" }} />
            {syncButtonLabel}
          </button>
        </>
      )}

      {/* Update available — renders nothing unless a newer release exists */}
      <UpdatePill />

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
            {activeOrg?.name ?? "Select Org"}
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
