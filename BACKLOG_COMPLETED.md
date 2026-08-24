# BACKLOG — COMPLETED

Closed items moved out of [BACKLOG.md](BACKLOG.md) to keep it readable. Entries
are kept in full — several carry load-bearing caveats (see the Cycle notes in
BACKLOG.md; S6 in particular is "decided and closed", not "implemented as
written"). Same rules as the backlog: never edit an item's criteria, never
delete an entry. When a backlog item reaches `done`, move its index line and
detail entry here.

## Index

### Done items (15)

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
