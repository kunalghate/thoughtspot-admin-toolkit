# BACKLOG

The org's work queue. Read it at the start of every cycle; change it only at a
cycle's **Records** step.

**Bright line — what a cycle may do to this file:** change a row's **Status**,
append **notes**, and append **new rows**. A cycle may NEVER edit an item's
**Priority** or **Acceptance criteria**, and NEVER delete a row. Priority and
criteria are the human's lever — they are set and re-scoped by a person, not by an
agent. (See [CLAUDE.md](CLAUDE.md) → "How the org works".) The one sanctioned
removal: when an item becomes **done** (completed/resolved), its index line and
detail entry are MOVED verbatim to
[BACKLOG_COMPLETED.md](BACKLOG_COMPLETED.md) — a move, never a deletion.

## ID taxonomy (mirrors the three standing goals)

| Prefix | Meaning |
|---|---|
| **S** | Stability / features — improve the app |
| **R** | Refactors — improve the app without changing behavior |
| **W** | Keep-current — ThoughtSpot REST v2 drift, dependency drift |
| **M** | Org / process — improve the agents, skills, and gates themselves |
| **F** | User feedback — items reported by real users, tracked in the Feedback section below |

**Priority:** P1 (urgent) → P4 (nice-to-have). **Status:** `open` · `in-progress`
· `in-review` · `done`. **Protected:** does the item likely touch a
[protected path](CLAUDE.md)? (If yes, its PR needs the `human-approved` label.)

## How this file is organized

The **Index** below is the scan view — one line per item. The **detail entry**
(under Open / In review / Done / Feedback) is the authoritative record: it holds
the full problem statement and acceptance criteria, verbatim. When a Status
changes, update it in BOTH the index line and the detail entry's status line.
New rows get an index line plus a detail entry in the matching section. When
an item reaches `done`, move its index line and detail entry to
[BACKLOG_COMPLETED.md](BACKLOG_COMPLETED.md).

## Index

### Open (41)

