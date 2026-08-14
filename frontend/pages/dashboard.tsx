/**
 * Dashboard — the admin's landing page.
 *
 * One aggregate read (dashboardApi.summary) + the per-entity sync log. All of
 * it comes from SQLite, so this renders instantly even when the cluster is
 * offline. Answers, in order: is my data fresh? what's on this cluster? is
 * anything failing? what did we change recently?
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Archive, ArrowRight, Briefcase, FolderSearch, Tags, Users, UsersRound } from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { dashboardApi, syncApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { DashboardSummary, EntityType, SyncLog } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  LIVEBOARD: "Liveboards",
  ANSWER: "Answers",
  WORKSHEET: "Worksheets",
  LOGICAL_TABLE: "Worksheets",
  ONE_TO_ONE_LOGICAL: "Tables",
  AGGR_WORKSHEET: "Agg Worksheets",
  SQL_VIEW: "SQL Views",
  USER_DEFINED: "User Defined",
};

const JOB_LABELS: Record<string, string> = {
  "sync:users": "User sync",
  "sync:groups": "Group sync",
  "sync:metadata": "Metadata sync",
  "sync:tags": "Tag sync",
  "sync:dependencies": "Lineage sync",
  "sync:orgs": "Org sync",
  lineage_deep_index: "Lineage deep index",
  archive: "Archiver action",
  archive_dryrun: "Archiver dry run",
  archive_restore: "Restore",
  bulk_delete: "Bulk delete",
  bulk_delete_dryrun: "Delete dry run",
  bulk_share: "Bulk sharing",
  bulk_share_dryrun: "Sharing dry run",
  user_delete: "User delete",
  user_delete_dryrun: "User delete dry run",
  user_transfer_ownership: "Ownership transfer",
  user_transfer_sharing: "Sharing transfer",
};

// Entities shown in the freshness card, with where clicking takes you.
const FRESHNESS_ROWS: { entity: EntityType; label: string; href: string }[] = [
  { entity: "metadata", label: "Metadata", href: "/metadata" },
  { entity: "users", label: "Users", href: "/users" },
  { entity: "groups", label: "Groups", href: "/groups" },
  { entity: "tags", label: "Tags", href: "/archiver" },
  { entity: "dependencies", label: "Lineage", href: "/relationships" },
];

/** Relative time with minute/hour granularity; backend sends naive-UTC ISO. */
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const mins = Math.floor((Date.now() - t) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

function freshnessColor(log: SyncLog | undefined): string {
  if (!log || log.status === "NOT_SYNCED" || log.status === "FAILED") return theme.color.danger;
  if (!log.synced_at) return theme.color.textMuted;
  const hours = (Date.now() - new Date(log.synced_at + "Z").getTime()) / 3_600_000;
  if (hours < 1) return theme.color.success;
  if (hours < 6) return theme.color.textMuted;
  return theme.color.warn;
}

function statusPillColors(status: string): { bg: string; fg: string } {
  if (status === "COMPLETE" || status === "SUCCESS") return { bg: theme.color.successSoft, fg: theme.color.success };
  if (status === "FAILED") return { bg: theme.color.dangerSoft, fg: theme.color.danger };
  if (status === "PARTIAL") return { bg: theme.color.warnSoft, fg: theme.color.warn };
  return { bg: theme.color.surface3, fg: theme.color.textMuted };
}

export default function DashboardPage() {
  return (
    <AppShell pageTitle="Dashboard">
      <DashboardContent />
    </AppShell>
  );
}

function DashboardContent() {
  const { activeCluster, activeOrg } = useShell();
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [syncLogs, setSyncLogs] = useState<SyncLog[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!activeCluster?.id || activeOrg?.org_id == null) return;
    let cancelled = false;
    dashboardApi.summary(activeCluster.id, activeOrg.org_id)
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    syncApi.status(activeCluster.id, activeOrg.org_id)
      .then((logs) => { if (!cancelled) setSyncLogs(logs); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [activeCluster?.id, activeOrg?.org_id]);

  if (error) {
    return (
      <div style={{ padding: 28 }}>
        <div style={{
          padding: "12px 16px", borderRadius: 8, fontSize: 13, fontFamily: theme.font.sans,
          background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`,
          color: theme.color.danger,
        }}>
          Couldn't load the dashboard: {error}
        </div>
      </div>
    );
  }

  if (!data) return null;

  const { counts } = data;
  const neverSynced = counts.objects_total === 0 && counts.users === 0;

  return (
    <div style={{ padding: 28, display: "flex", flexDirection: "column", gap: 20, maxWidth: 1200 }}>

      {neverSynced && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
          borderRadius: 8, background: theme.color.accentSoft,
          border: `1px solid ${theme.color.accentBorder}`,
          fontSize: 13, color: theme.color.textPrimary, fontFamily: theme.font.sans,
        }}>
          <AlertTriangle size={15} style={{ color: theme.color.warn, flexShrink: 0 }} />
          <span>
            Nothing synced yet — open <Link href="/metadata" style={{ color: theme.color.accent2, fontWeight: 600 }}>Metadata</Link> or{" "}
            <Link href="/users" style={{ color: theme.color.accent2, fontWeight: 600 }}>Users</Link> and
            press Sync to pull data from your cluster.
          </span>
        </div>
      )}

      {/* Stat tiles */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
        <StatTile icon={Users} label="Users" value={counts.users} href="/users" />
        <StatTile icon={UsersRound} label="Groups" value={counts.groups} href="/groups" />
        <StatTile icon={FolderSearch} label="Content objects" value={counts.objects_total} href="/metadata" />
        <StatTile icon={Tags} label="Tags" value={counts.tags} href="/archiver" />
        <StatTile
          icon={Archive}
          label="Stale (90d unused)"
          value={counts.stale_90d}
          href="/archiver"
          tone={counts.stale_90d > 0 ? "warn" : undefined}
        />
        <StatTile
          icon={Briefcase}
          label="Failed jobs (7d)"
          value={data.failed_jobs_7d}
          href="/jobs"
          tone={data.failed_jobs_7d > 0 ? "danger" : undefined}
        />
      </div>

      {/* Two-column detail area */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <ContentByTypeCard byType={counts.objects_by_type} total={counts.objects_total} />
          <FreshnessCard syncLogs={syncLogs} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <RecentJobsCard jobs={data.recent_jobs} />
          <RecentActivityCard activity={data.recent_activity} />
        </div>
      </div>
    </div>
  );
}

// ── Pieces ─────────────────────────────────────────────────────────────────────

function StatTile({ icon: Icon, label, value, href, tone }: {
  icon: typeof Users; label: string; value: number; href: string;
  tone?: "warn" | "danger";
}) {
  const valueColor =
    tone === "danger" ? theme.color.danger :
    tone === "warn" ? theme.color.warn :
    theme.color.textPrimary;
  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div
        style={{
          padding: "14px 16px", borderRadius: 9, cursor: "pointer",
          background: theme.color.surface, border: `1px solid ${theme.color.border}`,
          display: "flex", flexDirection: "column", gap: 8, height: "100%",
          transition: "border-color 120ms ease",
        }}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = theme.color.borderLight; }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = theme.color.border; }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Icon size={13} style={{ color: theme.color.textMuted }} />
          <span style={{ fontSize: 11.5, fontWeight: 500, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
            {label}
          </span>
        </div>
        <span style={{ fontSize: 26, fontWeight: 700, color: valueColor, fontFamily: theme.font.mono, lineHeight: 1 }}>
          {value.toLocaleString()}
        </span>
      </div>
    </Link>
  );
}

function Card({ title, action, children }: {
  title: string;
  action?: { label: string; href: string };
  children: React.ReactNode;
}) {
  return (
    <section style={{
      background: theme.color.surface, border: `1px solid ${theme.color.border}`,
      borderRadius: 9, padding: 18,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14 }}>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
          {title}
        </h3>
        {action && (
          <Link href={action.href} style={{
            display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12,
            color: theme.color.accent2, textDecoration: "none", fontFamily: theme.font.sans, fontWeight: 500,
          }}>
            {action.label} <ArrowRight size={11} />
          </Link>
        )}
      </div>
      {children}
    </section>
  );
}

/** Single-hue horizontal bars, sorted by count, direct-labeled. */
function ContentByTypeCard({ byType, total }: { byType: Record<string, number>; total: number }) {
  const rows = Object.entries(byType)
    .map(([type, count]) => ({ label: TYPE_LABELS[type] ?? type, count }))
    .sort((a, b) => b.count - a.count);
  const max = rows[0]?.count ?? 0;

  return (
    <Card title="Content by type" action={{ label: "Explore", href: "/metadata" }}>
      {rows.length === 0 ? (
        <EmptyLine text="No content synced yet." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
          {rows.map((r) => (
            <div key={r.label} style={{ display: "grid", gridTemplateColumns: "110px 1fr 56px", gap: 10, alignItems: "center" }}>
              <span style={{ fontSize: 12, color: theme.color.textSecondary, fontFamily: theme.font.sans, whiteSpace: "nowrap" }}>
                {r.label}
              </span>
              <div style={{ height: 8, borderRadius: 4, background: theme.color.surface3, overflow: "hidden" }}>
                <div style={{
                  width: max ? `${Math.max(2, (r.count / max) * 100)}%` : 0,
                  height: "100%", borderRadius: 4, background: theme.gradient.accent,
                }} />
              </div>
              <span style={{ fontSize: 12, color: theme.color.textPrimary, fontFamily: theme.font.mono, textAlign: "right" }}>
                {r.count.toLocaleString()}
              </span>
            </div>
          ))}
          <div style={{ marginTop: 2, fontSize: 11.5, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
            {total.toLocaleString()} objects total
          </div>
        </div>
      )}
    </Card>
  );
}

function FreshnessCard({ syncLogs }: { syncLogs: SyncLog[] }) {
  const byEntity = new Map(syncLogs.map((l) => [l.entity_type, l]));
  return (
    <Card title="Data freshness">
      <div style={{ display: "flex", flexDirection: "column" }}>
        {FRESHNESS_ROWS.map(({ entity, label, href }, i) => {
          const log = byEntity.get(entity);
          const synced = log && log.status !== "NOT_SYNCED" && log.synced_at;
          return (
            <Link key={entity} href={href} style={{ textDecoration: "none" }}>
              <div style={{
                display: "flex", alignItems: "center", gap: 8, padding: "8px 2px",
                borderTop: i > 0 ? `1px solid ${theme.color.border}` : "none",
                cursor: "pointer",
              }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: freshnessColor(log), flexShrink: 0 }} />
                <span style={{ fontSize: 12.5, color: theme.color.textPrimary, fontFamily: theme.font.sans, fontWeight: 500 }}>
                  {label}
                </span>
                {log?.status === "FAILED" && (
                  <span style={{ fontSize: 11, color: theme.color.danger, fontFamily: theme.font.sans }}>sync failed</span>
                )}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
                  {synced
                    ? `${(log!.record_count ?? 0).toLocaleString()} · ${timeAgo(log!.synced_at)}`
                    : "never synced"}
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </Card>
  );
}

function RecentJobsCard({ jobs }: { jobs: DashboardSummary["recent_jobs"] }) {
  return (
    <Card title="Recent jobs" action={{ label: "All jobs", href: "/jobs" }}>
      {jobs.length === 0 ? (
        <EmptyLine text="No jobs yet — syncs and bulk operations appear here." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {jobs.map((j, i) => {
            const pill = statusPillColors(j.status);
            return (
              <div key={j.id} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "8px 2px",
                borderTop: i > 0 ? `1px solid ${theme.color.border}` : "none",
              }}>
                <span style={{ fontSize: 12.5, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
                  {JOB_LABELS[j.job_type] ?? j.job_type}
                </span>
                <span style={{
                  padding: "1px 8px", borderRadius: 10, fontSize: 10.5, fontWeight: 600,
                  background: pill.bg, color: pill.fg, fontFamily: theme.font.sans, letterSpacing: "0.03em",
                }}>
                  {j.status}
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
                  {timeAgo(j.created_at)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function RecentActivityCard({ activity }: { activity: DashboardSummary["recent_activity"] }) {
  return (
    <Card title="Recent admin activity">
      {activity.length === 0 ? (
        <EmptyLine text="No admin actions yet — deletions, sharing changes, and transfers appear here." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {activity.map((a, i) => {
            const pill = statusPillColors(a.status);
            return (
              <div key={`${a.kind}-${i}`} style={{
                display: "flex", alignItems: "center", gap: 10, padding: "8px 2px",
                borderTop: i > 0 ? `1px solid ${theme.color.border}` : "none",
              }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: pill.fg, flexShrink: 0 }} />
                <span style={{ fontSize: 12.5, color: theme.color.textPrimary, fontFamily: theme.font.sans, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {a.label}
                </span>
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans, flexShrink: 0 }}>
                  {timeAgo(a.timestamp)}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function EmptyLine({ text }: { text: string }) {
  return (
    <p style={{ margin: 0, fontSize: 12.5, color: theme.color.textMuted, fontFamily: theme.font.sans, lineHeight: 1.5 }}>
      {text}
    </p>
  );
}
