import { useRouter } from "next/router";
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import {
  LayoutDashboard, Users, UsersRound, FolderSearch,
  Archive, Trash2, Share2, GitFork, Briefcase, Settings,
} from "lucide-react";
import { theme } from "@/lib/theme";
import type { Cluster } from "@/lib/types";

// Grouped so browsing tools and bulk (destructive) operations read as
// distinct zones — see docs/dev/DESIGN.md, "error prevention".
const BROWSE_ITEMS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/dashboard",     label: "Dashboard",     icon: LayoutDashboard },
  { href: "/users",         label: "Users",         icon: Users },
  { href: "/groups",        label: "Groups",        icon: UsersRound },
  { href: "/metadata",      label: "Metadata",      icon: FolderSearch },
  { href: "/relationships", label: "Relationships", icon: GitFork },
];

const OPERATE_ITEMS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/archiver",      label: "Archiver",      icon: Archive },
  { href: "/deleter",       label: "Bulk Delete",   icon: Trash2 },
  { href: "/sharing",       label: "Bulk Sharing",  icon: Share2 },
];

const BOTTOM_ITEMS: { href: string; label: string; icon: LucideIcon }[] = [
  { href: "/jobs",     label: "Jobs",     icon: Briefcase },
  { href: "/settings", label: "Settings", icon: Settings },
];

interface SidebarProps {
  activeCluster: Cluster | null;
  onSwitchCluster: () => void;
}

export default function Sidebar({ activeCluster, onSwitchCluster }: SidebarProps) {
  const router = useRouter();

  return (
    <aside style={{
      width: 220, flexShrink: 0, height: "100vh",
      background: theme.color.surface, borderRight: `1px solid ${theme.color.border}`,
      display: "flex", flexDirection: "column",
    }}>
      {/* Logo */}
      <div style={{ padding: "18px 20px 14px", borderBottom: `1px solid ${theme.color.border}` }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 28, height: 28, borderRadius: 7, flexShrink: 0,
            background: theme.gradient.accent,
            boxShadow: theme.shadow.glowAccent,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            <span style={{ color: theme.color.onAccent, fontSize: 12, fontWeight: 700 }}>TS</span>
          </div>
          <div>
            <div style={{
              display: "flex", alignItems: "center", gap: 6,
              fontSize: 13, fontWeight: 600, color: theme.color.textPrimary, fontFamily: theme.font.sans,
            }}>
              Admin Toolkit
              <span style={{
                fontSize: 9, fontWeight: 600, letterSpacing: 0.4, textTransform: "uppercase",
                padding: "1px 5px", borderRadius: 4,
                border: `1px solid ${theme.color.border}`,
                color: theme.color.textMuted,
              }}>
                Beta
              </span>
            </div>
            <div style={{ fontSize: 11, color: theme.color.textMuted, fontFamily: theme.font.sans }}>
              {activeCluster?.name ?? "Not connected"}
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: "10px 10px 0", overflowY: "auto" }}>
        <NavSection items={BROWSE_ITEMS} currentPath={router.pathname} />
        <SectionLabel text="Bulk operations" />
        <NavSection items={OPERATE_ITEMS} currentPath={router.pathname} />
        <div style={{ margin: "10px 0", borderTop: `1px solid ${theme.color.border}` }} />
        <NavSection items={BOTTOM_ITEMS} currentPath={router.pathname} />
      </nav>

      {/* Cluster switcher */}
      <div style={{ padding: "12px 10px", borderTop: `1px solid ${theme.color.border}` }}>
        <button
          onClick={onSwitchCluster}
          style={{
            width: "100%", padding: "8px 10px",
            border: `1px solid ${theme.color.border}`, borderRadius: 6,
            background: "transparent", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            textAlign: "left",
          }}
        >
          <div>
            <div style={{ fontSize: 10, color: theme.color.textMuted, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 500, fontFamily: theme.font.sans }}>
              Cluster
            </div>
            <div style={{ fontSize: 12, fontWeight: 500, color: theme.color.textPrimary, fontFamily: theme.font.sans }}>
              {activeCluster?.name ?? "—"}
            </div>
          </div>
          <span style={{ color: theme.color.textMuted, fontSize: 14 }}>⇅</span>
        </button>
        <p style={{ fontSize: 10, color: theme.color.textMuted, margin: "6px 2px 0", fontFamily: theme.font.sans }}>
          Go to Settings to add or remove clusters.
        </p>
      </div>

      {/* Credit */}
      <div style={{ padding: "8px 12px", borderTop: `1px solid ${theme.color.border}` }}>
        <p style={{ fontSize: 10, color: theme.color.textMuted, margin: 0, fontFamily: theme.font.sans }}>
          Built by Bibek Shrestha &amp; Kunal Ghate
        </p>
      </div>
    </aside>
  );
}

function SectionLabel({ text }: { text: string }) {
  return (
    <div style={{
      padding: "12px 10px 4px", fontSize: 10, fontWeight: 600,
      color: theme.color.textMuted, textTransform: "uppercase",
      letterSpacing: "0.07em", fontFamily: theme.font.sans,
    }}>
      {text}
    </div>
  );
}

function NavSection({ items, currentPath }: {
  items: { href: string; label: string; icon: LucideIcon }[];
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
              background: active ? theme.color.accentSoft : "transparent",
            }}>
              <Icon size={15} color={active ? theme.color.accent2 : theme.color.textMuted} style={{ flexShrink: 0 }} />
              <span style={{
                fontSize: 13, fontWeight: active ? 500 : 400,
                color: active ? theme.color.accent2 : theme.color.textPrimary,
                fontFamily: theme.font.sans,
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
