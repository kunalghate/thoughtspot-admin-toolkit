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
  AlertTriangle, Archive, ArrowRight, CheckCircle2, ChevronRight, FolderSearch,
  Loader2, RefreshCw, Users, UsersRound,
} from "lucide-react";
import AppShell, { useShell } from "@/components/Shell";
import { dashboardApi, syncApi } from "@/lib/api";
import { theme } from "@/lib/theme";
import { parseUtc } from "@/lib/utils";
import { typeLabelPlural } from "@/lib/objectTypes";
import type { DashboardSummary, EntityType } from "@/lib/types";

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

// Cache-freshness row, in the order an admin reads the nav: identity first,
// then content, then the derived graph. "dependencies" is the API/wire name for
// what the UI calls Lineage. Tags are omitted — the toolkit has no tag page to
// send anyone to, so a tag clock here would be freshness nobody acts on.
const FRESHNESS_ENTITIES: { entity: EntityType; label: string }[] = [
  { entity: "users", label: "Users" },
  { entity: "groups", label: "Groups" },
  { entity: "metadata", label: "Metadata" },
  { entity: "dependencies", label: "Lineage" },
];

/** Older than a day and the cache is worth a second look, not a hard alarm. */
const STALE_AFTER_MIN = 24 * 60;

/** While a job is in flight, re-read the summary this often. */
const LIVE_POLL_MS = 3_000;
/** Otherwise, refresh quietly so relative timestamps stay honest. */
const IDLE_POLL_MS = 60_000;

/** A failed sync job maps back to the entity whose sync can be retried. */
function retryableEntity(jobType: string): EntityType | null {
  const entity = jobType.startsWith("sync:") ? jobType.slice(5) : null;
  return SYNCABLE_ENTITIES.includes(entity as EntityType) ? (entity as EntityType) : null;
}

/** Whole minutes since `iso`, or null when it never happened. */
function ageMinutes(iso: string | null | undefined): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - parseUtc(iso).getTime()) / 60_000);
}

/** Exact local timestamp, for the tooltip behind a relative one. */
function absoluteTime(iso: string): string {
  return parseUtc(iso).toLocaleString();
}

