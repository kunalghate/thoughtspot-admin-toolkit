# BACKLOG

The org's work queue. Read it at the start of every cycle; change it only at a
cycle's **Records** step.

**Bright line — what a cycle may do to this file:** change a row's **Status**,
append **notes**, and append **new rows**. A cycle may NEVER edit an item's
**Priority** or **Acceptance criteria**, and NEVER delete a row. Priority and
criteria are the human's lever — they are set and re-scoped by a person, not by an
agent. (See [CLAUDE.md](CLAUDE.md) → "How the org works".)

## ID taxonomy (mirrors the three standing goals)

| Prefix | Meaning |
|---|---|
| **S** | Stability / features — improve the app |
| **R** | Refactors — improve the app without changing behavior |
| **W** | Keep-current — ThoughtSpot REST v2 drift, dependency drift |
| **M** | Org / process — improve the agents, skills, and gates themselves |

**Priority:** P1 (urgent) → P4 (nice-to-have). **Status:** `open` · `in-progress`
· `in-review` · `done`. **Protected:** does the item likely touch a
[protected path](CLAUDE.md)? (If yes, its PR needs the `human-approved` label.)

## Rows

| ID | P | Item | Acceptance criteria | Status | Protected |
|----|---|------|---------------------|--------|-----------|
| S1 | P3 | Wire frontend `vitest` into the suite | `cd frontend && npm test` runs at least one real component test and passes; CI `frontend` job runs it; [docs/dev/TESTING.md](docs/dev/TESTING.md) updated to drop "not yet wired" | in-review | no |
| S2 | P3 | Add a `/health` smoke check to CI | CI (via TestClient or a booted app) asserts `GET /health` returns 200; check runs in the pipeline and is documented in TESTING.md | open | yes (`.github/workflows/*`) |
| W1 | P3 | Add `mypy ts_admin/` to CI | `mypy ts_admin/` runs in the CI `backend` job and is green (baseline existing errors if needed, with a tracking note) | open | yes (`.github/workflows/*`) |
| M1 | P4 | Single-source the protected-path list | The protected-path patterns live in ONE place; the `guard` job and CLAUDE.md reference/derive from it (or a check fails on drift), so the two can't silently diverge | open | yes (`.github/workflows/*`, `CLAUDE.md`) |
| S3 | P2 | Group Management (read-only v1): group grid + detail drawer, user audit drawer, and the ThoughtSpot `fetch-permissions` API-drift fix | `GET /api/v1/groups` and `/groups/{guid}` serve the Groups page from cache (cluster + org scoped); `/users/{guid}/access` returns live EFFECTIVE permissions; `principal_permissions` hits `security/principals/fetch-permissions` with the correct body key and parses the real nested response; full verification bar green | done | no |
| S4 | P2 | Group detail drawer disagreed with the grid: `get_group_detail` was not org-scoped | `get_group_detail` scopes the group row, member count, and member list to a single org; a regression test seeds one GUID in two orgs and asserts the drawer count equals the grid count for each | done | no |
| S5 | P3 | Nested group membership is missing: `_sync_groups` parses `sub_groups` but never persists them | Users who belong to a group only via a sub-group appear in that group's `member_count` and member list (or the UI states explicitly that counts are direct members only); a unit test covers a group whose sole member arrives through a sub-group | open | no |
| M2 | P2 | The PR #14 merge silently reverted PR #12's vitest wiring, leaving `npm test` red on `main` with no gate to catch it | `frontend/vitest.config.mts`, `vitest.setup.ts`, the Legend test, and the testing-library/jsdom devDeps are restored and `npm test` is green; a CI gate (or the S1 CI wiring) runs `npm test` so a future merge cannot silently delete the suite again | in-review | yes (`.github/workflows/*` for the CI half) |
| S6 | P2 | Lineage edges whose **target** is deleted outlive it — ghost nodes in the graph | A model deleted in TS while its consuming liveboard is unchanged leaves no `CachedDependency`/`CachedColumnUsage` row behind; a unit test deletes a target from `CachedMetadata`, rebuilds, and asserts the edge is gone. (Phase 1 owns `USES`/non-LIVEBOARD, `_persist_column_map` owns `CONNECTS` + LIVEBOARD `USES`; neither purges by target GUID, and `build_column_map`'s `if not table_guids and not lb_guids: return 0` early-return skips the purge entirely) | done | no |
| S7 | P2 | `has_lb_edges` self-heal makes `incremental=True` a permanent full TML crawl on any org that legitimately yields zero liveboard edges | The self-heal fires at most once per org (keyed off a persisted "liveboard tier last built" marker, not "are there any rows"), so an org whose liveboards are all TML-inaccessible (403 stubs) does not re-export every liveboard on every dependencies sync forever; a unit test covers two consecutive incremental builds that produce zero liveboard edges and asserts the second exports nothing | open | no |
| S8 | P3 | `principal_permissions` / `fetch_permissions` have no wire-shape coverage and fail silently to `[]`, which `execute_transfer_sharing` records as SUCCESS | A test feeds each parser a recorded v2 response payload and asserts the extracted rows; `execute_transfer_sharing` does not write a SUCCESS audit-log row when the fetch returns zero rows for a user whose preview reported N | open | no |
| S9 | P3 | `_is_admin` joins on `cluster_id` only, so membership synced for one org blocks transfers executed in another | `_is_admin` (and the `admin_count` snapshot) scope membership to the org the operation runs in; a unit test seeds an admin membership in org 0 and asserts a transfer in org 5 is not blocked by it | open | no |
| M3 | P3 | New cluster-scoped **detail** read endpoints can't be registered in `READ_ENDPOINTS` (its extractor assumes `body["items"]`), so `/groups/{guid}` and `/users/{guid}/access` are unguarded | `READ_ENDPOINTS` (or a sibling registry) accepts detail-shaped responses and the two endpoints above are registered, OR CLAUDE.md documents the live-passthrough exemption explicitly so the rule isn't stated unconditionally while having a silent carve-out | open | yes (`tests/integration/test_cluster_isolation.py`) |
| W2 | P2 | `ruff format` drift breaks the format gate: unbounded `ruff>=0.4.0` pin lets a newer local/CI ruff reformat 5 files inherited from PR #10 | `ruff format --check ts_admin/ tests/` is green both locally and in CI. Resolve by (a) `ruff format`-ing the 5 drifted files (`ts_admin/services/lineage_service.py`, `tests/unit/test_lineage_columns.py`, `tests/unit/test_lineage_models.py`, `tests/unit/test_lineage_service.py`, `tests/integration/test_relationships_api.py`) AND (b) bounding the ruff version in `pyproject.toml` `[dev]` (e.g. a `~=` pin) so formatter output is reproducible across runs. Confirm whether CI's resolved ruff actually reddens `main` before/after. | done | no |
| S10 | P1 | The `Stale (90d unused)` tile counted objects the Archiver cannot touch: `MetadataService.stats` treated a null `last_accessed_at` as disuse across ALL object types, so tables/worksheets/SQL views (which carry no access telemetry) dominated it — 2,546 of 2,650 on a real cluster — while the tile linked to an Archiver scoped to `LIVEBOARD`/`ANSWER` | `stats` scopes staleness to `ARCHIVABLE_TYPES` and excludes System User content (mirroring the Archiver's own conditions); "aged out" (`stale_90d`) and "no access data" (`never_accessed`) are separate counts; unit tests cover both the type scoping and the System User exclusion | done | no |
| S11 | P1 | A never-synced entity rendered as a confident `0` (the Tags tile read "0" while Data freshness said "never synced" two cards down) | The dashboard payload carries a per-entity `synced` flag; the UI renders `—` plus a sync action instead of a number for any entity that has never synced successfully; an integration test asserts the flag is false before the first sync and true after | done | no |
| S12 | P1 | Failed jobs were shown without a reason: `Job.error`/`error_type`/`error_traceback` are persisted and `DashboardJob.error` was carried through the API, but the page rendered only a red `FAILED` pill, so diagnosing meant leaving the dashboard | Failed rows in Recent jobs show `error_type: error` inline (full text on hover) and a Retry action for sync jobs that re-triggers `POST /sync/{entity}`; an integration test asserts the failure reason reaches the client | done | no |
| S13 | P2 | `failed_jobs_7d` was computed by filtering the newest 200 jobs in Python, so on a busy cluster genuine failures fell out of the window and the tile under-reported | `failed_jobs_7d` is a SQL `COUNT` over the 7-day window with no row cap; a regression test seeds 250 newer jobs behind a failure and asserts it is still counted | done | no |
| S14 | P2 | `MetadataService.stats` loaded every `CachedMetadata` row into Python to count them — fine at 2,650 objects, linear memory at 100k | Inventory counts come from a SQL `GROUP BY`; staleness counts are `COUNT` queries; no full-table materialization remains in `stats` | done | no |
| S15 | P2 | The dashboard showed no signal an admin could act on: inactive users, empty groups, ungrouped users, and content owned by deleted users were all already in SQLite and never surfaced | The payload exposes an `attention` block (`inactive_users`, `empty_groups`, `users_without_group`, `orphaned_content`) as cheap aggregate queries; the page leads with a "Needs attention" band that shows an explicit all-clear when every count is zero; each signal returns 0 until BOTH entities it joins have synced, so an un-synced cluster raises no false alarm; integration tests cover the counts and the un-synced guard | done | no |
| S16 | P2 | "Recent admin activity" showed four identical 4-month-old rows: grouping was per `job_id` only and the feed had no age bound, so history read as current activity | Activity is bounded to `ACTIVITY_MAX_AGE_DAYS` and identical adjacent entries collapse into one row with a `×N` count; tests cover both the age cutoff and the collapse | done | no |
| S17 | P3 | Every dashboard number was a point-in-time count with no trend, and the page never polled — a running sync was invisible and relative timestamps froze at mount | `SyncLog.record_count` deltas between the two most recent successful syncs are shown next to each tile; `running_jobs` (with progress/total) is exposed and rendered as a progress bar; the page polls fast while work is in flight and slowly otherwise | done | no |
| S18 | P3 | Dashboard UX: blank page on first paint (`if (!data) return null`), a silently swallowed sync-status failure that rendered as "never synced", inventory and problems given identical visual weight, and ~40% of the viewport unused on a wide screen | Loading renders a skeleton; a failed read surfaces instead of being swallowed; problems and inventory are visually separated; the layout uses the available width. (Tags KPI and the Data freshness card were removed at the human's direction during this cycle) | done | no |
| S19 | P3 | Signals still unused in the cache: `view_count` (never-viewed content), `created_at` growth over time, `ContentPermission` (over-shared content), and `CachedDependency` dead-end models — the last being provable staleness rather than the access-date guesswork of S10 | At least the dependency-based signal ships: models/worksheets with no downstream consumer are counted and surfaced as a safe-to-review cleanup target, sourced from `CachedDependency` and scoped by cluster + org | open | no |
| S20 | P3 | Multi-cluster is a v1 pillar but the dashboard is single-cluster: no roll-up and no signal that another configured cluster has failing syncs | The dashboard indicates cross-cluster health (at minimum: other clusters with failed jobs in the last 7 days) without requiring a cluster switch; counts stay cluster-scoped | open | no |
| S21 | P3 | `_recent_activity` scans a fixed 300 raw rows per audit source, so one bulk share of 500 objects fills the window and hides every other activity type | The feed's per-source window cannot let a single large session crowd out other kinds of activity (e.g. group in SQL, or scan per-kind); a test seeds one oversized share session plus a deletion and asserts both appear | open | no |
| S22 | P2 | Ghost lineage nodes render as *normal* nodes: the `accessible` flag is plumbed end-to-end but nothing ever sets it `False`. **Proposed as the safe replacement for S6's deletion approach** — `ts_dependency.py:6-9` documents "inaccessible stubs" as a supported edge endpoint, so the ghost is a read-path gap, not a cache-integrity problem | `get_lineage_graph` sets `accessible=False` on any node whose `guid` has no `CachedMetadata` row for the same `(cluster_id, org_id)`, so a deleted-in-TS target renders dashed/0.6-opacity with the existing "Inaccessible" Legend key instead of as a normal node; no rows are deleted; a unit test deletes a target from `CachedMetadata`, reads the graph, and asserts the node comes back `accessible=False`. **Depends on S25** — the existence lookup this needs is exactly the one that goes quadratic without a composite index; do S25 first or resolve the node set with a single set-membership query rather than a per-node lookup | done | no |
| S23 | P2 | `_sync_metadata` is delete-all-then-repage-in-spec-order, so an interrupted metadata sync leaves a **non-empty but truncated** cache (liveboards + answers present, every model and table missing) that reads as fully synced | An interrupted metadata sync is distinguishable from a complete one — either the delete+repopulate happens in one transaction, or a completeness marker (e.g. the `sync_log` SUCCESS row) is required before any consumer treats `CachedMetadata` as authoritative; a test simulates a mid-pagination failure and asserts the cache is not reported as synced | in-review | no |
| S24 | P3 | `POST /sync/{entity}` has no concurrency guard, so a metadata sync and a dependencies build interleave on the same event loop while the metadata cache is mid-repopulation (`api/sync.py:85-138` creates jobs unconditionally) | Triggering a sync for an entity that already has a running job is rejected (or queued) rather than starting a second concurrent run; a test asserts the second trigger does not start while the first is RUNNING | open | no |
| S25 | P2 | `CachedMetadata` has only single-column indexes (`cluster_id`, `org_id`, `ts_guid`), so any correlated subquery or join keyed on `(cluster_id, org_id, ts_guid)` picks `ix_ts_metadata_cluster_id` — the least selective — and goes quadratic. The app never runs `ANALYZE` and sets no pragmas (`database.py:30-34`), so SQLite's no-stats heuristic has nothing better to choose | A `(cluster_id, org_id, ts_guid)` composite index exists on `ts_metadata` and `EXPLAIN QUERY PLAN` for a correlated lookup shows `SEARCH ts_metadata USING COVERING INDEX ... (cluster_id=? AND org_id=? AND ts_guid=?)`. **`create_all` does NOT add an index to an already-existing table**, so it ships as an explicit `CREATE INDEX IF NOT EXISTS` in `init_db()` (or a sentinel bump) and is verified against a DB created before the change | done | no |
| S26 | P2 | Sync background tasks block the FastAPI event loop: `api/sync.py:113` `background_tasks.add_task(run_sync, ...)` with an `async` target means Starlette awaits it inline, and `_persist_column_map` is synchronous — so slow DB work in a sync freezes ALL HTTP handling, including the job-status polling the UI uses to show sync progress, and `/health`. Compounded by the engine setting no `journal_mode` (no WAL), so a long write transaction holds the DB write lock | Long-running/blocking sync work does not occupy the event loop (e.g. the blocking section runs via `to_thread`/`run_in_executor`, or the job runs off the request path entirely), and job-status polling stays responsive while a large sync runs; WAL is enabled or the decision not to is recorded with a reason | open | no |
| M4 | P3 | A "spares X" guard test placed on a code path that full-rebuilds X is vacuous — `test_orphan_purge_spares_connects_edges` passed with its entire `relation == "USES"` restriction deleted, because `_persist_column_map` delete-alls and re-inserts CONNECTS after the purge in the same run | The org's review checklist (or the `reviewer` agent brief) requires every "spares/preserves X" guard test to be falsified by deleting the predicate it guards, and the test must be placed on a path that does not rebuild X; the lesson is recorded in `docs/dev/TESTING.md` | open | no |
| M5 | P2 | The verification bar proves *conformance to the criteria*, not that the change is safe — in the S6 cycle it went fully green (ruff, 181 unit + 129 integration, tsc, build, vitest) on a change that three review lenses then proved causes permanent data loss. Nothing in the bar can catch "this shouldn't be built at all", and the unit suite is also structurally blind to query-plan regressions (small in-memory fixtures pass in ms regardless of an O(n²) plan) | CLAUDE.md's verification bar states explicitly that a green bar is necessary but NOT sufficient and never authorises shipping on its own; the Review Board stays mandatory for any change that deletes rows, alters a purge/retention rule, or adds a correlated subquery or join, **even when every gate is green**; the same section requires `EXPLAIN QUERY PLAN` on a realistically-sized DB for new correlated subqueries/joins | open | yes (`CLAUDE.md`) |
| M6 | P2 | The org model has no defined route for "the backlog row's **acceptance criteria** are themselves the bug". S6's criteria mandated deleting an edge whose source liveboard is unchanged — by definition a row the run cannot rebuild — so no safe implementation existed, but a cycle may not edit criteria and the only available move was to improvise a records-only PR and stop | The improve-cycle skill defines an explicit **REJECT** outcome: when research or review shows a row's acceptance criteria cannot be satisfied safely, the cycle stops before shipping, leaves the row `open`, files the evidence + a proposed re-scope as a new row, and reports to the human — with the rejected implementation pushed (not PR'd) for inspection. The path is documented so it doesn't have to be reinvented per cycle | open | no |
| S27 | P1 | **Blocks any re-attempt of S7.** The lineage unit suite is mutation-vacuous: on the rejected S7 diff, 6 of 7 mutations to `build_column_map` left all 16 tests in `tests/unit/test_lineage_columns.py` green — including one making the builder never re-crawl any liveboard ever again. No test in the file seeds a **future** `lb_modified`, so "a genuinely changed liveboard is re-exported" has never been asserted; marker/watermark org-scoping and the write-ordering invariant were likewise unguarded | Every behavioural predicate in `build_column_map`'s incremental path is killed by at least one test: deleting `_changed`'s `modified_at > last_built` comparison, dropping `org_id` from any watermark read/write, and reordering the post-persist write each turn at least one test red. The mutation list and its results are recorded in [docs/dev/TESTING.md](docs/dev/TESTING.md) so the next lineage change starts from a suite that can detect its own failure modes | in-review | no |
| S28 | P2 | The liveboard incremental watermark is a bare timestamp, which cannot express "not yet crawled": a liveboard that has existed in TS since 2020 but enters `CachedMetadata` only after the first build (late/interrupted metadata sync, or newly shared with the admin — a permission grant does not bump `modified_at`) has `modified_at ≤ watermark` and is never crawled, so its USES edges are never built. **Verified present on `main` today** — not introduced by the rejected S7 diff, but S7's persisted marker made it permanent where the accidental `NULL`-watermark reset used to mask it | A liveboard whose lineage has never been built is crawled regardless of its `modified_at` — e.g. the changed-set is computed from a persisted crawled-GUID set (or `max(modified_at, metadata_first_seen)`) rather than a bare timestamp comparison; a unit test seeds a liveboard with a 2020 `modified_at` that arrives in `CachedMetadata` after the first build and asserts the second build exports it | open | no |
| S29 | P2 | `_persist_column_map` commits its DELETEs before inserting (`session.commit()` between the delete block and the insert loop), so a crash in that window — process kill, `serve` restart, sqlite lock timeout, or an S24 interleave — leaves a durably-empty scope. Today this self-heals only by accident, via `max(CachedColumnLineage.synced_at)` reading NULL; the rejected S7 diff removed that accident and made the loss permanent | The delete+repopulate in `_persist_column_map` is atomic (single transaction), or a crash in the window is detectable so the next build rebuilds the scope rather than trusting it; a test simulates a raise between the delete commit and the insert loop and asserts the following build restores the edges | open | no |
| M8 | P2 | Gate serialization is violated in practice by **file writes**, not just ports: during the S7 review, reviewer/QA agents wrote five scratch repro files into `tests/unit/` of the **shared** checkout, flipping `pytest tests/unit/` from green to red mid-verification and making QA's gate result untrustworthy. CLAUDE.md serializes port-bound gates but says nothing about agents writing into the working tree they share | The agent briefs (and CLAUDE.md's gate-serialization note) require review/QA repro artifacts to live in a `git worktree` or the scratchpad, never in `tests/` of the shared checkout; QA reports the working tree state it observed so a polluted run is visible rather than silent | open | yes (`CLAUDE.md`) |
| M9 | P2 | A cycle may not edit acceptance criteria, but nothing requires it to check whether the criteria's **prescribed mechanism** is sound before building. S7's criteria name a specific implementation ("keyed off a persisted 'liveboard tier last built' marker"), the CEO designed to it, and three review lenses then proved that mechanism removes a recovery path the criteria never mentioned. The rejected S6 diff failed the same way one cycle earlier | The improve-cycle skill requires the research step to explicitly answer "what does the current code do that the criteria's prescribed mechanism would remove?" and to report any load-bearing behaviour that no test names, BEFORE design; a criteria-mandated mechanism that fails that check is escalated under the M6 REJECT path instead of built | open | no |
| S30 | P2 | `build_answer_index`'s `_changed` twin (`lineage_service.py:897`) is byte-identical to `build_column_map`'s but has **zero** mutation coverage: dropping its `last_built is None` disjunct leaves the entire 37-test lineage suite green, while the same mutation at `:559` is now killed by S27. Answer-index incrementality is unpinned, and `build_answer_index` carries the same watermark shape S7 was rejected over (`max(CachedColumnUsage.synced_at)` over *surviving* rows, so partial deletion is permanent) | The ANSWER tier's incremental predicates are each killed by at least one test — at minimum, dropping `last_built is None` or the `modified_at > last_built` comparison at `lineage_service.py:897` turns a test red; the mutations and results are appended to the table in [docs/dev/TESTING.md](docs/dev/TESTING.md) | open | no |
| M7 | P3 | `docs/org-memory/` was effectively append-only, so a fact that a later PR had already fixed kept steering agents wrong: the "KNOWN RED `ruff format --check`" bullet was resolved by W2 in PR #11 but still misled two agents in the S6 cycle (one wrote a "record the pre-existing failure" instruction into its plan) | The Records step requires **pruning** — every cycle re-verifies the org-memory facts its work touched and DELETES or amends the ones that no longer hold, not just appends new ones; `docs/org-memory/README.md` states this and the ~120-line cap is described as enforced by pruning stale facts first | open | no |
| S31 | P2 | `preview_delete`/`dryrun_delete` report `owned_object_count` from a raw `count()` over `CachedMetadata` (`user_management_service.py:713`) with no completeness check, so a truncated metadata cache makes the delete-user safety warning read **"0 owned objects"** for a user who owns 40 worksheets — the admin deletes them and orphans the content. This is the same absence-as-evidence shape S23 guarded at `resolve_downstream`, on the one destructive path S23 deliberately left out of scope | `preview_delete`'s owned-object count either refuses (as the five S23 sites do) or is presented as unreliable when the metadata cache is not certified complete; a test seeds a truncated cache plus a user owning only non-cached types and asserts the count is not silently reported as 0 | open | no |
| S32 | P3 | `cache_authoritative` is produced end-to-end and consumed nowhere: `api/metadata.py` computes it on both metadata responses and `frontend/lib/types.ts` types it, but no component reads it (grep returns only the type declarations), so after an interrupted sync the Metadata Explorer still presents a truncated list as complete. Same produced-but-never-rendered shape as the `accessible` flag already recorded in org-memory | The metadata list/stats UI visibly marks the data as possibly-partial when `cache_authoritative` is false (banner, badge, or equivalent), so the flag's stated contract — "the UI must not present the list as complete" — is actually met; a frontend test or an explicit manual-verification note records it | open | no |
| S33 | P3 | The Topbar sync label/color has no test: S23 fixed a fail-open where `IN_PROGRESS` fell through to the "Synced Xm ago" branch and rendered green, and the fix (`"Syncing…"` + accent) is verified by reading only. `tsc` and `next build` cannot catch a wrong string or token, and vitest is not a CI gate, so a future edit could silently restore the fail-open with every gate green | `frontend/components/Shell/Topbar.tsx`'s `buildSyncLabel`/`buildSyncColor` are covered for every `SyncStatus` value including `IN_PROGRESS`, so a status with no branch fails a test rather than rendering as healthy | in-review | no |
| S34 | P2 | Nothing prevents two dependency syncs running concurrently for the same (cluster, org). Observed live: three simultaneous TML crawls on ps-internal-prod (one script + two UI "Sync Lineage" clicks) produced sustained 30s `metadata/tml/export` timeouts and retry backoff, tripling the wall-clock of all three. Not corrupting — each pass is a full delete-and-rebuild scoped to (cluster, org), so last writer wins with a complete set — but the UI gives no signal that a build is already in flight and happily starts another | Triggering a `dependencies` sync while one is already RUNNING for the same (cluster, org) either refuses with an actionable message or returns the in-flight job instead of starting a second crawl; a test asserts the second trigger does not produce a second RUNNING job | open | no |
| M11 | P2 | A best-effort pass that fails silently reports success at every level, and no gate catches it. `_sync_dependencies` swallows the column-map failure by design (the object tier must survive), so on a cluster where the TML pass aborted every time the job read COMPLETE, the Topbar read "Synced 2m ago", and the cache held 9,247 USES edges with **zero** CONNECTS and zero column rows. The full verification bar was green throughout — unit tests exercise the pass against canned TML that never fails, so the all-or-nothing batch flaw and the Model-alias parse gap were both invisible. Partially addressed (`column_error` now rides in the job result), but nothing asserts a degraded pass is visible to the user | Either a gate or a guard test makes a silently-degraded sync detectable: a `dependencies` sync whose column pass fails does not present as a clean COMPLETE in the Jobs UI, and the org's test conventions require best-effort passes to be exercised against a failing dependency, not only a happy-path fake | open | no |
| M10 | P2 | A fail-closed guard placed inside a Starlette background-task target is fail-**silent**, and the org's review/test conventions do not catch it: S23 shipped guards in `execute_share`/`execute_transfer` that raise after the 202 is already on the wire, so the 409 never reaches the caller and the `Job` row strands at `QUEUED`/`error=None` until a restart. All 241 unit tests passed on the broken guard because service-level tests call the coroutine directly — the one thing production cannot do | The `reviewer`/`implementer` briefs (or `docs/dev/TESTING.md`) require that any refusal on a `background_tasks.add_task`-dispatched endpoint is (a) checked in the router before `create_job` and (b) covered by a TestClient test asserting the status code AND that no `Job` row was created; a service-level unit test alone is documented as insufficient for this class | open | no |
| M12 | P2 | Frontend `vitest` executes on **zero** automated entry points, so a test written to satisfy a backlog row guards nothing. `.github/workflows/ci.yml` runs only `npm ci` → `npx tsc --noEmit` → `npm run build` (no `npm test` step), and `Makefile:20` `test:` is `pytest tests/ -v` with no frontend target — while CLAUDE.md documents `make test` as "pytest + vitest + Playwright". Found during S33: its 37 new tests are enforced by CI only where they produce a *type* error. Broader than S1 ("wire vitest into CI"), which understates it by not covering `make test` or the CLAUDE.md drift | `npm test` runs in CI AND from a documented make target; CLAUDE.md's description of `make test` matches what the Makefile does (or the claim is corrected); a deliberately-broken frontend assertion is caught by a gate rather than only by a human running vitest by hand | open | yes (`.github/workflows/*`, `CLAUDE.md`) |
| S36 | P1 | Startup crash-recovery deletes `CachedMetadata` rows for objects that were never deleted, is not cluster/org-scoped, and ignores Bulk Deleter jobs. `main.py:70` matches `job_type == "archive"` only (the Deleter creates `bulk_delete`, so it gets no recovery at all), and `:73-88` infers "deleted" from `tml_export_status == "SUCCESS"` — but `_execute_delete` exports EVERY object in Phase A before Phase B deletes any, so the most likely crash window is exactly where that inference is maximally wrong: the recovery purges the cache for N objects of which zero were deleted. The admin then sees them vanish from Metadata Explorer and the Archiver while they are still live in ThoughtSpot, and their `ArchiveRecord`s report `is_restorable=True`, so "restoring" them creates duplicates. `:82`'s `sql_delete` also has no `cluster_id`/`org_id` predicate, though the stuck `Job` row carries `cluster_id` | Crash recovery does not infer deletion from TML-export status: either `_execute_delete` records per-GUID delete confirmation (an `ArchiveRecord` field set only after `delete_metadata` returns) and recovery purges only confirmed rows, or recovery purges nothing and instead marks the metadata `sync_log` non-authoritative so the next read re-syncs. The delete is scoped to the stuck job's `cluster_id` and org. Recovery behaves identically for `job_type="bulk_delete"`. Tests: (a) a RUNNING archive delete job with all records `tml_export_status="SUCCESS"` and no confirmed deletes removes zero `CachedMetadata` rows; (b) the same `ts_guid` in two clusters, recovery for a stuck job in cluster A leaves cluster B's row intact | open | yes (`ts_admin/main.py`) |
| S37 | P2 | `BearerTokenAuth` silently discards `org_id`, so every sync on a bearer-token cluster stamps one org's data with another org's id. `config.py:74-76` accepts `org_id` and throws it away for `AuthType.BEARER` (`ts_client/auth.py:170-176` has no `org_id` field at all, unlike `BasicAuth:86` and `TrustedAuth:133`) — despite `build_auth_strategy`'s own docstring stating org context is the ONLY way the TS API scopes content. Bearer is a first-class option in the Connections UI. An admin on org 5 syncing metadata gets org 0's objects written with `org_id=5`; the Archiver and Bulk Delete then build their input set from that cache, so a cleanup the admin believes is scoped to org 5 selects org 0's production content — and the dry-run agrees with itself, because preview and execute read the same mis-stamped cache. Related: `_sync_users`/`_sync_groups` pass no `org_id` while `_sync_metadata` and every lineage build do, so the handlers disagree about the contract | A `BEARER` cluster cannot silently produce org-mismatched cache rows: either `BearerTokenAuth` carries and applies an org context, or `build_auth_strategy(org_id=...)` fails loudly (surfaced as a FAILED sync with an actionable message) when the strategy cannot honour the org, or the Connections UI blocks BEARER on multi-org clusters. A unit test asserts `build_auth_strategy(org_id=5)` on a BEARER cluster does not return an object that silently ignores 5. The org-scoping contract in `config.py:62-64` and the four sync handlers agree on whether `org_id` is passed | open | yes (`ts_admin/config.py`) |
| S38 | P2 | `POST /api/v1/deleter/delete-tag-only` is a destructive, synchronous, cluster-wide write with no dry-run, in a product whose non-negotiable UX pattern is "dry-run required for all destructive operations". `api/deleter.py:156-176` calls `client.delete_tag` inline — no job, no preview of how many objects lose the label — and it is absent from `DRYRUN_ENDPOINTS`, while being wired into the UI at `frontend/lib/api.ts:485`. Compounding it: the TS-side delete removes the tag cluster-wide but the local strip is scoped to one org (`deleter_service.py:195-197`), so every other org's cached rows keep showing a tag that no longer exists | A dry-run (endpoint or preview field) reports the count of objects that would lose the tag before any write; the endpoint is registered in `DRYRUN_ENDPOINTS`; the local tag strip is applied across every org of the cluster (or the tag cache is invalidated). A test asserts a second org's cached rows no longer carry the deleted tag | open | yes (`tests/integration/test_dryrun_safety.py`) |
| S39 | P3 | The audit log has seven writers and zero readers, and no `org_id`. `AuditLog` is written by `deletion_service`, `bulk_sharing_service`, `deleter_service`, `user_management_service` (×3) and `archiver_service` (×2); no router, service or frontend file reads it — grep returns only writers and tests. "Audit log" is listed as an MVP v1 feature in CLAUDE.md, yet today an admin can only see it by opening the SQLite file. The model also carries no `org_id`, so even once a reader exists it cannot be org-scoped like `ArchiveRecord`, `ShareRecord` and `UserActionRecord` all are | A cluster+org-scoped `GET /api/v1/audit` serves the audit trail and is registered in `READ_ENDPOINTS`; `AuditLog` gains `org_id` and every writer sets it; a test asserts a destructive action in org 5 does not appear in org 0's feed | open | yes (`tests/integration/test_cluster_isolation.py`) |
| S40 | P3 | The transfer-ownership modal's type chips collapse to the current selection, making multi-type selection impossible and hiding what the user actually owns. `user_management_service.py:279-282` computes `by_type` from the ALREADY-FILTERED rows, and `TransferOwnershipModal.tsx:125-135` renders the chip row straight from that response, refetching on every chip click. A user owning 12 liveboards and 8 answers shows both chips; clicking `Liveboard` makes the `Answer 8` chip disappear, so there is no way to select both and the modal now states the user owns nothing but liveboards — if the admin proceeds, 8 answers are silently left on the departing account. Secondary: entering the `confirming` step re-fires `loadPreview`, so every transfer runs the preview query twice | The chip set is derived from an UNFILTERED preview (a separate unfiltered `by_type`, or the chips are held from the first response and not overwritten), so every type the user owns stays selectable and multi-type selection works, with each chip's count reflecting the owned total for that type. A test previews a user owning two types, selects one, and asserts both chips are still rendered | open | no |
| S41 | P3 | Grid selection is silently lost past ~2,000 rows on the infinite row model. `pages/sharing.tsx:198-201`, `pages/archiver.tsx:425-431` and `pages/users.tsx:168-170` derive the action set from `api.getSelectedRows()` with `maxBlocksInCache={10}` × `cacheBlockSize={200}`; when AG Grid evicts a block its row nodes go with it, so checkboxes ticked near the top of a 10k-object org stop being returned once the admin scrolls far enough. The "N selected" counter changes without the admin unchecking anything, and the bulk action runs on a subset. Graded PLAUSIBLE — needs confirmation against the pinned AG Grid version before fixing | Selection is held in page state keyed by GUID and survives block eviction, or the grid caps/warns when a selection can no longer be guaranteed. A test (or a recorded manual measurement against a >2,000-row org) establishes the behaviour at the pinned version first | open | no |
| M13 | P2 | Two bug-hunt passes graded the three grid pages' stale-response guards "clean" while sorting was dead in production on all three: the guard returned from AG Grid's `getRows` without calling `successCallback` OR `failCallback`, leaking the block loader's concurrency slots (cap 2), so the grid stopped issuing requests after the second superseded load. Reading only our code the guard looks correct — the bug is only visible against the LIBRARY's contract. No gate could see it either: `tsc` and `next build` pass, and there is no browser-level check of any interaction | The `bug-hunter` and `reviewer` briefs require checking the third-party contract whenever our code short-circuits inside a library-supplied callback (done in this cycle — verify it stays), AND the org gains at least one automated interaction check that would have caught it: a Playwright (or vitest + AG Grid) test that sorts a grid twice and asserts rows are still rendered, running in a gate rather than by hand | open | no |
| S42 | P2 | No sync verifies that the session is actually operating in the org whose id it stamps on every cached row. `build_auth_strategy(org_id=...)` is best-effort — `BearerTokenAuth` discards it outright (S37), and `_sync_users`/`_sync_groups` never pass it at all — yet `_sync_metadata` writes `org_id=N` onto every row regardless. When auth and stamp disagree, one org's content is cached under another org's id, and the Archiver and Bulk Deleter then build their input set from that cache: a cleanup the admin believes is scoped to org 5 selects org 0's production content, and the dry-run agrees with itself because preview and execute read the same mis-stamped cache. Verified live that the org a session is really in is observable: `GET /api/rest/2.0/auth/session/user` returns `current_org: {id, name}` for every auth type (`auth/session/token` does NOT carry an org scope, so token introspection is not the route) | Before a sync writes any row stamped `org_id=N`, it confirms the live session's `current_org.id == N` and fails the job with an actionable message otherwise — one check per sync, not per page. The guard is auth-type agnostic, so it fails closed on BEARER (S37) and on any future auth mechanism that silently ignores org. It reads `current_org`, NOT the `orgs` membership list, since the docs state a cluster admin's `orgs` contains only Primary unless explicitly added to each Org. Tests: (a) a session whose `current_org.id` is 0 while the sync runs for org 5 fails the job and writes zero `CachedMetadata` rows; (b) a matching org proceeds normally; (c) the check is issued once per sync run | open | no |
| W3 | P2 | `search_metadata` queries the `SQL_VIEW` subtype unconditionally, but the `subtypes` enum tags it **Version: 10.11.0.cl or later** — and the seven specs run in a single generator loop, so one spec's 400 raises `TSInvalidParametersError` and kills the WHOLE metadata sync, discarding the specs that already succeeded. A customer below 10.11 therefore may have no metadata cache at all, and the failure names a subtype rather than the version gate. Not visible on ps-internal-prod or se-demo, both 26.8 | The `SQL_VIEW` spec is skipped when the cluster's `release_version` is below 10.11.0 (`test_connection` already retrieves it). Independently — and this is the load-bearing half — a failure in ONE spec of `search_metadata` is recorded and the remaining specs still run: a test injects a 400 on the `SQL_VIEW` spec and asserts LIVEBOARD/ANSWER/WORKSHEET results are still yielded and the sync completes as PARTIAL rather than FAILED | open | no |
| W4 | P3 | `permission_type` on `security/metadata/fetch-permissions` is tagged **Version: 10.3.0.cl or later**, and below that release the key is silently ignored (v2 drops unknown body keys without erroring). The difference is not cosmetic: measured on ps-internal-prod, `DEFINED` returns 0 principals for a liveboard where the effective set is 138. So a customer below 10.3 sees the EFFECTIVE set in the Metadata Explorer drawer and in the bulk-sharing preview diff while the UI labels it as direct shares — the exact inverse of the bug fixed in `be64fd5`, and with no warning | The permissions path is version-aware: when the cluster's `release_version` parses below 10.3.0, the drawer and the sharing preview either label their result as effective access or the direct/effective split is disabled with an explicit message. A unit test drives both branches off a stubbed `release_version` | open | no |
| W5 | P3 | `metadata/search` is paginated with `include_stats` and `include_details`, neither of which is on the documented list of parameters that support pagination — the reference says "if you are using other parameters to search metadata, set `record_size` to `-1` and `record_offset` to `0`". Tested live and it currently holds (3x200 pages = 365 records, 365 unique, identical set to `record_size: -1`, zero duplicates), so this is a documented-contract violation rather than an observed bug — but it is precisely the shape that silently drops objects on a larger cluster or a future release, and "objects mysteriously missing from the cache" is a symptom this project has chased before | For each of the seven `search_metadata` specs, a live comparison on the largest available org asserts the paged GUID set equals the `record_size: -1` GUID set with zero duplicates, run against both live clusters. Any spec that diverges switches to `record_size: -1`. The result is recorded in `docs/org-memory/codebase.md` so the next person does not re-derive it | open | no |
| W6 | P2 | Follow-on to W3, which is a strict improvement but leaves a sharp edge. Below 10.11.0 the `SQL_VIEW` subtype value does not exist, so W3 skips that spec — and records a skipped spec exactly like a failed one, which is correct (those objects genuinely go unenumerated) but means the metadata sync on such a cluster ends **PARTIAL forever**. `require_authoritative_metadata` therefore keeps refusing, so the Archiver, Bulk Delete, Bulk Sharing and transfer previews 409 permanently. Better than the old behaviour (one spec's 400 killed the whole crawl and left no cache at all), but a customer below 10.11 still cannot use the destructive features. Note the docs place the 10.11 tag on the `subtypes` FILTER VALUE, not on SQL Views themselves — the objects exist, we just cannot ask for them by that name | Below 10.11.0 the metadata crawl falls back to a single unfiltered `LOGICAL_TABLE` pass and derives each object's effective subtype from the response, so the spec set is complete and the sync can certify SUCCESS. A test drives a stubbed `release_version` of 10.10.0, asserts no request carries `subtypes: ["SQL_VIEW"]`, asserts SQL-view objects still land in `CachedMetadata` with the right `object_type`, and asserts the sync completes SUCCESS rather than PARTIAL | open | no |
| S43 | P3 | `BasicAuth` omits `org_id` when it is `None`, and the spec states that a token minted with no `org_id` and no secret key logs the user into "the Org context of their **previous login session**" — i.e. whichever org the admin last used in the ThoughtSpot browser UI. So any token-scoped call made through a basic-auth connection with no org selected lands in a non-deterministic org. `_sync_tags` does pass `org_id` so it is protected today, but the next `build_auth_strategy()` call with no org on a token-scoped endpoint is a coin flip that presents as "the sync worked yesterday and returned different data today". `TrustedAuth` is deterministic (the secret key's org) | `BasicAuth`/`TrustedAuth` with `org_id=None` either default to `0` explicitly or log a warning naming the resolved org; after login the session's actual org is asserted via `GET /auth/session/user` -> `current_org.id` (see S42) and a mismatch fails loudly rather than silently caching another org's content. A unit test covers the `org_id=None` path | open | yes (`ts_admin/config.py`, `ts_admin/ts_client/auth.py`) |
| S44 | P2 | A bulk share cannot be verified, because `security/metadata/share` returns a bare **204 No Content** — no per-object, no per-principal status — and no alternative bulk-share endpoint reports one. So "the share succeeded" is currently an assumption, and the audit-log row recording it is a guess rather than a record. This is an API limitation, not a coding defect, but it is exactly the gap that let the 404-ing `security/share` path (fixed in `9b60fa5`) go unnoticed. The read-back already exists: `bulk_sharing_service` calls `security/metadata/fetch-permissions` per object to build the PREVIEW diff | After `execute_share`, each object is re-read with the same helper the preview uses and the result is compared to what was requested; the job result and the audit row record `verified_ok` / `verified_failed` counts, and a run where nothing changed is not reported as SUCCESS. A test whose stub returns unchanged permissions asserts `verified_failed == n` | open | no |
| S45 | P3 | `COLLECTION` is a first-class ThoughtSpot object type that this app never syncs — 6 exist on ps-internal-prod. It is in the `type` enum for `metadata/search`, `security/metadata/share` (with dual `share_mode` + `content_share_mode`), `tags/assign` and `principals/fetch-permissions`, so collections are invisible in the Metadata Explorer, unshareable via Bulk Sharing, and un-archivable. `INSIGHT_SPEC` (7 on the same cluster) and the `PRIVATE_WORKSHEET` subtype are likewise unqueried | `COLLECTION` is synced into `CachedMetadata` like any other type and appears in the Metadata Explorer with a label in `TYPE_LABELS`; sharing a collection sends `content_share_mode` as well as `share_mode`, since the two control different things and omitting the former silently defaults collection contents to `READ_ONLY`. A decision on `INSIGHT_SPEC`/`PRIVATE_WORKSHEET` is recorded either way | open | no |
| M14 | P2 | A blanket `except Exception` around a chunk loop converts ANY failure — including a `TypeError` from our own code — into a "chunk failed" that the job reports as **PARTIAL**, and `mark_partial` is checked before the `succeeded == 0` branch, so a totally-failed job never reports FAILED. This is the mechanism that hid three 404-ing endpoints for the life of the project: Bulk Sharing, transfer sharing and bulk user delete each reported PARTIAL with zero items affected, attached to a "run a sync and retry" message, rather than failing. It also swallowed a live `TypeError` during the fix itself. Sites: `bulk_sharing_service.py:690`, and the equivalents in `user_management_service` transfer-sharing and delete | A job in which zero items succeeded reports FAILED, never PARTIAL — the `succeeded == 0` branch is evaluated first at every such site, with a test per site. Separately, the org's review checklist requires that a blanket `except Exception` wrapping a batch loop is either narrowed to named exception classes or justified in a comment naming what it is allowed to swallow; CLAUDE.md already forbids bare `except Exception`, so the ~20 pre-existing sites are inventoried and tracked rather than left implicit | open | no |
| S35 | P3 | The Topbar header layout has no automated guard, and jsdom cannot provide one — it does not lay out, so a vitest render test can only assert inline style strings, not geometry. S33's review found two CONFIRMED layout regressions (offline badge painting on top of the sync indicator; page title collapsing to width 0) that `tsc`, `next build` and 39 green vitest tests were all blind to; they were caught only by an agent measuring a hand-built CSS replica in headless Chromium. A residual overlap of 3–20px also remains below 720px offline, where the header is over-subscribed even with the title column at zero width | A Playwright viewport assertion covers the header's non-overlap invariant at the widths that matter (at minimum 1024 and 900, offline and online): the offline badge's right edge stays left of the sync column's left edge, and the org selector stays within the viewport; the &lt;720px residual is either fixed with a responsive badge (icon-only + `title`) or explicitly accepted in the test as an out-of-support width | open | no |

> Seeded 2026-07-15 at bootstrap (BOOT) from Step 0 discovery. Run
> `/improve-cycle discover` to add more rows from a bug-hunt sweep.

## Cycle notes

- 2026-07-15 (S1): Delivered the non-protected core of S1 in PR (branch
  `improve/S1-wire-vitest`, commit `65d612d`): `frontend/vitest.config.mts` +
  `vitest.setup.ts` + a real `Legend` render test (iterates `NODE_STYLE_ORDER`,
  asserts every node-type label) + jsdom/testing-library devDeps + regenerated
  lockfile + `docs/dev/TESTING.md`. `cd frontend && npm test` passes (2/2).
  **Criterion "CI `frontend` job runs it" is intentionally NOT in this PR** — it
  requires editing `.github/workflows/ci.yml` (protected). The exact 3-line YAML
  step is provided in the PR body for a human to add under the `human-approved`
  label. Left `in-review`; the CI-wiring remainder is the only unmet sub-criterion.
- 2026-07-15 (S1): While running the QA bar, discovered the pre-existing
  `ruff format --check` failure on 5 PR-#10 files (unbounded ruff pin) → filed **W2**
  above. Not fixed in the S1 PR (finding and fixing kept in separate diffs).
- 2026-07-15 (W2): Fixed in branch `improve/W2-ruff-format-drift` (commit `4f4da28`):
  `ruff format`-ed the 5 files + bounded the pin to `ruff>=0.15.0,<0.16.0`. Bar
  green (`ruff format --check` = 82 files already formatted; pytest 134+100). Opened
  as **PR #11** (base `main`). At the human's direction, W2 was fixed FIRST so S1's
  CI is clean: **S1 (PR #12) is stacked on the W2 branch** — after #11 merges,
  retarget #12's base to `main`. Both PRs touch no protected paths.
- 2026-08-14 (S3/S4/M2): Review-first cycle on the uncommitted Group Management
  tree (branch `improve/S3-group-management`). Committed the feature work as-is,
  then fixed what two parallel `reviewer` lenses independently CONFIRMED:
  the empty-`not_in` group purge (data loss on any empty `groups/search` page),
  `get_group_detail` not being org-scoped (S4 — drawer disagreed with the grid),
  the stale `sync_log` row surviving a rebuildable-cache drop, the support bundle
  losing its traceback across a log rotation, and the `UserDetailDrawer` stale-
  response race. Each fix carries a regression test; the S4 tests were verified to
  fail without the fix. **M2 was found outside the diff:** PR #14 silently reverted
  merged PR #12's vitest wiring (config, setup, Legend test, testing-library +
  jsdom devDeps) and left `npm test` RED on `main` — restored here, but the CI half
  (a job that runs `npm test`) touches `.github/workflows/` and needs a human.
- 2026-08-14 (S10–S21): Dashboard research cycle, prompted by a human reading the
  live PS-internal Prod dashboard. Filed S10–S21 from that read and fixed S10–S18
  in branch `improve/S10-dashboard-truthfulness`. The through-line of the P1 rows
  is **truthfulness**: the page stated two numbers it did not have (`stale_90d`
  counting types with no access telemetry; `0` for a never-synced entity) and
  withheld one it did (the recorded reason a job failed). S19–S21 are left open —
  each needs a design call (which dependency shape counts as "dead-end", what a
  cross-cluster roll-up costs when other clusters are offline, how to window the
  activity feed per kind) rather than a mechanical patch.
  **Human direction mid-cycle:** the Tags KPI tile and the Data freshness card
  were removed from the page entirely. That also removed the dashboard's second
  fetch (`syncApi.status`) — `synced`/`deltas` now come from the one aggregate
  read — so the "silently swallowed sync-status failure" half of S18 is resolved
  by deletion rather than by handling.
- 2026-08-14 (S25 → S22, closing S6): **Human decision: close S6 in favour of
  S22.** Shipped S25 first because it blocks S22, then S22 itself.
  **S25** — `CachedMetadata` gains
  `ix_ts_metadata_cluster_org_guid (cluster_id, org_id, ts_guid)`. Declared on the
  model so `create_all` builds it on fresh DBs, and *also* issued as an explicit
  `CREATE INDEX IF NOT EXISTS` from a new `database._create_missing_indexes()`,
  because `create_all` never adds an index to an already-existing table — an
  installed DB would otherwise never get it. That backfill hook is additive and
  idempotent, deliberately unlike the `_REBUILDABLE_SENTINELS` drop path next to
  it: no data is destroyed, so it needs no re-sync. Any future index on an
  existing table should be added to `_BACKFILL_INDEXES` rather than to the model
  alone.
  **S22** — instead of a new per-node existence lookup, the existing
  `_enrich_owner_names` was widened into `_enrich_from_metadata`: it already ran
  the chunked `ts_guid IN (...)` set-membership query the `accessible` flag
  needs, so resolving both from one pass avoids the second query the S25 finding
  warned about. A node whose GUID has no `CachedMetadata` row for the
  `(cluster, org)` now comes back `accessible=False` and renders through the
  dashed/0.6-opacity path and "Inaccessible" legend key that were already built
  and already tested. **Zero rows are deleted** — that is the whole difference
  from the rejected S6 purge, and a regression test asserts two identical reads
  return identical graphs with the edge count unchanged.
  **CONNECTIONs are exempt** from the check: they come from TML connection
  references and are never `CachedMetadata` rows, so absence proves nothing and
  flagging them would dim every connection node on the graph. This is the
  read-path counterpart to the `ts_dependency.py:6-9` "inaccessible stubs are a
  supported endpoint" contract that S6 contradicted.
  Per M4, every new assertion was mutation-checked: removing the `accessible`
  assignment reddens the S22 test, and dropping the index declaration reddens all
  three S25 tests. No frontend change was needed — the flag was plumbed
  end-to-end and had simply never been set.
  **S6's Status is `done` in the sense of "decided and closed", not "implemented
  as written".** Its acceptance criteria — which an agent may not edit — still
  describe the deletion approach that was rejected; they were satisfied by S22's
  no-deletion equivalent instead. Read this entry before trusting that row.
- 2026-08-14 (S6): **Attempted, built, then REJECTED at review. S6 stays `open`
  and needs a human re-scope — its acceptance criteria are not safely
  satisfiable as written.** The plan (`_purge_orphaned_targets`) was implemented
  in full on branch `improve/S6-lineage-orphan-purge` (commit `b8a86de`, pushed
  for reference, deliberately NOT opened as a PR). It passed the entire
  verification bar — ruff, 181 unit + 129 integration, tsc, build, vitest — and
  the acceptance-criteria test provably failed without the fix. It is still
  wrong. Two `reviewer` lenses (correctness, regression/data-integrity)
  independently reproduced permanent data loss:
  1. **The builder writes exactly the rows the purge deletes.**
     `build_column_map:656` deliberately emits a liveboard→model edge whose
     target is absent from the metadata cache (`meta_by_guid.get(guid, ("",""))`).
     A model created in TS *after* the last metadata sync is enough: build 1
     writes the edge, build 2 — a bit-identical re-run — deletes it. Two
     consecutive identical builds now produce different databases.
  2. **The loss is permanent, not self-healing.** The design bet ("recovered on
     the liveboard's next re-export") is false under `incremental=True`: the
     unchanged liveboard is never re-exported, and the only self-heal
     (`has_lb_edges`) fires solely when *every* liveboard edge is gone, so one
     surviving edge suppresses it forever. Same shape for ANSWER usage rows —
     `build_answer_index`'s watermark is `max(synced_at)` over *surviving* rows.
  3. **The "cache is non-empty" guard guards the wrong shape** — see S23.
  4. `test_orphan_purge_spares_connects_edges` was **vacuous** — it passed with
     the entire `relation == "USES"` restriction deleted (see M4).
  The criteria are self-defeating: purging an edge whose source liveboard is
  unchanged is *by definition* purging something the run cannot rebuild. And
  `ts_dependency.py:6-9` documents "inaccessible stubs" as a **supported** edge
  endpoint, so deletion contradicts the table's own design. Filed **S22** as the
  proposed safe replacement (set the already-plumbed `accessible` flag on the
  read path — zero deletion), plus **S23**, **S24**, **M4** from the same review.
  Recommendation for the human: re-scope S6 onto S22, or close S6 in favour of it.
  The **performance** lens (reporting after the reject decision) added findings
  that outlive the rejected diff: `CachedMetadata` has no composite index, so any
  correlated lookup keyed on `(cluster_id, org_id, ts_guid)` binds `cluster_id`
  only and goes quadratic — measured 50.6 s vs 72.6 ms at 32k rows (≈697×). Filed
  as **S25**, which **blocks S22** (the `accessible` fix needs exactly that
  lookup). It also found sync background tasks run on the event loop rather than a
  threadpool, so slow DB work freezes all HTTP including job-status polling and
  `/health` → **S26**. Neither is caused by the rejected purge; both are latent
  on `main` today.
  Filed **M5**, **M6**, **M7** for the three process gaps this cycle exposed: a
  fully green bar certified a data-loss change (M5); the org model had no defined
  route for "the acceptance criteria are themselves wrong", which is exactly what
  S6 turned out to be (M6); and append-only org-memory let a fact W2 had already
  fixed mislead two agents (M7). **M5 is `yes` on Protected** — it amends
  CLAUDE.md's verification-bar section, so it needs a human and the
  `human-approved` label; this cycle did not touch that file.
- 2026-08-15 (S7): **Attempted, built, then REJECTED at review — the second
  consecutive cycle to do so on this code path. S7 stays `open`.** Unlike S6, S7's
  acceptance criteria are *satisfiable*; what failed is the **mechanism the
  criteria prescribe**. The implementation is pushed for inspection at
  `improve/S7-liveboard-tier-marker` @ `3cdda08` (deliberately NOT opened as a
  PR). It passed the entire verification bar — ruff, 186 unit + 129 integration,
  tsc, build, vitest — and its acceptance test provably failed without the fix.
  It is still wrong.
  The design: replace the `has_lb_edges` probe with a persisted `SyncLog` marker
  (`entity_type="dependencies_liveboard_tml"`) whose `synced_at` also becomes the
  liveboard tier's incremental watermark, replacing
  `max(CachedColumnLineage.synced_at)`. Four review lenses + QA reported; three
  found CONFIRMED blockers, and they converge on one root cause:
  **a watermark derived from the data it certifies self-invalidates when that
  data is destroyed; an independent marker does not.**
  1. **`has_lb_edges` was never the real self-heal.** The actual recovery was
     accidental: `_persist_column_map` delete-and-rebuilds `CachedColumnLineage`
     every run, so any build producing zero lineage rows (all logical-table TML
     403-stubbed, or `table_guids == []` from a mid-repopulation cache) left
     `max(synced_at)` NULL and force-re-crawled every liveboard. No test named
     this. The research brief, the architect plan, and the CEO all missed it;
     the correctness lens found it by mutation.
  2. **Total edge loss became permanent.** A crash between
     `_persist_column_map`'s delete-commit and its insert loop leaves the edges
     gone and the *previous* build's SUCCESS marker intact, suppressing the
     re-crawl forever. Measured against `main`: `main` recovers `['lb-1']`, the
     branch stays `[]`. Filed as **S29**.
  3. **The upgrade path both causes loss and removes the recovery.** On every
     existing DB the marker is absent, so "no marker ⇒ all liveboards changed"
     drags the build past the `if not table_guids and not lb_guids: return 0`
     early return with a *partially* repopulated metadata cache (S23 shape,
     reachable without user error via S24). `_persist_column_map`'s
     `not_in(all_lb_guids)` purge then deletes the absent liveboards' edges.
     `main` never even purges here. This also invalidates the org-memory bullet
     justifying the unguarded `not_in`: it assumed `all_lb_guids` is
     empty-or-complete, which only holds while nothing else can force
     `lb_guids` non-empty.
  4. **An existing regression test was made vacuous** — M4's lesson recurring.
     `main`'s `test_column_map_self_heals_missing_liveboard_edges` fails
     verbatim on the branch; one added `sql_delete(SyncLog)` line in its *setup*
     makes it green, and that line deletes exactly the state blockers 2 and 3
     land in. The CEO's own plan authorised the rewrite — the failure is at the
     design step, not the implementation step.
  5. **The suite could not detect any of it.** 6 of 7 mutations to
     `build_column_map` left all 16 tests green, including
     `_lb_changed` → never re-crawl anything, ever. Filed as **S27** (P1) and it
     **blocks any re-attempt of S7**: harden the tests first, then change the
     behaviour. That sequencing is the cycle's main recommendation.
  Not everything was negative. The **security** lens cleared protected paths,
  org/cluster scoping, and marker leakage into the UI; the **performance** lens
  found no material issue and measured the intended win (a 500-liveboard stub org
  goes from 11 `tml_export` calls *every* sync to 11 once, then 1) plus a strictly
  cheaper read path (two partition scans at 4.9 ms + 24.7 ms → one 0.049 ms
  lookup). The marker idea is sound; making it the *sole* watermark with no
  invalidator is what fails. A re-attempt should persist a **crawled-GUID set**
  co-located with the edges, so the same event that destroys the edges destroys
  the record.
  Also filed: **S28** (a bare timestamp watermark cannot express "not yet
  crawled" — a liveboard shared with the admin after a build, or arriving via a
  late metadata sync, is never crawled; present on `main` today), **M8** (review
  agents wrote scratch repros into `tests/unit/` of the shared checkout and
  reddened QA's gate run mid-verification), and **M9** (nothing requires a cycle
  to check whether criteria-prescribed *mechanisms* are sound before building —
  the direct cause of this reject and, one cycle earlier, of S6's).
  **Shipped instead:** the two missing regression tests that pin `main`'s real
  behaviour — a changed liveboard re-exports, and total edge loss recovers via
  the lineage-table reset — so the next attempt starts against a suite that can
  fail. No production code changed in this PR.
- 2026-08-15 (S27): Shipped. **Measured, not assumed:** `build_column_map`'s
  incremental path had 48 candidate mutations, of which only 9 were killed by the
  pre-existing suite. The gap was concentrated in one place — **not a single
  `cluster_id` or `org_id` predicate in any of the seven queries in the path was
  killed by any test**, even though multi-cluster is a v1 pillar.
  `tests/integration/test_cluster_isolation.py` structurally cannot cover it:
  `READ_ENDPOINTS` tests read *endpoints*, not cache *writers*.
  Added `tests/unit/test_lineage_incremental.py` (7 tests) + a mutation-harness
  section in `docs/dev/TESTING.md`. Zero production code changed. The reviewer
  independently re-ran 27 line-scoped mutations and agreed with the published
  table on all 27, **including the rows documented as SURVIVED** — the table is
  honest rather than overstated.
  Two fixture facts were discovered the hard way and are recorded in-code, because
  the obvious fixture is vacuous: a single diagonal `(c2, org 1)` shadow is
  excluded by *either* predicate alone and kills neither scoping mutation, so
  **two** shadows are required (`(c1, org 1)` and `(c2, org 0)`); and a shadow
  sharing the liveboard GUID is protected by the very `not_in(all_lb_guids)`
  predicate under test, so each shadow needs a scope-unique liveboard.
  Review found one PLAUSIBLE issue and it is the ironic one: **the anti-vacuity
  tests were themselves fixture-vacuous.** Stubbing `_seed_shadow` to write nothing
  left all 7 green, because "X is unchanged" holds trivially when X is empty —
  and those two tests are the sole killer of 12 scoping mutations. Fixed by
  asserting the fixture wrote rows; the fix was proven by re-running the stub
  probe and confirming both tests now fail. General rule now in org-memory: **any
  test whose assertion is "X is unchanged" must first assert X is non-empty.**
  Also fixed: `pythonpath = ["."]` added to `pyproject.toml`, because the new
  file's `from tests.unit... import` resolved *only* via the hatchling editable
  install's `.pth`. A bare `tests/conftest.py` was tried first and **proven
  insufficient** (no `tests/__init__.py` ⇒ prepend-mode inserts `tests/`, not the
  repo root) — verified by renaming the `.pth` away.
  **Criteria note for the human:** S27's criteria enumerate three example
  mutations, one of which — "reordering the post-persist write" — describes the
  *rejected* S7 branch and **does not exist on `main`**. The CEO wrote that row
  while the rejected design was still in hand; it is the M9 failure mode inside
  the row filed to prevent it. This cycle built to the criteria's general clause
  and left the row `in-review` for re-scoping. Four mutations are knowingly
  unkilled and documented: `:752` (equivalent mutant — `in_([])` is always false),
  `:758`/`:766` (not killable with realistic data), and O1, the delete-phase
  commit, which needs crash injection and belongs to **S29**.
  Filed **S30**: `build_answer_index`'s `_changed` twin at `:897` is byte-identical
  to `:559` but has zero coverage — the mutation now killed at `:559` leaves all 37
  tests green at `:897`.
- 2026-08-14: Filed S6–S9 and M3 from the same review rather than fixing them —
  each needs a design decision (a target-keyed purge, a persisted build marker,
  recorded API fixtures, an org-scoping semantics call, a guard-registry change)
  rather than a mechanical patch, and one reviewer-suggested guard was already
  proven wrong by the existing suite mid-cycle.
- 2026-08-15 (S23): Shipped in PR (branch `improve/S23-metadata-completeness-marker`)
  as a **completeness marker**, not a single transaction. Criteria mechanism (a),
  "the delete+repopulate happens in one transaction", was **refused with evidence**:
  `update_progress` opens a second pooled connection inside the page loop, and the
  engine sets no WAL and no `timeout` connect-arg, so a transaction held across the
  paginated crawl makes every metadata sync die on `database is locked` — and the
  unit fixtures' `StaticPool` collapses both connections into one, so the bar would
  have shown green. The criteria are an either/or, so this is a documented refusal
  of one branch, **not** an M6 REJECT. Mechanism (b) is write-ahead: `IN_PROGRESS`
  is written BEFORE the delete, because `_write_sync_log("FAILED")` only runs from
  `run_sync`'s in-process handlers and a SIGKILL would otherwise leave the previous
  SUCCESS row certifying a cache the committed delete destroyed.
  The review board CONFIRMED two defects on the first build, both invisible to a
  green bar: the `execute_*` guards raised inside a background task (fail-silent,
  job stranded `QUEUED` forever), and the `IN_PROGRESS` marker made a *healthy*
  in-flight sync render as "Sync interrupted" / "Never synced — sync now". Both
  fixed and pinned by mutation. Filed **S31** (`preview_delete`'s owned-object
  count is the same absence-as-evidence hole, left out of S23's scope), **S32**
  (`cache_authoritative` produced but never rendered), **S33** (no Topbar test),
  and **M10** (a guard inside a background task is fail-silent and the org's test
  conventions do not catch it).
- 2026-08-18 (S37, note — API reference, not a code change): Checked the bearer/org
  question against the live ThoughtSpot REST v2 reference (SpotterCode MCP) rather
  than reasoning from our own code, and the row's framing needs correcting before
  anyone works it. **A bearer token carries a baked-in org context fixed at
  creation time** — `auth/token/full`'s response carries `scope.{access_type,
  org_id}`, and `auth/token/object`'s request documents `org_id` as "ID of the Org
  context to log in to … if not specified and secret key is provided then user
  will be logged into the org corresponding to the secret key". So S37 canNOT be
  fixed by "give `BearerTokenAuth` an `org_id` field and pass it": the token the
  admin pasted was minted for one org, cannot be re-scoped per request, and
  minting a new one needs username+password or a secret key — precisely the
  credentials the bearer auth type exists to avoid. The real options are (a) pin a
  bearer cluster to its token's org, or (b) refuse multi-org operation on bearer
  clusters. Either way the row's stated criterion "either `BearerTokenAuth`
  carries an org context and applies it" is not achievable as written; treat the
  second and third alternatives in that criterion as the live ones.
- 2026-08-18 (S37 → proposed S42, note): The same lookup surfaced a **general**
  fix that is strictly better than a bearer-specific one. Verified live:
  `GET /api/rest/2.0/auth/session/user` returns `current_org: {id, name}` (plus
  the user's `orgs` list) — so the org a session is ACTUALLY operating in is
  observable, cheaply, for every auth type. `GET /api/rest/2.0/auth/session/token`
  does **not** carry an org scope, so token introspection is not the route.
  A guard that asserts `current_org.id == org_id` before any sync writes rows
  stamped with that `org_id` would fail closed on the bearer bug AND on any
  future auth mechanism that silently ignores org — instead of writing one org's
  content under another org's id, which is what makes the Archiver and Bulk
  Delete select the wrong production content today. Caveat from the docs to
  handle: a cluster admin's `orgs` list contains only Primary unless they were
  explicitly added to each Org, so the guard must read `current_org`, not
  membership. Filed as S42 below.
