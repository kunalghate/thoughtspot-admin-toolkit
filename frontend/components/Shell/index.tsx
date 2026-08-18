/**
 * AppShell — wraps every page with Sidebar + Topbar.
 *
 * Usage:
 *   <AppShell pageTitle="Users" entityType="users" onSyncComplete={reload}>
 *     <UsersPage />
 *   </AppShell>
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/router";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { theme } from "@/lib/theme";
import { clustersApi, jobsApi, syncApi } from "@/lib/api";
import { useToast } from "../Toast";
import type { Cluster, Org, SyncLog, EntityType } from "@/lib/types";

/** Heuristic: does a backend error string describe an expired/invalid session? */
function looksLikeAuthError(message?: string | null): boolean {
  return /expired|login|credential|session|unauthor|401/i.test(message ?? "");
}

// ── Shell context — lets any page read the active cluster + org ────────────────

interface ShellContextValue {
  activeCluster: Cluster | null;
  activeOrg: Org | null;
  clusterOnline: boolean | null;  // null = still checking
}

const ShellContext = createContext<ShellContextValue>({
  activeCluster: null,
  activeOrg: null,
  clusterOnline: null,
});

export function useShell(): ShellContextValue {
  return useContext(ShellContext);
}

// ── Org persistence helpers ────────────────────────────────────────────────────
// Full org object stored as JSON so it can be restored synchronously before
// the async org-list fetch completes — eliminates the flash of the primary org.

const _ORG_KEY = (clusterId: string) => `ts_admin_active_org_${clusterId}`;

/** Read cached org synchronously — used to set org BEFORE async calls fire. */
function _readCachedOrg(clusterId: string): Org | null {
  try {
    const raw = localStorage.getItem(_ORG_KEY(clusterId));
    if (raw) return JSON.parse(raw) as Org;
  } catch { /* localStorage unavailable or corrupt */ }
  return null;
}

/**
 * After the org list loads, confirm the saved org is still valid.
 * Falls back to list[0] if the saved org was removed from the cluster.
 */
function _restoreOrg(orgs: Org[], clusterId: string): Org | null {
  try {
    const raw = localStorage.getItem(_ORG_KEY(clusterId));
    if (raw) {
      const saved = JSON.parse(raw) as Org;
      const match = orgs.find((o) => o.org_id === saved.org_id);
      if (match) return match;
    }
  } catch { /* localStorage unavailable */ }
  return orgs[0] ?? null;
}

function _saveOrg(org: Org | null, clusterId: string): void {
  try {
    if (org != null) {
      localStorage.setItem(_ORG_KEY(clusterId), JSON.stringify(org));
    } else {
      localStorage.removeItem(_ORG_KEY(clusterId));
    }
  } catch { /* localStorage unavailable */ }
}

// ── Connection-test throttle ───────────────────────────────────────────────────
// AppShell remounts on every page navigation; without a throttle each nav fires
// a live roundtrip to the ThoughtSpot cluster just to re-answer "still online?".
// Cache the last verdict per cluster for a short window instead.

const _CONN_TTL_MS = 60_000;
const _CONN_KEY = (clusterId: string) => `ts_admin_conn_${clusterId}`;

interface ConnVerdict { online: boolean; authError: boolean; }

