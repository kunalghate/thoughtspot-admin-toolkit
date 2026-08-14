/**
 * Dashboard — the admin's landing page.
 *
 * One aggregate read (dashboardApi.summary). All of it comes from SQLite, so
 * this renders instantly even when the cluster is offline. Answers, in order:
 * what needs my attention? what's on this cluster? what did we change
 * recently?
 *
 * Two rules the page is built around:
 *   - Never state a number we don't have. An entity that has never synced
 *     renders as "—" with a Sync action, not as a confident 0.
 *   - Problems outrank inventory. Anything actionable lives above the fold in
 *     "Needs attention"; the counts below it are reference material.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle, Archive, ArrowRight, Briefcase, CheckCircle2, FolderSearch,
  Loader2, RefreshCw, Users, UsersRound,
} from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { dashboardApi, syncApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import type { DashboardSummary, EntityType } from "@/lib/types";

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

// Entities the dashboard can re-sync in place (retry on a failed sync job,
// or the "never synced" nudge on a tile).
const SYNCABLE_ENTITIES: EntityType[] = ["metadata", "users", "groups", "tags", "dependencies"];

/** While a job is in flight, re-read the summary this often. */
const LIVE_POLL_MS = 3_000;
/** Otherwise, refresh quietly so relative timestamps stay honest. */
const IDLE_POLL_MS = 60_000;

