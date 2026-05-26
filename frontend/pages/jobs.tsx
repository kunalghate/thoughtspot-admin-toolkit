/**
 * Jobs page — paginated list of every background job, plus a sub-tab
 * that surfaces the per-object deletion history (rows from
 * archive_records produced by Archiver and Bulk Deleter).
 */
import { useState } from "react";
import AppShell from "@/components/Shell";
import { JobsList } from "@/components/Jobs/JobsList";
import { HistoryTab } from "@/components/Deleter/HistoryTab";

export default function JobsPage() {
  const [activeTab, setActiveTab] = useState<"jobs" | "deleted">("jobs");

  return (
    <AppShell pageTitle="Jobs">
      <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
        <div style={{
          display: "flex", gap: 0, borderBottom: "1px solid #E8E1D5",
          background: "#FAF8F4", paddingLeft: 24, flexShrink: 0,
        }}>
          {(["jobs", "deleted"] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "10px 18px", fontSize: 13, fontWeight: activeTab === tab ? 600 : 400,
                fontFamily: "Geist, sans-serif", cursor: "pointer", border: "none",
                background: "transparent", color: activeTab === tab ? "#6D28D9" : "#7A7068",
                borderBottom: activeTab === tab ? "2px solid #8B5CF6" : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              {tab === "jobs" ? "Jobs" : "Deleted Items"}
            </button>
          ))}
        </div>

        {activeTab === "jobs" ? <JobsList /> : <HistoryTab />}
      </div>
    </AppShell>
  );
}