function _readConnCache(clusterId: string): ConnVerdict | null {
  try {
    const raw = sessionStorage.getItem(_CONN_KEY(clusterId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ConnVerdict & { ts: number };
    if (Date.now() - parsed.ts < _CONN_TTL_MS) return parsed;
  } catch { /* sessionStorage unavailable or corrupt */ }
  return null;
}

function _saveConnCache(clusterId: string, verdict: ConnVerdict): void {
  try {
    sessionStorage.setItem(_CONN_KEY(clusterId), JSON.stringify({ ...verdict, ts: Date.now() }));
  } catch { /* sessionStorage unavailable */ }
}

interface AppShellProps {
  pageTitle: string;
  entityType?: EntityType;      // determines which sync log entry to show in topbar
  onSyncComplete?: () => void;  // called when sync job finishes — use to reload page data
  children: React.ReactNode;
}

export default function AppShell({ pageTitle, entityType, onSyncComplete, children }: AppShellProps) {
  const router = useRouter();
  const [, setClusters]               = useState<Cluster[]>([]);
  const [activeCluster, setActiveCluster] = useState<Cluster | null>(null);
  const [orgs, setOrgs]               = useState<Org[]>([]);
  const [activeOrg, setActiveOrg]     = useState<Org | null>(null);
  const [syncLogs, setSyncLogs]       = useState<SyncLog[]>([]);
  const [isSyncing, setIsSyncing]     = useState(false);
  const [syncProgress, setSyncProgress] = useState<{ processed: number; total: number } | null>(null);
  const [clusterOnline, setClusterOnline] = useState<boolean | null>(null);
  // The cluster's login is no longer valid even though it may still look
  // "connected" — drives the reconnect banner.
  const [sessionExpired, setSessionExpired] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const toast = useToast();

  // Load clusters on mount
  useEffect(() => {
    clustersApi.list().then((list) => {
      setClusters(list);
      // Pick the cluster the backend says is active — NOT list[0]. Every
      // write path (sync especially) runs against the backend's active
      // cluster, so seeding the shell from list order silently puts the UI on
      // a different cluster than the one it operates on.
      if (list.length > 0) setActiveCluster(list.find((c) => c.is_active) ?? list[0]);
      // Only redirect if no clusters AND not already on a settings page
      if (list.length === 0 && !router.pathname.startsWith("/settings")) {
        router.push("/settings/connections");
      }
    }).catch(() => {
      // API unreachable — stay on the current page, don't redirect
    });
  }, [router.pathname]);

  // When active cluster changes: paint orgs from the local cache immediately,
  // then (throttled) test connectivity and refresh orgs live. Pages gate their
  // data fetches on activeOrg, so it must never wait on a live TS roundtrip —
  // browsing reads from SQLite by design.
  useEffect(() => {
    if (!activeCluster) return;
    const clusterId = activeCluster.id;
    let cancelled = false;
    let liveOrgsLoaded = false;

    setSessionExpired(false);

    // Restore saved org immediately from cache so there is no flash of the
    // primary org while the async org-list fetch is in flight.
    const cached = _readCachedOrg(clusterId);
    if (cached) setActiveOrg(cached);

    // Fast path: org list from SQLite — resolves in milliseconds and unblocks
    // every page that needs an org context.
    clustersApi.listCachedOrgs(clusterId).then((list) => {
      if (cancelled || liveOrgsLoaded || list.length === 0) return;
      setOrgs(list);
      setActiveOrg(_restoreOrg(list, clusterId));
    }).catch(() => { /* cache empty or unreadable — live path below still runs */ });

    const applyLiveOrgs = () =>
      clustersApi.listOrgs(clusterId).then((list) => {
        if (cancelled) return;
        liveOrgsLoaded = true;
        setOrgs(list);
        setActiveOrg(_restoreOrg(list, clusterId));
      });

    // Throttled connectivity check: reuse a recent verdict instead of hitting
    // the live cluster on every page navigation.
    const recent = _readConnCache(clusterId);
    if (recent) {
      setClusterOnline(recent.online);
      setSessionExpired(!recent.online && recent.authError);
      if (recent.online) applyLiveOrgs().catch(() => {});
      return () => { cancelled = true; };
    }

    setClusterOnline(null);  // "checking"
    clustersApi.testConnection(clusterId).then((result) => {
      if (cancelled) return;
      const online = result.success;
      // A failed test that reads like an auth rejection means the session is
      // dead even though the cluster is configured — surface the reconnect path.
      const authError = !online && looksLikeAuthError(result.error);
      setClusterOnline(online);
      setSessionExpired(authError);
      _saveConnCache(clusterId, { online, authError });
      if (online) return applyLiveOrgs();
      // Offline — the cached-org paint above already covered the fallback.
    }).catch(() => {
      // Connection test itself failed (network error) — treat as offline but
      // don't cache the verdict, so recovery is picked up on the next nav.
      if (!cancelled) setClusterOnline(false);
    });

    return () => { cancelled = true; };
  }, [activeCluster?.id]);

  // Load sync status when cluster + org changes
  useEffect(() => {
    if (!activeCluster) return;
    syncApi.status(activeCluster.id, activeOrg?.org_id ?? 0).then(setSyncLogs).catch(() => {});
  }, [activeCluster?.id, activeOrg?.org_id]);

  // Tick every 60s so relative sync timestamps stay fresh
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  // Clean up polling on unmount
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const activeSyncLog = entityType
    ? syncLogs.find((l) => l.entity_type === entityType) ?? null
    : null;

  // Lineage is DERIVED from the metadata cache, so the backend fails it closed
  // until a metadata sync is certified complete. Say so on the button rather
  // than letting the click queue a job that can only fail.
  const blockedReason =
    entityType === "dependencies" && syncLogs.find((l) => l.entity_type === "metadata")?.status !== "SUCCESS"
      ? "Sync Metadata first — lineage is built from the metadata cache."
      : null;

  const handleSync = useCallback(async () => {
    if (!activeCluster || !activeOrg || !entityType) return;
    setIsSyncing(true);
    setSyncProgress(null);

    try {
      const { job_id } = await syncApi.trigger(activeCluster.id, activeOrg.org_id, entityType);

      // Poll until the job reaches a terminal state. The backend job lifecycle is
      // QUEUED → RUNNING → COMPLETE | FAILED | PARTIAL, so only those three mean
      // "done". Checking for terminal states (rather than "not running") avoids a
      // race where the first tick catches a still-QUEUED job and wrongly concludes
      // the sync already finished — which would reload stale data and stop the bar.
      const TERMINAL = ["COMPLETE", "FAILED", "PARTIAL"];
      let pollErrors = 0;
      pollRef.current = setInterval(async () => {
        try {
          const updated = await jobsApi.get(job_id);
          pollErrors = 0;
          setSyncProgress({ processed: updated.progress, total: updated.total });

          if (!TERMINAL.includes(updated.status)) return;

          clearInterval(pollRef.current!);
          pollRef.current = null;
          setIsSyncing(false);
          setSyncProgress(null);

          if (updated.status === "FAILED") {
            // Don't let the failure hide in a /jobs row — say it out loud, and
            // if the session is the cause, flip the banner + offer reconnect.
            if (looksLikeAuthError(updated.error)) {
              setSessionExpired(true);
              toast.error("ThoughtSpot session expired", {
                hint: "This instance's login is no longer valid. Reconnect to continue.",
                action: { label: "Reconnect", onClick: () => router.push("/settings/connections") },
              });
            } else {
              // The error copy tells people to send a support bundle — give
              // them the door to it instead of making them hunt for Settings.
              toast.error("Sync failed", {
                hint: updated.error ?? "Open Jobs for details.",
                action: { label: "Open Diagnostics", onClick: () => router.push("/settings/diagnostics") },
              });
            }
          }

          // Refresh sync log then reload page data
          const logs = await syncApi.status(activeCluster.id, activeOrg?.org_id ?? 0);
          setSyncLogs(logs);
          onSyncComplete?.();
        } catch {
          // Tolerate transient poll failures (network blip, server busy) — only
          // give up after several consecutive errors so a single hiccup doesn't
          // strand the UI in a permanent "Syncing…" state.
          if (++pollErrors < 5) return;
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setIsSyncing(false);
          setSyncProgress(null);
          toast.error("Lost track of the sync", { hint: "Check the Jobs page for status." });
        }
      }, 2000);
    } catch (e) {
      setIsSyncing(false);
      setSyncProgress(null);
      toast.error("Couldn't start sync", { hint: e instanceof Error ? e.message : String(e) });
    }
  }, [activeCluster?.id, activeOrg?.org_id, entityType, onSyncComplete, router, toast]);

  return (
    <ShellContext.Provider value={{ activeCluster, activeOrg, clusterOnline }}>
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", fontFamily: theme.font.sans }}>
      <Sidebar
        activeCluster={activeCluster}
        onSwitchCluster={() => router.push("/settings/connections")}
      />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <Topbar
          pageTitle={pageTitle}
          entityType={entityType}
          clusterName={activeCluster?.name ?? ""}
          activeOrg={activeOrg}
          orgs={orgs}
          syncLog={activeSyncLog}
          syncProgress={syncProgress}
          blockedReason={blockedReason}
          onOrgChange={(org) => {
            setActiveOrg(org);
            if (activeCluster) _saveOrg(org, activeCluster.id);
          }}
          onSync={handleSync}
          isSyncing={isSyncing}
          clusterOnline={clusterOnline}
          now={now}
        />
        {sessionExpired && (
          <div
            role="alert"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 20px",
              background: theme.color.dangerSoft,
              borderBottom: `1px solid ${theme.color.dangerBorder}`,
              color: theme.color.danger,
              fontSize: 13,
            }}
          >
            <span style={{ fontWeight: 600 }}>ThoughtSpot session expired.</span>
            <span style={{ color: theme.color.danger }}>
              This instance's login is no longer valid — reads from cache still work, but syncs and live actions will
              fail until you reconnect.
            </span>
            <button
              onClick={() => router.push("/settings/connections")}
              style={{
                marginLeft: "auto",
                background: theme.color.danger,
                color: theme.color.onAccent,
                border: "none",
                borderRadius: 6,
                padding: "6px 14px",
                fontSize: 12,
                fontWeight: 600,
                cursor: "pointer",
                whiteSpace: "nowrap",
              }}
            >
              Reconnect
            </button>
          </div>
        )}
        <main style={{ flex: 1, overflowY: "auto", background: theme.color.bg }}>
          {children}
        </main>
      </div>
    </div>
    </ShellContext.Provider>
  );
}
