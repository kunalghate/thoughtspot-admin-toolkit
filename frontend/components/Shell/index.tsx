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
      if (list.length > 0) setActiveCluster(list[0]);
      // Only redirect if no clusters AND not already on a settings page
      if (list.length === 0 && !router.pathname.startsWith("/settings")) {
        router.push("/settings/connections");
      }
    }).catch(() => {
      // API unreachable — stay on the current page, don't redirect
    });
  }, [router.pathname]);

  // When active cluster changes: check connectivity, then load orgs appropriately
  useEffect(() => {
    if (!activeCluster) return;

    setClusterOnline(null);  // reset to "checking"
    setSessionExpired(false);

    // Restore saved org immediately from cache so there is no flash of the
    // primary org while the async org-list fetch is in flight.
    const cached = _readCachedOrg(activeCluster.id);
    if (cached) setActiveOrg(cached);

    // Non-blocking: test connectivity first, then load orgs
    clustersApi.testConnection(activeCluster.id).then((result) => {
      const online = result.success;
      setClusterOnline(online);
      // A failed test that reads like an auth rejection means the session is
      // dead even though the cluster is configured — surface the reconnect path.
      setSessionExpired(!online && looksLikeAuthError(result.error));

      if (online) {
        // Cluster is reachable — fetch orgs live
        return clustersApi.listOrgs(activeCluster.id).then((list) => {
          setOrgs(list);
          setActiveOrg(_restoreOrg(list, activeCluster.id));
        });
      } else {
        // Cluster offline — fall back to SQLite cache
        return clustersApi.listCachedOrgs(activeCluster.id).then((list) => {
          setOrgs(list);
          setActiveOrg(_restoreOrg(list, activeCluster.id));
        });
      }
    }).catch(() => {
      // Connection test itself failed (network error) — try cache
      setClusterOnline(false);
      clustersApi.listCachedOrgs(activeCluster.id).then((list) => {
        setOrgs(list);
        setActiveOrg(_restoreOrg(list, activeCluster.id));
      }).catch(() => {});
    });
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
                hint: "This cluster's login is no longer valid. Reconnect to continue.",
                action: { label: "Reconnect", onClick: () => router.push("/settings/connections") },
              });
            } else {
              toast.error("Sync failed", { hint: updated.error ?? "Open Jobs for details." });
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
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", fontFamily: "Geist, sans-serif" }}>
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
              background: "#FDF2F2",
              borderBottom: "1px solid #F3C6C6",
              color: "#7A2018",
              fontSize: 13,
            }}
          >
            <span style={{ fontWeight: 600 }}>ThoughtSpot session expired.</span>
            <span style={{ color: "#9A4338" }}>
              This cluster's login is no longer valid — reads from cache still work, but syncs and live actions will
              fail until you reconnect.
            </span>
            <button
              onClick={() => router.push("/settings/connections")}
              style={{
                marginLeft: "auto",
                background: "#C0392B",
                color: "#fff",
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
        <main style={{ flex: 1, overflowY: "auto", background: "#F2EDE3" }}>
          {children}
        </main>
      </div>
    </div>
    </ShellContext.Provider>
  );
}