| ID | P | Item | Protected |
|----|---|------|-----------|
| S36 | P1 | Startup crash-recovery purges cache rows for never-deleted objects | yes (`ts_admin/main.py`) |
| M5 | P2 | A green verification bar is necessary but not sufficient | yes (`CLAUDE.md`) |
| M8 | P2 | Gate serialization violated by file writes into the shared checkout | yes (`CLAUDE.md`) |
| M10 | P2 | Fail-closed guard inside a background task is fail-silent | — |
| M11 | P2 | A silently-degraded best-effort pass reports success at every level | — |
| M12 | P2 | Frontend vitest executes on zero automated entry points | yes (`.github/workflows/*`, `CLAUDE.md`) |
| M13 | P2 | Library-contract blindness: dead grid sorting graded 'clean' twice | — |
| M14 | P2 | Blanket except in batch loops: total failure reports PARTIAL | — |
| S7 | P2 | has_lb_edges self-heal makes incremental a permanent full crawl | — |
| S26 | P2 | Sync background tasks block the FastAPI event loop | — |
| S28 | P2 | Bare-timestamp watermark cannot express 'not yet crawled' | — |
| S29 | P2 | _persist_column_map delete-commit crash window | — |
| S30 | P2 | build_answer_index incremental predicates have zero mutation coverage | — |
| S31 | P2 | preview_delete trusts a possibly-truncated cache ('0 owned objects') | — |
| S37 | P2 | BearerTokenAuth silently discards org_id | yes (`ts_admin/config.py`) |
| S38 | P2 | delete-tag-only is destructive with no dry-run | yes (`tests/integration/test_dryrun_safety.py`) |
| S42 | P2 | No sync verifies the session org matches the stamped org_id | — |
| S44 | P2 | A bulk share cannot be verified (bare 204) | — |
| W3 | P2 | SQL_VIEW subtype below 10.11 kills the whole metadata sync | — |
| W6 | P2 | Below 10.11 the metadata sync ends PARTIAL forever | — |
| M3 | P3 | READ_ENDPOINTS cannot register detail-shaped endpoints | yes (`tests/integration/test_cluster_isolation.py`) |
| M4 | P3 | Vacuous 'spares X' guard tests | — |
| M7 | P3 | org-memory was append-only; stale facts mislead agents | — |
| S2 | P3 | Add a /health smoke check to CI | yes (`.github/workflows/*`) |
| S5 | P3 | Nested group membership never persisted | — |
| S8 | P3 | principal_permissions fails silently to [] (recorded as SUCCESS) | — |
| S9 | P3 | _is_admin joins on cluster_id only | — |
| S19 | P3 | Unused cache signals (dead-end models, view_count, over-sharing) | — |
| S20 | P3 | Dashboard is single-cluster despite multi-cluster v1 | — |
| S21 | P3 | _recent_activity window crowd-out by one bulk session | — |
| S32 | P3 | cache_authoritative is produced end-to-end and consumed nowhere | — |
| S35 | P3 | Topbar header layout has no automated guard (jsdom can't) | — |
| S39 | P3 | Audit log: seven writers, zero readers, no org_id | yes (`tests/integration/test_cluster_isolation.py`) |
| S40 | P3 | Transfer-ownership type chips collapse to the current selection | — |
| S41 | P3 | Grid selection silently lost past ~2,000 rows | — |
| S43 | P3 | org_id=None basic-auth lands in a nondeterministic org | yes (`ts_admin/config.py`, `ts_admin/ts_client/auth.py`) |
| S45 | P3 | COLLECTION objects are never synced | — |
| W1 | P3 | Add mypy to CI | yes (`.github/workflows/*`) |
| W4 | P3 | permission_type silently ignored below 10.3 (DEFINED vs EFFECTIVE) | — |
| W5 | P3 | metadata/search paginated outside the documented contract | — |
| M1 | P4 | Single-source the protected-path list | yes (`.github/workflows/*`, `CLAUDE.md`) |

### In review (7)

| ID | P | Item | Protected |
|----|---|------|-----------|
| S27 | P1 | Lineage unit suite is mutation-vacuous | — |
| M2 | P2 | PR #14 silently reverted the vitest wiring | yes (`.github/workflows/*` for the CI half) |
| S23 | P2 | Interrupted metadata sync reads as fully synced | — |
| S1 | P3 | Wire frontend vitest into the suite | — |
| S33 | P3 | Topbar sync label/color has no test | — |
| M6 | P2 | No REJECT route when acceptance criteria are themselves the bug | — |
| M9 | P2 | Criteria-prescribed mechanisms are never soundness-checked before build | — |

### Done

Completed items live in [BACKLOG_COMPLETED.md](BACKLOG_COMPLETED.md).

### Feedback (7)

| ID | P | Type | Item | Status | Protected |
|----|---|------|------|--------|-----------|
| F1 | P3 | Feature | Connections list page with per-connection object counts | open | yes (`tests/integration/test_cluster_isolation.py`) |
| F2 | P3 | Feature | Connection + external db/schema/table on the table list and in lineage | open | — |
| F3 | P4 | Feature | 'Created by' on the users list; 'Created' date on the groups list | open | — |
| F8 | P4 | Feature | CSV export for Users and Groups | open | — |
| F9 | P3 | Feature | Export tagged content to TML without deleting | open | — |
| F10 | P3 | Feature | Transfer ownership of selected objects from the Metadata screen | open | yes (`tests/integration/test_dryrun_safety.py`) |
| F11 | P3 | Feature | Select-all on the Archiver results grid | open | — |

> Seeded 2026-07-15 at bootstrap (BOOT) from Step 0 discovery. Run
> `/improve-cycle discover` to add more rows from a bug-hunt sweep.

## Open items

Ordered by priority, then ID.

### S36 — Startup crash-recovery purges cache rows for never-deleted objects

`P1` · **open** · protected: yes (`ts_admin/main.py`)

Startup crash-recovery deletes `CachedMetadata` rows for objects that were never deleted, is not cluster/org-scoped, and ignores Bulk Deleter jobs. `main.py:70` matches `job_type == "archive"` only (the Deleter creates `bulk_delete`, so it gets no recovery at all), and `:73-88` infers "deleted" from `tml_export_status == "SUCCESS"` — but `_execute_delete` exports EVERY object in Phase A before Phase B deletes any, so the most likely crash window is exactly where that inference is maximally wrong: the recovery purges the cache for N objects of which zero were deleted. The admin then sees them vanish from Metadata Explorer and the Archiver while they are still live in ThoughtSpot, and their `ArchiveRecord`s report `is_restorable=True`, so "restoring" them creates duplicates. `:82`'s `sql_delete` also has no `cluster_id`/`org_id` predicate, though the stuck `Job` row carries `cluster_id`

**Acceptance criteria:** Crash recovery does not infer deletion from TML-export status: either `_execute_delete` records per-GUID delete confirmation (an `ArchiveRecord` field set only after `delete_metadata` returns) and recovery purges only confirmed rows, or recovery purges nothing and instead marks the metadata `sync_log` non-authoritative so the next read re-syncs. The delete is scoped to the stuck job's `cluster_id` and org. Recovery behaves identically for `job_type="bulk_delete"`. Tests: (a) a RUNNING archive delete job with all records `tml_export_status="SUCCESS"` and no confirmed deletes removes zero `CachedMetadata` rows; (b) the same `ts_guid` in two clusters, recovery for a stuck job in cluster A leaves cluster B's row intact

### M5 — A green verification bar is necessary but not sufficient

`P2` · **open** · protected: yes (`CLAUDE.md`)

The verification bar proves *conformance to the criteria*, not that the change is safe — in the S6 cycle it went fully green (ruff, 181 unit + 129 integration, tsc, build, vitest) on a change that three review lenses then proved causes permanent data loss. Nothing in the bar can catch "this shouldn't be built at all", and the unit suite is also structurally blind to query-plan regressions (small in-memory fixtures pass in ms regardless of an O(n²) plan)

**Acceptance criteria:** CLAUDE.md's verification bar states explicitly that a green bar is necessary but NOT sufficient and never authorises shipping on its own; the Review Board stays mandatory for any change that deletes rows, alters a purge/retention rule, or adds a correlated subquery or join, **even when every gate is green**; the same section requires `EXPLAIN QUERY PLAN` on a realistically-sized DB for new correlated subqueries/joins

### M8 — Gate serialization violated by file writes into the shared checkout

`P2` · **open** · protected: yes (`CLAUDE.md`)

Gate serialization is violated in practice by **file writes**, not just ports: during the S7 review, reviewer/QA agents wrote five scratch repro files into `tests/unit/` of the **shared** checkout, flipping `pytest tests/unit/` from green to red mid-verification and making QA's gate result untrustworthy. CLAUDE.md serializes port-bound gates but says nothing about agents writing into the working tree they share

**Acceptance criteria:** The agent briefs (and CLAUDE.md's gate-serialization note) require review/QA repro artifacts to live in a `git worktree` or the scratchpad, never in `tests/` of the shared checkout; QA reports the working tree state it observed so a polluted run is visible rather than silent

### M10 — Fail-closed guard inside a background task is fail-silent

`P2` · **open** · protected: no

A fail-closed guard placed inside a Starlette background-task target is fail-**silent**, and the org's review/test conventions do not catch it: S23 shipped guards in `execute_share`/`execute_transfer` that raise after the 202 is already on the wire, so the 409 never reaches the caller and the `Job` row strands at `QUEUED`/`error=None` until a restart. All 241 unit tests passed on the broken guard because service-level tests call the coroutine directly — the one thing production cannot do

**Acceptance criteria:** The `reviewer`/`implementer` briefs (or `docs/dev/TESTING.md`) require that any refusal on a `background_tasks.add_task`-dispatched endpoint is (a) checked in the router before `create_job` and (b) covered by a TestClient test asserting the status code AND that no `Job` row was created; a service-level unit test alone is documented as insufficient for this class

### M11 — A silently-degraded best-effort pass reports success at every level

`P2` · **open** · protected: no

A best-effort pass that fails silently reports success at every level, and no gate catches it. `_sync_dependencies` swallows the column-map failure by design (the object tier must survive), so on a cluster where the TML pass aborted every time the job read COMPLETE, the Topbar read "Synced 2m ago", and the cache held 9,247 USES edges with **zero** CONNECTS and zero column rows. The full verification bar was green throughout — unit tests exercise the pass against canned TML that never fails, so the all-or-nothing batch flaw and the Model-alias parse gap were both invisible. Partially addressed (`column_error` now rides in the job result), but nothing asserts a degraded pass is visible to the user

**Acceptance criteria:** Either a gate or a guard test makes a silently-degraded sync detectable: a `dependencies` sync whose column pass fails does not present as a clean COMPLETE in the Jobs UI, and the org's test conventions require best-effort passes to be exercised against a failing dependency, not only a happy-path fake

### M12 — Frontend vitest executes on zero automated entry points

`P2` · **open** · protected: yes (`.github/workflows/*`, `CLAUDE.md`)

Frontend `vitest` executes on **zero** automated entry points, so a test written to satisfy a backlog row guards nothing. `.github/workflows/ci.yml` runs only `npm ci` → `npx tsc --noEmit` → `npm run build` (no `npm test` step), and `Makefile:20` `test:` is `pytest tests/ -v` with no frontend target — while CLAUDE.md documents `make test` as "pytest + vitest + Playwright". Found during S33: its 37 new tests are enforced by CI only where they produce a *type* error. Broader than S1 ("wire vitest into CI"), which understates it by not covering `make test` or the CLAUDE.md drift

**Acceptance criteria:** `npm test` runs in CI AND from a documented make target; CLAUDE.md's description of `make test` matches what the Makefile does (or the claim is corrected); a deliberately-broken frontend assertion is caught by a gate rather than only by a human running vitest by hand

### M13 — Library-contract blindness: dead grid sorting graded 'clean' twice

`P2` · **open** · protected: no

Two bug-hunt passes graded the three grid pages' stale-response guards "clean" while sorting was dead in production on all three: the guard returned from AG Grid's `getRows` without calling `successCallback` OR `failCallback`, leaking the block loader's concurrency slots (cap 2), so the grid stopped issuing requests after the second superseded load. Reading only our code the guard looks correct — the bug is only visible against the LIBRARY's contract. No gate could see it either: `tsc` and `next build` pass, and there is no browser-level check of any interaction

**Acceptance criteria:** The `bug-hunter` and `reviewer` briefs require checking the third-party contract whenever our code short-circuits inside a library-supplied callback (done in this cycle — verify it stays), AND the org gains at least one automated interaction check that would have caught it: a Playwright (or vitest + AG Grid) test that sorts a grid twice and asserts rows are still rendered, running in a gate rather than by hand

### M14 — Blanket except in batch loops: total failure reports PARTIAL

`P2` · **open** · protected: no

A blanket `except Exception` around a chunk loop converts ANY failure — including a `TypeError` from our own code — into a "chunk failed" that the job reports as **PARTIAL**, and `mark_partial` is checked before the `succeeded == 0` branch, so a totally-failed job never reports FAILED. This is the mechanism that hid three 404-ing endpoints for the life of the project: Bulk Sharing, transfer sharing and bulk user delete each reported PARTIAL with zero items affected, attached to a "run a sync and retry" message, rather than failing. It also swallowed a live `TypeError` during the fix itself. Sites: `bulk_sharing_service.py:690`, and the equivalents in `user_management_service` transfer-sharing and delete

**Acceptance criteria:** A job in which zero items succeeded reports FAILED, never PARTIAL — the `succeeded == 0` branch is evaluated first at every such site, with a test per site. Separately, the org's review checklist requires that a blanket `except Exception` wrapping a batch loop is either narrowed to named exception classes or justified in a comment naming what it is allowed to swallow; CLAUDE.md already forbids bare `except Exception`, so the ~20 pre-existing sites are inventoried and tracked rather than left implicit

### S7 — has_lb_edges self-heal makes incremental a permanent full crawl

`P2` · **open** · protected: no

`has_lb_edges` self-heal makes `incremental=True` a permanent full TML crawl on any org that legitimately yields zero liveboard edges

**Acceptance criteria:** The self-heal fires at most once per org (keyed off a persisted "liveboard tier last built" marker, not "are there any rows"), so an org whose liveboards are all TML-inaccessible (403 stubs) does not re-export every liveboard on every dependencies sync forever; a unit test covers two consecutive incremental builds that produce zero liveboard edges and asserts the second exports nothing

### S26 — Sync background tasks block the FastAPI event loop

`P2` · **open** · protected: no

Sync background tasks block the FastAPI event loop: `api/sync.py:113` `background_tasks.add_task(run_sync, ...)` with an `async` target means Starlette awaits it inline, and `_persist_column_map` is synchronous — so slow DB work in a sync freezes ALL HTTP handling, including the job-status polling the UI uses to show sync progress, and `/health`. Compounded by the engine setting no `journal_mode` (no WAL), so a long write transaction holds the DB write lock

**Acceptance criteria:** Long-running/blocking sync work does not occupy the event loop (e.g. the blocking section runs via `to_thread`/`run_in_executor`, or the job runs off the request path entirely), and job-status polling stays responsive while a large sync runs; WAL is enabled or the decision not to is recorded with a reason

### S28 — Bare-timestamp watermark cannot express 'not yet crawled'

`P2` · **open** · protected: no

The liveboard incremental watermark is a bare timestamp, which cannot express "not yet crawled": a liveboard that has existed in TS since 2020 but enters `CachedMetadata` only after the first build (late/interrupted metadata sync, or newly shared with the admin — a permission grant does not bump `modified_at`) has `modified_at ≤ watermark` and is never crawled, so its USES edges are never built. **Verified present on `main` today** — not introduced by the rejected S7 diff, but S7's persisted marker made it permanent where the accidental `NULL`-watermark reset used to mask it

**Acceptance criteria:** A liveboard whose lineage has never been built is crawled regardless of its `modified_at` — e.g. the changed-set is computed from a persisted crawled-GUID set (or `max(modified_at, metadata_first_seen)`) rather than a bare timestamp comparison; a unit test seeds a liveboard with a 2020 `modified_at` that arrives in `CachedMetadata` after the first build and asserts the second build exports it

### S29 — _persist_column_map delete-commit crash window

`P2` · **open** · protected: no

`_persist_column_map` commits its DELETEs before inserting (`session.commit()` between the delete block and the insert loop), so a crash in that window — process kill, `serve` restart, sqlite lock timeout, or an S24 interleave — leaves a durably-empty scope. Today this self-heals only by accident, via `max(CachedColumnLineage.synced_at)` reading NULL; the rejected S7 diff removed that accident and made the loss permanent

**Acceptance criteria:** The delete+repopulate in `_persist_column_map` is atomic (single transaction), or a crash in the window is detectable so the next build rebuilds the scope rather than trusting it; a test simulates a raise between the delete commit and the insert loop and asserts the following build restores the edges

### S30 — build_answer_index incremental predicates have zero mutation coverage

`P2` · **open** · protected: no

`build_answer_index`'s `_changed` twin (`lineage_service.py:897`) is byte-identical to `build_column_map`'s but has **zero** mutation coverage: dropping its `last_built is None` disjunct leaves the entire 37-test lineage suite green, while the same mutation at `:559` is now killed by S27. Answer-index incrementality is unpinned, and `build_answer_index` carries the same watermark shape S7 was rejected over (`max(CachedColumnUsage.synced_at)` over *surviving* rows, so partial deletion is permanent)

**Acceptance criteria:** The ANSWER tier's incremental predicates are each killed by at least one test — at minimum, dropping `last_built is None` or the `modified_at > last_built` comparison at `lineage_service.py:897` turns a test red; the mutations and results are appended to the table in [docs/dev/TESTING.md](docs/dev/TESTING.md)

### S31 — preview_delete trusts a possibly-truncated cache ('0 owned objects')

`P2` · **open** · protected: no

`preview_delete`/`dryrun_delete` report `owned_object_count` from a raw `count()` over `CachedMetadata` (`user_management_service.py:713`) with no completeness check, so a truncated metadata cache makes the delete-user safety warning read **"0 owned objects"** for a user who owns 40 worksheets — the admin deletes them and orphans the content. This is the same absence-as-evidence shape S23 guarded at `resolve_downstream`, on the one destructive path S23 deliberately left out of scope

**Acceptance criteria:** `preview_delete`'s owned-object count either refuses (as the five S23 sites do) or is presented as unreliable when the metadata cache is not certified complete; a test seeds a truncated cache plus a user owning only non-cached types and asserts the count is not silently reported as 0

### S37 — BearerTokenAuth silently discards org_id

`P2` · **open** · protected: yes (`ts_admin/config.py`)

`BearerTokenAuth` silently discards `org_id`, so every sync on a bearer-token cluster stamps one org's data with another org's id. `config.py:74-76` accepts `org_id` and throws it away for `AuthType.BEARER` (`ts_client/auth.py:170-176` has no `org_id` field at all, unlike `BasicAuth:86` and `TrustedAuth:133`) — despite `build_auth_strategy`'s own docstring stating org context is the ONLY way the TS API scopes content. Bearer is a first-class option in the Connections UI. An admin on org 5 syncing metadata gets org 0's objects written with `org_id=5`; the Archiver and Bulk Delete then build their input set from that cache, so a cleanup the admin believes is scoped to org 5 selects org 0's production content — and the dry-run agrees with itself, because preview and execute read the same mis-stamped cache. Related: `_sync_users`/`_sync_groups` pass no `org_id` while `_sync_metadata` and every lineage build do, so the handlers disagree about the contract

**Acceptance criteria:** A `BEARER` cluster cannot silently produce org-mismatched cache rows: either `BearerTokenAuth` carries and applies an org context, or `build_auth_strategy(org_id=...)` fails loudly (surfaced as a FAILED sync with an actionable message) when the strategy cannot honour the org, or the Connections UI blocks BEARER on multi-org clusters. A unit test asserts `build_auth_strategy(org_id=5)` on a BEARER cluster does not return an object that silently ignores 5. The org-scoping contract in `config.py:62-64` and the four sync handlers agree on whether `org_id` is passed

### S38 — delete-tag-only is destructive with no dry-run

`P2` · **open** · protected: yes (`tests/integration/test_dryrun_safety.py`)

`POST /api/v1/deleter/delete-tag-only` is a destructive, synchronous, cluster-wide write with no dry-run, in a product whose non-negotiable UX pattern is "dry-run required for all destructive operations". `api/deleter.py:156-176` calls `client.delete_tag` inline — no job, no preview of how many objects lose the label — and it is absent from `DRYRUN_ENDPOINTS`, while being wired into the UI at `frontend/lib/api.ts:485`. Compounding it: the TS-side delete removes the tag cluster-wide but the local strip is scoped to one org (`deleter_service.py:195-197`), so every other org's cached rows keep showing a tag that no longer exists

**Acceptance criteria:** A dry-run (endpoint or preview field) reports the count of objects that would lose the tag before any write; the endpoint is registered in `DRYRUN_ENDPOINTS`; the local tag strip is applied across every org of the cluster (or the tag cache is invalidated). A test asserts a second org's cached rows no longer carry the deleted tag

### S42 — No sync verifies the session org matches the stamped org_id

`P2` · **open** · protected: no

No sync verifies that the session is actually operating in the org whose id it stamps on every cached row. `build_auth_strategy(org_id=...)` is best-effort — `BearerTokenAuth` discards it outright (S37), and `_sync_users`/`_sync_groups` never pass it at all — yet `_sync_metadata` writes `org_id=N` onto every row regardless. When auth and stamp disagree, one org's content is cached under another org's id, and the Archiver and Bulk Deleter then build their input set from that cache: a cleanup the admin believes is scoped to org 5 selects org 0's production content, and the dry-run agrees with itself because preview and execute read the same mis-stamped cache. Verified live that the org a session is really in is observable: `GET /api/rest/2.0/auth/session/user` returns `current_org: {id, name}` for every auth type (`auth/session/token` does NOT carry an org scope, so token introspection is not the route)

**Acceptance criteria:** Before a sync writes any row stamped `org_id=N`, it confirms the live session's `current_org.id == N` and fails the job with an actionable message otherwise — one check per sync, not per page. The guard is auth-type agnostic, so it fails closed on BEARER (S37) and on any future auth mechanism that silently ignores org. It reads `current_org`, NOT the `orgs` membership list, since the docs state a cluster admin's `orgs` contains only Primary unless explicitly added to each Org. Tests: (a) a session whose `current_org.id` is 0 while the sync runs for org 5 fails the job and writes zero `CachedMetadata` rows; (b) a matching org proceeds normally; (c) the check is issued once per sync run

### S44 — A bulk share cannot be verified (bare 204)

`P2` · **open** · protected: no

A bulk share cannot be verified, because `security/metadata/share` returns a bare **204 No Content** — no per-object, no per-principal status — and no alternative bulk-share endpoint reports one. So "the share succeeded" is currently an assumption, and the audit-log row recording it is a guess rather than a record. This is an API limitation, not a coding defect, but it is exactly the gap that let the 404-ing `security/share` path (fixed in `9b60fa5`) go unnoticed. The read-back already exists: `bulk_sharing_service` calls `security/metadata/fetch-permissions` per object to build the PREVIEW diff

**Acceptance criteria:** After `execute_share`, each object is re-read with the same helper the preview uses and the result is compared to what was requested; the job result and the audit row record `verified_ok` / `verified_failed` counts, and a run where nothing changed is not reported as SUCCESS. A test whose stub returns unchanged permissions asserts `verified_failed == n`

### W3 — SQL_VIEW subtype below 10.11 kills the whole metadata sync

`P2` · **open** · protected: no

`search_metadata` queries the `SQL_VIEW` subtype unconditionally, but the `subtypes` enum tags it **Version: 10.11.0.cl or later** — and the seven specs run in a single generator loop, so one spec's 400 raises `TSInvalidParametersError` and kills the WHOLE metadata sync, discarding the specs that already succeeded. A customer below 10.11 therefore may have no metadata cache at all, and the failure names a subtype rather than the version gate. Not visible on ps-internal-prod or se-demo, both 26.8

**Acceptance criteria:** The `SQL_VIEW` spec is skipped when the cluster's `release_version` is below 10.11.0 (`test_connection` already retrieves it). Independently — and this is the load-bearing half — a failure in ONE spec of `search_metadata` is recorded and the remaining specs still run: a test injects a 400 on the `SQL_VIEW` spec and asserts LIVEBOARD/ANSWER/WORKSHEET results are still yielded and the sync completes as PARTIAL rather than FAILED

### W6 — Below 10.11 the metadata sync ends PARTIAL forever

`P2` · **open** · protected: no

Follow-on to W3, which is a strict improvement but leaves a sharp edge. Below 10.11.0 the `SQL_VIEW` subtype value does not exist, so W3 skips that spec — and records a skipped spec exactly like a failed one, which is correct (those objects genuinely go unenumerated) but means the metadata sync on such a cluster ends **PARTIAL forever**. `require_authoritative_metadata` therefore keeps refusing, so the Archiver, Bulk Delete, Bulk Sharing and transfer previews 409 permanently. Better than the old behaviour (one spec's 400 killed the whole crawl and left no cache at all), but a customer below 10.11 still cannot use the destructive features. Note the docs place the 10.11 tag on the `subtypes` FILTER VALUE, not on SQL Views themselves — the objects exist, we just cannot ask for them by that name

**Acceptance criteria:** Below 10.11.0 the metadata crawl falls back to a single unfiltered `LOGICAL_TABLE` pass and derives each object's effective subtype from the response, so the spec set is complete and the sync can certify SUCCESS. A test drives a stubbed `release_version` of 10.10.0, asserts no request carries `subtypes: ["SQL_VIEW"]`, asserts SQL-view objects still land in `CachedMetadata` with the right `object_type`, and asserts the sync completes SUCCESS rather than PARTIAL

### M3 — READ_ENDPOINTS cannot register detail-shaped endpoints

`P3` · **open** · protected: yes (`tests/integration/test_cluster_isolation.py`)

New cluster-scoped **detail** read endpoints can't be registered in `READ_ENDPOINTS` (its extractor assumes `body["items"]`), so `/groups/{guid}` and `/users/{guid}/access` are unguarded

**Acceptance criteria:** `READ_ENDPOINTS` (or a sibling registry) accepts detail-shaped responses and the two endpoints above are registered, OR CLAUDE.md documents the live-passthrough exemption explicitly so the rule isn't stated unconditionally while having a silent carve-out

### M4 — Vacuous 'spares X' guard tests

`P3` · **open** · protected: no

A "spares X" guard test placed on a code path that full-rebuilds X is vacuous — `test_orphan_purge_spares_connects_edges` passed with its entire `relation == "USES"` restriction deleted, because `_persist_column_map` delete-alls and re-inserts CONNECTS after the purge in the same run

**Acceptance criteria:** The org's review checklist (or the `reviewer` agent brief) requires every "spares/preserves X" guard test to be falsified by deleting the predicate it guards, and the test must be placed on a path that does not rebuild X; the lesson is recorded in `docs/dev/TESTING.md`

### M7 — org-memory was append-only; stale facts mislead agents

`P3` · **open** · protected: no

`docs/org-memory/` was effectively append-only, so a fact that a later PR had already fixed kept steering agents wrong: the "KNOWN RED `ruff format --check`" bullet was resolved by W2 in PR #11 but still misled two agents in the S6 cycle (one wrote a "record the pre-existing failure" instruction into its plan)

**Acceptance criteria:** The Records step requires **pruning** — every cycle re-verifies the org-memory facts its work touched and DELETES or amends the ones that no longer hold, not just appends new ones; `docs/org-memory/README.md` states this and the ~120-line cap is described as enforced by pruning stale facts first

### S2 — Add a /health smoke check to CI

`P3` · **open** · protected: yes (`.github/workflows/*`)

Add a `/health` smoke check to CI

**Acceptance criteria:** CI (via TestClient or a booted app) asserts `GET /health` returns 200; check runs in the pipeline and is documented in TESTING.md

### S5 — Nested group membership never persisted

`P3` · **open** · protected: no

Nested group membership is missing: `_sync_groups` parses `sub_groups` but never persists them

**Acceptance criteria:** Users who belong to a group only via a sub-group appear in that group's `member_count` and member list (or the UI states explicitly that counts are direct members only); a unit test covers a group whose sole member arrives through a sub-group

### S8 — principal_permissions fails silently to [] (recorded as SUCCESS)

`P3` · **open** · protected: no

`principal_permissions` / `fetch_permissions` have no wire-shape coverage and fail silently to `[]`, which `execute_transfer_sharing` records as SUCCESS

**Acceptance criteria:** A test feeds each parser a recorded v2 response payload and asserts the extracted rows; `execute_transfer_sharing` does not write a SUCCESS audit-log row when the fetch returns zero rows for a user whose preview reported N

### S9 — _is_admin joins on cluster_id only

`P3` · **open** · protected: no

`_is_admin` joins on `cluster_id` only, so membership synced for one org blocks transfers executed in another

**Acceptance criteria:** `_is_admin` (and the `admin_count` snapshot) scope membership to the org the operation runs in; a unit test seeds an admin membership in org 0 and asserts a transfer in org 5 is not blocked by it

### S19 — Unused cache signals (dead-end models, view_count, over-sharing)

`P3` · **open** · protected: no

Signals still unused in the cache: `view_count` (never-viewed content), `created_at` growth over time, `ContentPermission` (over-shared content), and `CachedDependency` dead-end models — the last being provable staleness rather than the access-date guesswork of S10

**Acceptance criteria:** At least the dependency-based signal ships: models/worksheets with no downstream consumer are counted and surfaced as a safe-to-review cleanup target, sourced from `CachedDependency` and scoped by cluster + org

### S20 — Dashboard is single-cluster despite multi-cluster v1

`P3` · **open** · protected: no

Multi-cluster is a v1 pillar but the dashboard is single-cluster: no roll-up and no signal that another configured cluster has failing syncs

**Acceptance criteria:** The dashboard indicates cross-cluster health (at minimum: other clusters with failed jobs in the last 7 days) without requiring a cluster switch; counts stay cluster-scoped

### S21 — _recent_activity window crowd-out by one bulk session

`P3` · **open** · protected: no

`_recent_activity` scans a fixed 300 raw rows per audit source, so one bulk share of 500 objects fills the window and hides every other activity type

**Acceptance criteria:** The feed's per-source window cannot let a single large session crowd out other kinds of activity (e.g. group in SQL, or scan per-kind); a test seeds one oversized share session plus a deletion and asserts both appear

### S32 — cache_authoritative is produced end-to-end and consumed nowhere

`P3` · **open** · protected: no

`cache_authoritative` is produced end-to-end and consumed nowhere: `api/metadata.py` computes it on both metadata responses and `frontend/lib/types.ts` types it, but no component reads it (grep returns only the type declarations), so after an interrupted sync the Metadata Explorer still presents a truncated list as complete. Same produced-but-never-rendered shape as the `accessible` flag already recorded in org-memory

**Acceptance criteria:** The metadata list/stats UI visibly marks the data as possibly-partial when `cache_authoritative` is false (banner, badge, or equivalent), so the flag's stated contract — "the UI must not present the list as complete" — is actually met; a frontend test or an explicit manual-verification note records it

### S35 — Topbar header layout has no automated guard (jsdom can't)

`P3` · **open** · protected: no

The Topbar header layout has no automated guard, and jsdom cannot provide one — it does not lay out, so a vitest render test can only assert inline style strings, not geometry. S33's review found two CONFIRMED layout regressions (offline badge painting on top of the sync indicator; page title collapsing to width 0) that `tsc`, `next build` and 39 green vitest tests were all blind to; they were caught only by an agent measuring a hand-built CSS replica in headless Chromium. A residual overlap of 3–20px also remains below 720px offline, where the header is over-subscribed even with the title column at zero width

**Acceptance criteria:** A Playwright viewport assertion covers the header's non-overlap invariant at the widths that matter (at minimum 1024 and 900, offline and online): the offline badge's right edge stays left of the sync column's left edge, and the org selector stays within the viewport; the &lt;720px residual is either fixed with a responsive badge (icon-only + `title`) or explicitly accepted in the test as an out-of-support width

### S39 — Audit log: seven writers, zero readers, no org_id

`P3` · **open** · protected: yes (`tests/integration/test_cluster_isolation.py`)

The audit log has seven writers and zero readers, and no `org_id`. `AuditLog` is written by `deletion_service`, `bulk_sharing_service`, `deleter_service`, `user_management_service` (×3) and `archiver_service` (×2); no router, service or frontend file reads it — grep returns only writers and tests. "Audit log" is listed as an MVP v1 feature in CLAUDE.md, yet today an admin can only see it by opening the SQLite file. The model also carries no `org_id`, so even once a reader exists it cannot be org-scoped like `ArchiveRecord`, `ShareRecord` and `UserActionRecord` all are

**Acceptance criteria:** A cluster+org-scoped `GET /api/v1/audit` serves the audit trail and is registered in `READ_ENDPOINTS`; `AuditLog` gains `org_id` and every writer sets it; a test asserts a destructive action in org 5 does not appear in org 0's feed

### S40 — Transfer-ownership type chips collapse to the current selection

`P3` · **open** · protected: no

The transfer-ownership modal's type chips collapse to the current selection, making multi-type selection impossible and hiding what the user actually owns. `user_management_service.py:279-282` computes `by_type` from the ALREADY-FILTERED rows, and `TransferOwnershipModal.tsx:125-135` renders the chip row straight from that response, refetching on every chip click. A user owning 12 liveboards and 8 answers shows both chips; clicking `Liveboard` makes the `Answer 8` chip disappear, so there is no way to select both and the modal now states the user owns nothing but liveboards — if the admin proceeds, 8 answers are silently left on the departing account. Secondary: entering the `confirming` step re-fires `loadPreview`, so every transfer runs the preview query twice

**Acceptance criteria:** The chip set is derived from an UNFILTERED preview (a separate unfiltered `by_type`, or the chips are held from the first response and not overwritten), so every type the user owns stays selectable and multi-type selection works, with each chip's count reflecting the owned total for that type. A test previews a user owning two types, selects one, and asserts both chips are still rendered

### S41 — Grid selection silently lost past ~2,000 rows

`P3` · **open** · protected: no

Grid selection is silently lost past ~2,000 rows on the infinite row model. `pages/sharing.tsx:198-201`, `pages/archiver.tsx:425-431` and `pages/users.tsx:168-170` derive the action set from `api.getSelectedRows()` with `maxBlocksInCache={10}` × `cacheBlockSize={200}`; when AG Grid evicts a block its row nodes go with it, so checkboxes ticked near the top of a 10k-object org stop being returned once the admin scrolls far enough. The "N selected" counter changes without the admin unchecking anything, and the bulk action runs on a subset. Graded PLAUSIBLE — needs confirmation against the pinned AG Grid version before fixing

**Acceptance criteria:** Selection is held in page state keyed by GUID and survives block eviction, or the grid caps/warns when a selection can no longer be guaranteed. A test (or a recorded manual measurement against a >2,000-row org) establishes the behaviour at the pinned version first

### S43 — org_id=None basic-auth lands in a nondeterministic org

`P3` · **open** · protected: yes (`ts_admin/config.py`, `ts_admin/ts_client/auth.py`)

`BasicAuth` omits `org_id` when it is `None`, and the spec states that a token minted with no `org_id` and no secret key logs the user into "the Org context of their **previous login session**" — i.e. whichever org the admin last used in the ThoughtSpot browser UI. So any token-scoped call made through a basic-auth connection with no org selected lands in a non-deterministic org. `_sync_tags` does pass `org_id` so it is protected today, but the next `build_auth_strategy()` call with no org on a token-scoped endpoint is a coin flip that presents as "the sync worked yesterday and returned different data today". `TrustedAuth` is deterministic (the secret key's org)

**Acceptance criteria:** `BasicAuth`/`TrustedAuth` with `org_id=None` either default to `0` explicitly or log a warning naming the resolved org; after login the session's actual org is asserted via `GET /auth/session/user` -> `current_org.id` (see S42) and a mismatch fails loudly rather than silently caching another org's content. A unit test covers the `org_id=None` path

### S45 — COLLECTION objects are never synced

`P3` · **open** · protected: no

`COLLECTION` is a first-class ThoughtSpot object type that this app never syncs — 6 exist on ps-internal-prod. It is in the `type` enum for `metadata/search`, `security/metadata/share` (with dual `share_mode` + `content_share_mode`), `tags/assign` and `principals/fetch-permissions`, so collections are invisible in the Metadata Explorer, unshareable via Bulk Sharing, and un-archivable. `INSIGHT_SPEC` (7 on the same cluster) and the `PRIVATE_WORKSHEET` subtype are likewise unqueried

**Acceptance criteria:** `COLLECTION` is synced into `CachedMetadata` like any other type and appears in the Metadata Explorer with a label in `TYPE_LABELS`; sharing a collection sends `content_share_mode` as well as `share_mode`, since the two control different things and omitting the former silently defaults collection contents to `READ_ONLY`. A decision on `INSIGHT_SPEC`/`PRIVATE_WORKSHEET` is recorded either way

### W1 — Add mypy to CI

`P3` · **open** · protected: yes (`.github/workflows/*`)

Add `mypy ts_admin/` to CI

**Acceptance criteria:** `mypy ts_admin/` runs in the CI `backend` job and is green (baseline existing errors if needed, with a tracking note)

### W4 — permission_type silently ignored below 10.3 (DEFINED vs EFFECTIVE)

`P3` · **open** · protected: no

`permission_type` on `security/metadata/fetch-permissions` is tagged **Version: 10.3.0.cl or later**, and below that release the key is silently ignored (v2 drops unknown body keys without erroring). The difference is not cosmetic: measured on ps-internal-prod, `DEFINED` returns 0 principals for a liveboard where the effective set is 138. So a customer below 10.3 sees the EFFECTIVE set in the Metadata Explorer drawer and in the bulk-sharing preview diff while the UI labels it as direct shares — the exact inverse of the bug fixed in `be64fd5`, and with no warning

**Acceptance criteria:** The permissions path is version-aware: when the cluster's `release_version` parses below 10.3.0, the drawer and the sharing preview either label their result as effective access or the direct/effective split is disabled with an explicit message. A unit test drives both branches off a stubbed `release_version`

### W5 — metadata/search paginated outside the documented contract

`P3` · **open** · protected: no

`metadata/search` is paginated with `include_stats` and `include_details`, neither of which is on the documented list of parameters that support pagination — the reference says "if you are using other parameters to search metadata, set `record_size` to `-1` and `record_offset` to `0`". Tested live and it currently holds (3x200 pages = 365 records, 365 unique, identical set to `record_size: -1`, zero duplicates), so this is a documented-contract violation rather than an observed bug — but it is precisely the shape that silently drops objects on a larger cluster or a future release, and "objects mysteriously missing from the cache" is a symptom this project has chased before

**Acceptance criteria:** For each of the seven `search_metadata` specs, a live comparison on the largest available org asserts the paged GUID set equals the `record_size: -1` GUID set with zero duplicates, run against both live clusters. Any spec that diverges switches to `record_size: -1`. The result is recorded in `docs/org-memory/codebase.md` so the next person does not re-derive it

### M1 — Single-source the protected-path list

`P4` · **open** · protected: yes (`.github/workflows/*`, `CLAUDE.md`)

Single-source the protected-path list

**Acceptance criteria:** The protected-path patterns live in ONE place; the `guard` job and CLAUDE.md reference/derive from it (or a check fails on drift), so the two can't silently diverge

## In review

### S27 — Lineage unit suite is mutation-vacuous

`P1` · **in-review** · protected: no

**Blocks any re-attempt of S7.** The lineage unit suite is mutation-vacuous: on the rejected S7 diff, 6 of 7 mutations to `build_column_map` left all 16 tests in `tests/unit/test_lineage_columns.py` green — including one making the builder never re-crawl any liveboard ever again. No test in the file seeds a **future** `lb_modified`, so "a genuinely changed liveboard is re-exported" has never been asserted; marker/watermark org-scoping and the write-ordering invariant were likewise unguarded

**Acceptance criteria:** Every behavioural predicate in `build_column_map`'s incremental path is killed by at least one test: deleting `_changed`'s `modified_at > last_built` comparison, dropping `org_id` from any watermark read/write, and reordering the post-persist write each turn at least one test red. The mutation list and its results are recorded in [docs/dev/TESTING.md](docs/dev/TESTING.md) so the next lineage change starts from a suite that can detect its own failure modes

### M2 — PR #14 silently reverted the vitest wiring

`P2` · **in-review** · protected: yes (`.github/workflows/*` for the CI half)

The PR #14 merge silently reverted PR #12's vitest wiring, leaving `npm test` red on `main` with no gate to catch it

**Acceptance criteria:** `frontend/vitest.config.mts`, `vitest.setup.ts`, the Legend test, and the testing-library/jsdom devDeps are restored and `npm test` is green; a CI gate (or the S1 CI wiring) runs `npm test` so a future merge cannot silently delete the suite again

### S23 — Interrupted metadata sync reads as fully synced

`P2` · **in-review** · protected: no

`_sync_metadata` is delete-all-then-repage-in-spec-order, so an interrupted metadata sync leaves a **non-empty but truncated** cache (liveboards + answers present, every model and table missing) that reads as fully synced

**Acceptance criteria:** An interrupted metadata sync is distinguishable from a complete one — either the delete+repopulate happens in one transaction, or a completeness marker (e.g. the `sync_log` SUCCESS row) is required before any consumer treats `CachedMetadata` as authoritative; a test simulates a mid-pagination failure and asserts the cache is not reported as synced

### S1 — Wire frontend vitest into the suite

`P3` · **in-review** · protected: no

Wire frontend `vitest` into the suite

**Acceptance criteria:** `cd frontend && npm test` runs at least one real component test and passes; CI `frontend` job runs it; [docs/dev/TESTING.md](docs/dev/TESTING.md) updated to drop "not yet wired"

### S33 — Topbar sync label/color has no test

`P3` · **in-review** · protected: no

The Topbar sync label/color has no test: S23 fixed a fail-open where `IN_PROGRESS` fell through to the "Synced Xm ago" branch and rendered green, and the fix (`"Syncing…"` + accent) is verified by reading only. `tsc` and `next build` cannot catch a wrong string or token, and vitest is not a CI gate, so a future edit could silently restore the fail-open with every gate green

**Acceptance criteria:** `frontend/components/Shell/Topbar.tsx`'s `buildSyncLabel`/`buildSyncColor` are covered for every `SyncStatus` value including `IN_PROGRESS`, so a status with no branch fails a test rather than rendering as healthy

### M6 — No REJECT route when acceptance criteria are themselves the bug

`P2` · **in-review** · protected: no

The org model has no defined route for "the backlog row's **acceptance criteria** are themselves the bug". S6's criteria mandated deleting an edge whose source liveboard is unchanged — by definition a row the run cannot rebuild — so no safe implementation existed, but a cycle may not edit criteria and the only available move was to improvise a records-only PR and stop

**Acceptance criteria:** The improve-cycle skill defines an explicit **REJECT** outcome: when research or review shows a row's acceptance criteria cannot be satisfied safely, the cycle stops before shipping, leaves the row `open`, files the evidence + a proposed re-scope as a new row, and reports to the human — with the rejected implementation pushed (not PR'd) for inspection. The path is documented so it doesn't have to be reinvented per cycle

### M9 — Criteria-prescribed mechanisms are never soundness-checked before build

`P2` · **in-review** · protected: no

A cycle may not edit acceptance criteria, but nothing requires it to check whether the criteria's **prescribed mechanism** is sound before building. S7's criteria name a specific implementation ("keyed off a persisted 'liveboard tier last built' marker"), the CEO designed to it, and three review lenses then proved that mechanism removes a recovery path the criteria never mentioned. The rejected S6 diff failed the same way one cycle earlier

**Acceptance criteria:** The improve-cycle skill requires the research step to explicitly answer "what does the current code do that the criteria's prescribed mechanism would remove?" and to report any load-bearing behaviour that no test names, BEFORE design; a criteria-mandated mechanism that fails that check is escalated under the M6 REJECT path instead of built

## Feedback (user-reported)

External user feedback lands here first. Each row was triaged against the code
on the date noted; **P is a proposed priority** — the human confirms or re-sets
it. When a feedback row duplicates an existing S/W row, it points at that row
instead of getting its own work stream. Same bright-line rules as above.

Batch 1 received 2026-08-24 (SE Demo evaluation). Triage 2026-08-24: none fully
done; F3/F6/F11 partially covered or already filed.

### F1 — Connections list page with per-connection object counts

`P3` · Feature · **open** · protected: yes (`tests/integration/test_cluster_isolation.py`)

**Ask:** **Connections list page** — list data connections (Snowflake, Databricks, …) with details and an object count per connection, so empty connections are findable

**Triage + acceptance criteria:** NOT DONE. Today connections appear only as name-only lineage nodes (built from TML `CONNECTS` edges, `lineage_service.py:1339`), so a connection never referenced by TML is invisible. `ts_client.list_connections()` (`client.py:1187`) exists but returns only `{id, name}` and is used solely for lineage GUID resolution. Criteria: a Connections view lists every connection from `connection/search` (synced into a cluster+org-scoped cache table) with type/details and a per-connection object count; zero-object connections are visible; the list endpoint is registered in `READ_ENDPOINTS`

### F2 — Connection + external db/schema/table on the table list and in lineage

`P3` · Feature · **open** · protected: no

**Ask:** **Table list shows connection + external db/schema/table**, and lineage traces model → table → connection → external object

**Triage + acceptance criteria:** PARTIAL. The lineage trace model → table → connection already works end-to-end (upstream BFS follows `USES` then `CONNECTS`). But connection nodes carry a name only — the TML `db:`/`schema:` fields are never parsed (`_parse_physical_source`, `lineage_service.py:443-461`) — and `CachedMetadata` has no connection/physical-source fields, so the Metadata Explorer table list cannot show them. Criteria: TABLE rows in the metadata grid show connection name and external database/schema/table; connection nodes in lineage expose the same; the TML parse captures `db`/`schema`

### F3 — 'Created by' on the users list; 'Created' date on the groups list

`P4` · Feature · **open** · protected: no

**Ask:** **"Created by" on the users list; "Created" date on the groups list**

**Triage + acceptance criteria:** PARTIAL — each grid has the inverse of what's asked. Groups already show "Created by" (`groups.tsx:121`) and `created_at` is already synced, serialized, sortable, and typed — the Created column is one ColDef copy of the Modified entry. Users show "Created" but have no creator: `TSUser`/`CachedUser` carry no author field, so first verify live whether `users/search` returns one at all; if yes, mirror the group author chain (model field → sync → self-join name resolution in `group_service.py:87`)

### F8 — CSV export for Users and Groups

`P4` · Feature · **open** · protected: no

**Ask:** **CSV export for Users and Groups**

**Triage + acceptance criteria:** NOT DONE, trivial. The existing pattern is frontend-only `gridApi.exportDataAsCsv()` (metadata.tsx:127, archiver, sharing, deleter history); users/groups pages already hold the `gridRef`. Criteria: both pages get the same Export CSV button. Known shared limitation (all four existing sites): infinite row model exports only cached blocks, not the full server-side set — matching existing behavior is in scope, a full-export endpoint is not

### F9 — Export tagged content to TML without deleting

`P3` · Feature · **open** · protected: no

**Ask:** **Export tagged content to TML without deleting** (cs_tools parity — export first, delete later once trusted)

**Triage + acceptance criteria:** NOT DONE. TML export exists only inside the delete pipeline (`deletion_service._execute_delete` → `_export_tml_resilient`); the archiver's `action` literal is tag/untag/delete only, and `download_tml` requires an already-deleted `ArchiveRecord`. Criteria: an export-only action reuses `_export_tml_resilient` without deleting, downloadable as a bundle; export-only `ArchiveRecord`s are distinguishable so restore does not offer to re-import objects that were never deleted; non-destructive, but registered wherever the safety tests require

### F10 — Transfer ownership of selected objects from the Metadata screen

`P3` · Feature · **open** · protected: yes (`tests/integration/test_dryrun_safety.py`)

**Ask:** **Transfer ownership of selected objects from the Metadata screen** (today transfer is all-objects-of-one-user, from the Users screen)

**Triage + acceptance criteria:** NOT DONE, but the client call is object-scoped already: `assign_metadata_owner` (`client.py:943`) takes `object_ids`, and `execute_transfer` chunks it — only the preview/record path is user-shaped. Criteria: metadata grid gains multi-select + a Transfer action (reusing `UserPicker`/the transfer modal); new `POST /metadata/transfer-owner` follows the full write pattern — verify live, dry-run first, audit log after — and is registered in `DRYRUN_ENDPOINTS`

### F11 — Select-all on the Archiver results grid

`P3` · Feature · **open** · protected: no

**Ask:** **Select-all on the Archiver results grid**

**Triage + acceptance criteria:** NOT DONE — deliberately: `headerCheckboxSelection` is unsupported on the infinite row model (documented in `Deleter/columns.ts:61-73`), and grid-held selection already silently drops past ~2,000 rows (**S41**). Criteria: a "Select all N matching" affordance backed by filter-criteria selection (execute accepts the same filter params as `/archiver/results`, or selection moves to page state keyed by GUID) — must resolve, not worsen, S41; the dry-run count reflects the true N

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
- 2026-08-18 (M6/M9 + M8/M7 halves, skill hardening): Folded the process rows
  whose acceptance criteria live in non-protected files into the `improve-cycle`
  skill and the agent briefs, at the human's direction. **M6** — the REJECT
  outcome is now a defined section in the skill (stop before PR, push for
  inspection, leave the row open, file re-scope rows against `main`, ship what
  outlives the reject). **M9** — the research phase and the `researcher` brief
  now carry the mandatory "what would the prescribed mechanism REMOVE, and what
  load-bearing behaviour has no test?" gate, routing failures to REJECT. Both →
  `in-review`. **M8 (non-protected half)** — detached-worktree isolation is now
  the DEFAULT in the skill, `reviewer`, and `qa-verifier` briefs (QA also reports
  the tree state it observed); the CLAUDE.md half stays open for a human. **M7
  (skill half)** — Records now requires pruning stale facts; the README/cap
  wording remains. Also added a "ThoughtSpot ground truth (SpotterCode MCP)"
  section: docs tools are always open (curl JSON-RPC fallback for department
  agents), `execute-thoughtspot-code` is probed per cycle (auth flip-flops —
  verified unauthenticated then authenticated on the same day), reads only,
  never `confirm_write_operations` in a cycle. S33's lessons landed as defaults
  too: CEO writes no product code; visual changes need a measured visual
  artifact; new-row criteria are written against `main`.
