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
| S6 | P2 | Lineage edges whose **target** is deleted outlive it — ghost nodes in the graph | A model deleted in TS while its consuming liveboard is unchanged leaves no `CachedDependency`/`CachedColumnUsage` row behind; a unit test deletes a target from `CachedMetadata`, rebuilds, and asserts the edge is gone. (Phase 1 owns `USES`/non-LIVEBOARD, `_persist_column_map` owns `CONNECTS` + LIVEBOARD `USES`; neither purges by target GUID, and `build_column_map`'s `if not table_guids and not lb_guids: return 0` early-return skips the purge entirely) | open | no |
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
| S22 | P2 | Ghost lineage nodes render as *normal* nodes: the `accessible` flag is plumbed end-to-end but nothing ever sets it `False`. **Proposed as the safe replacement for S6's deletion approach** — `ts_dependency.py:6-9` documents "inaccessible stubs" as a supported edge endpoint, so the ghost is a read-path gap, not a cache-integrity problem | `get_lineage_graph` sets `accessible=False` on any node whose `guid` has no `CachedMetadata` row for the same `(cluster_id, org_id)`, so a deleted-in-TS target renders dashed/0.6-opacity with the existing "Inaccessible" Legend key instead of as a normal node; no rows are deleted; a unit test deletes a target from `CachedMetadata`, reads the graph, and asserts the node comes back `accessible=False` | open | no |
| S23 | P2 | `_sync_metadata` is delete-all-then-repage-in-spec-order, so an interrupted metadata sync leaves a **non-empty but truncated** cache (liveboards + answers present, every model and table missing) that reads as fully synced | An interrupted metadata sync is distinguishable from a complete one — either the delete+repopulate happens in one transaction, or a completeness marker (e.g. the `sync_log` SUCCESS row) is required before any consumer treats `CachedMetadata` as authoritative; a test simulates a mid-pagination failure and asserts the cache is not reported as synced | open | no |
| S24 | P3 | `POST /sync/{entity}` has no concurrency guard, so a metadata sync and a dependencies build interleave on the same event loop while the metadata cache is mid-repopulation (`api/sync.py:85-138` creates jobs unconditionally) | Triggering a sync for an entity that already has a running job is rejected (or queued) rather than starting a second concurrent run; a test asserts the second trigger does not start while the first is RUNNING | open | no |
| M4 | P3 | A "spares X" guard test placed on a code path that full-rebuilds X is vacuous — `test_orphan_purge_spares_connects_edges` passed with its entire `relation == "USES"` restriction deleted, because `_persist_column_map` delete-alls and re-inserts CONNECTS after the purge in the same run | The org's review checklist (or the `reviewer` agent brief) requires every "spares/preserves X" guard test to be falsified by deleting the predicate it guards, and the test must be placed on a path that does not rebuild X; the lesson is recorded in `docs/dev/TESTING.md` | open | no |

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
- 2026-08-14: Filed S6–S9 and M3 from the same review rather than fixing them —
  each needs a design decision (a target-keyed purge, a persisted build marker,
  recorded API fixtures, an org-scoping semantics call, a guard-registry change)
  rather than a mechanical patch, and one reviewer-suggested guard was already
  proven wrong by the existing suite mid-cycle.
