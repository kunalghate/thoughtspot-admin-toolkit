/**
 * AppShell — wraps every page with Sidebar + Topbar.
 *
 * Usage:
 *   <AppShell pageTitle="Users" entityType="users">
 *     <UsersPage />
 *   </AppShell>
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/router";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { clustersApi, syncApi } from "@/lib/api";
import type { Cluster, Org, SyncLog, EntityType } from "@/lib/types";

interface AppShellProps {
  pageTitle: string;
  entityType?: EntityType;   // determines which sync log entry to show in topbar
  children: React.ReactNode;
}

export default function AppShell({ pageTitle, entityType, children }: AppShellProps) {
  const router = useRouter();
  const [clusters, setClusters]       = useState<Cluster[]>([]);
  const [activeCluster, setActiveCluster] = useState<Cluster | null>(null);
  const [orgs, setOrgs]               = useState<Org[]>([]);
  const [activeOrg, setActiveOrg]     = useState<Org | null>(null);
  const [syncLogs, setSyncLogs]       = useState<SyncLog[]>([]);
  const [isSyncing, setIsSyncing]     = useState(false);

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
    syncApi.status(activeCluster.id).then(setSyncLogs).catch(() => {});
  }, [activeCluster?.id, activeOrg?.org_id]);

  const activeSyncLog = entityType
    ? syncLogs.find((l) => l.entity_type === entityType) ?? null
    : null;

  const handleSync = useCallback(async () => {
    if (!activeCluster || !activeOrg || !entityType) return;
    setIsSyncing(true);
    try {
      await syncApi.trigger(activeCluster.id, activeOrg.org_id, entityType);
      const updated = await syncApi.status(activeCluster.id);
      setSyncLogs(updated);
    } catch {
      // error surfaced in Topbar via syncLog.status === "FAILED"
    } finally {
      setIsSyncing(false);
    }
  }, [activeCluster?.id, activeOrg?.org_id, entityType]);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden", fontFamily: "Geist, sans-serif" }}>
      <Sidebar
        activeCluster={activeCluster}
        onSwitchCluster={() => router.push("/settings/connections")}
      />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <Topbar
          pageTitle={pageTitle}
          clusterName={activeCluster?.name ?? ""}
          activeOrg={activeOrg}
          orgs={orgs}
          syncLog={activeSyncLog}
          onOrgChange={setActiveOrg}
          onSync={handleSync}
          isSyncing={isSyncing}
        />
        <main style={{ flex: 1, overflowY: "auto", background: "#F2EDE3" }}>
          {children}
        </main>
      </div>
    </div>
  );
}