/** Relative time with minute/hour granularity; backend sends naive-UTC ISO. */
function timeAgo(iso: string | null | undefined): string {
  const mins = ageMinutes(iso);
  if (mins === null) return "never";
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
  // Generation counter, not a boolean ref: the effect below resets a boolean to
  // false in the same commit that the cleanup sets it true, so a shared flag
  // never rejects anything and a slow read for the PREVIOUS (cluster, org) can
  // paint its numbers under the new one's Topbar. Same guard the grid pages
  // use, and it survives being read from a stale `load` closure.
  const loadIdRef = useRef(0);

  const clusterId = activeCluster?.id;
  const orgId = activeOrg?.org_id;

  const load = useCallback(async (loadId: number) => {
    if (!clusterId || orgId == null) return;
    try {
      const summary = await dashboardApi.summary(clusterId, orgId);
      if (loadId !== loadIdRef.current) return;
      setData(summary);
      setError(null);
    } catch (e) {
      if (loadId !== loadIdRef.current) return;
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [clusterId, orgId]);

  useEffect(() => {
    const loadId = ++loadIdRef.current;
    setData(null);
    void load(loadId);
  }, [load]);

  // Poll fast while work is in flight, slowly otherwise — this is also what
  // keeps the relative timestamps from freezing at mount time.
  const busy = (data?.running_jobs.length ?? 0) > 0 || syncing.size > 0;
  useEffect(() => {
    if (!clusterId || orgId == null) return;
    const iv = setInterval(() => { void load(loadIdRef.current); }, busy ? LIVE_POLL_MS : IDLE_POLL_MS);
    return () => clearInterval(iv);
  }, [load, busy, clusterId, orgId]);

  const triggerSync = useCallback(async (entity: EntityType) => {
    if (!clusterId || orgId == null) return;
    setSyncing((prev) => new Set(prev).add(entity));
    try {
      await syncApi.trigger(clusterId, orgId, entity);
      await load(loadIdRef.current);
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

  const { counts, synced, synced_at, attention } = data;
  const neverSynced = !synced.metadata && !synced.users;
  // Server-reported in-flight syncs, OR-ed with the local optimistic set. The
  // local set is per-mount React state, so it is empty after a reload/new tab
  // and cannot be the only source — a healthy multi-minute sync would otherwise
  // render as "Never synced — sync now".
  const inFlight = data.syncing ?? {};
  // `syncing` (the server map) is derived from the IN_PROGRESS SyncLog marker,
  // which only `_sync_metadata` writes — so for users and groups it is always
  // false and a first sync rendered "Never synced — sync now" for its whole
  // run, right below a RunningJobsBar saying the opposite, inviting a second
  // concurrent sync. `running_jobs` carries `sync:{entity}` for every entity,
  // so read that too.
  const runningEntities = new Set(
    data.running_jobs
      .map((j) => (j.job_type.startsWith("sync:") ? j.job_type.slice(5) : null))
      .filter((e): e is string => e !== null),
  );
  const isSyncing = (entity: "users" | "groups" | "metadata") =>
    syncing.has(entity) || inFlight[entity] === true || runningEntities.has(entity);

  return (
    <div style={{ padding: 28, display: "flex", flexDirection: "column", gap: 20, maxWidth: 1320 }}>

      {error && <PageError message={error} inline />}

      {neverSynced && (
        <Banner>
          Nothing synced yet — open <Link href="/metadata" style={linkStyle}>Metadata</Link> or{" "}
          <Link href="/users" style={linkStyle}>Users</Link> and press Sync to pull data from your instance.
        </Banner>
      )}

      <RunningJobsBar jobs={data.running_jobs} />

      <AttentionBand
        attention={attention}
        staleCount={counts.stale_90d}
        failedJobs={data.failed_jobs_7d}
        neverSynced={neverSynced}
      />

      {/* Inventory — reference counts, deliberately quieter than the band above.
          Failed jobs are NOT a tile: that number already leads the attention
          band, and printing it twice makes the page read as two dashboards. */}
      <div className="dash-tiles">
        <StatTile icon={Users} label="Users" value={counts.users}
                  synced={synced.users} href="/users"
                  onSync={() => triggerSync("users")} syncing={isSyncing("users")} />
        <StatTile icon={UsersRound} label="Groups" value={counts.groups}
                  synced={synced.groups} href="/groups"
                  onSync={() => triggerSync("groups")} syncing={isSyncing("groups")} />
        <StatTile icon={FolderSearch} label="Content objects" value={counts.objects_total}
                  synced={synced.metadata} href="/metadata"
                  onSync={() => triggerSync("metadata")} syncing={isSyncing("metadata")} />
        <StatTile icon={Archive} label="Archivable content" value={counts.archivable_total}
                  synced={synced.metadata} href="/archiver" syncing={isSyncing("metadata")}
                  hint="Liveboards + answers — the only types the Archiver can act on" />
      </div>

      <CacheFreshnessCard syncedAt={synced_at} onSync={triggerSync} syncing={syncing} />

      {/* Left = what's on the cluster, right = what the cluster has been doing.
          Both cards are row lists of similar length, so the two columns end on
          the same line; the audit feed spans below them, where its long labels
          have room. */}
      <div className="dash-split">
        <ContentByTypeCard byType={counts.objects_by_type} total={counts.objects_total} />
        <RecentJobsCard jobs={data.recent_jobs} onRetry={triggerSync} syncing={syncing} />
      </div>

      <RecentActivityCard activity={data.recent_activity} />
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
      <div style={{ height: 96, borderRadius: 9, background: theme.color.surface, border: `1px solid ${theme.color.border}` }} />
      <div className="dash-tiles">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} style={{
            height: 74, borderRadius: 9, background: theme.color.surface,
            border: `1px solid ${theme.color.border}`,
          }} />
        ))}
      </div>
      <div className="dash-split">
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
  // Danger before warn, then biggest first — the eye should land on the worst
  // thing on the cluster, not on whatever happened to be declared first.
  const items = [
    { n: failedJobs, label: `job${failedJobs === 1 ? "" : "s"} failed in the last 7 days`, href: "/jobs", tone: "danger" as const },
    { n: attention.orphaned_content, label: "objects owned by a user who no longer exists", href: "/users", tone: "danger" as const },
    { n: attention.inactive_users, label: "inactive users still on the instance", href: "/users", tone: "warn" as const },
    { n: attention.empty_groups, label: "groups with no members", href: "/groups", tone: "warn" as const },
    { n: attention.users_without_group, label: "users belong to no group", href: "/users", tone: "warn" as const },
    { n: staleCount, label: "liveboards/answers unused for 90+ days", href: "/archiver", tone: "warn" as const },
  ]
    .filter((i) => i.n > 0)
    .sort((a, b) => (a.tone === b.tone ? b.n - a.n : a.tone === "danger" ? -1 : 1));

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
    <Card title="Needs attention" meta={`${items.length} ${items.length === 1 ? "item" : "items"}`}>
      <div className="dash-attention">
        {items.map((item) => (
          <Link key={item.label} href={item.href} className="dash-attn-row">
            <span style={{
              fontSize: 17, fontWeight: 700, fontFamily: theme.font.mono, lineHeight: 1,
              textAlign: "right", fontVariantNumeric: "tabular-nums",
              color: item.tone === "danger" ? theme.color.danger : theme.color.warn,
            }}>
              {item.n.toLocaleString()}
            </span>
            <span style={{ fontSize: 12.5, color: theme.color.textSecondary, fontFamily: theme.font.sans }}>
              {item.label}
            </span>
            <ChevronRight className="chev" size={12} style={{ color: theme.color.textMuted }} />
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

function StatTile({ icon: Icon, label, value, href, tone, synced, hint, onSync, syncing }: {
  icon: typeof Users; label: string; value: number; href: string;
  tone?: "warn" | "danger";
  /** false → this entity has never synced, so `value` is unknown, not zero. */
  synced?: boolean;
  hint?: string;
  onSync?: () => void;
  /** true → a sync is in flight (locally triggered OR reported by the server),
   *  so the unknown value is temporary and must not read as "never synced". */
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
            // `syncing` matters on this branch too: a tile with no Sync action
            // (Archivable) still has to say "syncing…" rather than the flatly
            // false "never synced" while a sync is in flight.
            <span style={{ fontSize: 11.5, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              {syncing ? "syncing…" : "never synced"}
            </span>
          )
        )}
      </div>
    </Link>
  );
}

function Card({ title, meta, action, children }: {
  title: string;
  /** Quiet count that sits with the title (e.g. "89 objects"). */
  meta?: string;
  action?: { label: string; href: string };
  children: React.ReactNode;
}) {
  return (
    <section style={{
      background: theme.color.surface, border: `1px solid ${theme.color.border}`,
      borderRadius: 9, padding: 18,
    }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 14 }}>
        <h3 style={{
          margin: 0, display: "flex", alignItems: "baseline", gap: 8,
          fontSize: 13, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans,
        }}>
          {title}
          {meta && (
            <span style={{ fontSize: 11.5, fontWeight: 400, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              {meta}
            </span>
          )}
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

/**
 * Ranked single-hue bar list. The job here is magnitude comparison, so the bars
 * are scaled to the largest type (not to the total) — that's the comparison the
 * admin is actually making. One hue, direct labels, counts in a fixed right
 * gutter so they share an edge. Two API types collapse onto one label
 * ("Worksheets"), so rows are merged by label rather than keyed by raw type.
 */
function ContentByTypeCard({ byType, total }: { byType: Record<string, number>; total: number }) {
  const merged = new Map<string, number>();
  for (const [type, count] of Object.entries(byType)) {
    const label = typeLabelPlural(type);
    merged.set(label, (merged.get(label) ?? 0) + count);
  }
  const rows = [...merged].map(([label, count]) => ({ label, count })).sort((a, b) => b.count - a.count);
  const max = rows.length > 0 ? rows[0].count : 0;

  return (
    <Card
      title="Content by type"
      meta={rows.length > 0 ? `${total.toLocaleString()} objects` : undefined}
      action={{ label: "Explore", href: "/metadata" }}
    >
      {rows.length === 0 ? (
        <EmptyLine text="No content synced yet." />
      ) : (
        <div>
          {rows.map((r) => (
            <div key={r.label} className="dash-bar-row">
              <span style={{
                fontSize: 12, color: theme.color.textSecondary, fontFamily: theme.font.sans,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {r.label}
              </span>
              <div style={{ height: 8, borderRadius: 4, background: theme.color.surface3, overflow: "hidden" }}>
                <div style={{
                  width: `${max > 0 ? Math.max((r.count / max) * 100, 2) : 0}%`,
                  height: "100%", borderRadius: "0 4px 4px 0", background: theme.gradient.accent,
                }} />
              </div>
              <span style={{
                fontSize: 12, color: theme.color.textPrimary, fontFamily: theme.font.mono,
                textAlign: "right", fontVariantNumeric: "tabular-nums",
              }}>
                {r.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

/**
 * Per-entity cache age. Syncs are lazy and independent (ADR-005), so there is
 * no single "last synced" for a cluster — five separate clocks is the honest
 * shape, and it also tells the admin *which* entity to re-sync.
 *
 * The dot is the fast read (fresh / stale / never); the exact timestamp is in
 * the tooltip for when "3d ago" isn't precise enough.
 */
function CacheFreshnessCard({ syncedAt, onSync, syncing }: {
  syncedAt: DashboardSummary["synced_at"];
  onSync: (entity: EntityType) => void;
  syncing: Set<string>;
}) {
  return (
    <Card title="Cache freshness" meta="each entity syncs on its own — nothing forces a full sync">
      <div className="dash-fresh">
        {FRESHNESS_ENTITIES.map(({ entity, label }) => {
          const iso = syncedAt[entity] ?? null;
          const age = ageMinutes(iso);
          const tone =
            age === null ? { dot: theme.color.textMuted, fg: theme.color.textMuted } :
            age > STALE_AFTER_MIN ? { dot: theme.color.warn, fg: theme.color.warn } :
            { dot: theme.color.success, fg: theme.color.textPrimary };

          return (
            <div key={entity} className="dash-fresh-cell">
              <span style={{
                fontSize: 10.5, fontWeight: 600, letterSpacing: "0.06em", textTransform: "uppercase",
                color: theme.color.textMuted, fontFamily: theme.font.sans,
              }}>
                {label}
              </span>
              <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: tone.dot, flexShrink: 0 }} />
                <span
                  title={iso ? absoluteTime(iso) : "never synced"}
                  style={{
                    fontSize: 12.5, color: tone.fg, fontFamily: theme.font.sans,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                >
                  {timeAgo(iso)}
                </span>
              </div>
              <button
                className="sync"
                onClick={() => onSync(entity)}
                disabled={syncing.has(entity)}
                style={{
                  alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 4,
                  padding: 0, border: "none", background: "none",
                  cursor: syncing.has(entity) ? "default" : "pointer",
                  fontSize: 11, fontFamily: theme.font.sans, fontWeight: 500, color: theme.color.accent2,
                  // Hover-only by default, but a never-synced or in-flight
                  // entity keeps its action on screen — that one is a nudge.
                  ...(age === null || syncing.has(entity) ? { opacity: 1 } : null),
                }}
              >
                <RefreshCw size={10} /> {syncing.has(entity) ? "Syncing…" : "Sync now"}
              </button>
            </div>
          );
        })}
      </div>
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
