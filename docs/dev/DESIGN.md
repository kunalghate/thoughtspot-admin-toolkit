# Design Direction — ThoughtSpot Admin Toolkit

The visual and interaction contract for this app. New UI must follow it; changes
to it happen here first, then in code. The token implementation lives in
`frontend/styles/theme.css` + `frontend/lib/theme/tokens.ts` (Compendium Light /
Dark). Colors come from tokens only — a hardcoded hex in a component is a
review-blocking defect.

> This doc replaces the earlier cream/purple "Geist" spec, which described the
> pre-Compendium design. HTML wireframes in `wireframes/` predate the current
> theme too: still useful for *layout and flow*, not for colors or type.

## Who this is for

A ThoughtSpot **administrator** doing consequential work: auditing content,
deleting in bulk, transferring ownership. This is a professional data tool, not
a marketing surface. Research on enterprise/admin UX (Nielsen Norman Group's
usability heuristics and complex-application studies; the density and restraint
conventions of IBM Carbon, Atlassian, and Linear-class tools) converges on the
same priorities, which we adopt:

1. **Visibility of system status** — the admin must always know what data
   they're looking at (which cluster, which org, how fresh) and what the app is
   doing (sync progress, job state).
2. **Error prevention over error recovery** — destructive work gets previews
   and forcing functions, not just "Are you sure?" dialogs.
3. **Recognition over recall** — filters, criteria, and counts are spelled out
   in words, never encoded ("unused 90d AND unmodified 90d", not "90d AND 90d").
4. **Density with hierarchy** — admins scan tables of thousands of rows;
   compact type and tight rows are correct here. Hierarchy comes from weight
   and spacing, not decoration.

## Visual language

**No decorative styling. Color always means something.**

- **No gradients, no glows, no signature stripes.** Filled controls use the
  solid `--primary` fill. Shadows are neutral elevation only. (The legacy
  `--gradient-*` / `--glow-accent` token names remain but resolve to solid
  values — never reintroduce actual gradients through them.)
- **One primary, one accent.** `--primary` (indigo) fills primary buttons,
  the logo tile, and progress bars. `--accent` (cyan) is reserved for *state*:
  focus rings, active filters, selection, links, live dots. If an element is
  neither interactive nor stateful, it gets a neutral.
- **Status colors are semantic triples** (`solid / soft / border` for success,
  warn, danger) and are used *only* for status. Danger red appears exclusively
  on destructive actions and failures — never as decoration, so it never
  loses its meaning.
- **Contrast is non-negotiable:** all text ≥ 4.5:1 (WCAG AA) in both themes.
  `--text-muted` is the floor — do not lighten past it. Filled controls pair
  `--primary` with `--on-accent`, which meets AA in both themes.
- **Both themes always.** Every new surface is checked in Compendium Light and
  Dark before it ships.

**Type:** Inter for UI, JetBrains Mono for data identity (GUIDs, counts, log
text). UI sizes 11–15px; grid body 13px; page titles 15px/600; micro labels
10–11px uppercase letter-spaced. Sentence case everywhere else.

**Motion:** 100–200ms ease transitions on hover/open only. Indeterminate
progress and status pulses are the only looping animations. Nothing moves for
decoration.

### Spacing and shape

| Level | Value |
|---|---|
| Page padding | 24–28px |
| Card padding | 18–20px |
| Section gap | 24px |
| Group gap | 12–16px |
| Inline gap (icon + label) | 5–8px |

Radius ladder (tokens exist): cards 9px, controls 6–8px, pills 999px.
Shell dimensions: sidebar 220px fixed; topbar 52px.

## Interaction patterns (the app's grammar)

New features compose these; don't invent parallel patterns.

- **Shell:** left sidebar = places, topbar = context (cluster · org) + data
  freshness + the page's sync action. Pages without a syncable entity show no
  sync UI. Switching clusters is deliberate (Settings → Connections); switching
  orgs is lightweight (topbar dropdown). Offline badges the topbar and falls
  back to cache reads.
