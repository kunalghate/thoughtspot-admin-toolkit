/**
 * Jobs page — currently surfaces the shared deletion history (rows
 * written to archive_records by both the Archiver and the Bulk Deleter).
 *
 * Future job types (sync logs, share batches) will land here as
 * additional tabs alongside "Deletions".
 */
import AppShell from "@/components/Shell";
import { HistoryTab } from "@/components/Deleter/HistoryTab";

export default function JobsPage() {
  return (
    <AppShell pageTitle="Jobs">
      <HistoryTab />
    </AppShell>
  );
}
