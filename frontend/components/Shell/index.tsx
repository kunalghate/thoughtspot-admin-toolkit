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
}

const ShellContext = createContext<ShellContextValue>({ activeCluster: null, activeOrg: null });

export function useShell(): ShellContextValue {
  return useContext(ShellContext);
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

  // Load orgs when active cluster changes
  useEffect(() => {
    if (!activeCluster) return;
    clustersApi.listOrgs(activeCluster.id).then((list) => {
      setOrgs(list);
      setActiveOrg(list[0] ?? null);
    }).catch(() => {});
  }, [activeCluster?.id]);

  // Load sync status when cluster + org changes
  useEffect(() => {
    if (!activeCluster) return;
    syncApi.status(activeCluster.id, activeOrg?.org_id ?? 0).then(setSyncLogs).catch(() => {});
  }, [activeCluster?.id, activeOrg?.org_id]);

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
    <ShellContext.Provider value={{ activeCluster, activeOrg }}>
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
          onOrgChange={setActiveOrg}
          onSync={handleSync}
          isSyncing={isSyncing}
        />
        <main style={{ flex: 1, overflowY: "auto", background: "#F2EDE3" }}>
          {children}
        </main>
      </div>
    </div>
    </ShellContext.Provider>
  );
}
