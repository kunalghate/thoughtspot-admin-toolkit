# Design System

Visual language for the ThoughtSpot Admin Toolkit frontend.
Reference this when building new pages or components.

---

## Color Palette

### Base (Anthropic-inspired)

| Role | Token | Hex | Usage |
|---|---|---|---|
| Background | `--bg` | `#F2EDE3` | Page background, sidebar footer, inputs |
| Surface | `--surface` | `#FAF8F4` | Cards, sidebar, topbar, panels |
| Border | `--border` | `#E8E1D5` | All borders, dividers |
| Border light | `--border-light` | `#F0EBE3` | Subtle row dividers inside cards |

### Text

| Role | Token | Hex | Usage |
|---|---|---|---|
| Primary | `--text` | `#1A1714` | Headings, body, table cells |
| Muted | `--muted` | `#7A7068` | Labels, secondary info |
| Faint | `--faint` | `#A89E96` | Placeholders, timestamps, column headers |

### Accent (Apple Intelligence-inspired)

| Role | Token | Hex | Usage |
|---|---|---|---|
| Accent | `--accent` | `#8B5CF6` | Buttons, links, focus rings, active indicators |
| Accent dark | `--accent-dark` | `#6D28D9` | Text on light accent bg, hover states |
| Accent light | `--accent-light` | `#EDE9FE` | Active nav bg, selected row bg, chip bg |
| Accent border | `--accent-border` | `#C4B5FD` | Borders on accent-tinted elements |
| Logo gradient | — | `#8B5CF6 → #6D28D9` | Logo mark only |

### Semantic

| Role | Hex | Usage |
|---|---|---|
| Success | `#059669` | Connected dot, success badges, positive states |
| Success bg | `#D1FAE5` | Success badge background |
| Danger | `#DC2626` | Destructive actions, error states |
| Danger bg | `#FEE2E2` | Error badge background |
| Warning | `#D97706` | Stale data indicators, caution states |
| Warning bg | `#FEF3C7` | Warning badge background |

---

## Typography

**Font:** Geist (body, UI) · Geist Mono (numbers, code, logo mark)
Load from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=Geist+Mono:wght@400;500;600;700&display=swap" rel="stylesheet" />
```

### Scale

| Role | Font | Size | Weight | Usage |
|---|---|---|---|---|
| Page title | Geist | 15px | 600 | Topbar page name |
| Section heading | Geist | 13px | 600 | Card titles, section labels |
| Body | Geist | 13px | 400 | Table cells, descriptions |
| Small | Geist | 12px | 400 | Metadata, sub-labels |
| Micro label | Geist | 10–11px | 600 | Column headers (uppercase), tag labels |
| Display numbers | Geist Mono | 26–30px | 700 | Stat card values |
| Inline numbers | Geist Mono | 13px | 400 | Table numeric columns |
| Code / URLs | Geist Mono | 12px | 400 | Cluster URLs, config values |

---

## Spacing

| Level | Value | Usage |
|---|---|---|
| Page padding | 28px | Outer content area inset |
| Card padding | 20px | Inside cards and panels |
| Section gap | 24px | Between major layout sections |
| Group gap | 14–16px | Between related elements |
| Element gap | 8–10px | Between tightly related items |
| Inline gap | 6px | Icon + label, dot + text |

---

## Layout

### Shell
```
┌─────────────────────────────────────────────────────┐
│  Sidebar (220px)  │  Topbar (52px height)            │
│  ─────────────────┤  ─────────────────────────────── │
│  Logo (52px)      │  Page content (scrollable)        │
│  Nav items        │                                   │
│  ─────────────────│                                   │
│  Cluster pill     │                                   │
└─────────────────────────────────────────────────────┘
```

- **Sidebar:** 220px fixed, `#FAF8F4`, `border-right: 1px solid #E8E1D5`
- **Topbar:** 52px, `#FAF8F4`, `border-bottom: 1px solid #E8E1D5`
- **Content:** `padding: 28px`, scrollable, background `#F2EDE3`

### Org selector (topbar, always top-right)
Two-part pill showing cluster (static) and org (interactive):
```
[ ● Production | Finance ▾ ]
  └─ static    └─ dropdown trigger
```
- Cluster name: non-clickable context. Switching cluster requires Settings → Connections.
- Org name: opens dropdown listing all orgs on the current cluster.

### Cluster indicator (sidebar footer)
Static pill showing active cluster with a connection status dot and a "Switch →" link that navigates to Settings → Connections.

---

## Border Radius

| Element | Radius |
|---|---|
| Cards, panels | 9–12px |
| Buttons, inputs, chips | 6–7px |
| Badges, pills | 99px (fully rounded) |
| Avatars | 50% (circle) |
| Logo mark | 7px |

---

## Component Patterns

### Buttons

```
Primary:  bg #8B5CF6, text white, radius 6px, padding 6px 13px
Ghost:    bg transparent, border #E8E1D5, text #7A7068
Danger:   bg #FEE2E2, border #FECACA, text #DC2626
```
- Font: Geist 12px weight 500 for topbar buttons, 13px weight 600 for modal/panel actions
- Disable after first click on destructive actions until operation completes

### Bulk action bar
Appears when rows are selected in any data grid. Dark background (`#1A1714`), white text. Contains selected count, action buttons, and a clear selection link.

### Badges / Status chips

```
Active:   bg #D1FAE5, text #065F46
Inactive: bg #F0EBE3, text #7A7068
Fresh:    bg #D1FAE5, text #065F46
Stale:    bg #FEF3C7, text #92400E
Error:    bg #FEE2E2, text #991B1B
Group:    bg #EDE9FE, text #6D28D9
```

### Data grids (AG Grid)
- Column headers: `#F2EDE3` background, `#A89E96` text, 10px uppercase
- Row hover: `#F2EDE3`
- Selected row: `#EDE9FE` (accent light)
- Row divider: `1px solid #F0EBE3`
- Checkbox accent: `#8B5CF6`

### Input focus state
```css
border-color: #8B5CF6;
box-shadow: 0 0 0 3px #EDE9FE;
background: #FAF8F4;
```

### Cards
```css
background: #FAF8F4;
border: 1px solid #E8E1D5;
border-radius: 9px;
padding: 20px;
```

---

## Sync status indicators

Shown per-entity on each page and on the Settings → Sync page.

| State | Color | Label format |
|---|---|---|
| < 1 hour | `#A89E96` (gray) | "Synced 23 min ago" |
| 1–6 hours | `#D97706` (amber) | "Synced 3h ago" |
| > 6 hours | `#D97706` + ⚠ | "Synced 8h ago ⚠" |
| Never | `#DC2626` (red) | "Never synced" |
| In progress | — | "Syncing... 847 / 2,400" |

---

## Wireframes

HTML wireframes for all key screens are in `wireframes/`:

| File | Screen |
|---|---|
| `dashboard.html` | Instance health, sync status, activity feed |
| `users.html` | User table with bulk action bar |
| `archiver.html` | 4-step wizard + dry-run modal |
| `settings-connections.html` | Cluster management + edit panel |
| `org-switcher.html` | Org dropdown interaction |

Open any wireframe in a browser — they link to each other via the navigation in the bottom-right corner.