/** A failed sync job maps back to the entity whose sync can be retried. */
function retryableEntity(jobType: string): EntityType | null {
  const entity = jobType.startsWith("sync:") ? jobType.slice(5) : null;
  return SYNCABLE_ENTITIES.includes(entity as EntityType) ? (entity as EntityType) : null;
}

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
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<Set<string>>(new Set());
  const cancelled = useRef(false);

  const clusterId = activeCluster?.id;
  const orgId = activeOrg?.org_id;

  const load = useCallback(async () => {
    if (!clusterId || orgId == null) return;
    try {
      const summary = await dashboardApi.summary(clusterId, orgId);
      if (cancelled.current) return;
      setData(summary);
      setError(null);
    } catch (e) {
      if (cancelled.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [clusterId, orgId]);

  useEffect(() => {
    cancelled.current = false;
    setData(null);
    void load();
    return () => { cancelled.current = true; };
  }, [load]);

  // Poll fast while work is in flight, slowly otherwise — this is also what
  // keeps the relative timestamps from freezing at mount time.
  const busy = (data?.running_jobs.length ?? 0) > 0 || syncing.size > 0;
  useEffect(() => {
    if (!clusterId || orgId == null) return;
    const iv = setInterval(() => { void load(); }, busy ? LIVE_POLL_MS : IDLE_POLL_MS);
    return () => clearInterval(iv);
  }, [load, busy, clusterId, orgId]);

  const triggerSync = useCallback(async (entity: EntityType) => {
    if (!clusterId || orgId == null) return;
    setSyncing((prev) => new Set(prev).add(entity));
    try {
      await syncApi.trigger(clusterId, orgId, entity);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing((prev) => {
        const next = new Set(prev);
        next.delete(entity);
        return next;
      });
    }
  }, [clusterId, orgId, load]);

  if (error && !data) return <PageError message={error} />;
  if (!data) return <DashboardSkeleton />;

  const { counts, synced, deltas, attention } = data;
  const neverSynced = !synced.metadata && !synced.users;

  return (
    <div style={{ padding: 28, display: "flex", flexDirection: "column", gap: 20, maxWidth: 1320 }}>

      {error && <PageError message={error} inline />}

      {neverSynced && (
        <Banner>
          Nothing synced yet — open <Link href="/metadata" style={linkStyle}>Metadata</Link> or{" "}
          <Link href="/users" style={linkStyle}>Users</Link> and press Sync to pull data from your cluster.
        </Banner>
      )}

      <RunningJobsBar jobs={data.running_jobs} />

      <AttentionBand
        attention={attention}
        staleCount={counts.stale_90d}
        failedJobs={data.failed_jobs_7d}
        neverSynced={neverSynced}
      />

      {/* Inventory — reference counts, deliberately quieter than the band above. */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
        <StatTile icon={Users} label="Users" value={counts.users} delta={deltas.users}
                  synced={synced.users} href="/users"
                  onSync={() => triggerSync("users")} syncing={syncing.has("users")} />
        <StatTile icon={UsersRound} label="Groups" value={counts.groups} delta={deltas.groups}
                  synced={synced.groups} href="/groups"
                  onSync={() => triggerSync("groups")} syncing={syncing.has("groups")} />
        <StatTile icon={FolderSearch} label="Content objects" value={counts.objects_total} delta={deltas.metadata}
                  synced={synced.metadata} href="/metadata"
                  onSync={() => triggerSync("metadata")} syncing={syncing.has("metadata")} />
        <StatTile icon={Archive} label="Archivable content" value={counts.archivable_total}
                  synced={synced.metadata} href="/archiver"
                  hint="Liveboards + answers — the only types the Archiver can act on" />
        <StatTile icon={Briefcase} label="Jobs (7d failed)" value={data.failed_jobs_7d}
                  synced href="/jobs" tone={data.failed_jobs_7d > 0 ? "danger" : undefined} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", gap: 16, alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <ContentByTypeCard byType={counts.objects_by_type} total={counts.objects_total} />
          <RecentActivityCard activity={data.recent_activity} />
        </div>
        <RecentJobsCard jobs={data.recent_jobs} onRetry={triggerSync} syncing={syncing} />
      </div>
    </div>
  );
}

// ── Pieces ─────────────────────────────────────────────────────────────────────

const linkStyle = { color: theme.color.accent2, fontWeight: 600 } as const;

function PageError({ message, inline }: { message: string; inline?: boolean }) {
  return (
    <div style={{ padding: inline ? 0 : 28 }}>
      <div style={{
        padding: "12px 16px", borderRadius: 8, fontSize: 13, fontFamily: theme.font.sans,
        background: theme.color.dangerSoft, border: `1px solid ${theme.color.dangerBorder}`,
        color: theme.color.danger,
      }}>
        Couldn&apos;t load the dashboard: {message}
      </div>
    </div>
  );
}

function Banner({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10, padding: "12px 16px",
      borderRadius: 8, background: theme.color.accentSoft,
      border: `1px solid ${theme.color.accentBorder}`,
      fontSize: 13, color: theme.color.textPrimary, fontFamily: theme.font.sans,
    }}>
      <AlertTriangle size={15} style={{ color: theme.color.warn, flexShrink: 0 }} />
      <span>{children}</span>
    </div>
  );
}

/** Grey placeholders instead of a blank white page on first paint. */
function DashboardSkeleton() {
  return (
    <div style={{ padding: 28, display: "flex", flexDirection: "column", gap: 20, maxWidth: 1320 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 12 }}>
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} style={{
            height: 74, borderRadius: 9, background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
          }} />
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(380px, 1fr))", gap: 16 }}>
        {Array.from({ length: 2 }, (_, i) => (
          <div key={i} style={{
            height: 220, borderRadius: 9, background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
          }} />
        ))}
      </div>
    </div>
  );
}

/**
 * Everything the admin might need to act on, in one band. When it is empty it
 * says so — a dashboard that can only ever look worried is no signal at all.
 */