- **Tables are the primary surface.** AG Grid, infinite row model, server-side
  filter/sort. Toolbar = search + type pills + count + Export CSV. Column
  filters via header funnels. Counts always visible ("2,552 objects").
- **Selection → contextual action bar.** Checkbox column (pinned left, 40px)
  is the only selection mechanism; row clicks never toggle selection (a stray
  click must not arm a bulk action). Selecting rows reveals an action bar with
  the selection count and available operations; destructive ones are
  danger-styled and carry the count in the label ("Delete 3 users…").
- **Drawer = inspect, modal = commit, popover = criteria.** Right-hand drawers
  show detail (permissions, dependents) without losing table context. Modals
  are for operations that change things. Small criteria live in popovers off
  their pill (stale criteria) with Reset/Apply.
- **Destructive flow is always: select → preview (dry run) → confirm →
  audit.** The primary confirm button stays disabled until the preview has
  run (ShareModal's "Preview changes" gate is the reference implementation).
  Previews state consequences in numbers. Destructive buttons disable after
  first click until the operation settles. Every execution lands in
  History/Jobs with a restorable record where possible.
- **Every live write gets at least a count-confirm.** Lightweight reversible
  writes (tag/untag) don't need the full dry-run ceremony, but they never fire
  straight off a button click — a dialog states the action and the count
  ("Tag 3 objects as 'Stale'?") before anything reaches the cluster. Buttons
  that open such a step carry a trailing ellipsis.
- **In modals, suggestion lists render in-flow, not as overlays.** An absolute
  dropdown inside a modal covers the footer and eats the user's next click;
  push content down instead (PrincipalPicker is the reference).
- **Empty states teach the next step** (Relationships' 1-2-3 explainer is the
  reference), and error states say what happened *and* what to do, escalating
  auth failures to the reconnect banner.

## Freshness is explicit

Per-entity sync status in the topbar; dot color ages with the data:

| State | Dot | Label |
|---|---|---|
| Syncing | accent | "Syncing 847 / 2,400" (or running count) |
| < 1h | success | "Synced just now / 23m ago" |
| 1–6h | muted | "Synced 3h ago" |
| > 6h | warn | "Synced 8h ago" |
| Never / failed | danger | "Never synced" / "Sync failed" |

Never show live-fetched and cached data without labeling which is which.

## Performance rules that shape UX

- Browsing reads SQLite; first paint must never block on a live ThoughtSpot
  roundtrip. Live checks run in the background, throttled, and refine the UI
  when they land.
- Grids stay on the infinite row model so 10k-object clusters scroll flat.

## Copy

- Buttons are verbs with objects: "Sync Users", "Delete 3 users…", "Apply Tag".
- Trailing ellipsis on actions that open another step.
- Numbers over adjectives: "831 stale objects", not "many objects".
- No jargon the TS admin doesn't already use (Liveboard, Worksheet, org).

## Dashboard

The landing page answers, in order: *is my data fresh? what's on this cluster?
is anything failing? what did we change recently?* One aggregate SQLite read
(`GET /api/v1/dashboard`) + the sync log. Stat tiles (mono numbers; warn/danger
tones only when the number demands attention), a single-hue "content by type"
bar list with direct labels, per-entity freshness rows (aged dots), recent
jobs, and the merged audit feed (deletions grouped per session). Everything
links to the page where the admin acts on it. No live cluster calls.

## Known debts (accepted, tracked)

- "Deleted Items" (restore) lives under Jobs; the Bulk Delete page links to it
  (`/jobs?tab=deleted`). Consider promoting it when the deleter is next touched.
- AG Grid selection uses the deprecated v32 string API (works, warns).
  Migrate all grids to the object `rowSelection` API in one pass.
- Groups is a "coming soon" placeholder.

Resolved: Settings tabs unified to underline style; fonts self-hosted via
@fontsource (no CDN); Dashboard built.
