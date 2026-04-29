# Bulk Deleter — Plan

## Context

**Why we're building this.** Admins regularly need to delete *targeted sets* of ThoughtSpot content — every dashboard built on a deprecated worksheet, every object tagged for retirement, or an exact list a stakeholder hands them. The existing Content Archiver covers *discovery-based* deletion (stale by inactivity), but it doesn't cover targeted deletion. The CS Tools CLI exposed this as `bulk-deleter` with three modes: `downstream`, `from-tag`, `from-tabular`. We're translating those into a web UX while reusing as much of Archiver's safety machinery as possible.

**How it differs from Archiver.**
- **Archiver** = discovery-driven. Set staleness criteria → system finds candidates.
- **Bulk Deleter** = targeting-driven. Admin already knows the target; just needs to express it.

The destruction pipeline (TML backup → metadata.delete → audit log → archive_record) is identical, so we share that backend code path. The intake (how the user picks the target set) is fundamentally different, so it gets its own page.

**Backup choice for v1: Option A** — TML export happens inside `_execute_delete()` (same as Archiver). Per-object TML download is available from History after the delete completes. No pre-flight backup ZIP. Bulk-deleted items appear in the same `archive_records` table and are restorable from the existing History/Restore flow.

---

## UX flow (the spine of the design)

A new page at `/deleter` with three top-level mode tabs. Each tab expresses one way to pick the target set, then funnels into a shared "Review → DryRun → Execute → History" pipeline that reuses Archiver components.

```
┌───────────────────────────────────────────────────────────────────┐
│  AppShell (cluster/org switcher in topbar — already global)       │
├───────────────────────────────────────────────────────────────────┤
│  Bulk Deleter                                                     │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│  │ 🔗 Downstream │ │ 🏷️ From Tag  │ │ 📋 From List │  ← mode tabs │
│  └──────────────┘ └──────────────┘ └──────────────┘               │
│                                                                   │
│  ── intake area (per-mode widget) ────────────────────            │
│  ── resolved set summary banner ───────────────────────           │
│  ── grid of resolved items (deselectable) ────────────────        │
│                                                                   │
│  [Action bar]  N items selected · [Delete N items] (red)          │
└───────────────────────────────────────────────────────────────────┘
                          │
                          ▼ (click Delete)
                ┌──────────────────────┐
                │ DryRunModal (reused) │ polling → ready → running → complete
                └──────────────────────┘
                          │
                          ▼ (after complete)
                  History tab (shared)
```

### Mode 1 — Downstream

**Intake.** Search-autocomplete that hits `CachedMetadata` (Worksheet/Table/Model only). User types, picks a root.

**Resolve.** On pick, call `client.fetch_dependents([root])` once, enrich each dependent against `CachedMetadata`, fan into the grid. Show "Root: *Sales Worksheet* (Worksheet) — has **47** dependents (32 Liveboards, 15 Answers)." with a "Change root" link.

**Edge cases.** Zero dependents → empty-state with "Nothing depends on this. Pick a different root." Note: the root itself is **never** included in the deletion set (matches CLI semantics).

### Mode 2 — From Tag