function AttentionBand({ attention, staleCount, failedJobs, neverSynced }: {
  attention: DashboardSummary["attention"];
  staleCount: number;
  failedJobs: number;
  neverSynced: boolean;
}) {
  const items = [
    { n: failedJobs, label: `job${failedJobs === 1 ? "" : "s"} failed in the last 7 days`, href: "/jobs", tone: "danger" as const },
    { n: attention.orphaned_content, label: "objects owned by a user who no longer exists", href: "/users", tone: "danger" as const },
    { n: attention.inactive_users, label: "inactive users still on the cluster", href: "/users", tone: "warn" as const },
    { n: attention.empty_groups, label: "groups with no members", href: "/groups", tone: "warn" as const },
    { n: attention.users_without_group, label: "users belong to no group", href: "/users", tone: "warn" as const },
    { n: staleCount, label: "liveboards/answers unused for 90+ days", href: "/archiver", tone: "warn" as const },
  ].filter((i) => i.n > 0);

  if (neverSynced) return null;

  if (items.length === 0) {
    return (
      <section style={{
        display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderRadius: 9,
        background: theme.color.successSoft, border: `1px solid ${theme.color.successBorder}`,
      }}>
        <CheckCircle2 size={15} style={{ color: theme.color.success, flexShrink: 0 }} />
        <span style={{ fontSize: 12.5, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
          Nothing needs attention — no failed jobs, orphaned content, or empty groups.
        </span>
      </section>
    );
  }

  return (
    <Card title="Needs attention">
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 8 }}>
        {items.map((item) => (
          <Link key={item.label} href={item.href} style={{ textDecoration: "none" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "6px 2px" }}>
              <span style={{
                fontSize: 17, fontWeight: 700, fontFamily: theme.font.mono, lineHeight: 1,
                color: item.tone === "danger" ? theme.color.danger : theme.color.warn,
              }}>
                {item.n.toLocaleString()}
              </span>
              <span style={{ fontSize: 12.5, color: theme.color.textSecondary, fontFamily: theme.font.sans }}>
                {item.label}
              </span>
            </div>
          </Link>
        ))}
      </div>
    </Card>
  );
}

function RunningJobsBar({ jobs }: { jobs: DashboardSummary["running_jobs"] }) {
  if (jobs.length === 0) return null;
  return (
    <section style={{
      background: theme.color.surface, border: `1px solid ${theme.color.border}`,
      borderRadius: 9, padding: "12px 18px", display: "flex", flexDirection: "column", gap: 8,
    }}>
      {jobs.map((j) => {
        const pct = j.total > 0 ? Math.round((j.progress / j.total) * 100) : null;
        return (
          <div key={j.id} style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <Loader2 size={13} style={{ color: theme.color.accent2, flexShrink: 0 }} />
            <span style={{ fontSize: 12.5, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
              {JOB_LABELS[j.job_type] ?? j.job_type} running
            </span>
            {pct !== null && (
              <div style={{ flex: 1, maxWidth: 220, height: 6, borderRadius: 3, background: theme.color.surface3, overflow: "hidden" }}>
                <div style={{ width: `${pct}%`, height: "100%", background: theme.color.accent2 }} />
              </div>
            )}
            <span style={{ flex: pct === null ? 1 : undefined }} />
            <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.mono }}>
              {pct !== null ? `${j.progress.toLocaleString()} / ${j.total.toLocaleString()}` : j.status}
            </span>
          </div>
        );
      })}
    </section>
  );
}

