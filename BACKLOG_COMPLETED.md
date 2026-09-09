# BACKLOG — COMPLETED

Closed items moved out of [BACKLOG.md](BACKLOG.md) to keep it readable. Entries
are kept in full — several carry load-bearing caveats (see the Cycle notes in
BACKLOG.md; S6 in particular is "decided and closed", not "implemented as
written"). Same rules as the backlog: never edit an item's criteria, never
delete an entry. When a backlog item reaches `done`, move its index line and
detail entry here.

## Index

### Done items (34)

| ID | P | Item | Protected |
|----|---|------|-----------|
| S3 | P2 | Group Management (read-only v1) | — |
| S4 | P2 | get_group_detail was not org-scoped | — |
| S6 | P2 | Lineage edges outlive deleted targets (closed via S22) | — |
| S10 | P1 | Stale-90d tile counted objects the Archiver cannot touch | — |
| S11 | P1 | Never-synced entity rendered as a confident 0 | — |
| S12 | P1 | Failed jobs shown without a reason | — |
| S13 | P2 | failed_jobs_7d capped at the newest 200 jobs | — |
| S14 | P2 | stats loaded every CachedMetadata row into Python | — |
| S15 | P2 | Dashboard surfaced no actionable signal | — |
| S16 | P2 | Recent admin activity showed stale duplicate rows | — |
| S17 | P3 | No trends, no polling, frozen timestamps | — |
| S18 | P3 | Dashboard UX: blank first paint, swallowed failure, wasted width | — |
| S22 | P2 | Ghost lineage nodes rendered as normal nodes | — |
| S25 | P2 | No composite index on (cluster_id, org_id, ts_guid) | — |
| W2 | P2 | ruff format drift from unbounded pin | — |
| S24 | P3 | POST /sync/{entity} has no concurrency guard | — |
| S34 | P2 | Concurrent dependency syncs for the same (cluster, org) | — |
| F4 | P2 | Lineage zoom controls near-invisible in dark mode | — |
| F5 | P4 | Dashboard: vertically align the per-job status pills | — |
| F6 | — | Repeated Sync clicks start duplicate syncs (closed via S24/S34) | — |
| F7 | P3 | Direct table-to-answer arrow drawn even when the answer sits on a model | — |
| S27 | P1 | Lineage unit suite is mutation-vacuous | — |
| S36 | P1 | Startup crash-recovery purges cache rows for never-deleted objects | yes (`ts_admin/main.py`) |
| S23 | P2 | Interrupted metadata sync reads as fully synced | — |
| S33 | P3 | Topbar sync label/color has no test | — |
| M6 | P2 | No REJECT route when acceptance criteria are themselves the bug | — |
| M9 | P2 | Criteria-prescribed mechanisms are never soundness-checked before build | — |
| F12 | P2 | Browser serves stale UI after a pip upgrade until hard refresh | yes (`ts_admin/main.py`) |
| S31 | P2 | preview_delete trusts a possibly-truncated cache ('0 owned objects') | — |
| F1 | P3 | Connections list page with per-connection object counts | yes (`tests/integration/test_cluster_isolation.py`) |
| F3 | P4 | 'Created by' on the users list; 'Created' date on the groups list | — |
| F8 | P4 | CSV export for Users and Groups | — |
| F9 | P3 | Export tagged content to TML without deleting | — |
| F10 | P3 | Transfer ownership of selected objects from the Metadata screen | yes (`tests/integration/test_dryrun_safety.py`) |

## Completed items

### S3 — Group Management (read-only v1)

`P2` · **done** · protected: no

Group Management (read-only v1): group grid + detail drawer, user audit drawer, and the ThoughtSpot `fetch-permissions` API-drift fix

**Acceptance criteria:** `GET /api/v1/groups` and `/groups/{guid}` serve the Groups page from cache (cluster + org scoped); `/users/{guid}/access` returns live EFFECTIVE permissions; `principal_permissions` hits `security/principals/fetch-permissions` with the correct body key and parses the real nested response; full verification bar green

### S4 — get_group_detail was not org-scoped

`P2` · **done** · protected: no

Group detail drawer disagreed with the grid: `get_group_detail` was not org-scoped

**Acceptance criteria:** `get_group_detail` scopes the group row, member count, and member list to a single org; a regression test seeds one GUID in two orgs and asserts the drawer count equals the grid count for each

### S6 — Lineage edges outlive deleted targets (closed via S22)

`P2` · **done** · protected: no

Lineage edges whose **target** is deleted outlive it — ghost nodes in the graph

**Acceptance criteria:** A model deleted in TS while its consuming liveboard is unchanged leaves no `CachedDependency`/`CachedColumnUsage` row behind; a unit test deletes a target from `CachedMetadata`, rebuilds, and asserts the edge is gone. (Phase 1 owns `USES`/non-LIVEBOARD, `_persist_column_map` owns `CONNECTS` + LIVEBOARD `USES`; neither purges by target GUID, and `build_column_map`'s `if not table_guids and not lb_guids: return 0` early-return skips the purge entirely)

### S10 — Stale-90d tile counted objects the Archiver cannot touch

`P1` · **done** · protected: no

The `Stale (90d unused)` tile counted objects the Archiver cannot touch: `MetadataService.stats` treated a null `last_accessed_at` as disuse across ALL object types, so tables/worksheets/SQL views (which carry no access telemetry) dominated it — 2,546 of 2,650 on a real cluster — while the tile linked to an Archiver scoped to `LIVEBOARD`/`ANSWER`

**Acceptance criteria:** `stats` scopes staleness to `ARCHIVABLE_TYPES` and excludes System User content (mirroring the Archiver's own conditions); "aged out" (`stale_90d`) and "no access data" (`never_accessed`) are separate counts; unit tests cover both the type scoping and the System User exclusion

### S11 — Never-synced entity rendered as a confident 0

`P1` · **done** · protected: no

A never-synced entity rendered as a confident `0` (the Tags tile read "0" while Data freshness said "never synced" two cards down)

**Acceptance criteria:** The dashboard payload carries a per-entity `synced` flag; the UI renders `—` plus a sync action instead of a number for any entity that has never synced successfully; an integration test asserts the flag is false before the first sync and true after

### S12 — Failed jobs shown without a reason

`P1` · **done** · protected: no

Failed jobs were shown without a reason: `Job.error`/`error_type`/`error_traceback` are persisted and `DashboardJob.error` was carried through the API, but the page rendered only a red `FAILED` pill, so diagnosing meant leaving the dashboard

**Acceptance criteria:** Failed rows in Recent jobs show `error_type: error` inline (full text on hover) and a Retry action for sync jobs that re-triggers `POST /sync/{entity}`; an integration test asserts the failure reason reaches the client

### S13 — failed_jobs_7d capped at the newest 200 jobs

`P2` · **done** · protected: no

`failed_jobs_7d` was computed by filtering the newest 200 jobs in Python, so on a busy cluster genuine failures fell out of the window and the tile under-reported

**Acceptance criteria:** `failed_jobs_7d` is a SQL `COUNT` over the 7-day window with no row cap; a regression test seeds 250 newer jobs behind a failure and asserts it is still counted

### S14 — stats loaded every CachedMetadata row into Python

`P2` · **done** · protected: no

`MetadataService.stats` loaded every `CachedMetadata` row into Python to count them — fine at 2,650 objects, linear memory at 100k

**Acceptance criteria:** Inventory counts come from a SQL `GROUP BY`; staleness counts are `COUNT` queries; no full-table materialization remains in `stats`

### S15 — Dashboard surfaced no actionable signal

`P2` · **done** · protected: no

The dashboard showed no signal an admin could act on: inactive users, empty groups, ungrouped users, and content owned by deleted users were all already in SQLite and never surfaced

**Acceptance criteria:** The payload exposes an `attention` block (`inactive_users`, `empty_groups`, `users_without_group`, `orphaned_content`) as cheap aggregate queries; the page leads with a "Needs attention" band that shows an explicit all-clear when every count is zero; each signal returns 0 until BOTH entities it joins have synced, so an un-synced cluster raises no false alarm; integration tests cover the counts and the un-synced guard

### S16 — Recent admin activity showed stale duplicate rows

`P2` · **done** · protected: no

"Recent admin activity" showed four identical 4-month-old rows: grouping was per `job_id` only and the feed had no age bound, so history read as current activity

**Acceptance criteria:** Activity is bounded to `ACTIVITY_MAX_AGE_DAYS` and identical adjacent entries collapse into one row with a `×N` count; tests cover both the age cutoff and the collapse

### S17 — No trends, no polling, frozen timestamps

`P3` · **done** · protected: no

Every dashboard number was a point-in-time count with no trend, and the page never polled — a running sync was invisible and relative timestamps froze at mount

**Acceptance criteria:** `SyncLog.record_count` deltas between the two most recent successful syncs are shown next to each tile; `running_jobs` (with progress/total) is exposed and rendered as a progress bar; the page polls fast while work is in flight and slowly otherwise

### S18 — Dashboard UX: blank first paint, swallowed failure, wasted width

`P3` · **done** · protected: no

Dashboard UX: blank page on first paint (`if (!data) return null`), a silently swallowed sync-status failure that rendered as "never synced", inventory and problems given identical visual weight, and ~40% of the viewport unused on a wide screen

**Acceptance criteria:** Loading renders a skeleton; a failed read surfaces instead of being swallowed; problems and inventory are visually separated; the layout uses the available width. (Tags KPI and the Data freshness card were removed at the human's direction during this cycle)

### S22 — Ghost lineage nodes rendered as normal nodes

`P2` · **done** · protected: no

Ghost lineage nodes render as *normal* nodes: the `accessible` flag is plumbed end-to-end but nothing ever sets it `False`. **Proposed as the safe replacement for S6's deletion approach** — `ts_dependency.py:6-9` documents "inaccessible stubs" as a supported edge endpoint, so the ghost is a read-path gap, not a cache-integrity problem

**Acceptance criteria:** `get_lineage_graph` sets `accessible=False` on any node whose `guid` has no `CachedMetadata` row for the same `(cluster_id, org_id)`, so a deleted-in-TS target renders dashed/0.6-opacity with the existing "Inaccessible" Legend key instead of as a normal node; no rows are deleted; a unit test deletes a target from `CachedMetadata`, reads the graph, and asserts the node comes back `accessible=False`. **Depends on S25** — the existence lookup this needs is exactly the one that goes quadratic without a composite index; do S25 first or resolve the node set with a single set-membership query rather than a per-node lookup

### S25 — No composite index on (cluster_id, org_id, ts_guid)

`P2` · **done** · protected: no

`CachedMetadata` has only single-column indexes (`cluster_id`, `org_id`, `ts_guid`), so any correlated subquery or join keyed on `(cluster_id, org_id, ts_guid)` picks `ix_ts_metadata_cluster_id` — the least selective — and goes quadratic. The app never runs `ANALYZE` and sets no pragmas (`database.py:30-34`), so SQLite's no-stats heuristic has nothing better to choose

**Acceptance criteria:** A `(cluster_id, org_id, ts_guid)` composite index exists on `ts_metadata` and `EXPLAIN QUERY PLAN` for a correlated lookup shows `SEARCH ts_metadata USING COVERING INDEX ... (cluster_id=? AND org_id=? AND ts_guid=?)`. **`create_all` does NOT add an index to an already-existing table**, so it ships as an explicit `CREATE INDEX IF NOT EXISTS` in `init_db()` (or a sentinel bump) and is verified against a DB created before the change

### W2 — ruff format drift from unbounded pin

`P2` · **done** · protected: no

`ruff format` drift breaks the format gate: unbounded `ruff>=0.4.0` pin lets a newer local/CI ruff reformat 5 files inherited from PR #10

**Acceptance criteria:** `ruff format --check ts_admin/ tests/` is green both locally and in CI. Resolve by (a) `ruff format`-ing the 5 drifted files (`ts_admin/services/lineage_service.py`, `tests/unit/test_lineage_columns.py`, `tests/unit/test_lineage_models.py`, `tests/unit/test_lineage_service.py`, `tests/integration/test_relationships_api.py`) AND (b) bounding the ruff version in `pyproject.toml` `[dev]` (e.g. a `~=` pin) so formatter output is reproducible across runs. Confirm whether CI's resolved ruff actually reddens `main` before/after.

### S24 — POST /sync/{entity} has no concurrency guard

`P3` · **done** (PR #34) · protected: no

`POST /sync/{entity}` has no concurrency guard, so a metadata sync and a dependencies build interleave on the same event loop while the metadata cache is mid-repopulation (`api/sync.py:85-138` creates jobs unconditionally)

**Acceptance criteria:** Triggering a sync for an entity that already has a running job is rejected (or queued) rather than starting a second concurrent run; a test asserts the second trigger does not start while the first is RUNNING

**Done:** PR #34 — sync endpoints now return the in-flight job (HTTP 409-free adopt) instead of starting a duplicate; `tests/integration/test_sync_api.py` asserts the second trigger produces no second RUNNING job. The F6 frontend detail (dashboard disable + derived in-flight predicate for `CacheFreshnessCard`/`RecentJobsCard`) shipped in the same PR

### S34 — Concurrent dependency syncs for the same (cluster, org)

`P2` · **done** (PR #34) · protected: no

Nothing prevents two dependency syncs running concurrently for the same (cluster, org). Observed live: three simultaneous TML crawls on ps-internal-prod (one script + two UI "Sync Lineage" clicks) produced sustained 30s `metadata/tml/export` timeouts and retry backoff, tripling the wall-clock of all three. Not corrupting — each pass is a full delete-and-rebuild scoped to (cluster, org), so last writer wins with a complete set — but the UI gives no signal that a build is already in flight and happily starts another

**Acceptance criteria:** Triggering a `dependencies` sync while one is already RUNNING for the same (cluster, org) either refuses with an actionable message or returns the in-flight job instead of starting a second crawl; a test asserts the second trigger does not produce a second RUNNING job

**Done:** PR #34 — a `dependencies` trigger while one is RUNNING for the same (cluster, org) adopts the in-flight job; metadata-before-lineage ordering waits for an in-flight metadata sync and chains one when metadata was never synced (`sync_service.py`, `lineage_service.py`; unit + integration tests added)

### F4 — Lineage zoom controls near-invisible in dark mode

`P2` · Bug · **done** (PR #34) · protected: no

**Ask:** **Lineage zoom controls near-invisible in dark mode**

**Triage + acceptance criteria:** CONFIRMED. `<Controls />` (`LineageFlow.tsx:437`) is unthemed: no `colorMode` prop and no `.react-flow__controls` override anywhere, so dark mode renders light-colored icons on a `#fefefe` pill. Criteria: controls are themed via `colorMode` or `--xy-controls-button-*` tokens under the existing dark-theme selector (token route matches how AG Grid is themed in `theme.css`); both themes checked per DESIGN.md

**Done:** PR #34 — controls themed via `--xy-controls-button-*` tokens in `theme.css` under the existing theme selectors; both themes checked

### F5 — Dashboard: vertically align the per-job status pills

`P4` · Bug · **done** (PR #34) · protected: no

**Ask:** **Dashboard: vertically align the per-job status pills** ("Complete" etc.)

**Triage + acceptance criteria:** CONFIRMED plausible. `RecentJobsCard` puts the status pill after a variable-width label in a flex row (`dashboard.tsx:657-666`), so pills start at different x per row. The codebase already solves this with a fixed gutter (`.dash-attn-row`, `theme.css:461`). Criteria: status pills share a left edge (fixed label column or minWidth), matching the attention-row pattern

**Done:** PR #34 — status pills share a left edge via a fixed label gutter matching the attention-row pattern (`dashboard.tsx`, `theme.css`)

### F6 — Repeated Sync clicks start duplicate syncs of the same type

`—` · Bug · **done** (via S24/S34, PR #34) · protected: no

**Ask:** **Repeatedly pressing Sync starts duplicate syncs of the same type**

**Triage + acceptance criteria:** ALREADY FILED — this is **S24** (no backend concurrency guard on `POST /sync/{entity}`) and **S34** (same for dependencies, observed live). Triage adds a frontend detail for whoever works S24: the dashboard's disable lasts only for the POST round-trip (`dashboard.tsx:161-175`), and `CacheFreshnessCard`/`RecentJobsCard` get the raw local set rather than the derived in-flight predicate the tiles use (`dashboard.tsx:241,249`) — fix both when S24 lands

**Done:** PR #34 — closed alongside S24/S34; the frontend detail (round-trip-only disable, raw local set on the cards) was fixed in the same PR (topbar/dashboard adopt in-flight syncs)

### F7 — Direct table-to-answer arrow drawn even when the answer sits on a model

`P3` · Bug · **done** · protected: no

**Ask:** **Lineage draws a direct table → answer arrow even when the answer sits on a model** (e.g. "Average Sales by Weekly Date" on SE Demo)

**Triage + acceptance criteria:** CONFIRMED. ThoughtSpot's `dependent_objects` sweep for a physical table returns transitive dependents, and `_edges_from_dependents` (`lineage_service.py:307-368`) writes every pair, so both ANSWER→MODEL and ANSWER→DB_TABLE edges land in the cache and both render. Criteria: a transitive ANSWER/LIVEBOARD→table edge is suppressed when a path through a model exists in the same build (post-pass transitive reduction in `build_object_graph`, not a render-only hide — `_downstream_closure_count` and the consumer drawer count through the cache); a genuinely table-backed answer keeps its direct edge; unit test covers both

**Done:** `_reduce_transitive_edges` in `lineage_service.py` drops answer→table shortcut edges when a path through a model exists; unit tests in `test_lineage_service.py` cover both the reduction and the genuinely table-backed answer keeping its direct edge

### S27 — Lineage unit suite is mutation-vacuous

`P1` · **done** · protected: no

**Blocks any re-attempt of S7.** The lineage unit suite is mutation-vacuous: on the rejected S7 diff, 6 of 7 mutations to `build_column_map` left all 16 tests in `tests/unit/test_lineage_columns.py` green — including one making the builder never re-crawl any liveboard ever again. No test in the file seeds a **future** `lb_modified`, so "a genuinely changed liveboard is re-exported" has never been asserted; marker/watermark org-scoping and the write-ordering invariant were likewise unguarded

**Acceptance criteria:** Every behavioural predicate in `build_column_map`'s incremental path is killed by at least one test: deleting `_changed`'s `modified_at > last_built` comparison, dropping `org_id` from any watermark read/write, and reordering the post-persist write each turn at least one test red. The mutation list and its results are recorded in [docs/dev/TESTING.md](docs/dev/TESTING.md) so the next lineage change starts from a suite that can detect its own failure modes

**Closed 2026-08-24.** The coverage shipped in PRs #21/#22; the row stayed
`in-review` only because one of its three enumerated mutations — "reordering the
post-persist write" — describes the *rejected* S7 branch and has never existed
on `main` (the M9 failure mode, inside the row filed to prevent it). Re-measured
against current `main` before closing, and the criteria's general clause holds:
dropping `_changed`'s `modified_at > last_built` comparison, forcing `_changed`
to always re-crawl, and dropping either `org_id` or `cluster_id` from the
`last_built` watermark read each turn at least one test red (4/4 killed). The
one criterion that cannot be met is dropped as unmeetable rather than faked.
Answer-index coverage — the same gap in the byte-identical twin — is tracked
separately as **S30**.

### S36 — Startup crash-recovery purges cache rows for never-deleted objects

`P1` · **done** · protected: yes (`ts_admin/main.py`)

Startup crash-recovery deletes `CachedMetadata` rows for objects that were never deleted, is not cluster/org-scoped, and ignores Bulk Deleter jobs. `main.py:70` matches `job_type == "archive"` only (the Deleter creates `bulk_delete`, so it gets no recovery at all), and `:73-88` infers "deleted" from `tml_export_status == "SUCCESS"` — but `_execute_delete` exports EVERY object in Phase A before Phase B deletes any, so the most likely crash window is exactly where that inference is maximally wrong: the recovery purges the cache for N objects of which zero were deleted. The admin then sees them vanish from Metadata Explorer and the Archiver while they are still live in ThoughtSpot, and their `ArchiveRecord`s report `is_restorable=True`, so "restoring" them creates duplicates. `:82`'s `sql_delete` also has no `cluster_id`/`org_id` predicate, though the stuck `Job` row carries `cluster_id`

**Acceptance criteria:** Crash recovery does not infer deletion from TML-export status: either `_execute_delete` records per-GUID delete confirmation (an `ArchiveRecord` field set only after `delete_metadata` returns) and recovery purges only confirmed rows, or recovery purges nothing and instead marks the metadata `sync_log` non-authoritative so the next read re-syncs. The delete is scoped to the stuck job's `cluster_id` and org. Recovery behaves identically for `job_type="bulk_delete"`. Tests: (a) a RUNNING archive delete job with all records `tml_export_status="SUCCESS"` and no confirmed deletes removes zero `CachedMetadata` rows; (b) the same `ts_guid` in two clusters, recovery for a stuck job in cluster A leaves cluster B's row intact

**Cycle note (2026-08-24):** Fixed by making delete confirmation explicit rather
than inferred. `ArchiveRecord` gains `deleted_confirmed_at`, written in the same
transaction as the cache purge and only after `delete_metadata` returns for that
chunk; crash-recovery now purges only confirmed rows, scoped to the stuck job's
`cluster_id` and each record's `org_id`, and `_is_delete_job` covers
`bulk_delete` as well as `archive`+`action=delete`. The restore path and
`is_restorable` read the same field, which closes the duplicate-object half of
the bug. Existing installs get an additive `ALTER TABLE` with a one-shot
backfill stamping historical SUCCESS rows, so upgrading does not retroactively
make old archives unrestorable. 10/10 mutations killed — table in
[docs/dev/TESTING.md](docs/dev/TESTING.md).

**Closed 2026-08-24 (reconcile).** Its PR merged to `main`; the row was never flipped out of `in-review` because the merge happens outside the cycle. Re-verified against current `main` before closing.

### S23 — Interrupted metadata sync reads as fully synced

`P2` · **done** · protected: no

`_sync_metadata` is delete-all-then-repage-in-spec-order, so an interrupted metadata sync leaves a **non-empty but truncated** cache (liveboards + answers present, every model and table missing) that reads as fully synced

**Acceptance criteria:** An interrupted metadata sync is distinguishable from a complete one — either the delete+repopulate happens in one transaction, or a completeness marker (e.g. the `sync_log` SUCCESS row) is required before any consumer treats `CachedMetadata` as authoritative; a test simulates a mid-pagination failure and asserts the cache is not reported as synced

**Closed 2026-08-24 (reconcile).** Its PR merged to `main`; the row was never flipped out of `in-review` because the merge happens outside the cycle. Re-verified against current `main` before closing.

### S33 — Topbar sync label/color has no test

`P3` · **done** · protected: no

The Topbar sync label/color has no test: S23 fixed a fail-open where `IN_PROGRESS` fell through to the "Synced Xm ago" branch and rendered green, and the fix (`"Syncing…"` + accent) is verified by reading only. `tsc` and `next build` cannot catch a wrong string or token, and vitest is not a CI gate, so a future edit could silently restore the fail-open with every gate green

**Acceptance criteria:** `frontend/components/Shell/Topbar.tsx`'s `buildSyncLabel`/`buildSyncColor` are covered for every `SyncStatus` value including `IN_PROGRESS`, so a status with no branch fails a test rather than rendering as healthy

**Closed 2026-08-24 (reconcile).** Its PR merged to `main`; the row was never flipped out of `in-review` because the merge happens outside the cycle. Re-verified against current `main` before closing.

### M6 — No REJECT route when acceptance criteria are themselves the bug

`P2` · **done** · protected: no

The org model has no defined route for "the backlog row's **acceptance criteria** are themselves the bug". S6's criteria mandated deleting an edge whose source liveboard is unchanged — by definition a row the run cannot rebuild — so no safe implementation existed, but a cycle may not edit criteria and the only available move was to improvise a records-only PR and stop

**Acceptance criteria:** The improve-cycle skill defines an explicit **REJECT** outcome: when research or review shows a row's acceptance criteria cannot be satisfied safely, the cycle stops before shipping, leaves the row `open`, files the evidence + a proposed re-scope as a new row, and reports to the human — with the rejected implementation pushed (not PR'd) for inspection. The path is documented so it doesn't have to be reinvented per cycle

**Closed 2026-08-24 (reconcile).** Its PR merged to `main`; the row was never flipped out of `in-review` because the merge happens outside the cycle. Re-verified against current `main` before closing.

### M9 — Criteria-prescribed mechanisms are never soundness-checked before build

`P2` · **done** · protected: no

A cycle may not edit acceptance criteria, but nothing requires it to check whether the criteria's **prescribed mechanism** is sound before building. S7's criteria name a specific implementation ("keyed off a persisted 'liveboard tier last built' marker"), the CEO designed to it, and three review lenses then proved that mechanism removes a recovery path the criteria never mentioned. The rejected S6 diff failed the same way one cycle earlier

**Acceptance criteria:** The improve-cycle skill requires the research step to explicitly answer "what does the current code do that the criteria's prescribed mechanism would remove?" and to report any load-bearing behaviour that no test names, BEFORE design; a criteria-mandated mechanism that fails that check is escalated under the M6 REJECT path instead of built

**Closed 2026-08-24 (reconcile).** Its PR merged to `main`; the row was never flipped out of `in-review` because the merge happens outside the cycle. Re-verified against current `main` before closing.

### F12 — Browser serves stale UI after a pip upgrade until hard refresh

`P2` · Bug · **done** · protected: yes (`ts_admin/main.py`)

**Ask:** Reported 2026-08-24: after upgrading 0.3 → 0.4 the app kept running the
old cached UI; a hard refresh was required to see the new version.

**Triage + acceptance criteria:** CONFIRMED. `main.py` mounts the built frontend
with plain Starlette `StaticFiles`, which sends `ETag`/`Last-Modified` but no
`Cache-Control`, so browsers heuristically reuse cached copies (including
`index.html`) without revalidating — every upgrade shows the old UI. Criteria:
HTML and all non-hashed files are served `Cache-Control: no-cache` (revalidate
every load; conditional requests still 304 and the 304 carries the header);
content-hashed `/_next/static/*` assets are `public, max-age=31536000,
immutable`; an integration test pins both policies and the 304 case.

**Closed 2026-08-24 (reconcile).** Shipped in PR #35 (`c2afdbe`). Verified on current `main`: [ts_admin/main.py:46-48](ts_admin/main.py#L46-L48) sets `public, max-age=31536000, immutable` for hashed `/_next/static/*` and `no-cache` elsewhere, and `tests/integration/test_static_cache_headers.py` pins both policies plus the 304 case (3 passed).

### S31 — preview_delete trusts a possibly-truncated cache ('0 owned objects')

`P2` · **done** · protected: no

`preview_delete`/`dryrun_delete` report `owned_object_count` from a raw `count()` over `CachedMetadata` (`user_management_service.py:713`) with no completeness check, so a truncated metadata cache makes the delete-user safety warning read **"0 owned objects"** for a user who owns 40 worksheets — the admin deletes them and orphans the content. This is the same absence-as-evidence shape S23 guarded at `resolve_downstream`, on the one destructive path S23 deliberately left out of scope

**Acceptance criteria:** `preview_delete`'s owned-object count either refuses (as the five S23 sites do) or is presented as unreliable when the metadata cache is not certified complete; a test seeds a truncated cache plus a user owning only non-cached types and asserts the count is not silently reported as 0

**2026-09-08 (cycle).** Implemented as a **flag**, not a refusal — the criteria
permitted either. Refusing at `dryrun_delete` would have destroyed `missing_live`
(live `search_users`), `admin_count` (`UserGroupMembership`) and `unrecognized`
(`CachedUser`) — three safety signals with no metadata-cache dependency — and
permanently blocked offboarding on clusters whose metadata sync is PARTIAL
forever (W3/W6) and on fresh installs. `preview_delete` now returns
`metadata_cache_authoritative`, read before AND after the count loop in one
session; the delete modal warns and gates Confirm behind an acknowledgement when
it is false. Review Board raised 7 CONFIRMED findings on the first
implementation, all fixed. PR pending human review.

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PR #38, commit `148e04d`). `metadata_cache_authoritative` is computed by comparing the sync marker by identity before AND after the count loop in one session (`user_management_service.py:1276`/`:1310`/`:1314`), carried through the dry-run job result (`:1375`) and the API model (`api/users.py:184`); the delete modal treats it tri-state and gates Confirm behind an acknowledgement (`DeleteUsersModal.tsx:139`, `:347-351`). The acceptance test is `tests/unit/test_stale_cache_guard.py:444` `test_the_acceptance_case_a_zero_count_is_never_bare`, proven non-vacuous (the same user reads non-zero certified first), with three further kill tests at `:492`, `:521`, `:552`. Deviation from the criteria's wording: the fixture truncates rows for a user known to own one rather than using a user owning only non-cached types — the same absence-as-evidence shape, and the stronger fixture.


### F1 — Connections list page with per-connection object counts

`P3` · Feature · **done** · protected: yes (`tests/integration/test_cluster_isolation.py`)

**Ask:** **Connections list page** — list data connections (Snowflake, Databricks, …) with details and an object count per connection, so empty connections are findable

**Triage + acceptance criteria:** NOT DONE. Today connections appear only as name-only lineage nodes (built from TML `CONNECTS` edges, `lineage_service.py:1339`), so a connection never referenced by TML is invisible. `ts_client.list_connections()` (`client.py:1187`) exists but returns only `{id, name}` and is used solely for lineage GUID resolution. Criteria: a Connections view lists every connection from `connection/search` (synced into a cluster+org-scoped cache table) with type/details and a per-connection object count; zero-object connections are visible; the list endpoint is registered in `READ_ENDPOINTS`

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PR #44). All four clauses hold: `search_connections()` → `POST /api/rest/2.0/connection/search` (`ts_client/client.py:1271`, `:1285`), synced by `_sync_connections` (`sync_service.py:615`, registered `:907`) into `CachedConnection` (`models/cache/ts_connection.py:6`, table `ts_connections`) with `cluster_id` FK `:27` and `org_id` `:28`; type/details plus a per-connection object count computed at read time by GROUP BY over `CachedMetadata.connection_guid` (`connection_service.py:80-93`, `:109-120`); zero-object connections are rows, not omissions (`:113-118` defaults to `{}`), with `linked_rows`/`metadata_rows` at `:136-137` preventing an unsynced metadata cache from reporting every connection as unused; endpoint registered in `READ_ENDPOINTS` as `connections-list` (`tests/integration/test_cluster_isolation.py:54-58`).


### F3 — 'Created by' on the users list; 'Created' date on the groups list

`P4` · Feature · **done** · protected: no

**Ask:** **"Created by" on the users list; "Created" date on the groups list**

**Triage + acceptance criteria:** PARTIAL — each grid has the inverse of what's asked. Groups already show "Created by" (`groups.tsx:121`) and `created_at` is already synced, serialized, sortable, and typed — the Created column is one ColDef copy of the Modified entry. Users show "Created" but have no creator: `TSUser`/`CachedUser` carry no author field, so first verify live whether `users/search` returns one at all; if yes, mirror the group author chain (model field → sync → self-join name resolution in `group_service.py:87`)

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PRs #39 and #46) — both halves. Users 'Created by': `CachedUser.author_guid` (`models/cache/ts_user.py:32`) with a re-sync backfill registered at `database.py:60`, written from `user.author_id` during sync (`sync_service.py:159`, `:173`), resolved by self-join mirroring the group chain (`user_management_service.py:278`, `:286`, GUID fallback `:121-131`), surfaced at `api/users.py:62`, in the CSV at `:281`, and in the grid at `frontend/pages/users.tsx:157`. Groups 'Created' date: `frontend/pages/groups.tsx:126`.


### F8 — CSV export for Users and Groups

`P4` · Feature · **done** · protected: no

**Ask:** **CSV export for Users and Groups**

**Triage + acceptance criteria:** NOT DONE, trivial. The existing pattern is frontend-only `gridApi.exportDataAsCsv()` (metadata.tsx:127, archiver, sharing, deleter history); users/groups pages already hold the `gridRef`. Criteria: both pages get the same Export CSV button. Known shared limitation (all four existing sites): infinite row model exports only cached blocks, not the full server-side set — matching existing behavior is in scope, a full-export endpoint is not

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PR #39), and shipped a stronger mechanism than the criteria asked for. Rather than the frontend-only `gridApi.exportDataAsCsv()` pattern — which the criteria named while flagging that it exports only cached infinite-model blocks — both pages call a streaming backend endpoint: `ts_admin/api/users.py:288` and `ts_admin/api/groups.py:123` over shared helpers in `ts_admin/api/csv_export.py:29`, `:66`, fronted by `frontend/components/ExportCsvButton.tsx:16` (`exportCsvHref` `:43`) from `users.tsx:243` and `groups.tsx:159`. The known partial-export limitation the criteria accepted as in-scope therefore does not apply. Covered by `tests/integration/test_users_api.py:320-454` and `tests/integration/test_groups_api.py:156-227`. Residue filed as a new row (M15): neither `export.csv` route is in `READ_ENDPOINTS`.


### F9 — Export tagged content to TML without deleting

`P3` · Feature · **done** · protected: no

**Ask:** **Export tagged content to TML without deleting** (cs_tools parity — export first, delete later once trusted)

**Triage + acceptance criteria:** NOT DONE. TML export exists only inside the delete pipeline (`deletion_service._execute_delete` → `_export_tml_resilient`); the archiver's `action` literal is tag/untag/delete only, and `download_tml` requires an already-deleted `ArchiveRecord`. Criteria: an export-only action reuses `_export_tml_resilient` without deleting, downloadable as a bundle; export-only `ArchiveRecord`s are distinguishable so restore does not offer to re-import objects that were never deleted; non-destructive, but registered wherever the safety tests require

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PR #41). Action literal extended to `export` (`api/archiver.py:82`, re-validated `:415`); `_export_tml_resilient` reused unchanged via the new `export_only` path (`deletion_service.py:428`, param `:270`), returning before Phase B at `:517-524` through `_finish_export_only` (`:179`) which writes the AuditLog and job status; dispatch at `archiver_service.py:538`, `:546-547`; bundle download `GET /archiver/export/{job_id}/download` (`api/archiver.py:603`, cluster-scoped `:627-633`). Export-only records are discriminated by reusing the existing S36 field rather than adding a new one — `tml_export_status == "SUCCESS"` with `deleted_confirmed_at IS NULL` (`models/archive_record.py:55`, rationale `deletion_service.py:511-516`) — and restore genuinely filters on it (`archiver_service.py:867-876`, `is_restorable` `:1272`, crash recovery `main.py:123`, `:148`). No `DRYRUN_ENDPOINTS` row is required: export is non-destructive and runs through the already-registered `/archiver/dryrun` → `/archiver/execute` pair. Covered by `tests/integration/test_archiver_export_api.py`.


### F10 — Transfer ownership of selected objects from the Metadata screen

`P3` · Feature · **done** · protected: yes (`tests/integration/test_dryrun_safety.py`)

**Ask:** **Transfer ownership of selected objects from the Metadata screen** (today transfer is all-objects-of-one-user, from the Users screen)

**Triage + acceptance criteria:** NOT DONE, but the client call is object-scoped already: `assign_metadata_owner` (`client.py:943`) takes `object_ids`, and `execute_transfer` chunks it — only the preview/record path is user-shaped. Criteria: metadata grid gains multi-select + a Transfer action (reusing `UserPicker`/the transfer modal); new `POST /metadata/transfer-owner` follows the full write pattern — verify live, dry-run first, audit log after — and is registered in `DRYRUN_ENDPOINTS`

**2026-09-08 (reconcile).** Verified MET on `main` @ `fb0cc0f` (PR #42) — all four sub-criteria. Shipped as `POST /metadata/transfer/{preview,dryrun,execute}` rather than the criteria's `POST /metadata/transfer-owner`: a deliberate mirror of `/users/transfer/*` that reuses `user_management_service` instead of standing up a parallel path. Grid multi-select and Transfer action reuse the existing modal (`frontend/pages/metadata.tsx:313`, `:317`, toolbar `:259-284`, `TransferOwnershipModal` `:340-343`). Verify live: `user_management_service.py:501` `verify_metadata_exists`, with `missing`/`uncached` diffs `:503-505`. Dry-run first: `api/metadata.py:277` → `dryrun_transfer` (`user_management_service.py:442`). Audit log after: `:751-770`, plus one `UserActionRecord` per distinct source owner (`:640`, grouping `:596-618`). Registered in `DRYRUN_ENDPOINTS` as `metadata-transfer-dryrun` (`tests/integration/test_dryrun_safety.py:64-74`). Covered by `tests/integration/test_metadata_api.py:367-456`.
