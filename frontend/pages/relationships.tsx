import { useState } from "react";
import AppShell, { useShell } from "@/components/Shell";
import { RelationshipsView } from "@/components/Relationships/RelationshipsView";
import { theme } from "@/lib/theme";

export default function RelationshipsPage() {
  // Bumped when the topbar "Sync" (dependencies) finishes so the view reloads.
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Relationships" entityType="dependencies" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
      <RelationshipsContent syncVersion={syncVersion} />
    </AppShell>
  );
}

function RelationshipsContent({ syncVersion }: { syncVersion: number }) {
  const { activeCluster, activeOrg } = useShell();

  if (!activeCluster) {
    return (
      <div style={{
        height: "100%", display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 14, color: theme.color.textMuted, fontFamily: theme.font.sans,
      }}>
        Select a cluster from the topbar to start.
      </div>
    );
  }

  return (
    <RelationshipsView
      clusterId={activeCluster.id}
      orgId={activeOrg?.org_id ?? 0}
      syncVersion={syncVersion}
    />
  );
}