function StatTile({ icon: Icon, label, value, href, tone, delta, synced, hint, onSync, syncing }: {
  icon: typeof Users; label: string; value: number; href: string;
  tone?: "warn" | "danger";
  delta?: number;
  /** false → this entity has never synced, so `value` is unknown, not zero. */
  synced?: boolean;
  hint?: string;
  onSync?: () => void;
  syncing?: boolean;
}) {
  const known = synced !== false;
  const valueColor =
    !known ? theme.color.textMuted :
    tone === "danger" ? theme.color.danger :
    tone === "warn" ? theme.color.warn :
    theme.color.textPrimary;

  return (
    <Link href={href} style={{ textDecoration: "none" }}>
      <div
        title={hint}
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
        <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span style={{ fontSize: 26, fontWeight: 700, color: valueColor, fontFamily: theme.font.mono, lineHeight: 1 }}>
            {known ? value.toLocaleString() : "—"}
          </span>
          {known && delta !== undefined && delta !== 0 && (
            <span style={{
              fontSize: 11.5, fontFamily: theme.font.mono,
              color: delta > 0 ? theme.color.success : theme.color.warn,
            }}>
              {delta > 0 ? "+" : ""}{delta.toLocaleString()}
            </span>
          )}
        </div>
        {!known && (
          onSync ? (
            <button
              onClick={(e) => { e.preventDefault(); onSync(); }}
              disabled={syncing}
              style={{
                alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 4,
                padding: 0, border: "none", background: "none", cursor: syncing ? "default" : "pointer",
                fontSize: 11.5, fontFamily: theme.font.sans, fontWeight: 500, color: theme.color.accent2,
              }}
            >
              <RefreshCw size={10} /> {syncing ? "Syncing…" : "Never synced — sync now"}
            </button>
          ) : (
            <span style={{ fontSize: 11.5, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              never synced
            </span>
          )
        )}
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

/** Compact two-column list — a sorted count table, not a chart pretending to be one. */
function ContentByTypeCard({ byType, total }: { byType: Record<string, number>; total: number }) {
  const rows = Object.entries(byType)
    .map(([type, count]) => ({ label: TYPE_LABELS[type] ?? type, count }))
    .sort((a, b) => b.count - a.count);

  return (
    <Card title="Content by type" action={{ label: "Explore", href: "/metadata" }}>
      {rows.length === 0 ? (
        <EmptyLine text="No content synced yet." />
      ) : (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", columnGap: 20 }}>
            {rows.map((r) => (
              <div key={r.label} style={{
                display: "flex", alignItems: "baseline", justifyContent: "space-between",
                gap: 10, padding: "5px 0",
              }}>
                <span style={{ fontSize: 12, color: theme.color.textSecondary, fontFamily: theme.font.sans }}>
                  {r.label}
                </span>
                <span style={{ fontSize: 12, color: theme.color.textPrimary, fontFamily: theme.font.mono }}>
                  {r.count.toLocaleString()}
                </span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 11.5, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
            {total.toLocaleString()} objects total
          </div>
        </>
      )}
    </Card>
  );
}

function RecentJobsCard({ jobs, onRetry, syncing }: {
  jobs: DashboardSummary["recent_jobs"];
  onRetry: (entity: EntityType) => void;
  syncing: Set<string>;
}) {
  return (
    <Card title="Recent jobs" action={{ label: "All jobs", href: "/jobs" }}>
      {jobs.length === 0 ? (
        <EmptyLine text="No jobs yet — syncs and bulk operations appear here." />
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {jobs.map((j, i) => {
            const pill = statusPillColors(j.status);
            const entity = j.status === "FAILED" ? retryableEntity(j.job_type) : null;
            return (
              <div key={j.id} style={{
                display: "flex", flexDirection: "column", gap: 3, padding: "8px 2px",
                borderTop: i > 0 ? `1px solid ${theme.color.border}` : "none",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
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
                  {entity && (
                    <button
                      onClick={() => onRetry(entity)}
                      disabled={syncing.has(entity)}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 3, padding: 0,
                        border: "none", background: "none", cursor: syncing.has(entity) ? "default" : "pointer",
                        fontSize: 11.5, fontFamily: theme.font.sans, fontWeight: 500, color: theme.color.accent2,
                      }}
                    >
                      <RefreshCw size={10} /> {syncing.has(entity) ? "Retrying…" : "Retry"}
                    </button>
                  )}
                  <span style={{ fontSize: 12, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
                    {timeAgo(j.created_at)}
                  </span>
                </div>
                {/* The reason a job failed is the whole point of showing it. */}
                {j.status === "FAILED" && (j.error || j.error_type) && (
                  <span
                    title={j.error ?? undefined}
                    style={{
                      fontSize: 11.5, color: theme.color.danger, fontFamily: theme.font.sans,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    }}
                  >
                    {j.error_type ? `${j.error_type}: ` : ""}{j.error ?? "no detail recorded"}
                  </span>
                )}
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
        <EmptyLine text="No admin actions in the last 30 days — deletions, sharing changes, and transfers appear here." />
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
                {a.count > 1 && (
                  <span style={{ fontSize: 11, color: theme.color.textMuted, fontFamily: theme.font.mono, flexShrink: 0 }}>
                    ×{a.count}
                  </span>
                )}
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
