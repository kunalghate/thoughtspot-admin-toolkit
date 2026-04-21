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
import type { Cluster, Org, SyncLog, EntityType } from "@/lib/types";

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
  const [now, setNow] = useState(() => Date.now());
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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

    // Restore saved org immediately from cache so there is no flash of the
    // primary org while the async org-list fetch is in flight.
    const cached = _readCachedOrg(activeCluster.id);
    if (cached) setActiveOrg(cached);

    // Non-blocking: test connectivity first, then load orgs
    clustersApi.testConnection(activeCluster.id).then((result) => {
      const online = result.success;
      setClusterOnline(online);

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

      // Poll until the job finishes
      pollRef.current = setInterval(async () => {
        try {
          const updated = await jobsApi.get(job_id);
          setSyncProgress({ processed: updated.progress, total: updated.total });

          if (updated.status !== "PENDING" && updated.status !== "RUNNING") {
            clearInterval(pollRef.current!);
            pollRef.current = null;
            setIsSyncing(false);
            setSyncProgress(null);
            // Refresh sync log then reload page data
            const logs = await syncApi.status(activeCluster.id, activeOrg?.org_id ?? 0);
            setSyncLogs(logs);
            onSyncComplete?.();
          }
        } catch {
          clearInterval(pollRef.current!);
          pollRef.current = null;
          setIsSyncing(false);
          setSyncProgress(null);
        }
      }, 2000);
    } catch {
      setIsSyncing(false);
      setSyncProgress(null);
    }
  }, [activeCluster?.id, activeOrg?.org_id, entityType, onSyncComplete]);

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
        <main style={{ flex: 1, overflowY: "auto", background: "#F2EDE3" }}>
          {children}
        </main>
      </div>
    </div>
    </ShellContext.Provider>
  );
}
