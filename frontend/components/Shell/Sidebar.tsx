import { useRouter } from "next/router";
import Link from "next/link";
import clsx from "clsx";
import {
  LayoutDashboard, Users, UsersRound, FolderSearch,
  ArchiveBox, Share2, GitFork, Briefcase, Settings,
} from "lucide-react";
import type { Cluster } from "@/lib/types";

const NAV_ITEMS = [
  { href: "/dashboard",     label: "Dashboard",     icon: LayoutDashboard },
  { href: "/users",         label: "Users",          icon: Users },
  { href: "/groups",        label: "Groups",         icon: UsersRound },
  { href: "/metadata",      label: "Metadata",       icon: FolderSearch },
  { href: "/archiver",      label: "Archiver",       icon: ArchiveBox },
  { href: "/sharing",       label: "Bulk Sharing",   icon: Share2 },
  { href: "/relationships", label: "Relationships",  icon: GitFork },
] as const;

const BOTTOM_ITEMS = [
  { href: "/jobs",     label: "Jobs",     icon: Briefcase },
  { href: "/settings", label: "Settings", icon: Settings },
] as const;

interface SidebarProps {
  activeCluster: Cluster | null;
  onSwitchCluster: () => void;
}

export default function Sidebar({ activeCluster, onSwitchCluster }: SidebarProps) {
  const router = useRouter();

  return (
    <aside style={{
      width: 220, flexShrink: 0, height: "100vh",
      background: "#FAF8F4", borderRight: "1px solid #E8E1D5",
      display: "flex", flexDirection: "column",
    }}>
      {/* Logo */}
      <div style={{ padding: "18px 20px 14px", borderBottom: "1px solid #E8E1D5" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 7, flexShrink: 0,
            background: "linear-gradient(135deg, #8B5CF6 0%, #6D28D9 100%)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <span style={{ color: "white", fontSize: 12, fontWeight: 700 }}>TS</span>
          </div>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
              Admin Toolkit
            </div>
            <div style={{ fontSize: 11, color: "#7A7068", fontFamily: "Geist, sans-serif" }}>
              {activeCluster?.name ?? "Not connected"}
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: "10px 10px 0", overflowY: "auto" }}>
        <NavSection items={NAV_ITEMS} currentPath={router.pathname} />
        <div style={{ margin: "10px 0", borderTop: "1px solid #F0EBE3" }} />
        <NavSection items={BOTTOM_ITEMS} currentPath={router.pathname} />
      </nav>

      {/* Cluster switcher */}
      <div style={{ padding: "12px 10px", borderTop: "1px solid #E8E1D5" }}>
        <button
          onClick={onSwitchCluster}
          style={{
            width: "100%", padding: "8px 10px",
            border: "1px solid #E8E1D5", borderRadius: 6,
            background: "transparent", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            textAlign: "left",
          }}
        >
          <div>
            <div style={{ fontSize: 10, color: "#7A7068", textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 500, fontFamily: "Geist, sans-serif" }}>
              Cluster
            </div>
            <div style={{ fontSize: 12, fontWeight: 500, color: "#1A1714", fontFamily: "Geist, sans-serif" }}>
              {activeCluster?.name ?? "—"}
            </div>
          </div>
          <span style={{ color: "#7A7068", fontSize: 14 }}>⇅</span>
        </button>
        <p style={{ fontSize: 10, color: "#A89E96", margin: "6px 2px 0", fontFamily: "Geist, sans-serif" }}>
          Go to Settings to add or remove clusters.
        </p>
      </div>
    </aside>
  );
}

function NavSection({ items, currentPath }: {
  items: readonly { href: string; label: string; icon: React.ComponentType<{ size?: number }> }[];
  currentPath: string;
}) {
  return (
    <>
      {items.map(({ href, label, icon: Icon }) => {
        const active = currentPath === href || currentPath.startsWith(href + "/");
        return (
          <Link key={href} href={href} style={{ textDecoration: "none" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: 9,
              padding: "7px 10px", borderRadius: 6, marginBottom: 2, cursor: "pointer",
              background: active ? "#EDE9FE" : "transparent",
              transition: "background 0.1s",
            }}>
              <Icon size={15} style={{ color: active ? "#6D28D9" : "#7A7068", flexShrink: 0 }} />
              <span style={{
                fontSize: 13, fontWeight: active ? 500 : 400,
                color: active ? "#6D28D9" : "#1A1714",
                fontFamily: "Geist, sans-serif",
              }}>
                {label}
              </span>
            </div>
          </Link>
        );
      })}
    </>
  );
}
