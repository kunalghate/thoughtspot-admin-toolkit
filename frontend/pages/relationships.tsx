import { useState } from "react";
import AppShell, { useShell } from "@/components/Shell";
import { RelationshipsView } from "@/components/Relationships/RelationshipsView";
import { theme } from "@/lib/theme";

export default function RelationshipsPage() {
  // Bumped when the topbar "Sync" (dependencies) finishes so the view reloads.
  const [syncVersion, setSyncVersion] = useState(0);
  return (
    <AppShell pageTitle="Lineage" entityType="dependencies" onSyncComplete={() => setSyncVersion((v) => v + 1)}>
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
        Select an instance from the topbar to start.
      </div>
    );
  }

  const orgId = activeOrg?.org_id ?? 0;

  return (
    // Keyed on the instance/org: selection, path trail and the graph cache are
    // all scoped to one org's metadata cache, so switching must start clean
    // rather than leave a stale object selected (its guid 404s in the new org).
    <RelationshipsView
      key={`${activeCluster.id}:${orgId}`}
      clusterId={activeCluster.id}
      orgId={orgId}
      syncVersion={syncVersion}
    />
  );
}