**Intake.** Tag dropdown populated from cached tags (reuse the dropdown logic from Archiver's "Apply Tag" selection bar at [frontend/pages/archiver.tsx:792](frontend/pages/archiver.tsx#L792)).

**Resolve.** Filter `CachedMetadata` where `tag_names` JSON array contains the tag. Show "Tag: *TODO_DELETE* — **23** items (18 Liveboards, 5 Answers)."

**Optional sub-mode: "Also delete the tag itself"** — checkbox below the grid. If checked, after objects are deleted, call `client.delete_tag(tag_id)`. Default off.

**Edge cases.** Zero tagged items → empty-state suggesting another tag.

### Mode 3 — From List

**Intake.** A two-tab widget:
- **Paste GUIDs** — multi-line textarea, accepts newline- or comma-separated IDs.
- **Upload CSV** — drag-drop / file picker. Expect a `guid` column (or first column if no header).

**Resolve.** Parse → dedupe → look up each GUID in `CachedMetadata` (`_fetch_objects_by_guids`, [ts_admin/services/archiver_service.py:41](ts_admin/services/archiver_service.py#L41)). Show "Resolved: **47 of 50** GUIDs found. **3 unrecognized** (expand to view)." Unrecognized list is shown collapsed; user can fix typos and re-resolve.

**Edge cases.** All unrecognized → "None of those GUIDs exist in this cluster's cache. Run a Metadata sync first?" with a sync link.

### Common pipeline (after mode produces a resolved set)

1. **Grid** — identical column set to Archiver. Reuse [frontend/components/ArchiverGrid/columns.ts](frontend/components/ArchiverGrid/columns.ts) wholesale (just move it to the shared `components/Deleter/columns.ts` location). Columns:
   - **Name** (flex 3, primary identifier)
   - **Type** (TypeChip — Liveboard / Answer / Worksheet badges)
   - **Owner** — surfaces "wait, that's the CFO's dashboard" mistakes
   - **Tags** (up to 2 with "+N more") — flags items that carry *other* tags the user might care about (e.g. `TODO_DELETE` plus `KEEP_FOREVER`)
   - **Last Accessed**, **Days Unused**, **Views** — sanity checks even when the user picked by lineage/tag/list ("this was viewed 5 days ago, am I sure?")
   - **Modified**, **Created** — date context to catch "I'm deleting something edited yesterday"
   - All rows pre-selected by default; user can deselect specific rows before clicking Delete.
   - One shared `columns.ts` for both Archiver and Bulk Deleter — no presets needed.
2. **Action bar** — sticky bottom strip showing "**N** items selected · [Delete N items]" (red button).
3. **DryRunModal** — reused 1:1 from Archiver. Same state machine (polling → ready → running → complete), same typed "DELETE" confirmation, same impact cards, same dependency warnings, same per-object grid.
4. **Execute** — calls shared `deletion_service._execute_delete()`. TML backup → delete → audit log → `archive_records` insert.
5. **History** — promoted to a top-level page (or sub-route) that lists *all* deletions from both Archiver and Bulk Deleter. Same Restore action.

### Cross-cutting UX rules

- **Cluster/Org awareness.** Switching cluster or org clears the current mode's intake state (with a confirm if a selection exists).
- **State preservation across mode tabs.** Switching from "Downstream" to "From Tag" and back keeps the previously-picked root. Lost only on cluster/org switch or full page reload.
- **Empty cache handling.** All three modes depend on `CachedMetadata`. If the user lands here with an empty cache for the current cluster/org, show a "Sync first" CTA at the page level.
- **System User exclusion.** Same guard as Archiver — `owner_name != "System User"` filters at query time.
- **Friction proportional to blast radius.** Typed "DELETE" confirmation is required regardless of count. (Considered tier-2 friction for >100 items but skipped for v1.)
- **Reminder of safety net.** Modal footer copy: *"TML backups will be available for restore from History after deletion completes."*

---

## Architecture summary

**Backend:**
- Extract `_execute_delete()` and `dryrun()` from [ts_admin/services/archiver_service.py](ts_admin/services/archiver_service.py) into a new shared `ts_admin/services/deletion_service.py`. Archiver continues to call them; new Bulk Deleter router calls them too.
- New router `ts_admin/api/deleter.py` with mode-specific resolve endpoints + dryrun/execute that delegate to the shared service.
- New ts_client method: `delete_tag(tag_id)` on [ts_admin/ts_client/client.py](ts_admin/ts_client/client.py) (only needed for the optional "delete the tag itself" sub-mode).
- Add `job_type="bulk_delete"` (and `"bulk_delete_dryrun"`) so History can label the source. No new model fields required — `archive_records` already has `job_id`, which links back to `Job.job_type`.

**Frontend:**
- New page `frontend/pages/deleter.tsx` with mode tabs.
- New components in `frontend/components/DeleterIntake/`: `DownstreamPicker.tsx`, `TagPicker.tsx`, `ListPaste.tsx`.
- **Promoted to shared** (move out of `ArchiverGrid/`): `DryRunModal.tsx` → `components/Deleter/DryRunModal.tsx`, `columns.ts` → `components/Deleter/columns.ts`. Both Archiver and Bulk Deleter import from the new location.
- History tab: lift the existing History tab content out of `frontend/pages/archiver.tsx` into a shared component (or new page `frontend/pages/deletions.tsx`), backed by the same `archive_records` table. Both Archiver and Bulk Deleter pages link to it.

---

## Files to create / modify

**New files:**
- [ts_admin/services/deletion_service.py](ts_admin/services/deletion_service.py) — shared `_execute_delete`, `dryrun`, `dryrun_objects`. Cut from `archiver_service.py`.
- [ts_admin/api/deleter.py](ts_admin/api/deleter.py) — router with `/resolve/downstream`, `/resolve/tag`, `/resolve/list`, `/dryrun`, `/dryrun/{job_id}/objects`, `/execute`, `/results`.
- [frontend/pages/deleter.tsx](frontend/pages/deleter.tsx) — page with three mode tabs.
- [frontend/components/DeleterIntake/DownstreamPicker.tsx](frontend/components/DeleterIntake/DownstreamPicker.tsx)
- [frontend/components/DeleterIntake/TagPicker.tsx](frontend/components/DeleterIntake/TagPicker.tsx)
- [frontend/components/DeleterIntake/ListPaste.tsx](frontend/components/DeleterIntake/ListPaste.tsx)
- [frontend/components/Deleter/DryRunModal.tsx](frontend/components/Deleter/DryRunModal.tsx) — moved from `ArchiverGrid/`.
- [frontend/components/Deleter/columns.ts](frontend/components/Deleter/columns.ts) — moved from `ArchiverGrid/`.
- [tests/integration/test_deleter_api.py](tests/integration/test_deleter_api.py) — one happy-path test per mode + one dry-run + one execute (mocking the TS client).
- [tests/unit/test_deletion_service.py](tests/unit/test_deletion_service.py) — for the extracted service.

**Modified files:**
- [ts_admin/services/archiver_service.py](ts_admin/services/archiver_service.py) — replace lines 599–1024 (`_execute_delete`, `dryrun`, `dryrun_objects`) with thin imports from `deletion_service`. Keep `execute()` (tag/untag wrapper) intact since that calls into `_execute_delete`.
- [ts_admin/api/archiver.py](ts_admin/api/archiver.py) — re-route `/dryrun` and `/execute` to delegate to `deletion_service`. Behavior unchanged for end users.
- [ts_admin/main.py](ts_admin/main.py) — register `deleter.router` alongside `archiver.router`.
- [ts_admin/ts_client/client.py](ts_admin/ts_client/client.py) — add `delete_tag(tag_id)` only if we're shipping the "Also delete the tag" sub-mode in v1.
- [frontend/lib/api.ts](frontend/lib/api.ts) — add `deleterApi` with `resolveDownstream`, `resolveTag`, `resolveList`, `results`, `dryrun`, `dryrunObjects`, `execute`. Mirror the shape of `archiverApi`.
- [frontend/lib/types.ts](frontend/lib/types.ts) — `DeleterMode`, `DeleterResolveResponse`, reuse `ArchiverItem` for grid rows.
- [frontend/components/Shell/index.tsx](frontend/components/Shell/index.tsx) — add nav link "Bulk Delete" → `/deleter`.
- [frontend/pages/archiver.tsx](frontend/pages/archiver.tsx) — switch to importing the shared `DryRunModal` and `columns` from `Deleter/`. History tab continues to work; if we extract the History view, point both pages at it.

**Functions/utilities to reuse (do not re-implement):**
- [`_fetch_objects_by_guids`](ts_admin/services/archiver_service.py#L41) — chunked SQLite lookup for resolve modes.
- [`_chunks`](ts_admin/services/archiver_service.py#L35) — chunking helper.
- [`client.fetch_dependents`](ts_admin/ts_client/client.py#L555) — for downstream resolve.
- [`client.delete_metadata`](ts_admin/ts_client/client.py#L475) — already wired; called inside `_execute_delete`.
- `JobService.create_job / mark_running / update_progress / mark_complete / mark_partial / mark_failed` — same job lifecycle.
- `AuditLog` writer pattern — same `action_type="bulk_delete"` (vs Archiver's `"delete"`).
- `serializeFilterModel` from [frontend/lib/agGridFilters.ts](frontend/lib/agGridFilters.ts).

**No DB migration required** — `archive_records` and `jobs` already have everything we need.

---

## Phasing (suggested commit boundaries)

1. **Refactor**: extract `deletion_service.py` from `archiver_service.py`. Archiver tests must still pass unchanged.
2. **Backend Bulk Deleter**: add `api/deleter.py`, the three resolve endpoints, and dryrun/execute delegating to the shared service.
3. **Frontend mode tabs + intake widgets**: scaffold `pages/deleter.tsx` with the three tabs, intake components, and resolved-set grid. No delete actions yet.
4. **Wire DryRunModal**: move modal/columns to the shared `Deleter/` location, update Archiver imports, hook up Deleter "Delete N items" button.
5. **Shared History page**: extract History tab content into a shared component or `pages/deletions.tsx`; both pages link to it.
6. **Optional**: `delete_tag` ts_client method + "Also delete the tag" checkbox in From-Tag mode.
7. **Tests + docs**: integration tests per mode, `docs/dev/features/deleter/HOW_IT_WORKS.md` mirroring the archiver doc.

---

## Verification

- `make dev` — start FastAPI + Next.js. Open `http://localhost:3000/deleter`.
- **Downstream mode**: pick a Worksheet that has dashboards built on it; verify dependents grid; open dry-run; confirm impact summary matches; cancel before delete.
- **From Tag mode**: tag two test objects with `BULK_DELETE_TEST`; pick the tag; verify both appear; deselect one; delete the other; check it's gone in the ThoughtSpot UI; check the surviving one still has the tag.
- **From List mode**: paste two GUIDs (one valid, one bogus); verify "1 of 2 resolved" + bogus listed under unrecognized; delete the valid one.
- **Restore round-trip**: after a Bulk Delete, open History → Restore → verify the object reappears in ThoughtSpot with a new GUID.
- **Audit log** — confirm one row per execute with `action_type="bulk_delete"`, parameters containing the resolved object IDs, status SUCCESS/PARTIAL/FAILED.
- **Tests**: `make test` — unit tests for `deletion_service` (mocked TS client), integration tests for each `/api/v1/deleter/*` endpoint, vitest for intake component validation (CSV parser, GUID dedupe).
- **Regression**: existing Archiver tests untouched — refactor is internal-only.
